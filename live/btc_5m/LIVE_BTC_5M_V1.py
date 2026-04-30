# -*- coding: utf-8 -*-
"""
LIVE_BTC_5M_V1.py
=================
Live trading bot for Polymarket Bitcoin 5-minute Up/Down markets.

Strategy is V3 with user-approved tweaks (logged here for transparency):
  - BOT40 (sec 0-40): buy at limit prices 0.29 (sec 0-30) or 0.35 (sec 30-40).
    If |distance| >= 25, only with the flow side.
    [CHANGED FROM V3: phase-1 price 0.31 -> 0.29 to get cheaper fills.]
  - BOT120 (sec 0-120, full window): buy if |distance| >= 68, direction-only
    (UP if BTC > target, DOWN if BTC < target), price cap 0.80.
    [CHANGED FROM V3: active range 41-120 -> 0-120 (V4-style); threshold 60 -> 68;
     added price cap 0.80 (V3 had no cap).]
  - Buy only, never sells, holds to settlement.
  - BOT40 and BOT120 can both fire in the same market — each at most once per market.

Safety:
  - Default mode is DRY-RUN (decisions printed and logged, no real orders).
  - Live mode requires --live flag AND a confirmation prompt at startup.
  - Per trade: $5 (configurable via DOLLARS_PER_TRADE).
  - Daily loss cap: bot stops itself after $20 net losses today.
  - Wallet cap: bot refuses to trade if wallet > $100.

Run:
  python LIVE_BTC_5M_V1.py                    # dry-run, prompts for market URL
  python LIVE_BTC_5M_V1.py --url <URL>        # dry-run with URL pre-set
  python LIVE_BTC_5M_V1.py --live --url <URL> # LIVE trading (will prompt to confirm)

Requires:
  pip install requests websockets python-dotenv
  pip install py-clob-client      # only needed for --live mode
"""

import argparse
import asyncio
import csv
import json
import math
import os
import re
import signal
import ssl
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, date, timezone
from pathlib import Path
from typing import Deque, Dict, List, Optional, Tuple
from collections import deque

import requests
import websockets

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.constants import POLYGON
    from py_clob_client.clob_types import OrderArgs
    HAS_CLOB = True
except Exception:
    HAS_CLOB = False

try:
    from playwright.async_api import async_playwright
    HAS_PLAYWRIGHT = True
except Exception:
    async_playwright = None
    HAS_PLAYWRIGHT = False


# =============================================================================
# STRATEGY CONSTANTS  (mirror research V3)
# =============================================================================
BOT40_MIN_SEC = 0
BOT40_MAX_SEC = 40
BOT40_LIMIT_END_SEC = 30
BOT40_LIMIT_PRICE = 0.29          # CHANGED from V3 (was 0.31) — user request 2026-04-30
BOT40_FALLBACK_PRICE = 0.35
BOT40_FLOW_DIST_THRESHOLD = 25.0
BOT120_MIN_SEC = 0                # CHANGED from V3 (was 41) — V4-style, full window
BOT120_MAX_SEC = 120
MIN_DIST_BOT120 = 68.0            # CHANGED from V3 (was 60.0) — user request
BOT120_MAX_PRICE = 0.80           # NEW vs V3 — price cap on BOT120 buys


# =============================================================================
# LIVE TRADING CONFIG
# =============================================================================
DOLLARS_PER_TRADE = 5.0
MAX_BUY_PER_BOT_PER_MARKET = 5.0   # one fill per bot per market
MAX_DAILY_LOSS_USD = 20.0
MAX_WALLET_EXPOSURE_USD = 100.0


# =============================================================================
# NETWORK / DATA
# =============================================================================
GAMMA_MARKETS = "https://gamma-api.polymarket.com/markets"
POLY_BOOK_WS = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
BINANCE_WS = "wss://stream.binance.com:9443/ws/btcusdt@trade"
CLOB_HOST = "https://clob.polymarket.com"
POLYGON_CHAIN_ID = 137

DATA_DIR_NAME = "data"   # created next to this script
HEARTBEAT_EVERY_SEC = 10
STALE_AFTER_SEC = 20
SCREEN_REFRESH_EVERY_SEC = 1
HTTP_TIMEOUT = 20


# =============================================================================
# ANSI
# =============================================================================
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"
RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
CYAN = "\033[36m"


# =============================================================================
# UTILITIES
# =============================================================================
def now_local_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def safe_float(v) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def fmt(v: Optional[float], digits: int = 2) -> str:
    if v is None:
        return "-"
    try:
        return f"{float(v):,.{digits}f}"
    except Exception:
        return str(v)


def color_money(v: Optional[float]) -> str:
    if v is None:
        return "-"
    s = f"{v:+,.2f}"
    if v > 0:
        return f"{GREEN}{s}{RESET}"
    if v < 0:
        return f"{RED}{s}{RESET}"
    return s


def clear_screen() -> None:
    print("\033[2J\033[H", end="")


def now_epoch_s() -> int:
    return int(time.time())


