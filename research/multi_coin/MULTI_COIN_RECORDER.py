#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
WS_BINANCE_POLY_RESEARCH_RECORDER.py

Research recorder for Polymarket 5-minute BTC Up/Down markets.

Purpose:
- Record Binance BTC price and Polymarket UP/DOWN order book at the same time.
- Record target and distance on every second.
- Clear old recorder data at startup so old/new runs never mix.
- Produce simple CSV files for analysis without Excel.

Files created under: /root/data_ws_binance_poly_research/
- combined_per_second.csv   -> main file: time, binance, target, distance, UP/DOWN bid/ask/qty
- binance_ticks.csv         -> every Binance trade tick
- poly_book_ticks.csv       -> every Polymarket book/price update parsed
- markets.csv               -> each loaded market + target/tokens
- events.csv                -> start, rollover, errors, reconnects
- raw_poly_messages.jsonl   -> raw Polymarket WS messages for debugging

Run on the server:
    python3 WS_BINANCE_POLY_RESEARCH_RECORDER.py

At startup paste the active 5-minute Polymarket BTC event URL.
The recorder rolls to the next market every 300 seconds by changing the slug suffix.
"""

import asyncio
import csv
import json
import math
import os
import re
import shutil
import signal
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Deque, Dict, List, Optional, Tuple

import requests
import websockets

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None

# NOTE: BINANCE_WS and DATA_DIR are populated at runtime by async_main() based
# on the --coin / --ticker CLI args. The values below are placeholders so existing
# references in the file remain valid at import time.
BINANCE_WS = "wss://stream.binance.com:9443/ws/btcusdt@trade"
POLY_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
DATA_DIR = "data_ws_binance_poly_research"

# Multi-coin runtime configuration. Set by main() from CLI args.
COIN: str = "BTC"
BINANCE_TICKER: str = "BTCUSDT"
SLUG_PREFIX: str = "btc-updown-5m-"

# Multi-window configuration. WINDOW set by main() based on --window CLI arg.
# Supported values: "5m", "15m", "1h", "4h", "1d"
WINDOW: str = "5m"

# Per-window: step in seconds + slug pattern type ("epoch" or "calendar_h" or "calendar_d")
WINDOW_CONFIG = {
    "5m":  {"step": 300,    "pattern": "epoch",      "name_style": "short"},
    "15m": {"step": 900,    "pattern": "epoch",      "name_style": "short"},
    "4h":  {"step": 14400,  "pattern": "epoch",      "name_style": "short"},
    "1h":  {"step": 3600,   "pattern": "calendar_h", "name_style": "long"},
    "1d":  {"step": 86400,  "pattern": "calendar_d", "name_style": "long"},
}

COIN_SHORT_NAMES = {"BTC":"btc", "ETH":"eth", "SOL":"sol", "XRP":"xrp", "DOGE":"doge", "BNB":"bnb", "HYPE":"hype"}
COIN_LONG_NAMES  = {"BTC":"bitcoin", "ETH":"ethereum", "SOL":"solana", "XRP":"xrp", "DOGE":"dogecoin", "BNB":"bnb", "HYPE":"hype"}

# Polymarket's own Chainlink RTDS WebSocket — same source they use for market resolution.
# The price at sec=0 of each market becomes our authoritative target_price.
CHAINLINK_WS = "wss://ws-live-data.polymarket.com"
CHAINLINK_SYMBOL: str = "btc/usd"   # set by main() based on coin

COIN_TO_CHAINLINK_SYMBOL = {
    "BTC":  "btc/usd",
    "ETH":  "eth/usd",
    "SOL":  "sol/usd",
    "XRP":  "xrp/usd",
    "DOGE": "doge/usd",
    "BNB":  "bnb/usd",
    "HYPE": "hype/usd",
}
HTTP_TIMEOUT = 20
SCREEN_REFRESH_SEC = 1.0
STALE_AFTER_SEC = 10.0
MAX_HISTORY_SEC = 7200
RAW_POLY_SAVE = False  # CHANGED 2026-05-01 — raw .jsonl was 25GB/day per coin, would fill disk in 10h with 6 coins. Parsed data is in poly_book_ticks.csv anyway.

ANSI_RESET = "\033[0m"
ANSI_GREEN = "\033[32m"
ANSI_RED = "\033[31m"
ANSI_YELLOW = "\033[33m"
ANSI_BOLD = "\033[1m"
ANSI_CYAN = "\033[36m"


def now_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def ts_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def clear_screen() -> None:
    print("\033[2J\033[H", end="")


def fmt(v: Optional[float], digits: int = 2) -> str:
    if v is None:
        return "-"
    try:
        return f"{float(v):,.{digits}f}"
    except Exception:
        return str(v)


def color_delta(v: Optional[float], digits: int = 2) -> str:
    if v is None:
        return "-"
    s = f"{float(v):+,.{digits}f}"
    if v > 0:
        return f"{ANSI_GREEN}{s}{ANSI_RESET}"
    if v < 0:
        return f"{ANSI_RED}{s}{ANSI_RESET}"
    return s


def safe_float(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        if math.isfinite(x):
            return x
        return None
    except Exception:
        return None


def now_epoch_s() -> int:
    return int(time.time())


def floor_to_5m_epoch(epoch_s: Optional[int] = None) -> int:
    """Legacy. Use floor_to_window_epoch() instead. Kept for backward compat."""
    if epoch_s is None:
        epoch_s = now_epoch_s()
    return (epoch_s // 300) * 300


def _et_tz():
    """Return the ET timezone (handles EDT/EST automatically). Falls back to a
    fixed -4 offset (EDT) if zoneinfo is unavailable."""
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/New_York")
    except Exception:
        from datetime import timezone as _tz, timedelta as _td
        return _tz(_td(hours=-4))


def floor_to_window_epoch(epoch_s: Optional[int] = None) -> int:
    """Round time down to the current WINDOW boundary.
    For 1h/1d (calendar slugs) the floor is computed in ET, NOT UTC, so the
    floored epoch maps to the correct ET hour/date."""
    if epoch_s is None:
        epoch_s = now_epoch_s()
    cfg = WINDOW_CONFIG[WINDOW]
    pattern = cfg["pattern"]
    if pattern == "epoch":
        # 5m, 15m, 4h — UTC-aligned epoch boundaries
        step = cfg["step"]
        return (epoch_s // step) * step
    elif pattern == "calendar_h":
        from datetime import datetime as _dt
        et = _dt.fromtimestamp(epoch_s, tz=_et_tz())
        floored = et.replace(minute=0, second=0, microsecond=0)
        return int(floored.timestamp())
    elif pattern == "calendar_d":
        from datetime import datetime as _dt
        et = _dt.fromtimestamp(epoch_s, tz=_et_tz())
        floored = et.replace(hour=0, minute=0, second=0, microsecond=0)
        return int(floored.timestamp())
    raise ValueError(f"unknown window pattern: {pattern}")


def build_window_slug(epoch_s: Optional[int] = None) -> str:
    """Build the Polymarket slug for the current WINDOW + COIN at the given epoch.
    Returns the slug WITHOUT the URL prefix (e.g. 'btc-updown-5m-1777748700' or
    'bitcoin-up-or-down-may-2-2026-12pm-et')."""
    cfg = WINDOW_CONFIG[WINDOW]
    if epoch_s is None:
        epoch_s = now_epoch_s()
    if cfg["pattern"] == "epoch":
        ep = (epoch_s // cfg["step"]) * cfg["step"]
        short = COIN_SHORT_NAMES.get(COIN, COIN.lower())
        return f"{short}-updown-{WINDOW}-{ep}"
    elif cfg["pattern"] in ("calendar_h", "calendar_d"):
        from datetime import datetime as _dt
        ts = _dt.fromtimestamp(epoch_s, tz=_et_tz())
        month = ts.strftime("%B").lower()
        day = ts.day
        year = ts.year
        long = COIN_LONG_NAMES.get(COIN, COIN.lower())
        if cfg["pattern"] == "calendar_h":
            hour_12 = ts.hour % 12 or 12
            ampm = "am" if ts.hour < 12 else "pm"
            return f"{long}-up-or-down-{month}-{day}-{year}-{hour_12}{ampm}-et"
        else:  # calendar_d
            return f"{long}-up-or-down-on-{month}-{day}-{year}"
    raise ValueError(f"unknown window pattern: {cfg['pattern']}")


def sec_from_start(market_epoch: Optional[int]) -> Optional[int]:
    if market_epoch is None:
        return None
    return max(0, now_epoch_s() - int(market_epoch))


def extract_slug_and_suffix(url: str) -> Tuple[str, int]:
    m = re.search(r"/event/([^/?#]+)", url.strip())
    if not m:
        raise ValueError("Could not extract slug from URL. Paste a Polymarket /event/ URL.")
    slug = m.group(1).strip().strip("/")
    sm = re.search(r"(\d+)$", slug)
    if not sm:
        raise ValueError("Slug does not end with numeric 5-minute suffix.")
    return slug, int(sm.group(1))


def slug_with_new_suffix(slug: str, new_suffix: int) -> str:
    return re.sub(r"\d+$", str(new_suffix), slug)


def event_url_from_slug(slug: str) -> str:
    return f"https://polymarket.com/event/{slug}"


def parse_target_from_question(question: str) -> Optional[float]:
    if not question:
        return None
    nums = re.findall(r"(?<!\d)(\d{2,3}(?:,\d{3})+|\d{4,6})(?!\d)", question.replace("$", ""))
    candidates: List[float] = []
    for n in nums:
        try:
            x = float(n.replace(",", ""))
            if 10000 <= x <= 500000:
                candidates.append(x)
        except Exception:
            pass
    return candidates[0] if candidates else None




def parse_target_from_market_obj(market_obj: dict, question: str = "") -> Optional[float]:
    """Extract Price To Beat from Gamma market object when available."""
    try:
        event_meta = market_obj.get("eventMetadata") or market_obj.get("event_metadata") or {}
        if isinstance(event_meta, str):
            try:
                event_meta = json.loads(event_meta)
            except Exception:
                event_meta = {}
        if isinstance(event_meta, dict):
            for key in ("priceToBeat", "price_to_beat", "targetPrice", "target_price"):
                x = safe_float(event_meta.get(key))
                if x is not None and 10000 <= x <= 500000:
                    return x
        for key in ("priceToBeat", "price_to_beat", "targetPrice", "target_price", "line", "strikePrice", "strike_price"):
            x = safe_float(market_obj.get(key))
            if x is not None and 10000 <= x <= 500000:
                return x
        return parse_target_from_question(question)
    except Exception:
        return parse_target_from_question(question)

def extract_target_from_next_data(url: str, slug: str) -> Optional[float]:
    """Browser-free target extraction. Polymarket pages embed all market metadata
    in a <script id="__NEXT_DATA__"> JSON tag. Fetch the page with plain HTTP,
    parse the JSON, find the event whose slug matches our market, and return
    its eventMetadata.priceToBeat. Avoids Playwright entirely.
    """
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT, headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ),
        })
        r.raise_for_status()
        html = r.text
    except Exception:
        return None
    m = re.search(r'<script[^>]*id="__NEXT_DATA__"[^>]*>(.*?)</script>',
                  html, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except Exception:
        return None
    events = []

    def walk(obj):
        if isinstance(obj, dict):
            if "slug" in obj and ("eventMetadata" in obj or "priceToBeat" in obj):
                events.append(obj)
            for v in obj.values():
                walk(v)
        elif isinstance(obj, list):
            for v in obj:
                walk(v)

    walk(data)

    def read_ptb(event):
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
                if 0 < x < 10_000_000:  # generous range — works for $0.10 (DOGE) up to $1M (extreme BTC)
                    return x
        return None

    # 1. exact slug match
    for ev in events:
        if ev.get("slug") == slug:
            v = read_ptb(ev)
            if v is not None:
                return v
    # 2. epoch-suffix match
    m_suf = re.search(r"(\d+)$", slug)
    epoch = m_suf.group(1) if m_suf else None
    if epoch:
        for ev in events:
            ev_slug = ev.get("slug") or ""
            m_ev = re.search(r"(\d+)$", ev_slug)
            if m_ev and m_ev.group(1) == epoch:
                v = read_ptb(ev)
                if v is not None:
                    return v
    return None


def extract_target_from_html(url: str) -> Optional[float]:
    """Fast HTTP fallback for Price to Beat / target from the public page HTML."""
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        html = r.text
    except Exception:
        return None
    patterns = [
        r'Price\s*to\s*Beat[^\d$]{0,120}\$\s*([0-9]{1,3}(?:,[0-9]{3})*\.\d+)',
        r'PRICE\s*TO\s*BEAT[^\d$]{0,120}\$\s*([0-9]{1,3}(?:,[0-9]{3})*\.\d+)',
        r'priceToBeat[^0-9]{0,80}([0-9]{1,3}(?:,[0-9]{3})*\.\d+)',
        r'targetPrice[^0-9]{0,80}([0-9]{1,3}(?:,[0-9]{3})*\.\d+)',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.IGNORECASE | re.DOTALL)
        if m:
            x = safe_float(m.group(1).replace(',', ''))
            if x is not None and 10000 <= x <= 500000:
                return x
    return None


def extract_target_from_rendered_page(url: str) -> Tuple[Optional[float], str]:
    """DISABLED 2026-05-02 — was crashing the server (6 Chromium browsers on
    4GB RAM caused load=33 and OOM). Chainlink WS now provides the canonical
    target via target_chainlink_at_open. This stub returns immediately so the
    fallback chain doesn't spawn browsers."""
    return None, "playwright_disabled_use_chainlink"


