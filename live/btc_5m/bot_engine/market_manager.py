# -*- coding: utf-8 -*-
"""
market_manager.py — Polymarket market lifecycle for ONE coin.

Responsibilities:
  * Parse / build slug + URL + 5-min suffix arithmetic.
  * Resolve target price from /markets API + page HTML + __NEXT_DATA__ + Playwright.
  * Stream order-book updates from the Polymarket WebSocket.
  * Orchestrate market rollover every 5 minutes.

One MarketManager per coin. The multi-coin master spawns N managers, one for
each coin in COIN_PARAMS, all sharing the wallet but each with its own
order-book state.

NOTE: distance / strategy decisions live in strategy.py — this module only
maintains the market context (what slug we're on, what's in the order book,
what's the target price).
"""
import asyncio
import json
import math
import re
import ssl
import time
from typing import Callable, Dict, List, Optional, Tuple

import requests
import websockets

from bot_config import (
    GAMMA_MARKETS_BY_SLUG,
    POLY_WS_URL,
    HEARTBEAT_EVERY_SEC,
    RENDER_RETRY_WINDOW_SEC,
    RENDER_RETRY_INTERVAL_SEC,
)

try:
    from playwright.async_api import async_playwright
except Exception:
    async_playwright = None


def _safe_float(x):
    try:
        v = float(x)
        if math.isfinite(v):
            return v
        return None
    except Exception:
        return None