def floor_to_5m_epoch(epoch_s: Optional[int] = None) -> int:
    if epoch_s is None:
        epoch_s = now_epoch_s()
    return (epoch_s // 300) * 300


def slug_with_new_suffix(slug: str, new_suffix: int) -> str:
    return re.sub(r"\d+$", str(new_suffix), slug)


def extract_slug(url: str) -> str:
    m = re.search(r"/event/([^/?#]+)", url.strip())
    if not m:
        raise ValueError("Could not extract slug from URL. Paste a Polymarket /event/ URL.")
    return m.group(1).strip().strip("/")


def event_url_from_slug(slug: str) -> str:
    return f"https://polymarket.com/event/{slug}"


def parse_target_from_question(question: str) -> Optional[float]:
    if not question:
        return None
    nums = re.findall(r"(?<!\d)(\d{2,3}(?:,\d{3})+|\d{4,6})(?!\d)", question.replace("$", ""))
    for n in nums:
        x = safe_float(n.replace(",", ""))
        if x is not None and 10000 <= x <= 500000:
            return x
    return None


def parse_target_from_market_obj(market_obj: dict) -> Optional[float]:
    try:
        meta = market_obj.get("eventMetadata") or market_obj.get("event_metadata") or {}
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        if isinstance(meta, dict):
            for key in ("priceToBeat", "price_to_beat", "targetPrice", "target_price"):
                x = safe_float(meta.get(key))
                if x is not None and 10000 <= x <= 500000:
                    return x
        for key in ("priceToBeat", "price_to_beat", "targetPrice", "target_price",
                    "line", "strikePrice", "strike_price"):
            x = safe_float(market_obj.get(key))
            if x is not None and 10000 <= x <= 500000:
                return x
    except Exception:
        pass
    return None


def extract_target_from_html(url: str) -> Optional[float]:
    try:
        r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        html = r.text
    except Exception:
        return None
    patterns = [
        r'Price\s*to\s*Beat[^\d$]{0,120}\$\s*([0-9]{1,3}(?:,[0-9]{3})*\.\d+)',
        r'priceToBeat[^0-9]{0,80}([0-9]{1,3}(?:,[0-9]{3})*\.\d+)',
        r'targetPrice[^0-9]{0,80}([0-9]{1,3}(?:,[0-9]{3})*\.\d+)',
    ]
    for pat in patterns:
        m = re.search(pat, html, re.IGNORECASE | re.DOTALL)
        if m:
            x = safe_float(m.group(1).replace(",", ""))
            if x is not None and 10000 <= x <= 500000:
                return x
    return None


async def extract_target_from_rendered_page(url: str) -> Tuple[Optional[float], str]:
    """
    Reliable target capture by rendering the Polymarket page in headless Chromium
    (Playwright). Polymarket renders 'Price to Beat' via JavaScript, so the raw
    HTML scrape misses it but the rendered page exposes it.

    Returns: (target_price_or_none, source_or_error_string).
    """
    if not HAS_PLAYWRIGHT or async_playwright is None:
        return None, "playwright_not_installed"
    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            try:
                page = await browser.new_page(
                    viewport={"width": 1600, "height": 1400},
                    user_agent=(
                        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ),
                )
                await page.goto(url, wait_until="domcontentloaded", timeout=60000)
                await page.wait_for_timeout(3500)
                rendered_parts: List[str] = []
                try:
                    rendered_parts.append(await page.locator("body").inner_text(timeout=10000))
                except Exception:
                    pass
                try:
                    txt = await page.text_content("body")
                    if txt:
                        rendered_parts.append(txt)
                except Exception:
                    pass
                try:
                    rendered_parts.append(await page.content())
                except Exception:
                    pass
                rendered = "\n".join(t for t in rendered_parts if t)
                # primary: "Price to Beat" anchor + first BTC-like dollar value after it
                for label in (r'Price\s*to\s*Beat', r'PRICE\s*TO\s*BEAT'):
                    m = re.search(label, rendered, re.IGNORECASE)
                    if not m:
                        continue
                    window = rendered[m.start(): m.start() + 1200]
                    nums = re.findall(r'\$\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.\d+)?)', window)
                    for n in nums:
                        x = safe_float(n.replace(",", ""))
                        if x is not None and 10000 <= x <= 500000:
                            return x, "rendered_price_to_beat"
                    nums = re.findall(r'(?<!\d)([0-9]{2,3}(?:,[0-9]{3})+(?:\.\d+)?)(?!\d)', window)
                    for n in nums:
                        x = safe_float(n.replace(",", ""))
                        if x is not None and 10000 <= x <= 500000:
                            return x, "rendered_no_dollar"
                # secondary: regex over rendered text
                for pat in (
                    r'Price\s*to\s*Beat[\s\S]{0,300}?\$\s*([0-9]{1,3}(?:,[0-9]{3})+(?:\.\d+)?)',
                    r'priceToBeat[^0-9]{0,120}([0-9]{1,3}(?:,[0-9]{3})+(?:\.\d+)?)',
                ):
                    m = re.search(pat, rendered, re.IGNORECASE | re.DOTALL)
                    if m:
                        x = safe_float(m.group(1).replace(",", ""))
                        if x is not None and 10000 <= x <= 500000:
                            return x, "rendered_regex"
                return None, "rendered_target_not_found"
            finally:
                try:
                    await browser.close()
                except Exception:
                    pass
    except Exception as e:
        return None, f"rendered_error:{type(e).__name__}:{e}"


# =============================================================================
# DATA STRUCTURES
# =============================================================================
@dataclass
class MarketInfo:
    slug: str
    suffix: int
    market_epoch: int
    url: str
    question: str = ""
    start_iso: str = ""
    end_iso: str = ""
    target_price: Optional[float] = None
    target_source: Optional[str] = None
    up_token: Optional[str] = None
    down_token: Optional[str] = None
    loaded_at: float = 0.0   # wall-clock when market info was loaded (used for fresh-ask check)


@dataclass
class OrderBookSide:
    bid: Optional[float] = None
    ask: Optional[float] = None
    last: Optional[float] = None
    ask_levels: List[Tuple[float, float]] = field(default_factory=list)  # (price, qty) sorted ascending
    updated_at: float = 0.0
    updates: int = 0

    def stale(self) -> bool:
        if self.updated_at <= 0:
            return True
        return (time.time() - self.updated_at) > STALE_AFTER_SEC

    def has_fresh_ask_for_market(self, market_loaded_at: float) -> bool:
        """Strict V3-style check: ask exists AND updated AFTER the current market was loaded."""
        if self.ask is None:
            return False
        if self.updated_at <= 0:
            return False
        if market_loaded_at <= 0:
            return not self.stale()
        return self.updated_at >= market_loaded_at and not self.stale()


@dataclass
class BotState:
    name: str
    start_sec: int
    end_sec: int
    bought_in_market: bool = False     # one buy per market per bot
    spent_this_market: float = 0.0
    last_decision: str = "WAIT"
    last_note: str = "init"

    def reset_market(self) -> None:
        self.bought_in_market = False
        self.spent_this_market = 0.0
        self.last_decision = "WAIT"
        self.last_note = "new market"


@dataclass
class FilledOrder:
    order_id: str
    bot: str
    side: str         # "UP" / "DOWN"
    market_slug: str
    market_epoch: int
    sec_from_start: int
    price: float
    size_shares: float
    spent_usd: float
    btc_price_at_entry: Optional[float]
    target_price_at_entry: Optional[float]
    distance_at_entry: Optional[float]
    flow_side_at_entry: Optional[str]
    with_flow: int
    placed_ts: str
    filled: bool = False
    fill_ts: str = ""
    settled: bool = False
    settle_ts: str = ""
    winner_side: str = ""
    payout_usd: float = 0.0
    pnl_usd: float = 0.0
    dry_run: bool = False


@dataclass
class Decision:
    bot: str            # "BOT40" / "BOT120"
    side: str           # "UP" / "DOWN"
    price_limit: float  # max price we'll pay
    size_usd: float     # how many dollars to spend
    reason: str         # human-readable note for logs


# =============================================================================
# CSV LOGGER  (live data, append-only — never wipes)
# =============================================================================
class LiveLogger:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.paths = {
            "trades":    self.data_dir / "live_trades.csv",
            "decisions": self.data_dir / "live_decisions.csv",
            "errors":    self.data_dir / "live_errors.csv",
            "events":    self.data_dir / "live_events.csv",
            "pnl":       self.data_dir / "live_pnl_daily.csv",
        }
        self._init_if_missing("trades", [
            "ts", "dry_run", "bot", "slug", "market_epoch", "sec_from_start",
            "side", "limit_price", "size_usd", "shares", "btc_price",
            "target_price", "distance", "flow_side", "with_flow",
            "order_id", "fill_status", "fill_price", "settled",
            "winner_side", "payout_usd", "pnl_usd",
        ])
        self._init_if_missing("decisions", [
            "ts", "slug", "sec_from_start", "phase",
            "bot40_decision", "bot40_note",
            "bot120_decision", "bot120_note",
            "btc", "target", "distance", "flow_side",
            "up_bid", "up_ask", "down_bid", "down_ask",
        ])
        self._init_if_missing("errors", ["ts", "where", "error"])
        self._init_if_missing("events", ["ts", "event", "detail"])
        self._init_if_missing("pnl", [
            "date", "trades", "wins", "losses", "gross_spent", "gross_payout", "net_pnl",
        ])

    def _init_if_missing(self, key: str, headers: List[str]) -> None:
        p = self.paths[key]
        if not p.exists():
            with open(p, "w", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(headers)

    def _append(self, key: str, row: List) -> None:
        try:
            with open(self.paths[key], "a", newline="", encoding="utf-8") as f:
                csv.writer(f).writerow(row)
        except Exception as e:
            print(f"{RED}LOG ERROR ({key}): {e}{RESET}")

    def event(self, event: str, detail: str) -> None:
        self._append("events", [now_local_str(), event, detail])

    def error(self, where: str, err: str) -> None:
        self._append("errors", [now_local_str(), where, err])

    def decision(self, slug: str, sec: int, phase: str,
                 bot40_dec: str, bot40_note: str,
                 bot120_dec: str, bot120_note: str,
                 btc, target, distance, flow_side,
                 up_bid, up_ask, down_bid, down_ask) -> None:
        self._append("decisions", [
            now_local_str(), slug, sec, phase,
            bot40_dec, bot40_note, bot120_dec, bot120_note,
            btc, target, distance, flow_side,
            up_bid, up_ask, down_bid, down_ask,
        ])

    def trade(self, fo: FilledOrder, fill_status: str, fill_price: Optional[float]) -> None:
        self._append("trades", [
            fo.placed_ts, int(fo.dry_run), fo.bot, fo.market_slug, fo.market_epoch,
            fo.sec_from_start, fo.side, fo.price, fo.spent_usd, fo.size_shares,
            fo.btc_price_at_entry, fo.target_price_at_entry, fo.distance_at_entry,
            fo.flow_side_at_entry, fo.with_flow, fo.order_id, fill_status, fill_price,
            int(fo.settled), fo.winner_side, fo.payout_usd, fo.pnl_usd,
        ])

    def pnl_daily(self, d: str, trades: int, wins: int, losses: int,
                  gross_spent: float, gross_payout: float, net: float) -> None:
        self._append("pnl", [d, trades, wins, losses,
                             round(gross_spent, 4), round(gross_payout, 4), round(net, 4)])


# =============================================================================
# WALLET / CLOB CLIENT WRAPPER
# =============================================================================
class Wallet:
    def __init__(self, dry_run: bool, logger: LiveLogger):
        self.dry_run = dry_run
        self.logger = logger
        self.private_key: Optional[str] = None
        self.address: Optional[str] = None
        self.rpc_url: Optional[str] = None
        self.client = None
        self.api_creds = None

    def load_env(self, env_paths: List[Path]) -> None:
        # Try each .env path
        if load_dotenv is not None:
            for p in env_paths:
                if p.exists():
                    load_dotenv(dotenv_path=str(p), override=True)
        # support both naming conventions
        self.private_key = os.environ.get("WALLET_PRIVATE_KEY") or os.environ.get("MY_PRIVATE_KEY")
        self.address = os.environ.get("WALLET_ADDRESS") or os.environ.get("MY_ADDRESS")
        self.rpc_url = os.environ.get("POLYGON_RPC_URL")
        if not self.private_key:
            print(f"{YELLOW}WARNING: no private key in env (looked for WALLET_PRIVATE_KEY / MY_PRIVATE_KEY).{RESET}")
            print(f"{YELLOW}  Dry-run will still work; live mode will fail.{RESET}")

    def connect(self) -> bool:
        """Initialize CLOB client. Only needed for live mode. Returns True on success."""
        if self.dry_run:
            return True
        if not HAS_CLOB:
            print(f"{RED}py-clob-client not installed. Run: pip install py-clob-client{RESET}")
            return False
        if not self.private_key:
            print(f"{RED}Cannot connect to CLOB: no private key in .env{RESET}")
            return False
        try:
            self.client = ClobClient(host=CLOB_HOST, key=self.private_key, chain_id=POLYGON_CHAIN_ID)
            try:
                self.api_creds = self.client.create_or_derive_api_creds()
                self.client.set_api_creds(self.api_creds)
            except Exception as e:
                self.logger.error("clob_api_creds", f"{type(e).__name__}: {e}")
                print(f"{RED}Failed to create/derive API creds: {e}{RESET}")
                return False
            self.logger.event("CLOB_CONNECTED", f"address={self.address}")
            return True
        except Exception as e:
            self.logger.error("clob_connect", f"{type(e).__name__}: {e}")
            print(f"{RED}CLOB connect failed: {e}{RESET}")
            return False

    def get_usdc_balance(self) -> Optional[float]:
        """Return USDC balance available for trading. Approximate via CLOB if possible."""
        if self.dry_run or self.client is None:
            return None
        try:
            # py-clob-client exposes get_balance_allowance for USDC.
            ba = self.client.get_balance_allowance({"asset_type": "COLLATERAL"})
            bal = ba.get("balance") if isinstance(ba, dict) else None
            return safe_float(bal) / 1_000_000.0 if bal is not None else None
        except Exception as e:
            self.logger.error("get_usdc_balance", f"{type(e).__name__}: {e}")
            return None

    def place_buy(self, token_id: str, price: float, size_shares: float) -> Tuple[Optional[str], str]:
        """
        Place a GTC buy limit order.
        Returns (order_id, status). status: 'placed', 'rejected:<reason>', 'error:<reason>', 'dry_run'.
        """
        if self.dry_run:
            return ("DRYRUN", "dry_run")
        if self.client is None:
            return (None, "rejected:not_connected")
        try:
            args = OrderArgs(
                price=round(float(price), 4),
                size=round(float(size_shares), 4),
                side="BUY",
                token_id=token_id,
            )
            signed = self.client.create_order(args)
            resp = self.client.post_order(signed)
            if isinstance(resp, dict):
                if resp.get("success"):
                    return (str(resp.get("orderID") or resp.get("orderId") or "?"), "placed")
                return (None, f"rejected:{resp.get('errorMsg') or resp}")
            return (None, "rejected:unexpected_response")
        except Exception as e:
            self.logger.error("place_buy", f"{type(e).__name__}: {e}")
            return (None, f"error:{type(e).__name__}")

    def fetch_open_orders(self) -> List[dict]:
        if self.dry_run or self.client is None:
            return []
        try:
            return self.client.get_orders() or []
        except Exception as e:
            self.logger.error("fetch_open_orders", f"{type(e).__name__}: {e}")
            return []


# =============================================================================
# BINANCE WS  (BTC/USDT trade ticks)
# =============================================================================
class BinanceEngine:
    def __init__(self, logger: LiveLogger):
        self.logger = logger
        self.price: Optional[float] = None
        self.update_ts: float = 0.0
        self.ticks_total: int = 0
        self.history: Deque[Tuple[float, float]] = deque(maxlen=600)
        self.status: str = "starting"
        self._stop = False
        self.reconnects = 0

    def stop(self) -> None:
        self._stop = True

    def age(self) -> Optional[float]:
        if self.update_ts <= 0:
            return None
        return time.time() - self.update_ts

    def is_fresh(self) -> bool:
        a = self.age()
        return a is not None and a < STALE_AFTER_SEC

    async def run(self) -> None:
        ssl_ctx = ssl.create_default_context()
        while not self._stop:
            try:
                self.status = "connecting"
                async with websockets.connect(BINANCE_WS, ssl=ssl_ctx,
                                              ping_interval=20, ping_timeout=20,
                                              max_size=2 ** 22) as ws:
                    self.status = "live"
                    self.logger.event("BINANCE_CONNECTED", "")
                    async for raw in ws:
                        if self._stop:
                            return
                        try:
                            msg = json.loads(raw)
                        except Exception:
                            continue
                        price = safe_float(msg.get("p"))
                        if price is None:
                            continue
                        now_ts = time.time()
                        self.price = price
                        self.update_ts = now_ts
                        self.ticks_total += 1
                        self.history.append((now_ts, price))
            except Exception as e:
                self.reconnects += 1
                self.status = "reconnecting"
                self.logger.error("binance", f"{type(e).__name__}: {e}")
                await asyncio.sleep(2)


# =============================================================================
# POLYMARKET MARKET MANAGER  (current 5-min market, target, tokens)
# =============================================================================
class MarketManager:
    def __init__(self, logger: LiveLogger):
        self.logger = logger
        self.base_slug: Optional[str] = None
        self.market: Optional[MarketInfo] = None
        self.loaded_epoch: Optional[int] = None
        self.last_rollover: str = "-"
        self.target_status: str = "idle"
        self.target_attempts: int = 0
        self._stop = False
        self._render_task: Optional[asyncio.Task] = None
        self._render_for_epoch: Optional[int] = None  # which market epoch the in-flight render is for

    def _kickoff_render(self) -> None:
        """V3-style: spawn a background render task without blocking the main loop."""
        if not HAS_PLAYWRIGHT:
            return
        if self.market is None or self.market.target_price is not None:
            return
        # if a task is already running for THIS market, don't spawn another
        if (self._render_task is not None and not self._render_task.done()
                and self._render_for_epoch == self.market.market_epoch):
            return
        self._render_for_epoch = self.market.market_epoch
        self._render_task = asyncio.create_task(self._render_capture(self.market.market_epoch))

    async def _render_capture(self, for_epoch: int) -> None:
        """Background task: render the current market URL with Playwright, store target if found.
        Slug-stale guard: only writes the result if the bot is STILL on the same market epoch."""
        try:
            if self.market is None:
                return
            url = self.market.url
            target_val, src = await extract_target_from_rendered_page(url)
            # stale guard: market may have rolled while we were rendering
            if self.market is None or self.market.market_epoch != for_epoch:
                self.logger.event("RENDER_STALE", f"epoch={for_epoch} discarded (rollover)")
                return
            if target_val is not None and self.market.target_price is None:
                self.market.target_price = float(target_val)
                self.market.target_source = src
                self.target_status = "captured"
                self.logger.event("TARGET_CAPTURED",
                                  f"slug={self.market.slug} target={target_val} src={src}")
            elif target_val is None:
                self.target_status = f"render_failed ({src})"
        except Exception as e:
            self.logger.error("render_capture", f"{type(e).__name__}: {e}")

    def stop(self) -> None:
        self._stop = True

    def sec_from_start(self) -> Optional[int]:
        if self.market is None:
            return None
        return max(0, now_epoch_s() - int(self.market.market_epoch))

    def fetch_by_slug(self, slug: str) -> Optional[MarketInfo]:
        try:
            r = requests.get(GAMMA_MARKETS, params={"slug": slug}, timeout=HTTP_TIMEOUT)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            self.logger.error("gamma_fetch", f"slug={slug} {type(e).__name__}: {e}")
            return None
        markets = data["markets"] if isinstance(data, dict) and "markets" in data else data
        if not markets:
            return None
        m0 = markets[0]
        question = m0.get("question") or m0.get("title") or ""
        start_iso = m0.get("startDate") or ""
        end_iso = m0.get("endDate") or ""
        # tokens
        raw_ids = m0.get("clobTokenIds") or []
        token_ids: List[str] = []
        if isinstance(raw_ids, str):
            try:
                parsed = json.loads(raw_ids)
                token_ids = [str(x) for x in parsed] if isinstance(parsed, list) else \
                    [x.strip() for x in raw_ids.split(",") if x.strip()]
            except Exception:
                token_ids = [x.strip() for x in raw_ids.split(",") if x.strip()]
        elif isinstance(raw_ids, list):
            token_ids = [str(x) for x in raw_ids]
        # outcomes
        outcomes = m0.get("outcomes") or []
        if isinstance(outcomes, str):
            try:
                p = json.loads(outcomes)
                outcomes = p if isinstance(p, list) else []
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
        # target
        target = parse_target_from_market_obj(m0)
        if target is None:
            target = parse_target_from_question(question)
        if target is None:
            target = extract_target_from_html(event_url_from_slug(slug))
        suffix_match = re.search(r"(\d+)$", slug)
        suffix = int(suffix_match.group(1)) if suffix_match else floor_to_5m_epoch()
        return MarketInfo(slug=slug, suffix=suffix, market_epoch=suffix,
                          url=event_url_from_slug(slug),
                          question=question, start_iso=start_iso, end_iso=end_iso,
                          target_price=target, up_token=up_token, down_token=down_token)

    async def rollover_loop(self, initial_slug: str) -> None:
        self.base_slug = initial_slug
        # initial load
        first = self.fetch_by_slug(initial_slug)
        if first is None:
            self.logger.error("rollover_initial", f"slug={initial_slug} not found")
            print(f"{RED}Initial market not found via Gamma. Check the URL.{RESET}")
            self._stop = True
            return
        first.loaded_at = time.time()
        self.market = first
        self.loaded_epoch = first.market_epoch
        self.logger.event("MARKET_LOADED",
                          f"slug={first.slug} target={first.target_price}")
        # if Gamma/HTML didn't return a target, kick off background render
        if first.target_price is None:
            self._kickoff_render()

        while not self._stop:
            try:
                desired = floor_to_5m_epoch()
                if self.loaded_epoch != desired:
                    old = self.loaded_epoch
                    desired_slug = slug_with_new_suffix(self.base_slug, desired)
                    fetched = None
                    for ep in (desired, desired + 300, desired - 300):
                        if ep <= 0:
                            continue
                        fetched = self.fetch_by_slug(slug_with_new_suffix(self.base_slug, ep))
                        if fetched is not None:
                            break
                    if fetched is not None:
                        fetched.loaded_at = time.time()
                        self.market = fetched
                        self.loaded_epoch = fetched.market_epoch
                    else:
                        self.market = MarketInfo(slug=desired_slug, suffix=desired,
                                                  market_epoch=desired,
                                                  url=event_url_from_slug(desired_slug),
                                                  loaded_at=time.time())
                        self.loaded_epoch = desired
                    self.last_rollover = f"{old} -> {self.loaded_epoch}"
                    self.target_attempts = 0
                    self.target_status = "captured" if (self.market and self.market.target_price) else "trying"
                    self.logger.event("MARKET_ROLLOVER",
                                      f"new_slug={self.market.slug} target={self.market.target_price}")
                    # always kick off a render on rollover if we don't have a target yet
                    if self.market and self.market.target_price is None:
                        self._kickoff_render()
                # ongoing target retry: cheap path (Gamma+HTML), then ensure render is in flight
                if self.market and self.market.target_price is None:
                    self.target_attempts += 1
                    if self.target_status not in ("rendering", "captured"):
                        self.target_status = "trying"
                    # cheap retry every cycle (~0.5s) — Gamma might come online late
                    if self.target_attempts % 6 == 1:  # try Gamma/HTML every ~3s, not every 0.5s
                        refreshed = self.fetch_by_slug(self.market.slug)
                        if refreshed and refreshed.target_price is not None:
                            self.market.target_price = refreshed.target_price
                            self.market.up_token = refreshed.up_token or self.market.up_token
                            self.market.down_token = refreshed.down_token or self.market.down_token
                            self.target_status = "captured"
                            self.logger.event("TARGET_CAPTURED",
                                              f"slug={self.market.slug} target={self.market.target_price} src=gamma_or_html")
                    # ensure a render task is running in the background (non-blocking)
                    self._kickoff_render()
                    if (self._render_task is not None and not self._render_task.done()
                            and self._render_for_epoch == self.market.market_epoch):
                        self.target_status = "rendering"
                else:
                    self.target_status = "captured" if (self.market and self.market.target_price) else "missing"
                await asyncio.sleep(0.5)
            except Exception as e:
                self.logger.error("rollover_loop", f"{type(e).__name__}: {e}")
                await asyncio.sleep(2)


# =============================================================================
# POLYMARKET ORDER BOOK FEED
# =============================================================================
class OrderBookFeed:
    def __init__(self, market_mgr: MarketManager, logger: LiveLogger):
        self.market_mgr = market_mgr
        self.logger = logger
        self.up = OrderBookSide()
        self.down = OrderBookSide()
        self.status = "starting"
        self.updates_total = 0
        self.reconnects = 0
        self._stop = False

    def stop(self) -> None:
        self._stop = True

    def side_for_token(self, asset_id: str) -> Optional[Tuple[str, OrderBookSide]]:
        m = self.market_mgr.market
        if m is None:
            return None
        if m.up_token and asset_id == str(m.up_token):
            return ("UP", self.up)
        if m.down_token and asset_id == str(m.down_token):
            return ("DOWN", self.down)
        return None

    def _levels(self, ev: dict, key: str) -> List[Tuple[float, float]]:
        levels = ev.get(key) or []
        out: List[Tuple[float, float]] = []
        if not isinstance(levels, list):
            return out
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
            out.append((px, qty))
        return out

    def _update_side(self, side: OrderBookSide, ev: dict) -> None:
        bid = safe_float(ev.get("best_bid") or ev.get("bid") or ev.get("b"))
        ask = safe_float(ev.get("best_ask") or ev.get("ask") or ev.get("a"))
        last = safe_float(ev.get("price") or ev.get("last_trade") or ev.get("last") or ev.get("p"))
        if bid is not None:
            side.bid = bid
        if ask is not None:
            side.ask = ask
        if last is not None:
            side.last = last
        asks = self._levels(ev, "asks")
        if asks:
            asks.sort(key=lambda x: x[0])
            side.ask_levels = asks
            side.ask = asks[0][0]
        side.updated_at = time.time()
        side.updates += 1

    def _handle_msg(self, raw: str) -> None:
        try:
            msg = json.loads(raw)
        except Exception:
            return
        events: List[dict] = []
        if isinstance(msg, list):
            events = [x for x in msg if isinstance(x, dict)]
        elif isinstance(msg, dict):
            data = msg.get("data")
            events = data if isinstance(data, list) else [msg]
        for ev in events:
            asset = str(ev.get("asset_id") or ev.get("token_id") or ev.get("market") or ev.get("asset") or "")
            outcome = str(ev.get("outcome") or "").upper()
            side_state = None
            side_name = None
            sd = self.side_for_token(asset)
            if sd is not None:
                side_name, side_state = sd
            elif outcome == "UP":
                side_name, side_state = "UP", self.up
            elif outcome == "DOWN":
                side_name, side_state = "DOWN", self.down
            if side_state is None:
                continue
            self._update_side(side_state, ev)
            self.updates_total += 1

    def reset_for_new_market(self) -> None:
        self.up = OrderBookSide()
        self.down = OrderBookSide()

    async def run(self) -> None:
        last_sub_key = None
        while not self._stop:
            try:
                self.status = "connecting"
                async with websockets.connect(POLY_BOOK_WS, ping_interval=20, ping_timeout=20,
                                              max_size=2 ** 24) as ws:
                    self.status = "connected"
                    self.logger.event("POLY_CONNECTED", "")
                    while not self._stop:
                        m = self.market_mgr.market
                        if not m or not m.up_token or not m.down_token:
                            await asyncio.sleep(0.25)
                            continue
                        sub_key = (m.slug, m.up_token, m.down_token)
                        if sub_key != last_sub_key:
                            self.reset_for_new_market()
                            payload = {
                                "type": "market",
                                "assets_ids": [str(m.up_token), str(m.down_token)],
                                "custom_feature_enabled": True,
                            }
                            await ws.send(json.dumps(payload))
                            self.logger.event("POLY_SUBSCRIBE",
                                              f"slug={m.slug} up={m.up_token} down={m.down_token}")
                            last_sub_key = sub_key
                            self.status = "live"
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                            if not raw or raw == "{}":
                                continue
                            self._handle_msg(raw)
                        except asyncio.TimeoutError:
                            try:
                                await ws.send("{}")
                            except Exception:
                                break
            except Exception as e:
                self.reconnects += 1
                self.status = "reconnecting"
                self.logger.error("poly_book", f"{type(e).__name__}: {e}")
                await asyncio.sleep(2)


# =============================================================================
# SAFETY  (daily loss + wallet cap + kill switch)
# =============================================================================
class Safety:
    def __init__(self, max_daily_loss: float, max_wallet: float, dry_run: bool, logger: LiveLogger):
        self.max_daily_loss = max_daily_loss
        self.max_wallet = max_wallet
        self.dry_run = dry_run
        self.logger = logger
        self.daily_pnl = 0.0
        self.daily_trades = 0
        self.daily_wins = 0
        self.daily_losses = 0
        self.daily_spent = 0.0
        self.daily_payout = 0.0
        self.day = today_str()
        self.killed = False
        self.kill_reason = ""

    def _maybe_rollover_day(self) -> None:
        td = today_str()
        if td != self.day:
            self.logger.pnl_daily(self.day, self.daily_trades, self.daily_wins, self.daily_losses,
                                  self.daily_spent, self.daily_payout, self.daily_pnl)
            self.day = td
            self.daily_pnl = 0.0
            self.daily_trades = 0
            self.daily_wins = 0
            self.daily_losses = 0
            self.daily_spent = 0.0
            self.daily_payout = 0.0
            self.killed = False
            self.kill_reason = ""

    def check_can_trade(self, wallet_balance: Optional[float]) -> Tuple[bool, str]:
        self._maybe_rollover_day()
        if self.killed:
            return (False, f"killed: {self.kill_reason}")
        if self.daily_pnl <= -self.max_daily_loss:
            self.killed = True
            self.kill_reason = f"daily loss {self.daily_pnl:.2f} <= -{self.max_daily_loss:.2f}"
            self.logger.event("KILL_DAILY_LOSS", self.kill_reason)
            return (False, self.kill_reason)
        if (not self.dry_run) and wallet_balance is not None and wallet_balance > self.max_wallet:
            return (False, f"wallet ${wallet_balance:.2f} > cap ${self.max_wallet:.2f}")
        return (True, "ok")

    def record_settlement(self, spent: float, payout: float) -> None:
        self._maybe_rollover_day()
        self.daily_trades += 1
        self.daily_spent += spent
        self.daily_payout += payout
        pnl = payout - spent
        self.daily_pnl += pnl
        if pnl >= 0:
            self.daily_wins += 1
        else:
            self.daily_losses += 1


# =============================================================================
# STRATEGY  (mirror V3 exactly)
# =============================================================================
class Strategy:
    """Decides what to buy. Pure function: given state, returns Decision or None."""

    @staticmethod
    def flow_side_from_distance(distance: Optional[float]) -> Optional[str]:
        if distance is None:
            return None
        if distance > 0:
            return "UP"
        if distance < 0:
            return "DOWN"
        return None

    @staticmethod
    def evaluate(sec: int, market: MarketInfo, btc: Optional[float],
                 distance: Optional[float], book: OrderBookFeed,
                 bot40: BotState, bot120: BotState) -> Tuple[Optional[Decision], str, str]:
        """
        Returns (decision_or_none, bot40_note, bot120_note).
        Note: this evaluates BOTH bots and returns the first valid decision in priority order.
        """
        bot40_note = ""
        bot120_note = ""
        target = market.target_price

        flow_side = Strategy.flow_side_from_distance(distance)
        up = book.up
        down = book.down
        loaded_at = market.loaded_at

        # --- BOT40 ---
        if BOT40_MIN_SEC <= sec <= BOT40_MAX_SEC and not bot40.bought_in_market:
            if btc is None or target is None or distance is None:
                bot40_note = "missing btc/target/distance"
            elif not (up.has_fresh_ask_for_market(loaded_at) or down.has_fresh_ask_for_market(loaded_at)):
                bot40_note = "no side fresh-for-market"
            else:
                price_cap = BOT40_LIMIT_PRICE if sec <= BOT40_LIMIT_END_SEC else BOT40_FALLBACK_PRICE
                eligible: List[Tuple[str, float]] = []
                if up.has_fresh_ask_for_market(loaded_at) and up.ask <= price_cap:
                    eligible.append(("UP", up.ask))
                if down.has_fresh_ask_for_market(loaded_at) and down.ask <= price_cap:
                    eligible.append(("DOWN", down.ask))
                note = f"price_cap<={price_cap:.2f}"
                if abs(distance) >= BOT40_FLOW_DIST_THRESHOLD and flow_side is not None:
                    eligible = [x for x in eligible if x[0] == flow_side]
                    note = f"price<={price_cap:.2f} flow_only({flow_side})"
                if eligible:
                    eligible.sort(key=lambda x: x[1])
                    side, ask = eligible[0]
                    bot40_note = f"BUY {side} @<={ask:.4f} ({note})"
                    return (Decision(bot="BOT40", side=side, price_limit=ask,
                                      size_usd=DOLLARS_PER_TRADE,
                                      reason=note),
                            bot40_note, "BOT40 already firing")
                bot40_note = f"WAIT no eligible side ({note})"

        # --- BOT120 ---
        if BOT120_MIN_SEC <= sec <= BOT120_MAX_SEC and not bot120.bought_in_market:
            if btc is None or target is None or distance is None:
                bot120_note = "missing btc/target/distance"
            elif abs(distance) < MIN_DIST_BOT120:
                bot120_note = f"WAIT |dist|={abs(distance):.1f} < {MIN_DIST_BOT120:.0f}"
            else:
                direction_side = "UP" if distance > 0 else "DOWN"
                side_state = up if direction_side == "UP" else down
                if not side_state.has_fresh_ask_for_market(loaded_at):
                    bot120_note = f"WAIT {direction_side} ask not fresh-for-market"
                elif side_state.ask > BOT120_MAX_PRICE:
                    bot120_note = f"WAIT {direction_side} ask={side_state.ask:.4f} > cap {BOT120_MAX_PRICE:.2f}"
                else:
                    bot120_note = f"BUY {direction_side} @<={side_state.ask:.4f} (dist={distance:.1f}, cap {BOT120_MAX_PRICE:.2f})"
                    return (Decision(bot="BOT120", side=direction_side,
                                      price_limit=side_state.ask,
                                      size_usd=DOLLARS_PER_TRADE,
                                      reason=f"dist={distance:.1f} cap{BOT120_MAX_PRICE:.2f}"),
                            bot40_note or "BOT40 done/out_of_phase", bot120_note)

        return (None, bot40_note, bot120_note)


# =============================================================================
# MAIN BOT
# =============================================================================
class LiveBot:
    def __init__(self, dry_run: bool, initial_url: Optional[str]):
        self.dry_run = dry_run
        self.initial_url = initial_url
        script_dir = Path(__file__).resolve().parent
        self.data_dir = script_dir / DATA_DIR_NAME
        self.logger = LiveLogger(self.data_dir)
        self.wallet = Wallet(dry_run=dry_run, logger=self.logger)
        self.binance = BinanceEngine(self.logger)
        self.market_mgr = MarketManager(self.logger)
        self.book = OrderBookFeed(self.market_mgr, self.logger)
        self.safety = Safety(MAX_DAILY_LOSS_USD, MAX_WALLET_EXPOSURE_USD, dry_run, self.logger)
        self.bot40 = BotState(name="BOT40", start_sec=BOT40_MIN_SEC, end_sec=BOT40_MAX_SEC)
        self.bot120 = BotState(name="BOT120", start_sec=BOT120_MIN_SEC, end_sec=BOT120_MAX_SEC)
        self.open_orders: List[FilledOrder] = []
        self.market_history: Dict[int, Dict] = {}   # market_epoch -> {orders: [...], settlement: ...}
        self.last_market_epoch: Optional[int] = None
        self._stop = False

    # --- helpers ---
    def distance(self) -> Optional[float]:
        if self.market_mgr.market is None or self.market_mgr.market.target_price is None:
            return None
        if self.binance.price is None:
            return None
        return float(self.binance.price) - float(self.market_mgr.market.target_price)

    # --- main per-second loop ---
    async def decision_loop(self) -> None:
        last_sec = -1
        while not self._stop:
            try:
                m = self.market_mgr.market
                sec = self.market_mgr.sec_from_start()
                # detect new market
                if m is not None and m.market_epoch != self.last_market_epoch:
                    if self.last_market_epoch is not None:
                        self.logger.event("MARKET_BOUNDARY", f"old={self.last_market_epoch} new={m.market_epoch}")
                    self.bot40.reset_market()
                    self.bot120.reset_market()
                    self.last_market_epoch = m.market_epoch

                if m is None or sec is None:
                    await asyncio.sleep(0.2)
                    continue
                if sec == last_sec:
                    await asyncio.sleep(0.2)
                    continue
                last_sec = sec

                btc = self.binance.price if self.binance.is_fresh() else None
                target = m.target_price
                dist = self.distance()
                phase = "BOT40" if 0 <= sec <= BOT40_MAX_SEC else (
                        "BOT120" if BOT120_MIN_SEC <= sec <= BOT120_MAX_SEC else "WAIT")
                flow_side = Strategy.flow_side_from_distance(dist)

                decision, b40_note, b120_note = Strategy.evaluate(
                    sec=sec, market=m, btc=btc, distance=dist,
                    book=self.book, bot40=self.bot40, bot120=self.bot120,
                )

                # log decisions every second
                self.logger.decision(
                    slug=m.slug, sec=sec, phase=phase,
                    bot40_decision=("BUY" if (decision and decision.bot == "BOT40") else self.bot40.last_decision),
                    bot40_note=b40_note or self.bot40.last_note,
                    bot120_decision=("BUY" if (decision and decision.bot == "BOT120") else self.bot120.last_decision),
                    bot120_note=b120_note or self.bot120.last_note,
                    btc=btc, target=target, distance=dist, flow_side=flow_side,
                    up_bid=self.book.up.bid, up_ask=self.book.up.ask,
                    down_bid=self.book.down.bid, down_ask=self.book.down.ask,
                )

                if decision is not None:
                    await self.try_execute(decision, sec, m, btc, target, dist, flow_side)

                # update bot state notes for screen
                if b40_note:
                    self.bot40.last_note = b40_note
                if b120_note:
                    self.bot120.last_note = b120_note

                await asyncio.sleep(0.05)
            except Exception as e:
                self.logger.error("decision_loop", f"{type(e).__name__}: {e}")
                await asyncio.sleep(1)

    async def try_execute(self, decision: Decision, sec: int, market: MarketInfo,
                          btc: Optional[float], target: Optional[float],
                          distance: Optional[float], flow_side: Optional[str]) -> None:
        bot_state = self.bot40 if decision.bot == "BOT40" else self.bot120
        if bot_state.bought_in_market:
            return
        # safety check
        wallet_bal = self.wallet.get_usdc_balance() if not self.dry_run else None
        ok, why = self.safety.check_can_trade(wallet_bal)
        if not ok:
            bot_state.last_decision = "BLOCKED"
            bot_state.last_note = f"safety: {why}"
            self.logger.event("SAFETY_BLOCK", f"bot={decision.bot} why={why}")
            return
        # token_id for the chosen side
        token_id = market.up_token if decision.side == "UP" else market.down_token
        if not token_id:
            bot_state.last_note = "missing token_id"
            return
        # convert size_usd into shares at limit price (so ~$5 spent if filled)
        price = max(0.01, min(decision.price_limit, 0.99))
        shares = round(decision.size_usd / price, 4)
        if shares <= 0:
            bot_state.last_note = "computed shares <= 0"
            return
        # PLACE ORDER
        order_id, status = self.wallet.place_buy(token_id=str(token_id), price=price, size_shares=shares)
        with_flow = int(flow_side is not None and flow_side == decision.side)
        fo = FilledOrder(
            order_id=order_id or "-",
            bot=decision.bot,
            side=decision.side,
            market_slug=market.slug,
            market_epoch=market.market_epoch,
            sec_from_start=sec,
            price=price,
            size_shares=shares,
            spent_usd=decision.size_usd,
            btc_price_at_entry=btc,
            target_price_at_entry=target,
            distance_at_entry=distance,
            flow_side_at_entry=flow_side,
            with_flow=with_flow,
            placed_ts=now_local_str(),
            filled=(self.dry_run or status == "placed"),  # in dry-run we treat as filled
            fill_ts=now_local_str() if (self.dry_run or status == "placed") else "",
            dry_run=self.dry_run,
        )
        self.open_orders.append(fo)
        # mark bot as bought even if order not yet confirmed — prevents duplicate firing same second
        bot_state.bought_in_market = True
        bot_state.spent_this_market += decision.size_usd
        bot_state.last_decision = "BUY"
        bot_state.last_note = f"{decision.side} @{price:.4f} size=${decision.size_usd:.2f} status={status}"
        self.logger.trade(fo, fill_status=status, fill_price=price if (self.dry_run or status == "placed") else None)
        self.logger.event("ORDER_PLACED",
                          f"bot={decision.bot} side={decision.side} price={price:.4f} "
                          f"size=${decision.size_usd:.2f} status={status} dry_run={self.dry_run}")
        if self.dry_run:
            print(f"{CYAN}[DRY-RUN] {decision.bot} buy {decision.side} @{price:.4f} "
                  f"size=${decision.size_usd:.2f} ({decision.reason}){RESET}")
        else:
            print(f"{GREEN if status == 'placed' else RED}[LIVE] {decision.bot} buy {decision.side} "
                  f"@{price:.4f} size=${decision.size_usd:.2f} status={status}{RESET}")

    # --- settlement loop: when a market finishes, decide W/L based on Binance close ---
    async def settlement_loop(self) -> None:
        last_settled_epoch = -1
        while not self._stop:
            try:
                m = self.market_mgr.market
                sec = self.market_mgr.sec_from_start()
                if m is None or sec is None:
                    await asyncio.sleep(1)
                    continue
                # settlement happens at second 300 (end of 5-min window)
                if sec >= 300 and m.market_epoch != last_settled_epoch:
                    last_settled_epoch = m.market_epoch
                    btc_now = self.binance.price
                    target = m.target_price
                    if btc_now is not None and target is not None:
                        winner = "UP" if btc_now >= target else "DOWN"
                        # close out positions for THIS market
                        for fo in list(self.open_orders):
                            if fo.market_epoch != m.market_epoch or fo.settled:
                                continue
                            if not fo.filled:
                                # never filled — write off
                                fo.settled = True
                                fo.settle_ts = now_local_str()
                                fo.winner_side = winner
                                fo.payout_usd = 0.0
                                fo.pnl_usd = 0.0  # didn't fill, no spend
                                self.logger.event("UNFILLED_EXPIRED",
                                                  f"order_id={fo.order_id} side={fo.side}")
                                continue
                            won = (fo.side == winner)
                            payout = fo.size_shares * 1.0 if won else 0.0
                            pnl = payout - fo.spent_usd
                            fo.settled = True
                            fo.settle_ts = now_local_str()
                            fo.winner_side = winner
                            fo.payout_usd = payout
                            fo.pnl_usd = pnl
                            self.safety.record_settlement(fo.spent_usd, payout)
                            self.logger.trade(fo, fill_status="settled", fill_price=fo.price)
                            self.logger.event("SETTLED",
                                              f"bot={fo.bot} side={fo.side} winner={winner} "
                                              f"pnl={pnl:+.2f} dry_run={fo.dry_run}")
                await asyncio.sleep(1)
            except Exception as e:
                self.logger.error("settlement_loop", f"{type(e).__name__}: {e}")
                await asyncio.sleep(2)

    # --- screen ---
    async def screen_loop(self) -> None:
        while not self._stop:
            try:
                clear_screen()
                m = self.market_mgr.market
                sec = self.market_mgr.sec_from_start()
                btc = self.binance.price
                target = m.target_price if m else None
                dist = self.distance()
                flow = Strategy.flow_side_from_distance(dist)

                mode = f"{RED}LIVE TRADING{RESET}" if not self.dry_run else f"{CYAN}DRY-RUN{RESET}"
                print(f"{BOLD}LIVE_BTC_5M_V1{RESET}   mode={mode}   {now_local_str()}")
                print("-" * 100)
                if m:
                    print(f"SLUG       : {m.slug}")
                    print(f"URL        : {m.url}")
                    print(f"SEC FROM 0 : {sec}   ROLLOVER: {self.market_mgr.last_rollover}")
                    print(f"TARGET     : {fmt(target, 2)}   status={self.market_mgr.target_status}   attempts={self.market_mgr.target_attempts}")
                else:
                    print("MARKET     : loading...")
                print(f"BINANCE    : {fmt(btc, 2)}   age={fmt(self.binance.age(), 2)}s   status={self.binance.status}   ticks={self.binance.ticks_total}")
                print(f"DISTANCE   : {fmt(dist, 2)}   flow={flow or '-'}")
                print(f"POLY WS    : status={self.book.status}   updates={self.book.updates_total}   reconnects={self.book.reconnects}")
                print("-" * 100)
                up = self.book.up
                down = self.book.down
                print(f"{'SIDE':<8}{'BID':<10}{'ASK':<10}{'LAST':<10}{'AGE':<8}{'UPDATES':<10}")
                print(f"{'UP':<8}{fmt(up.bid, 4):<10}{fmt(up.ask, 4):<10}{fmt(up.last, 4):<10}{fmt(time.time() - up.updated_at if up.updated_at else None, 1):<8}{up.updates:<10}")
                print(f"{'DOWN':<8}{fmt(down.bid, 4):<10}{fmt(down.ask, 4):<10}{fmt(down.last, 4):<10}{fmt(time.time() - down.updated_at if down.updated_at else None, 1):<8}{down.updates:<10}")
                print("-" * 100)
                print(f"BOT40  : {self.bot40.last_decision:<10}{self.bot40.last_note}")
                print(f"BOT120 : {self.bot120.last_decision:<10}{self.bot120.last_note}")
                print("-" * 100)
                print(f"DAILY ({self.safety.day})   trades={self.safety.daily_trades}   "
                      f"W={self.safety.daily_wins} L={self.safety.daily_losses}   "
                      f"PnL={color_money(self.safety.daily_pnl)}   "
                      f"limit_loss=${self.safety.max_daily_loss:.0f}")
                if self.safety.killed:
                    print(f"{RED}{BOLD}KILL SWITCH ACTIVE — {self.safety.kill_reason}{RESET}")
                if not self.dry_run:
                    bal = self.wallet.get_usdc_balance()
                    print(f"WALLET     : {fmt(bal, 2)} USDC   address={self.wallet.address}   cap=${self.safety.max_wallet:.0f}")
                else:
                    print(f"{DIM}WALLET     : (dry-run — wallet not queried){RESET}")
                print(f"OPEN/SETTLED orders this session: {len(self.open_orders)}")
                await asyncio.sleep(SCREEN_REFRESH_EVERY_SEC)
            except Exception:
                await asyncio.sleep(1)

    # --- orchestrate ---
    async def run(self) -> None:
        # 0. load env
        env_paths = [
            Path(__file__).resolve().parent / ".env",
            Path(__file__).resolve().parent.parent.parent / ".env",   # polymarket-bot/.env (if any)
        ]
        self.wallet.load_env(env_paths)

        # 1. connect to CLOB if needed
        if not self.dry_run:
            ok = self.wallet.connect()
            if not ok:
                print(f"{RED}Cannot start in live mode — CLOB connect failed.{RESET}")
                return
            bal = self.wallet.get_usdc_balance()
            print(f"USDC balance at startup: {fmt(bal, 2)}")
            if bal is not None and bal > MAX_WALLET_EXPOSURE_USD:
                print(f"{RED}Wallet has ${bal:.2f}, more than cap ${MAX_WALLET_EXPOSURE_USD}.{RESET}")
                print(f"{RED}Refusing to trade. Withdraw funds or raise the cap.{RESET}")
                return

        # 2. initial URL
        if self.initial_url:
            url = self.initial_url
        else:
            print("הדבק כתובת שוק 5 דקות של פולימרקט (paste a Polymarket 5-min event URL):")
            url = input("> ").strip()
        try:
            slug = extract_slug(url)
        except ValueError as e:
            print(f"{RED}{e}{RESET}")
            return

        # 3. signal handlers
        loop = asyncio.get_running_loop()
        for sig_name in ("SIGINT", "SIGTERM"):
            try:
                sig = getattr(signal, sig_name)
                loop.add_signal_handler(sig, lambda: setattr(self, "_stop", True))
            except Exception:
                pass

        self.logger.event("START", f"dry_run={self.dry_run} initial_slug={slug}")

        # 4. spawn loops
        tasks = [
            asyncio.create_task(self.binance.run(), name="binance"),
            asyncio.create_task(self.market_mgr.rollover_loop(slug), name="market_rollover"),
            asyncio.create_task(self.book.run(), name="poly_book"),
            asyncio.create_task(self.decision_loop(), name="decisions"),
            asyncio.create_task(self.settlement_loop(), name="settlements"),
            asyncio.create_task(self.screen_loop(), name="screen"),
        ]
        try:
            while not self._stop and not self.market_mgr._stop:
                await asyncio.sleep(0.5)
        finally:
            self._stop = True
            self.market_mgr.stop()
            self.book.stop()
            self.binance.stop()
            self.logger.event("STOP", "")
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)


# =============================================================================
# CLI
# =============================================================================
def parse_args(argv: List[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Live Polymarket BTC 5-min trading bot.")
    p.add_argument("--live", action="store_true",
                   help="Enable real trading (default is dry-run)")
    p.add_argument("--url", help="Initial Polymarket 5-min event URL (optional, will prompt if not given)")
    return p.parse_args(argv)


def confirm_live() -> bool:
    print(f"\n{RED}{BOLD}=========================================={RESET}")
    print(f"{RED}{BOLD}  LIVE TRADING MODE — REAL MONEY ON PLOY  {RESET}")
    print(f"{RED}{BOLD}=========================================={RESET}")
    print(f"{YELLOW}This bot will place real BUY orders on Polymarket using the wallet")
    print(f"private key in .env. Per-trade size: ${DOLLARS_PER_TRADE:.2f}.{RESET}")
    print(f"Daily loss cap: ${MAX_DAILY_LOSS_USD:.2f}. Wallet cap: ${MAX_WALLET_EXPOSURE_USD:.2f}.")
    print()
    answer = input("Type the words 'go live' to confirm: ").strip().lower()
    return answer == "go live"


def main() -> None:
    args = parse_args(sys.argv[1:])
    dry_run = not args.live
    if args.live:
        if not confirm_live():
            print("Live mode not confirmed. Exiting.")
            return
    bot = LiveBot(dry_run=dry_run, initial_url=args.url)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        print("\nstopped.")


if __name__ == "__main__":
    main()