def _DISABLED_extract_target_from_rendered_page(url: str) -> Tuple[Optional[float], str]:
    """Reliable target capture using rendered Polymarket page.

    Returns: (target_price, error_text)
    """
    if sync_playwright is None:
        return None, "playwright_not_installed"
    browser = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            page = browser.new_page(
                viewport={"width": 1600, "height": 1400},
                user_agent=(
                    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            # Polymarket often renders Price To Beat after the first DOM load.
            page.wait_for_timeout(3500)

            texts: List[str] = []
            try:
                texts.append(page.locator("body").inner_text(timeout=10000))
            except Exception as e:
                texts.append(f"BODY_INNER_TEXT_ERROR {e}")
            try:
                texts.append(page.text_content("body") or "")
            except Exception:
                pass
            try:
                texts.append(page.content())
            except Exception:
                pass

            rendered = "\n".join(t for t in texts if t)

            # Most reliable: use the words Price To Beat, then find the first BTC-like dollar value after it.
            label_patterns = [
                r'Price\s*to\s*Beat',
                r'Price\s*To\s*Beat',
                r'PRICE\s*TO\s*BEAT',
            ]
            for label in label_patterns:
                m = re.search(label, rendered, re.IGNORECASE)
                if not m:
                    continue
                window = rendered[m.start():m.start() + 1200]
                nums = re.findall(r'\$\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.\d+)?)', window)
                for n in nums:
                    x = safe_float(n.replace(",", ""))
                    if x is not None and 10000 <= x <= 500000:
                        return x, "rendered_price_to_beat"

                # Fallback if the $ sign is separated or omitted in rendered text.
                nums = re.findall(r'(?<!\d)([0-9]{2,3}(?:,[0-9]{3})+(?:\.\d+)?)(?!\d)', window)
                for n in nums:
                    x = safe_float(n.replace(",", ""))
                    if x is not None and 10000 <= x <= 500000:
                        return x, "rendered_price_to_beat_no_dollar"

            # Secondary fallback: exact text patterns.
            patterns = [
                r'Price\s*to\s*Beat[\s\S]{0,300}?\$\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.\d+)?)',
                r'PRICE\s*TO\s*BEAT[\s\S]{0,300}?\$\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.\d+)?)',
                r'priceToBeat[^0-9]{0,120}([0-9]{1,3}(?:,[0-9]{3})+(?:\.\d+)?)',
            ]
            for pat in patterns:
                m = re.search(pat, rendered, re.IGNORECASE | re.DOTALL)
                if m:
                    x = safe_float(m.group(1).replace(",", ""))
                    if x is not None and 10000 <= x <= 500000:
                        return x, "rendered_regex"

            return None, "rendered_target_not_found"
    except Exception as e:
        return None, f"rendered_error:{type(e).__name__}:{e}"
    finally:
        if browser is not None:
            try:
                browser.close()
            except Exception:
                pass


@dataclass
class OrderBookSide:
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    ask_qty_best: float = 0.0
    ask_usd_best: float = 0.0
    qty_le_029: float = 0.0
    qty_le_030: float = 0.0
    qty_le_031: float = 0.0
    qty_le_032: float = 0.0
    qty_le_035: float = 0.0
    usd_le_029: float = 0.0
    usd_le_030: float = 0.0
    usd_le_031: float = 0.0
    usd_le_032: float = 0.0
    usd_le_035: float = 0.0
    updated_at: float = 0.0
    updates: int = 0

    def stale(self) -> bool:
        if self.updated_at <= 0:
            return True
        return (time.time() - self.updated_at) > STALE_AFTER_SEC


@dataclass
class MarketInfo:
    slug: str
    suffix: int
    market_epoch: int
    url: str
    question: str = ""
    start_iso: str = ""
    end_iso: str = ""
    target_price: Optional[float] = None     # legacy field (from Gamma/HTML/Playwright)
    target_chainlink_at_open: Optional[float] = None  # NEW — the canonical target = Chainlink price at first tick of this market
    up_token: Optional[str] = None
    down_token: Optional[str] = None


class CsvStore:
    def __init__(self, data_dir: str = DATA_DIR) -> None:
        self.data_dir = data_dir
        self.paths: Dict[str, str] = {}

    def init_clean(self) -> None:
        if os.path.exists(self.data_dir):
            shutil.rmtree(self.data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        self.paths = {
            "combined": os.path.join(self.data_dir, "combined_per_second.csv"),
            "binance_ticks": os.path.join(self.data_dir, "binance_ticks.csv"),
            "poly_ticks": os.path.join(self.data_dir, "poly_book_ticks.csv"),
            "markets": os.path.join(self.data_dir, "markets.csv"),
            "events": os.path.join(self.data_dir, "events.csv"),
            "raw_poly": os.path.join(self.data_dir, "raw_poly_messages.jsonl"),
            "market_outcomes": os.path.join(self.data_dir, "market_outcomes.csv"),
        }
        self._init_csv(self.paths["combined"], [
            "local_ts", "epoch_sec", "market_slug", "market_epoch", "sec_from_start",
            "binance_price", "binance_age_sec", "target_price", "distance_signed", "distance_abs",
            # NEW columns — Chainlink RTDS (canonical resolution source)
            "chainlink_price", "chainlink_age_sec",
            "target_chainlink_at_open", "distance_chainlink_signed", "distance_chainlink_abs",
            "up_bid", "up_ask", "up_last", "up_ask_qty_best", "up_usd_best",
            "up_qty_le_029", "up_usd_le_029", "up_qty_le_030", "up_usd_le_030",
            "up_qty_le_031", "up_usd_le_031", "up_qty_le_032", "up_usd_le_032",
            "up_qty_le_035", "up_usd_le_035", "up_age_sec",
            "down_bid", "down_ask", "down_last", "down_ask_qty_best", "down_usd_best",
            "down_qty_le_029", "down_usd_le_029", "down_qty_le_030", "down_usd_le_030",
            "down_qty_le_031", "down_usd_le_031", "down_qty_le_032", "down_usd_le_032",
            "down_qty_le_035", "down_usd_le_035", "down_age_sec",
            "delta_1s", "delta_5s", "delta_10s", "delta_30s", "volatility_30s",
            "poly_updates_total", "binance_ticks_total", "chainlink_ticks_total", "market_url",
        ])
        self._init_csv(self.paths["binance_ticks"], [
            "local_ts", "event_time_ms", "trade_time_ms", "price", "qty", "latency_ms", "raw_len"
        ])
        self._init_csv(self.paths["poly_ticks"], [
            "local_ts", "market_slug", "side", "event_type", "asset_id", "bid", "ask", "last",
            "ask_qty_best", "ask_usd_best", "qty_le_029", "usd_le_029", "qty_le_030", "usd_le_030",
            "qty_le_031", "usd_le_031", "qty_le_032", "usd_le_032", "qty_le_035", "usd_le_035",
            "raw_len"
        ])
        self._init_csv(self.paths["markets"], [
            "local_ts", "market_slug", "market_epoch", "url", "target_price", "up_token", "down_token",
            "start_iso", "end_iso", "question"
        ])
        self._init_csv(self.paths["market_outcomes"], [
            "local_ts", "market_slug", "market_epoch", "target_price", "final_binance_price",
            "final_distance_signed", "final_distance_abs", "winner_side", "source"
        ])
        self._init_csv(self.paths["events"], ["local_ts", "event", "detail"])
        open(self.paths["raw_poly"], "w", encoding="utf-8").close()

    @staticmethod
    def _init_csv(path: str, headers: List[str]) -> None:
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(headers)

    @staticmethod
    def append_csv(path: str, row: List) -> None:
        with open(path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)

    def event(self, event: str, detail: str) -> None:
        self.append_csv(self.paths["events"], [now_local(), event, detail])

    def raw_poly(self, raw: str) -> None:
        if not RAW_POLY_SAVE:
            return
        with open(self.paths["raw_poly"], "a", encoding="utf-8") as f:
            f.write(raw.replace("\n", " ") + "\n")


class RecorderState:
    def __init__(self) -> None:
        self.market: Optional[MarketInfo] = None
        self.base_slug: Optional[str] = None
        self.loaded_epoch: Optional[int] = None
        self.last_rollover_text: str = "-"
        self.binance_price: Optional[float] = None
        self.binance_update_ts: float = 0.0
        self.binance_status: str = "starting"
        self.binance_ticks_total: int = 0
        self.binance_history: Deque[Tuple[float, float]] = deque()
        self.up = OrderBookSide()
        self.down = OrderBookSide()
        self.poly_status: str = "starting"
        self.poly_updates_total: int = 0
        self.reconnects_binance: int = 0
        self.reconnects_poly: int = 0
        self.last_error: str = "-"
        self.target_status: str = "idle"
        self.target_attempts: int = 0
        self.target_source: str = "-"
        self.target_last_error: str = "-"
        self.started_at: float = time.time()
        self.should_stop: bool = False
        # NEW — Polymarket Chainlink RTDS feed (same source they use for resolution)
        self.chainlink_price: Optional[float] = None
        self.chainlink_update_ts: float = 0.0
        self.chainlink_ticks_total: int = 0
        self.chainlink_status: str = "starting"
        self.reconnects_chainlink: int = 0

    def distance_signed(self) -> Optional[float]:
        if self.market is None or self.market.target_price is None or self.binance_price is None:
            return None
        # Signed distance = current price - Price To Beat.
        # Positive means BTC is above target; negative means BTC is below target.
        return float(self.binance_price) - float(self.market.target_price)

    def distance_abs(self) -> Optional[float]:
        d = self.distance_signed()
        return abs(d) if d is not None else None

    def binance_age(self) -> Optional[float]:
        if self.binance_update_ts <= 0:
            return None
        return time.time() - self.binance_update_ts

    def side_age(self, side: OrderBookSide) -> Optional[float]:
        if side.updated_at <= 0:
            return None
        return time.time() - side.updated_at

    def delta(self, seconds_back: int) -> Optional[float]:
        if self.binance_price is None or not self.binance_history:
            return None
        target_ts = time.time() - seconds_back
        old = None
        for ts, price in reversed(self.binance_history):
            if ts <= target_ts:
                old = price
                break
        if old is None:
            return None
        return self.binance_price - old

    def volatility_30s(self) -> Optional[float]:
        vals = [p for ts, p in self.binance_history if ts >= time.time() - 30]
        if len(vals) < 3:
            return None
        try:
            return statistics.pstdev(vals)
        except Exception:
            return None


def fetch_market_info_for_slug(slug: str) -> MarketInfo:
    r = requests.get(GAMMA_MARKETS, params={"slug": slug}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    data = r.json()
    if isinstance(data, dict) and "markets" in data:
        markets = data["markets"]
    else:
        markets = data
    if not markets:
        raise RuntimeError(f"Gamma returned no market for slug={slug}")
    m0 = markets[0]
    question = m0.get("question") or m0.get("title") or ""
    start_iso = m0.get("startDate") or ""
    end_iso = m0.get("endDate") or ""
    raw_ids = m0.get("clobTokenIds") or []
    token_ids: List[str] = []
    if isinstance(raw_ids, str):
        try:
            parsed = json.loads(raw_ids)
            if isinstance(parsed, list):
                token_ids = [str(x) for x in parsed]
            else:
                token_ids = [x.strip() for x in raw_ids.split(",") if x.strip()]
        except Exception:
            token_ids = [x.strip() for x in raw_ids.split(",") if x.strip()]
    elif isinstance(raw_ids, list):
        token_ids = [str(x) for x in raw_ids]
    outcomes = m0.get("outcomes") or []
    if isinstance(outcomes, str):
        try:
            parsed = json.loads(outcomes)
            outcomes = parsed if isinstance(parsed, list) else []
        except Exception:
            outcomes = [x.strip() for x in outcomes.split(",") if x.strip()]
    outcome_map: Dict[str, str] = {}
    for i, name in enumerate(outcomes):
        if i < len(token_ids):
            outcome_map[str(name).strip().upper()] = str(token_ids[i])
    up_token = outcome_map.get("UP")
    down_token = outcome_map.get("DOWN")
    if (not up_token or not down_token) and len(token_ids) >= 2:
        up_token = up_token or str(token_ids[0])
        down_token = down_token or str(token_ids[1])
    suffix = int(re.search(r"(\d+)$", slug).group(1))
    url = event_url_from_slug(slug)
    target = parse_target_from_market_obj(m0, question)
    if target is None:
        # NEW 2026-05-01: try __NEXT_DATA__ HTTP-only extraction.
        # Polymarket changed Gamma — eventMetadata is now null for current
        # markets, so we must read priceToBeat from the page's __NEXT_DATA__.
        # This is fast, browser-free, and works under load (avoids Playwright
        # contention when many recorders run in parallel).
        target = extract_target_from_next_data(url, slug)
    if target is None:
        target = extract_target_from_html(url)
    if target is None:
        target, _target_render_error = extract_target_from_rendered_page(url)
    return MarketInfo(
        slug=slug,
        suffix=suffix,
        market_epoch=suffix,
        url=url,
        question=question,
        start_iso=start_iso,
        end_iso=end_iso,
        target_price=target,
        up_token=up_token,
        down_token=down_token,
    )


def _get_event_type(ev: dict) -> str:
    return str(ev.get("event_type") or ev.get("type") or ev.get("event") or ev.get("msg_type") or "")


def _levels_from_event(ev: dict, key: str) -> List[Tuple[float, float]]:
    levels = ev.get(key) or []
    parsed: List[Tuple[float, float]] = []
    if not isinstance(levels, list):
        return parsed
    for level in levels:
        px = qty = None
        if isinstance(level, dict):
            px = safe_float(level.get("price") or level.get("px"))
            qty = safe_float(level.get("size") or level.get("amount") or level.get("quantity") or level.get("qty"))
        elif isinstance(level, (list, tuple)) and len(level) >= 2:
            px = safe_float(level[0])
            qty = safe_float(level[1])
        if px is None or qty is None:
            continue
        parsed.append((px, qty))
    return parsed


def update_book_side(side_state: OrderBookSide, ev: dict) -> None:
    bid = safe_float(ev.get("best_bid") or ev.get("bid") or ev.get("b"))
    ask = safe_float(ev.get("best_ask") or ev.get("ask") or ev.get("a"))
    last = safe_float(ev.get("price") or ev.get("last_trade") or ev.get("last") or ev.get("p"))
    if bid is not None:
        side_state.bid = bid
    if ask is not None:
        side_state.ask = ask
    if last is not None:
        side_state.last = last

    asks = _levels_from_event(ev, "asks")
    if asks:
        asks.sort(key=lambda x: x[0])
        best_px, best_qty = asks[0]
        side_state.ask = best_px
        side_state.ask_qty_best = best_qty
        side_state.ask_usd_best = best_px * best_qty
        for threshold in (0.29, 0.30, 0.31, 0.32, 0.35):
            qty_sum = sum(qty for px, qty in asks if px <= threshold)
            usd_sum = sum(px * qty for px, qty in asks if px <= threshold)
            setattr(side_state, f"qty_le_{str(threshold).replace('0.', '0')}", qty_sum)
            setattr(side_state, f"usd_le_{str(threshold).replace('0.', '0')}", usd_sum)
        # Attribute names above would be qty_le_029, etc. Keep explicit assignment for safety.
        side_state.qty_le_029 = sum(qty for px, qty in asks if px <= 0.29)
        side_state.usd_le_029 = sum(px * qty for px, qty in asks if px <= 0.29)
        side_state.qty_le_030 = sum(qty for px, qty in asks if px <= 0.30)
        side_state.usd_le_030 = sum(px * qty for px, qty in asks if px <= 0.30)
        side_state.qty_le_031 = sum(qty for px, qty in asks if px <= 0.31)
        side_state.usd_le_031 = sum(px * qty for px, qty in asks if px <= 0.31)
        side_state.qty_le_032 = sum(qty for px, qty in asks if px <= 0.32)
        side_state.usd_le_032 = sum(px * qty for px, qty in asks if px <= 0.32)
        side_state.qty_le_035 = sum(qty for px, qty in asks if px <= 0.35)
        side_state.usd_le_035 = sum(px * qty for px, qty in asks if px <= 0.35)

    side_state.updated_at = time.time()
    side_state.updates += 1


def identify_side(state: RecorderState, ev: dict) -> Tuple[Optional[str], Optional[OrderBookSide]]:
    if state.market is None:
        return None, None
    asset = str(ev.get("asset_id") or ev.get("token_id") or ev.get("market") or ev.get("asset") or "")
    outcome = str(ev.get("outcome") or ev.get("side_label") or ev.get("name") or "").upper()
    if state.market.up_token and asset == str(state.market.up_token):
        return "UP", state.up
    if state.market.down_token and asset == str(state.market.down_token):
        return "DOWN", state.down
    if outcome == "UP":
        return "UP", state.up
    if outcome == "DOWN":
        return "DOWN", state.down
    return None, None


def parse_poly_message(state: RecorderState, raw: str, csvs: CsvStore) -> None:
    if RAW_POLY_SAVE:
        csvs.raw_poly(raw)
    try:
        msg = json.loads(raw)
    except Exception:
        return
    events: List[dict] = []
    if isinstance(msg, list):
        events = [x for x in msg if isinstance(x, dict)]
    elif isinstance(msg, dict):
        if isinstance(msg.get("data"), list):
            events = [x for x in msg["data"] if isinstance(x, dict)]
        else:
            events = [msg]
    raw_len = len(raw)
    for ev in events:
        side_name, side_state = identify_side(state, ev)
        if side_state is None or side_name is None:
            continue
        update_book_side(side_state, ev)
        state.poly_updates_total += 1
        csvs.append_csv(csvs.paths["poly_ticks"], [
            now_local(), state.market.slug if state.market else "-", side_name, _get_event_type(ev),
            str(ev.get("asset_id") or ev.get("token_id") or ev.get("market") or ""),
            side_state.bid, side_state.ask, side_state.last,
            side_state.ask_qty_best, side_state.ask_usd_best,
            side_state.qty_le_029, side_state.usd_le_029,
            side_state.qty_le_030, side_state.usd_le_030,
            side_state.qty_le_031, side_state.usd_le_031,
            side_state.qty_le_032, side_state.usd_le_032,
            side_state.qty_le_035, side_state.usd_le_035,
            raw_len,
        ])


async def binance_loop(state: RecorderState, csvs: CsvStore) -> None:
    while not state.should_stop:
        try:
            state.binance_status = "connecting"
            async with websockets.connect(BINANCE_WS, ping_interval=20, ping_timeout=20, max_size=2**22) as ws:
                state.binance_status = "live"
                csvs.event("BINANCE_CONNECTED", "connected")
                async for raw in ws:
                    if state.should_stop:
                        break
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    price = safe_float(msg.get("p") or msg.get("price"))
                    qty = safe_float(msg.get("q") or msg.get("qty"))
                    event_ms = int(msg.get("E") or 0) if msg.get("E") else None
                    trade_ms = int(msg.get("T") or 0) if msg.get("T") else None
                    if price is None:
                        continue
                    now_ts = time.time()
                    now_ms = int(now_ts * 1000)
                    state.binance_price = price
                    state.binance_update_ts = now_ts
                    state.binance_ticks_total += 1
                    state.binance_history.append((now_ts, price))
                    cutoff = now_ts - MAX_HISTORY_SEC
                    while state.binance_history and state.binance_history[0][0] < cutoff:
                        state.binance_history.popleft()
                    latency_ms = now_ms - trade_ms if trade_ms else None
                    csvs.append_csv(csvs.paths["binance_ticks"], [
                        now_local(), event_ms, trade_ms, price, qty, latency_ms, len(raw)
                    ])
        except Exception as e:
            state.reconnects_binance += 1
            state.binance_status = "reconnecting"
            state.last_error = f"binance: {type(e).__name__}: {e}"
            csvs.event("BINANCE_RECONNECT", state.last_error)
            await asyncio.sleep(2)


async def chainlink_loop(state: RecorderState, csvs: CsvStore) -> None:
    """Subscribe to Polymarket Chainlink RTDS WebSocket for the coin's symbol.
    The price stream here is the SAME source Polymarket uses for market resolution.
    The price at first tick of each market is the canonical priceToBeat / target."""
    sub_msg = {
        "action": "subscribe",
        "subscriptions": [{
            "topic": "crypto_prices_chainlink",
            "type": "*",
            "filters": '{"symbol":"' + CHAINLINK_SYMBOL + '"}',
        }],
    }
    while not state.should_stop:
        try:
            state.chainlink_status = "connecting"
            async with websockets.connect(CHAINLINK_WS, ping_interval=20, ping_timeout=20,
                                          max_size=2 ** 20) as ws:
                await ws.send(json.dumps(sub_msg))
                state.chainlink_status = "live"
                csvs.event("CHAINLINK_CONNECTED", f"symbol={CHAINLINK_SYMBOL}")
                async for raw in ws:
                    if state.should_stop:
                        return
                    try:
                        msg = json.loads(raw)
                    except Exception:
                        continue
                    if not isinstance(msg, dict) or msg.get("topic") != "crypto_prices_chainlink":
                        continue
                    payload = msg.get("payload") or {}
                    if not isinstance(payload, dict):
                        continue
                    sym = payload.get("symbol")
                    if sym != CHAINLINK_SYMBOL:
                        continue
                    val = safe_float(payload.get("value"))
                    if val is None:
                        continue
                    now_ts = time.time()
                    state.chainlink_price = val
                    state.chainlink_update_ts = now_ts
                    state.chainlink_ticks_total += 1
                    # If we have a market loaded but no target yet, set it now (this is sec~0).
                    if state.market is not None and state.market.target_chainlink_at_open is None:
                        state.market.target_chainlink_at_open = val
                        csvs.event("TARGET_CHAINLINK_AT_OPEN",
                                   f"slug={state.market.slug} target={val}")
        except Exception as e:
            state.reconnects_chainlink += 1
            state.chainlink_status = "reconnecting"
            state.last_error = f"chainlink: {type(e).__name__}: {e}"
            csvs.event("CHAINLINK_RECONNECT", state.last_error)
            await asyncio.sleep(2)


async def poly_loop(state: RecorderState, csvs: CsvStore) -> None:
    last_sub_key = None
    while not state.should_stop:
        try:
            state.poly_status = "connecting"
            async with websockets.connect(POLY_WS, ping_interval=20, ping_timeout=20, max_size=2**24) as ws:
                state.poly_status = "connected"
                csvs.event("POLY_CONNECTED", "connected")
                while not state.should_stop:
                    if not state.market or not state.market.up_token or not state.market.down_token:
                        await asyncio.sleep(0.25)
                        continue
                    sub_key = (state.market.slug, state.market.up_token, state.market.down_token)
                    if sub_key != last_sub_key:
                        subscribe_msg = {
                            "type": "market",
                            "assets_ids": [str(state.market.up_token), str(state.market.down_token)],
                            "custom_feature_enabled": True,
                        }
                        await ws.send(json.dumps(subscribe_msg))
                        csvs.event("POLY_SUBSCRIBE", f"{state.market.slug} UP={state.market.up_token} DOWN={state.market.down_token}")
                        last_sub_key = sub_key
                        state.poly_status = "live"
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        if not raw or raw == "{}":
                            continue
                        parse_poly_message(state, raw, csvs)
                    except asyncio.TimeoutError:
                        try:
                            await ws.send("{}")
                        except Exception:
                            break
        except Exception as e:
            state.reconnects_poly += 1
            state.poly_status = "reconnecting"
            state.last_error = f"poly: {type(e).__name__}: {e}"
            csvs.event("POLY_RECONNECT", state.last_error)
            await asyncio.sleep(2)



async def target_retry_loop(state: RecorderState, csvs: CsvStore) -> None:
    """Keep trying to capture target from the rendered page until it exists.

    This fixes the main recorder problem:
    Polymarket orderbook WS does not include Price To Beat, so the recorder must
    capture it from Gamma/HTML/rendered page and retry after market load.
    """
    last_try_ts = 0.0
    while not state.should_stop:
        try:
            m = state.market
            if not m:
                await asyncio.sleep(0.5)
                continue

            if m.target_price is not None:
                state.target_status = "captured"
                await asyncio.sleep(1.0)
                continue

            now_ts = time.time()
            # Try frequently during the beginning of each market.
            interval = 2.0 if sec_from_start(m.market_epoch) <= 30 else 8.0
            if now_ts - last_try_ts < interval:
                await asyncio.sleep(0.25)
                continue
            last_try_ts = now_ts

            state.target_attempts += 1
            state.target_status = "trying"
            found = None
            source = "-"
            err = "-"

            # 1) Gamma again
            try:
                refreshed = fetch_market_info_for_slug(m.slug)
                if refreshed.target_price is not None:
                    found = refreshed.target_price
                    source = "gamma_or_html_in_fetch"
                    m.question = refreshed.question or m.question
                    m.start_iso = refreshed.start_iso or m.start_iso
                    m.end_iso = refreshed.end_iso or m.end_iso
                    m.up_token = refreshed.up_token or m.up_token
                    m.down_token = refreshed.down_token or m.down_token
            except Exception as e:
                err = f"gamma_retry:{type(e).__name__}:{e}"

            # 2) Direct HTML
            if found is None:
                try:
                    x = extract_target_from_html(m.url)
                    if x is not None:
                        found = x
                        source = "page_html_retry"
                except Exception as e:
                    err = f"html_retry:{type(e).__name__}:{e}"

            # 3) Rendered page (the one you asked for)
            if found is None:
                x, render_err = extract_target_from_rendered_page(m.url)
                if x is not None:
                    found = x
                    source = "rendered_page_retry"
                else:
                    err = render_err

            if found is not None:
                m.target_price = float(found)
                state.target_status = "captured"
                state.target_source = source
                state.target_last_error = "-"
                csvs.event("TARGET_CAPTURED", f"slug={m.slug} target={m.target_price} source={source} attempts={state.target_attempts}")
                csvs.append_csv(csvs.paths["markets"], [
                    now_local(), m.slug, m.market_epoch, m.url, m.target_price,
                    m.up_token, m.down_token, m.start_iso, m.end_iso, m.question
                ])
            else:
                state.target_status = "missing"
                state.target_last_error = err
                state.last_error = f"target: {err}"
                csvs.event("TARGET_RETRY_FAILED", f"slug={m.slug} attempts={state.target_attempts} err={err}")

            await asyncio.sleep(0.25)
        except Exception as e:
            state.target_status = "error"
            state.target_last_error = f"{type(e).__name__}:{e}"
            state.last_error = f"target_loop: {state.target_last_error}"
            csvs.event("TARGET_LOOP_ERROR", state.last_error)
            await asyncio.sleep(2.0)



def log_market_outcome_if_ready(state: RecorderState, csvs: CsvStore, source: str = "rollover") -> None:
    """Write market outcome row. Prefers Chainlink-based target+winner (canonical
    Polymarket resolution source); falls back to Binance+target_price if Chainlink
    isn't available."""
    m = state.market
    if m is None:
        return
    # Prefer Chainlink for both target and final price (matches Polymarket's resolution).
    target = m.target_chainlink_at_open if m.target_chainlink_at_open is not None else m.target_price
    final_price = state.chainlink_price if state.chainlink_price is not None else state.binance_price
    if target is None or final_price is None:
        return
    src_label = "chainlink" if (m.target_chainlink_at_open is not None and state.chainlink_price is not None) else "fallback_binance"
    try:
        signed = float(final_price) - float(target)
        winner_side = "UP" if signed >= 0 else "DOWN"
        csvs.append_csv(csvs.paths["market_outcomes"], [
            now_local(), m.slug, m.market_epoch, target, final_price,
            signed, abs(signed), winner_side, f"{source}/{src_label}",
        ])
        csvs.event("MARKET_OUTCOME", f"slug={m.slug} winner={winner_side} final={final_price} target={target} dist={signed:.2f} src={src_label}")
    except Exception as e:
        csvs.event("MARKET_OUTCOME_ERROR", f"{type(e).__name__}:{e}")

async def market_rollover_loop(state: RecorderState, csvs: CsvStore, initial_slug: str) -> None:
    state.base_slug = initial_slug
    step = WINDOW_CONFIG[WINDOW]["step"]
    pattern = WINDOW_CONFIG[WINDOW]["pattern"]
    while not state.should_stop:
        try:
            desired_epoch = floor_to_window_epoch()
            if state.loaded_epoch != desired_epoch:
                old_epoch = state.loaded_epoch
                if old_epoch is not None:
                    log_market_outcome_if_ready(state, csvs, "rollover")
                desired_slug = build_window_slug(desired_epoch)
                state.last_rollover_text = f"{old_epoch if old_epoch else '-'} -> {desired_epoch}"
                state.up = OrderBookSide()
                state.down = OrderBookSide()
                # Try current epoch first, then +/-1 step as recovery (only for epoch-style slugs)
                if pattern == "epoch":
                    candidates = (desired_epoch, desired_epoch + step, desired_epoch - step)
                else:
                    candidates = (desired_epoch,)
                fetched = None
                for ep in candidates:
                    if ep <= 0:
                        continue
                    try:
                        slug = build_window_slug(ep)
                        fetched = fetch_market_info_for_slug(slug)
                        break
                    except Exception as e:
                        state.last_error = f"fetch {ep}: {type(e).__name__}: {e}"
                        await asyncio.sleep(0.5)
                if fetched is not None:
                    state.market = fetched
                    state.loaded_epoch = fetched.market_epoch
                    state.last_rollover_text = f"{old_epoch if old_epoch else '-'} -> {state.loaded_epoch}"
                    csvs.append_csv(csvs.paths["markets"], [
                        now_local(), fetched.slug, fetched.market_epoch, fetched.url, fetched.target_price,
                        fetched.up_token, fetched.down_token, fetched.start_iso, fetched.end_iso, fetched.question
                    ])
                    csvs.event("MARKET_LOADED", f"slug={fetched.slug} target={fetched.target_price}")
                else:
                    fallback_slug = desired_slug
                    state.market = MarketInfo(slug=fallback_slug, suffix=desired_epoch, market_epoch=desired_epoch, url=event_url_from_slug(fallback_slug))
                    state.loaded_epoch = desired_epoch
                    csvs.event("MARKET_LOAD_FAILED", f"fallback slug={fallback_slug}; {state.last_error}")
            await asyncio.sleep(0.25)
        except Exception as e:
            state.last_error = f"rollover: {type(e).__name__}: {e}"
            csvs.event("ROLLOVER_ERROR", state.last_error)
            await asyncio.sleep(1)



async def target_retry_loop(state: RecorderState, csvs: CsvStore) -> None:
    """Retry target capture until the current market has a numeric target."""
    last_slug = None
    attempts_for_slug = 0
    while not state.should_stop:
        try:
            m = state.market
            if m is None:
                await asyncio.sleep(1)
                continue
            if m.slug != last_slug:
                last_slug = m.slug
                attempts_for_slug = 0
            if m.target_price is None and attempts_for_slug < 20:
                attempts_for_slug += 1
                state.target_attempts = attempts_for_slug
                # PRIORITY 1: __NEXT_DATA__ (HTTP-only, fast, no browser).
                # Polymarket changed Gamma — eventMetadata is now null for current
                # markets; the priceToBeat is now in the page's __NEXT_DATA__ JSON.
                target = await asyncio.to_thread(extract_target_from_next_data, m.url, m.slug)
                source = "next_data" if target is not None else None
                # PRIORITY 2: HTML regex fallback
                if target is None:
                    target = extract_target_from_html(m.url)
                    if target is not None:
                        source = "html"
                # PRIORITY 3: Playwright (slow, last resort — may timeout under load)
                if target is None:
                    rendered_target, err = await asyncio.to_thread(extract_target_from_rendered_page, m.url)
                    target = rendered_target
                    source = err if target is not None else "render_failed"
                    state.target_last_error = "-" if target is not None else err
                if target is not None:
                    m.target_price = float(target)
                    state.target_status = "captured"
                    state.target_source = source
                    csvs.event("TARGET_CAPTURED", f"slug={m.slug} target={target} source={source}")
                    csvs.append_csv(csvs.paths["markets"], [
                        now_local(), m.slug, m.market_epoch, m.url, m.target_price,
                        m.up_token, m.down_token, m.start_iso, m.end_iso, m.question
                    ])
                else:
                    state.target_status = "missing"
                    csvs.event("TARGET_RETRY_EMPTY", f"slug={m.slug} attempt={attempts_for_slug}")
            await asyncio.sleep(2)
        except Exception as e:
            state.last_error = f"target_retry: {type(e).__name__}: {e}"
            csvs.event("TARGET_RETRY_ERROR", state.last_error)
            await asyncio.sleep(2)

async def combined_per_second_loop(state: RecorderState, csvs: CsvStore) -> None:
    last_sec = None
    while not state.should_stop:
        try:
            sec = int(time.time())
            if sec != last_sec:
                last_sec = sec
                m = state.market
                signed = state.distance_signed()
                abs_d = state.distance_abs()
                # Chainlink-based distance (the canonical / authoritative one)
                cl_age = (time.time() - state.chainlink_update_ts) if state.chainlink_update_ts > 0 else None
                cl_target = m.target_chainlink_at_open if m else None
                cl_signed = None
                cl_abs = None
                if state.chainlink_price is not None and cl_target is not None:
                    cl_signed = float(state.chainlink_price) - float(cl_target)
                    cl_abs = abs(cl_signed)
                csvs.append_csv(csvs.paths["combined"], [
                    now_local(), sec, m.slug if m else "-", m.market_epoch if m else None, sec_from_start(m.market_epoch) if m else None,
                    state.binance_price, state.binance_age(), m.target_price if m else None, signed, abs_d,
                    state.chainlink_price, cl_age, cl_target, cl_signed, cl_abs,
                    state.up.bid, state.up.ask, state.up.last, state.up.ask_qty_best, state.up.ask_usd_best,
                    state.up.qty_le_029, state.up.usd_le_029, state.up.qty_le_030, state.up.usd_le_030,
                    state.up.qty_le_031, state.up.usd_le_031, state.up.qty_le_032, state.up.usd_le_032,
                    state.up.qty_le_035, state.up.usd_le_035, state.side_age(state.up),
                    state.down.bid, state.down.ask, state.down.last, state.down.ask_qty_best, state.down.ask_usd_best,
                    state.down.qty_le_029, state.down.usd_le_029, state.down.qty_le_030, state.down.usd_le_030,
                    state.down.qty_le_031, state.down.usd_le_031, state.down.qty_le_032, state.down.usd_le_032,
                    state.down.qty_le_035, state.down.usd_le_035, state.side_age(state.down),
                    state.delta(1), state.delta(5), state.delta(10), state.delta(30), state.volatility_30s(),
                    state.poly_updates_total, state.binance_ticks_total, state.chainlink_ticks_total, m.url if m else "-",
                ])
            await asyncio.sleep(0.05)
        except Exception as e:
            state.last_error = f"combined: {type(e).__name__}: {e}"
            csvs.event("COMBINED_LOG_ERROR", state.last_error)
            await asyncio.sleep(0.5)


def runtime_hms(start_ts: float) -> str:
    s = int(time.time() - start_ts)
    h = s // 3600
    m = (s % 3600) // 60
    sec = s % 60
    return f"{h:02d}:{m:02d}:{sec:02d}"


async def screen_loop(state: RecorderState) -> None:
    while not state.should_stop:
        try:
            clear_screen()
            m = state.market
            print(f"{ANSI_BOLD}BINANCE + POLYMARKET WS RESEARCH RECORDER{ANSI_RESET}")
            print(f"LOCAL TIME : {now_local()}   RUNTIME: {runtime_hms(state.started_at)}")
            print(f"DATA DIR   : /root/{DATA_DIR}  (old files cleared at startup)")
            print("-" * 110)
            if m:
                print(f"SLUG       : {m.slug}")
                print(f"URL        : {m.url}")
                print(f"SEC START  : {sec_from_start(m.market_epoch)}   ROLLOVER: {state.last_rollover_text}")
                print(f"TARGET     : {fmt(m.target_price, 2)} | status={state.target_status} | source={state.target_source} | attempts={state.target_attempts}")
            else:
                print("MARKET     : loading...")
            print(f"BINANCE    : {fmt(state.binance_price, 2)} | age={fmt(state.binance_age(), 2)}s | status={state.binance_status} | ticks={state.binance_ticks_total}")
            print(f"DISTANCE   : signed={color_delta(state.distance_signed(), 2)} | abs={fmt(state.distance_abs(), 2)}")
            print(f"POLY WS    : status={state.poly_status} | updates={state.poly_updates_total} | reconnects={state.reconnects_poly}")
            print("-" * 110)
            print(f"UP   BID:{fmt(state.up.bid,3):>8} ASK:{fmt(state.up.ask,3):>8} LAST:{fmt(state.up.last,3):>8} AGE:{fmt(state.side_age(state.up),2):>8}s  USD<=.31:{fmt(state.up.usd_le_031,2):>10} USD<=.35:{fmt(state.up.usd_le_035,2):>10}")
            print(f"DOWN BID:{fmt(state.down.bid,3):>8} ASK:{fmt(state.down.ask,3):>8} LAST:{fmt(state.down.last,3):>8} AGE:{fmt(state.side_age(state.down),2):>8}s  USD<=.31:{fmt(state.down.usd_le_031,2):>10} USD<=.35:{fmt(state.down.usd_le_035,2):>10}")
            print("-" * 110)
            print(f"DELTA 1s:{color_delta(state.delta(1),2):>15}  5s:{color_delta(state.delta(5),2):>15}  10s:{color_delta(state.delta(10),2):>15}  30s:{color_delta(state.delta(30),2):>15}")
            print(f"FILES      : combined_per_second.csv | binance_ticks.csv | poly_book_ticks.csv | markets.csv | market_outcomes.csv | events.csv")
            print(f"TARGET ERR : {state.target_last_error}")
            print(f"LAST ERROR : {state.last_error}")
            await asyncio.sleep(SCREEN_REFRESH_SEC)
        except Exception:
            await asyncio.sleep(1.0)


async def async_main() -> None:
    # Compute the current market slug for our window (5m/15m/1h/4h/1d)
    initial_slug = build_window_slug()
    print(f"COIN: {COIN}  TICKER: {BINANCE_TICKER}  WINDOW: {WINDOW}  DATA_DIR: {DATA_DIR}")
    print(f"BINANCE_WS: {BINANCE_WS}")
    print(f"Initial slug computed for current {WINDOW} market: {initial_slug}")

    csvs = CsvStore(data_dir=DATA_DIR)
    csvs.init_clean()
    csvs.event("START", f"Recorder started for {COIN} ({BINANCE_TICKER}) window={WINDOW}; old data cleared")

    state = RecorderState()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: setattr(state, "should_stop", True))
        except NotImplementedError:
            pass

    tasks = [
        asyncio.create_task(binance_loop(state, csvs)),
        asyncio.create_task(chainlink_loop(state, csvs)),
        asyncio.create_task(poly_loop(state, csvs)),
        asyncio.create_task(market_rollover_loop(state, csvs, initial_slug)),
        asyncio.create_task(target_retry_loop(state, csvs)),
        asyncio.create_task(combined_per_second_loop(state, csvs)),
        asyncio.create_task(screen_loop(state)),
    ]
    try:
        while not state.should_stop:
            await asyncio.sleep(0.5)
    finally:
        state.should_stop = True
        csvs.event("STOP", "recorder stopped")
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def parse_args():
    import argparse
    p = argparse.ArgumentParser(description="Multi-coin Binance+Polymarket research recorder.")
    p.add_argument("--coin", required=True, help="Coin symbol uppercase (e.g. BTC, ETH, SOL, XRP, DOGE, BNB, HYPE)")
    p.add_argument("--window", default="5m", choices=["5m", "15m", "1h", "4h", "1d"],
                   help="Polymarket market window. Default 5m. Other: 15m, 1h, 4h, 1d (daily)")
    p.add_argument("--ticker", default=None,
                   help="Binance ticker (default = <COIN>USDT)")
    p.add_argument("--slug-prefix", default=None,
                   help="(deprecated, ignored — slug pattern is now derived from --coin and --window)")
    p.add_argument("--data-dir", default=None,
                   help="Output directory (default = 'data_<coin-lower>_<window>_research')")
    return p.parse_args()


def main() -> None:
    global COIN, BINANCE_TICKER, BINANCE_WS, SLUG_PREFIX, DATA_DIR, CHAINLINK_SYMBOL, WINDOW
    args = parse_args()
    COIN = args.coin.upper()
    WINDOW = args.window
    BINANCE_TICKER = (args.ticker or f"{COIN}USDT").upper()
    BINANCE_WS = f"wss://stream.binance.com:9443/ws/{BINANCE_TICKER.lower()}@trade"
    # SLUG_PREFIX is computed from window — kept for backward compat / error messages
    SLUG_PREFIX = build_window_slug(0).rsplit("-", 1)[0] + "-" if WINDOW_CONFIG[WINDOW]["pattern"] == "epoch" else ""
    DATA_DIR = args.data_dir or f"data_{COIN.lower()}_{WINDOW}_research"
    CHAINLINK_SYMBOL = COIN_TO_CHAINLINK_SYMBOL.get(COIN, f"{COIN.lower()}/usd")
    try:
        asyncio.run(async_main())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