class MarketManager:
    """Lifecycle manager for one coin's Polymarket markets."""

    def __init__(self, coin: str, binance_engine, log_event: Callable[[str, str, str], None]):
        self.coin = coin
        self.binance = binance_engine
        self.log_event = log_event  # closure: log_event(slug, event, detail)

        self.current: Dict = {
            "input_url": None,
            "prefix": None,
            "base_suffix": None,
            "current_suffix": None,
            "slug": None,
            "url": None,
            "yes_token": None,
            "no_token": None,
            "question": None,
            "end_date": None,
            "target_price": None,
            "target_source": None,
            "target_event_meta": None,
            "target_line": None,
            "target_strike": None,
            "target_question": None,
            "target_rendered_page": None,
            "target_binance_prev_5m_close": None,
            "target_binance_open": None,
            "render_retry_attempts": 0,
            "render_retry_last_sec": None,
            "render_retry_status": "idle",
            "render_retry_last_source": "-",
            "render_retry_last_error": "-",
        }
        self.prices = {
            "UP": {"best_bid": None, "best_ask": None, "last_trade": None, "updated_at": 0.0, "asks": []},
            "DOWN": {"best_bid": None, "best_ask": None, "last_trade": None, "updated_at": 0.0, "asks": []},
        }
        self.meta = {"markets_scanned": 0, "last_rollover": "-"}
        self._render_task: Optional[asyncio.Task] = None

    @staticmethod
    def parse_initial_url(url: str) -> Tuple[str, str, int]:
        url = url.strip()
        if "/event/" not in url:
            raise ValueError("URL must contain /event/")
        slug = url.split("/event/")[1].strip().strip("/")
        suffix = slug.split("-")[-1]
        if not suffix.isdigit():
            raise ValueError("Bad slug format")
        prefix = slug[:-(len(suffix))]
        return slug, prefix, int(suffix)

    @staticmethod
    def build_slug_from_suffix(prefix: str, suffix: int) -> str:
        return f"{prefix}{suffix}"

    @staticmethod
    def build_url_from_slug(slug: str) -> str:
        return f"https://polymarket.com/event/{slug}"

    def seconds_from_market_start(self) -> int:
        if self.current["current_suffix"] is None:
            return 0
        return max(0, int(time.time()) - int(self.current["current_suffix"]))

    @staticmethod
    def extract_target_from_question(question: str) -> Optional[float]:
        if not question:
            return None
        m = re.search(r"(?:above|below)\s*\$?([0-9]{2,3}(?:,[0-9]{3})+|[0-9]{4,})", question, re.IGNORECASE)
        if m:
            try:
                return float(m.group(1).replace(",", ""))
            except Exception:
                return None
        return None

    def extract_target_from_page(self, url: str) -> Optional[float]:
        try:
            r = requests.get(url, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
            r.raise_for_status()
            html = r.text
        except Exception:
            return None
        patterns = [
            r'Price\s*to\s*Beat[^\d$]{0,40}\$\s*([0-9]{1,3}(?:,[0-9]{3})*\.\d+)',
            r'PRICE\s*TO\s*BEAT[^\d$]{0,40}\$\s*([0-9]{1,3}(?:,[0-9]{3})*\.\d+)',
            r'priceToBeat[^0-9]{0,20}([0-9]{1,3}(?:,[0-9]{3})*\.\d+)',
        ]
        for pat in patterns:
            m = re.search(pat, html, re.IGNORECASE | re.DOTALL)
            if m:
                try:
                    return float(m.group(1).replace(",", ""))
                except Exception:
                    pass
        return None

    def extract_target_from_next_data(self, url: str, slug: str) -> Tuple[Optional[float], str]:
        """Browser-free target extraction via Polymarket's __NEXT_DATA__ JSON."""
        try:
            r = requests.get(url, timeout=20, headers={
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            })
            r.raise_for_status()
            html = r.text
        except Exception as e:
            return None, f"http_error:{type(e).__name__}"

        m = re.search(
            r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
            html, re.DOTALL,
        )
        if not m:
            return None, "next_data_missing"
        try:
            data = json.loads(m.group(1))
        except Exception as e:
            return None, f"json_parse_error:{type(e).__name__}"

        events: List[Tuple[str, dict]] = []

        def walk(obj, path=""):
            if isinstance(obj, dict):
                if "slug" in obj and ("eventMetadata" in obj or "priceToBeat" in obj):
                    events.append((path, obj))
                for k, v in obj.items():
                    walk(v, f"{path}.{k}" if path else k)
            elif isinstance(obj, list):
                for i, v in enumerate(obj):
                    walk(v, f"{path}[{i}]")

        walk(data)

        def read_price_to_beat(event: dict) -> Optional[float]:
            meta = event.get("eventMetadata") or {}
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}
            for d in (meta, event):
                if not isinstance(d, dict):
                    continue
                for k in ("priceToBeat", "price_to_beat", "targetPrice", "target_price"):
                    v = d.get(k)
                    if v is None:
                        continue
                    try:
                        x = float(v)
                    except (TypeError, ValueError):
                        continue
                    # NOTE: range check is BTC-specific (10k-500k). For multi-coin,
                    # callers should validate the price range themselves OR pass an
                    # expected range. Currently kept generous to accept ETH (~3-5k)
                    # via fallback validation in callers.
                    if 0.0001 <= x <= 5_000_000:
                        return x
            return None

        for path, ev in events:
            if ev.get("slug") == slug:
                target = read_price_to_beat(ev)
                if target is not None:
                    return target, "next_data_slug_match"

        m_suffix = re.search(r"(\d+)$", slug)
        epoch_str = m_suffix.group(1) if m_suffix else None
        if epoch_str:
            for path, ev in events:
                ev_slug = ev.get("slug") or ""
                m_ev = re.search(r"(\d+)$", ev_slug)
                if m_ev and m_ev.group(1) == epoch_str:
                    target = read_price_to_beat(ev)
                    if target is not None:
                        return target, "next_data_epoch_match"

        return None, f"no_match_among_{len(events)}_events"

    async def extract_target_from_rendered_page(self, url: str) -> Tuple[Optional[float], str, str]:
        if async_playwright is None:
            return None, "-", "playwright_missing"
        browser = None
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                page = await browser.new_page(viewport={"width": 1400, "height": 1200})
                await page.goto(url, wait_until="domcontentloaded", timeout=45000)
                await page.wait_for_timeout(1200)
                text_blocks = []
                for target in ["body", "main"]:
                    try:
                        txt = await page.text_content(target)
                        if txt:
                            text_blocks.append(txt)
                    except Exception:
                        pass
                try:
                    html = await page.content()
                    if html:
                        text_blocks.append(html)
                except Exception:
                    pass
                rendered_text = "\n".join(text_blocks)
                patterns = [
                    (r'Price\s*to\s*Beat\s*\$\s*([0-9]{1,3}(?:,[0-9]{3})*\.\d+)', "body_price_to_beat"),
                    (r'Price\s*To\s*Beat\s*\$\s*([0-9]{1,3}(?:,[0-9]{3})*\.\d+)', "body_price_to_beat"),
                    (r'PRICE\s*TO\s*BEAT\s*\$\s*([0-9]{1,3}(?:,[0-9]{3})*\.\d+)', "body_price_to_beat"),
                    (r'Price\s*to\s*Beat[^\d$]{0,80}\$\s*([0-9]{1,3}(?:,[0-9]{3})*\.\d+)', "body_price_to_beat"),
                ]
                for pat, source in patterns:
                    m = re.search(pat, rendered_text, re.IGNORECASE | re.DOTALL)
                    if m:
                        try:
                            return float(m.group(1).replace(",", "")), source, "-"
                        except Exception as e:
                            return None, source, str(e)
                return None, "-", "not_found"
        except Exception as e:
            return None, "-", str(e)
        finally:
            if browser is not None:
                try:
                    await browser.close()
                except Exception:
                    pass

    def ensure_target_price(self, slug: str, url: str, question: str,
                            current_target: Optional[float],
                            market_obj: Optional[dict] = None
                           ) -> Tuple[Optional[float], Optional[str],
                                      Optional[float], Optional[float],
                                      Optional[float], Optional[float],
                                      Optional[float]]:
        if current_target is not None:
            return current_target, "current", None, None, None, None, None

        event_meta_target = None
        line_target = None
        strike_target = None
        question_target = self.extract_target_from_question(question)

        if market_obj is not None:
            event_meta = market_obj.get("eventMetadata")
            if isinstance(event_meta, str):
                try:
                    event_meta = json.loads(event_meta)
                except Exception:
                    event_meta = {}
            if not isinstance(event_meta, dict):
                event_meta = {}

            event_meta_target = _safe_float(event_meta.get("priceToBeat"))
            line_target = _safe_float(market_obj.get("line"))
            strike_target = _safe_float(market_obj.get("strikePrice"))

        if event_meta_target is not None:
            return event_meta_target, "eventMetadata.priceToBeat", event_meta_target, line_target, strike_target, question_target, None
        if line_target is not None:
            return line_target, "line", event_meta_target, line_target, strike_target, question_target, None
        if strike_target is not None:
            return strike_target, "strikePrice", event_meta_target, line_target, strike_target, question_target, None
        if question_target is not None:
            return question_target, "question", event_meta_target, line_target, strike_target, question_target, None

        page_target = self.extract_target_from_page(url)
        if page_target is not None:
            try:
                self.log_event(slug, "TARGET_FROM_PAGE", f"target_price={page_target:.2f}")
            except Exception:
                pass
            return page_target, "page_html", event_meta_target, line_target, strike_target, question_target, page_target

        return None, None, event_meta_target, line_target, strike_target, question_target, None

    def fetch_market_by_slug(self, slug: str) -> Optional[dict]:
        r = requests.get(f"{GAMMA_MARKETS_BY_SLUG}/slug/{slug}", timeout=20)
        r.raise_for_status()
        m = r.json()
        if not m:
            return None
        raw_ids = m.get("clobTokenIds")
        token_ids = []
        if isinstance(raw_ids, list):
            token_ids = raw_ids
        elif isinstance(raw_ids, str):
            try:
                parsed = json.loads(raw_ids)
                if isinstance(parsed, list):
                    token_ids = parsed
            except Exception:
                token_ids = []
        if len(token_ids) < 2:
            return None
        question = m.get("question") or m.get("title") or ""
        market_url = self.build_url_from_slug(slug)
        target_price, target_source, event_meta_target, line_target, strike_target, question_target, rendered_page_target = self.ensure_target_price(
            slug=slug,
            url=market_url,
            question=question,
            current_target=None,
            market_obj=m,
        )
        return {
            "slug": slug,
            "url": market_url,
            "question": question,
            "end_date": m.get("endDate"),
            "yes_token": str(token_ids[0]),
            "no_token": str(token_ids[1]),
            "target_price": target_price,
            "target_source": target_source,
            "target_event_meta": event_meta_target,
            "target_line": line_target,
            "target_strike": strike_target,
            "target_question": question_target,
            "target_rendered_page": rendered_page_target,
            "target_binance_prev_5m_close": None,
            "target_binance_open": None,
            "render_retry_attempts": 0,
            "render_retry_last_sec": None,
            "render_retry_status": "idle",
            "render_retry_last_source": "-",
            "render_retry_last_error": "-",
        }

    def capture_binance_open_target(self) -> None:
        if self.current.get("target_binance_open") is None and self.binance.price is not None:
            self.current["target_binance_open"] = float(self.binance.price)

    def capture_binance_prev_5m_close_target(self) -> None:
        boundary_epoch = self.current.get("current_suffix")
        close_px = self.binance.close_for_boundary(boundary_epoch)
        if close_px is not None:
            self.current["target_binance_prev_5m_close"] = float(close_px)

    async def capture_rendered_page_target(self) -> None:
        if self.current.get("target_rendered_page") is not None:
            self.current["render_retry_status"] = "captured"
            return
        slug_at_start = self.current.get("slug")
        url_at_start = self.current.get("url") or ""
        self.current["render_retry_status"] = "trying"
        rendered_target, source, err = await self.extract_target_from_rendered_page(url_at_start)
        if self.current.get("slug") != slug_at_start:
            return
        self.current["render_retry_attempts"] = int(self.current.get("render_retry_attempts") or 0) + 1
        self.current["render_retry_last_sec"] = self.seconds_from_market_start()
        self.current["render_retry_last_source"] = source
        self.current["render_retry_last_error"] = err
        if rendered_target is not None:
            self.current["target_rendered_page"] = float(rendered_target)
            self.current["target_price"] = float(rendered_target)
            self.current["target_source"] = source or "body_price_to_beat"
            self.current["render_retry_status"] = "captured"
            try:
                self.log_event(self.current.get("slug") or "-", "TARGET_RENDERED_PAGE",
                               f"target_price={rendered_target:.2f} | source={source}")
            except Exception:
                pass
        else:
            self.current["render_retry_status"] = "retry_wait"

    async def maybe_retry_rendered_target(self, sec: int) -> None:
        if self.current.get("target_rendered_page") is not None:
            self.current["render_retry_status"] = "captured"
            return
        if sec < 0:
            return
        if sec > RENDER_RETRY_WINDOW_SEC:
            if self.current.get("render_retry_status") != "fallback":
                self.current["render_retry_status"] = "fallback"
            return
        last_sec = self.current.get("render_retry_last_sec")
        if last_sec is not None and (sec - int(last_sec)) < RENDER_RETRY_INTERVAL_SEC:
            return
        self.kickoff_render_target()

    def resolve_target_in_use(self) -> Tuple[Optional[float], Optional[str]]:
        target = self.current.get("target_rendered_page")
        if target is not None:
            return target, "rendered_page"
        for key, src in (
            ("target_event_meta", "event_meta_temp"),
            ("target_line", "line_temp"),
            ("target_strike", "strike_temp"),
            ("target_question", "question_temp"),
        ):
            v = self.current.get(key)
            if v is not None:
                return float(v), src
        return None, None

    def kickoff_render_target(self) -> None:
        if self.current.get("target_rendered_page") is not None:
            return
        if self._render_task is not None and not self._render_task.done():
            return
        self._render_task = asyncio.create_task(self.capture_rendered_page_target())

    async def load_initial_market_from_url(self, url: str) -> None:
        slug, prefix, suffix = self.parse_initial_url(url)
        self.current.update({
            "input_url": url,
            "prefix": prefix,
            "base_suffix": suffix,
            "current_suffix": suffix,
            "slug": slug,
            "url": self.build_url_from_slug(slug),
        })
        market = self.fetch_market_by_slug(slug)
        if not market:
            raise RuntimeError(f"market not found for slug: {slug}")
        self.current.update(market)
        self.current["market_loaded_at"] = time.time()
        self.capture_binance_prev_5m_close_target()
        self.capture_binance_open_target()
        await self.capture_rendered_page_target()
        self.log_event(slug, "INIT", f"initial slug={slug} up={market['yes_token']} down={market['no_token']}")

    def side_from_asset(self, asset_id: str) -> Optional[str]:
        if str(asset_id) == str(self.current["yes_token"]):
            return "UP"
        if str(asset_id) == str(self.current["no_token"]):
            return "DOWN"
        return None

    def update_from_best_bid_ask(self, msg: dict) -> None:
        asset_id = str(msg.get("asset_id") or "")
        side = self.side_from_asset(asset_id)
        if not side:
            return
        bid = _safe_float(msg.get("best_bid"))
        ask = _safe_float(msg.get("best_ask"))
        if bid is not None:
            self.prices[side]["best_bid"] = bid
        if ask is not None:
            self.prices[side]["best_ask"] = ask
        self.prices[side]["updated_at"] = time.time()

    def update_from_last_trade(self, msg: dict) -> None:
        asset_id = str(msg.get("asset_id") or "")
        side = self.side_from_asset(asset_id)
        if not side:
            return
        px = _safe_float(msg.get("price"))
        if px is not None:
            self.prices[side]["last_trade"] = px
            self.prices[side]["updated_at"] = time.time()

    def update_from_book_snapshot(self, msg: dict) -> None:
        asset_id = str(msg.get("asset_id") or "")
        side = self.side_from_asset(asset_id)
        if not side:
            return
        bids = msg.get("bids") or []
        asks = msg.get("asks") or []
        best_bid = None
        try:
            if bids:
                best_bid = max(float(x.get("price")) for x in bids if x.get("price") is not None)
        except Exception:
            best_bid = None
        ask_rows = []
        try:
            for x in asks:
                p = _safe_float(x.get("price"))
                s = _safe_float(x.get("size") or x.get("amount"))
                if p is not None:
                    ask_rows.append({"price": p, "size": s})
            ask_rows.sort(key=lambda z: z["price"])
        except Exception:
            ask_rows = []
        self.prices[side]["asks"] = ask_rows
        if best_bid is not None:
            self.prices[side]["best_bid"] = best_bid
        if ask_rows:
            self.prices[side]["best_ask"] = ask_rows[0]["price"]
        self.prices[side]["updated_at"] = time.time()

    def handle_ws_message(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except Exception:
            return
        if isinstance(msg, list):
            for item in msg:
                self.handle_ws_message(json.dumps(item))
            return
        if msg == {}:
            return
        event_type = msg.get("event_type")
        if event_type == "best_bid_ask":
            self.update_from_best_bid_ask(msg)
            return
        if event_type == "last_trade_price":
            self.update_from_last_trade(msg)
            return
        if "bids" in msg or "asks" in msg:
            self.update_from_book_snapshot(msg)
            return

    def build_subscribe_payload(self) -> dict:
        return {
            "type": "market",
            "assets_ids": [self.current["yes_token"], self.current["no_token"]],
            "custom_feature_enabled": True,
        }

    async def ws_heartbeat(self, ws) -> None:
        while True:
            try:
                await ws.send("{}")
            except Exception:
                return
            await asyncio.sleep(HEARTBEAT_EVERY_SEC)

    async def rollover_to_next_market(self, on_settle: Callable[[], None]) -> None:
        """Move to the next 5-min market. Caller passes `on_settle` which
        finalizes positions for the just-closed market BEFORE we reset state.
        """
        old_slug = self.current["slug"]
        old_suffix = self.current["current_suffix"]
        on_settle()  # caller (master/strategy) settles positions
        self.current["current_suffix"] = int(self.current["current_suffix"]) + 300
        self.current["slug"] = self.build_slug_from_suffix(self.current["prefix"], self.current["current_suffix"])
        self.current["url"] = self.build_url_from_slug(self.current["slug"])
        market = self.fetch_market_by_slug(self.current["slug"])
        if not market:
            raise RuntimeError(f"next market not found for slug: {self.current['slug']}")
        self.current.update(market)
        self.current["market_loaded_at"] = time.time()
        self._render_task = None
        self.capture_binance_prev_5m_close_target()
        self.capture_binance_open_target()
        self.kickoff_render_target()
        for side in ["UP", "DOWN"]:
            self.prices[side] = {"best_bid": None, "best_ask": None, "last_trade": None, "updated_at": 0.0, "asks": []}
        self.meta["markets_scanned"] += 1
        self.meta["last_rollover"] = f"{old_suffix} -> {self.current['current_suffix']}"
        self.log_event(old_slug or "-", "ROLLOVER", f"to {self.current['slug']}")

    def side_has_fresh_ask_for_current_market(self, side: str) -> bool:
        ask = self.prices[side]["best_ask"]
        updated_at = self.prices[side]["updated_at"]
        loaded_at = float(self.current.get("market_loaded_at") or 0.0)
        return ask is not None and updated_at is not None and updated_at >= loaded_at
