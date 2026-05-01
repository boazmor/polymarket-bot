# -*- coding: utf-8 -*-
"""
LIVE_BTC_5M_V1.py
=================
Live trading bot for Polymarket BTC 5-min Up/Down markets.
Forked from research bot V3 (good_working_bot_kululu_V3.py) ג€” same engine,
same screen, same market manager. Strategy switched from TAKER (V3) to MAKER
(passive limit orders) per user request 2026-04-30.

STRATEGY (MAKER)
  BOT40 phase 1 (sec 0-30): place THREE simultaneous limit BUY orders at
    0.28, 0.29, and 0.30. Each is BOT40_MAKER_SIZE_USD. Total committed in book
    is 3 * BOT40_MAKER_SIZE_USD. Listens for fills. When total filled reaches
    MAX_BUY_USD, cancels remaining open orders.
  BOT40 phase 2 (sec 30-40): cancel any phase-1 orders still open. If
    total filled < MAX_BUY_USD, place a top-up order at 0.35 for the remaining.
  BOT120 (sec 0-120, full window): if |distance| >= 68, direction-only
    (UP if BTC > target, DOWN if BTC < target), price cap 0.80. Single buy
    per market. Direction-side BUY at best ask when conditions met.
  Both bots: BUY only, hold to settlement.

SAFETY (enforced in code)
  - Default mode is DRY-RUN (orders simulated, nothing sent).
  - --live flag PLUS confirmation prompt required for real orders.
  - Per-trade size: MAX_BUY_USD ($5 in test, $100 long-term goal).
  - Daily loss cap: MAX_DAILY_LOSS_USD ($40) -> kill switch on bot.
  - Wallet exposure cap: MAX_WALLET_USD ($200) -> refuse to trade.

Run:
  python LIVE_BTC_5M_V1.py                # dry-run, prompts for market URL
  python LIVE_BTC_5M_V1.py --live         # LIVE trading (will prompt to confirm)
"""
import argparse
import asyncio
import csv
import json
import math
import os
import re
import shutil
import ssl
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import requests
import websockets

try:
    from playwright.async_api import async_playwright
except Exception:
    async_playwright = None

try:
    from py_clob_client.client import ClobClient
    from py_clob_client.constants import POLYGON
    from py_clob_client.clob_types import OrderArgs
    HAS_CLOB = True
except Exception:
    HAS_CLOB = False

GAMMA_MARKETS_BY_SLUG = "https://gamma-api.polymarket.com/markets"
POLY_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@trade"
CLOB_HOST = "https://clob.polymarket.com"
POLYGON_CHAIN_ID = 137

HEARTBEAT_EVERY_SEC = 10
STALE_AFTER_SEC = 20
SCREEN_REFRESH_EVERY_SEC = 1
BOT40_MAX_SEC = 40
BOT120_MIN_SEC = 0           # CHANGED FROM V3 (was 41) ג€” V4-style, full window
BOT120_MAX_SEC = 120
ENTRY_THRESHOLD = 0.35
DATA_DIR = "data_live_btc_5m_v1"

ANSI_RESET = "\033[0m"
ANSI_RED = "\033[31m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_BOLD = "\033[1m"
ANSI_CYAN = "\033[36m"
ANSI_WHITE = "\033[37m"
ANSI_DIM = "\033[2m"
ANSI_BLINK = "\033[5m"
MIN_DIST_BOT120 = 68.0           # CHANGED FROM V3 (was 60.0) ג€” user request
BOT40_LIMIT_END_SEC = 30
BOT40_FALLBACK_PRICE = 0.35
BOT120_MAX_PRICE = 0.80          # NEW vs V3 ג€” price cap on BOT120 buys
RENDER_RETRY_WINDOW_SEC = 20
RENDER_RETRY_INTERVAL_SEC = 2
BOT40_FLOW_DIST_THRESHOLD = 25.0
BOT40_RESEARCH_PRICE_LEVELS = [round(x / 100.0, 2) for x in range(28, 43)]
BOT40_RESEARCH_SECONDS = list(range(32, 43))
BOT120_RESEARCH_SECONDS = [55, 57, 59, 62, 64]
BOT120_RESEARCH_DISTANCE_LEVELS = [55.0, 57.0, 59.0, 62.0, 64.0]

# ============================================================================
# LIVE TRADING ג€” strategy and safety constants
# ============================================================================
# Maker model: BOT40 phase 1 places THREE simultaneous limit orders at these
# price levels.  Each level gets BOT40_MAKER_SIZE_USD of capital reserved.
# Total in book = 3 * BOT40_MAKER_SIZE_USD.  Cap on actual fills = MAX_BUY_USD.
BOT40_MAKER_LEVELS = [0.28, 0.29, 0.30]

# Production size ג€” same for virtual (dry-run) testing and for live trading.
# Virtual uses no real money so size is risk-free for testing.
MAX_BUY_USD = 5.0       # TEST FILE — limited to $5/trade for first live verification
BOT40_MAKER_SIZE_USD = 3.0   # TEST FILE      # 3 simultaneous orders -> $180 reserved in book; cap actual fills at MAX_BUY_USD

# Safety stops (enforced inside the bot, not just policy)
MAX_DAILY_LOSS_USD = 15.0    # TEST FILE — tighter cap for testing        # bot kills itself after this much realized loss today
MAX_WALLET_USD = 200.0           # bot refuses to trade if wallet balance > this

# Modes
DRY_RUN_DEFAULT = True           # default; --live flag plus confirmation flips it


def color_text(txt: str, color: Optional[str]) -> str:
    return f"{color}{txt}{ANSI_RESET}" if color else txt


def color_money(v: float, blink: bool = False) -> str:
    s = f"${v:,.2f}"
    prefix = ANSI_BLINK if blink else ""
    if v > 0:
        return f"{prefix}{ANSI_GREEN}{s}{ANSI_RESET}"
    if v < 0:
        return f"{prefix}{ANSI_RED}{s}{ANSI_RESET}"
    return s


def trim_cell(text: str, width: int) -> str:
    text = str(text)
    if len(text) <= width:
        return text.ljust(width)
    if width <= 3:
        return text[:width]
    return (text[: width - 3] + "...").ljust(width)


def colorize_decision(text: str, active: bool = False) -> str:
    color = ANSI_YELLOW if active and "BUY" in str(text) else ANSI_WHITE
    prefix = ANSI_BLINK if active and "BUY" in str(text) else ""
    return f"{prefix}{color}{text}{ANSI_RESET}"


@dataclass
class VirtualPosition:
    sec: int
    side: str
    spent: float
    qty: float
    avg_fill: float
    entry_best_ask: Optional[float]
    entry_best_bid: Optional[float]
    entry_btc_price: Optional[float] = None
    entry_target_price: Optional[float] = None
    entry_distance: Optional[float] = None
    entry_flow_side: Optional[str] = None
    entry_with_flow: Optional[int] = None
    entry_ts: str = ""


@dataclass
class BotState:
    name: str
    start_sec: int
    end_sec: int
    positions: Dict[str, List[VirtualPosition]] = field(default_factory=lambda: {"UP": [], "DOWN": []})
    current_market_buy_count: int = 0
    current_market_spent: float = 0.0
    last_buy: Optional[dict] = None
    buy_done_for_market: bool = False
    executed_virtual_sec_side: set = field(default_factory=set)
    last_logged_sec_side: set = field(default_factory=set)
    last_decision: str = "NONE"
    last_note: str = "starting"
    triggers_seen: int = 0
    virtual_buy_count: int = 0
    virtual_spent_total: float = 0.0
    realized_pnl_total: float = 0.0
    realized_payout_total: float = 0.0
    settled_markets: int = 0
    wins: int = 0
    losses: int = 0
    pushes: int = 0

    def reset_market(self) -> None:
        self.positions = {"UP": [], "DOWN": []}
        self.current_market_buy_count = 0
        self.current_market_spent = 0.0
        self.last_buy = None
        self.buy_done_for_market = False
        self.executed_virtual_sec_side = set()
        self.last_logged_sec_side = set()
        self.last_decision = "NONE"
        self.last_note = "starting market"


class BinanceEngine:
    def __init__(self) -> None:
        self.price: Optional[float] = None
        self.updated_at: float = 0.0
        self.status: str = "starting"
        self.last_trade_ts_ms: Optional[int] = None
        self._current_bucket_start: Optional[int] = None
        self._last_trade_in_bucket: Optional[float] = None
        self.boundary_close_prices: Dict[int, float] = {}
        self._stop = False

    def snapshot(self) -> dict:
        return {
            "price": self.price,
            "updated_at": self.updated_at,
            "status": self.status,
            "last_trade_ts_ms": self.last_trade_ts_ms,
            "current_bucket_start": self._current_bucket_start,
            "last_trade_in_bucket": self._last_trade_in_bucket,
        }

    def close_for_boundary(self, boundary_epoch: Optional[int]) -> Optional[float]:
        if boundary_epoch is None:
            return None
        try:
            return self.boundary_close_prices.get(int(boundary_epoch))
        except Exception:
            return None

    async def run(self) -> None:
        ssl_ctx = ssl.create_default_context()
        while not self._stop:
            try:
                async with websockets.connect(
                    BINANCE_WS_URL,
                    ssl=ssl_ctx,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=2**20,
                    close_timeout=5,
                ) as ws:
                    self.status = "live"
                    while not self._stop:
                        raw = await asyncio.wait_for(ws.recv(), timeout=30)
                        msg = json.loads(raw)
                        px = msg.get("p") or msg.get("price")
                        if px is not None:
                            trade_px = float(px)
                            trade_ts_ms = int(msg.get("T") or msg.get("E") or int(time.time() * 1000))
                            bucket_start = (trade_ts_ms // 1000 // 300) * 300
                            if self._current_bucket_start is None:
                                self._current_bucket_start = bucket_start
                            elif bucket_start != self._current_bucket_start:
                                if self._last_trade_in_bucket is not None:
                                    self.boundary_close_prices[self._current_bucket_start + 300] = float(self._last_trade_in_bucket)
                                    if len(self.boundary_close_prices) > 2000:
                                        oldest = sorted(self.boundary_close_prices.keys())[:-1000]
                                        for k in oldest:
                                            self.boundary_close_prices.pop(k, None)
                                self._current_bucket_start = bucket_start
                            self._last_trade_in_bucket = trade_px
                            self.last_trade_ts_ms = trade_ts_ms
                            self.price = trade_px
                            self.updated_at = time.time()
            except asyncio.TimeoutError:
                self.status = "timeout"
            except Exception:
                self.status = "reconnecting"
                await asyncio.sleep(2)

    def stop(self) -> None:
        self._stop = True


# ============================================================================
# WALLET ג€” CLOB integration for live trading
# ============================================================================
class Wallet:
    """Polymarket CLOB wallet wrapper.
    Loads .env, creates ClobClient with signature_type=2 (Gnosis Safe proxy
    ג€” confirmed via reference_polymarket_wallet_setup memory), derives API
    credentials, and exposes order placement / cancellation / balance query.

    In dry-run mode the wallet is inert: place_buy returns a fake order_id
    and balance returns None ג€” the bot's V3 simulation logic kicks in instead.
    """

    SIGNATURE_TYPE_POLY_GNOSIS_SAFE = 2  # discovered 2026-05-01 ג€” see memory

    def __init__(self, dry_run: bool, env_paths: Optional[List[str]] = None):
        self.dry_run = dry_run
        self.env_paths = env_paths or [
            "/root/.env",
            os.path.join(os.path.dirname(__file__), ".env"),
        ]
        self.private_key: Optional[str] = None
        self.address: Optional[str] = None
        self.rpc_url: Optional[str] = None
        self.client = None  # py_clob_client.ClobClient instance once connected
        self.connected: bool = False
        self.last_error: str = ""

    def _load_env(self) -> bool:
        try:
            from dotenv import load_dotenv
        except Exception:
            self.last_error = "python-dotenv not installed"
            return False
        loaded_any = False
        for p in self.env_paths:
            if os.path.exists(p):
                load_dotenv(p, override=True)
                loaded_any = True
        if not loaded_any:
            self.last_error = "no .env file found in known locations"
            return False
        self.private_key = (
            os.environ.get("PRIVATE_KEY")
            or os.environ.get("MY_PRIVATE_KEY")
            or os.environ.get("WALLET_PRIVATE_KEY")
        )
        self.address = os.environ.get("WALLET_ADDRESS") or os.environ.get("MY_ADDRESS")
        self.rpc_url = os.environ.get("POLYGON_RPC_URL")
        if not self.private_key or len(self.private_key) < 60:
            self.last_error = "private key missing or invalid in .env"
            return False
        return True

    def connect(self) -> bool:
        """Initialize ClobClient and derive API credentials. Only needed for
        live mode. Returns True on success, False otherwise (last_error is set)."""
        if self.dry_run:
            return True  # nothing to do ג€” dry-run uses V3 simulation
        if not self._load_env():
            return False
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.constants import POLYGON
        except Exception as e:
            self.last_error = f"py-clob-client not installed: {e}"
            return False
        try:
            self.client = ClobClient(
                host="https://clob.polymarket.com",
                key=self.private_key,
                chain_id=POLYGON,
                signature_type=self.SIGNATURE_TYPE_POLY_GNOSIS_SAFE,
            )
            creds = self.client.create_or_derive_api_creds()
            self.client.set_api_creds(creds)
            self.connected = True
            return True
        except Exception as e:
            self.last_error = f"CLOB connect failed: {type(e).__name__}: {e}"
            self.connected = False
            return False

    def get_usdc_balance(self) -> Optional[float]:
        """Return Polymarket USDC balance for the Gnosis Safe wallet, or None
        if dry-run / unable to query."""
        if self.dry_run or not self.connected or self.client is None:
            return None
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType
            params = BalanceAllowanceParams(
                asset_type=AssetType.COLLATERAL,
                signature_type=self.SIGNATURE_TYPE_POLY_GNOSIS_SAFE,
            )
            resp = self.client.get_balance_allowance(params)
            if isinstance(resp, dict):
                bal_raw = resp.get("balance")
                if bal_raw is not None:
                    return float(int(bal_raw) / 1_000_000.0)
            return None
        except Exception as e:
            self.last_error = f"balance query failed: {type(e).__name__}: {e}"
            return None

    def place_buy(self, token_id: str, price: float, size_shares: float) -> Tuple[Optional[str], str]:
        """Place a GTC limit BUY on the CLOB.
        Returns (order_id, status). status is one of:
          'placed' / 'rejected:<reason>' / 'error:<reason>' / 'dry_run' / 'not_connected'
        """
        if self.dry_run:
            return ("DRYRUN-" + str(int(time.time() * 1000)), "dry_run")
        if not self.connected or self.client is None:
            return (None, "not_connected")
        try:
            from py_clob_client.clob_types import OrderArgs
            args = OrderArgs(
                price=round(float(price), 4),
                size=round(float(size_shares), 4),
                side="BUY",
                token_id=str(token_id),
            )
            signed = self.client.create_order(args)
            resp = self.client.post_order(signed)
            if isinstance(resp, dict):
                if resp.get("success") or resp.get("orderID") or resp.get("orderId"):
                    oid = str(resp.get("orderID") or resp.get("orderId") or "?")
                    return (oid, "placed")
                err = resp.get("errorMsg") or resp.get("error") or str(resp)
                return (None, f"rejected:{err}")
            return (None, "rejected:unexpected_response")
        except Exception as e:
            self.last_error = f"place_buy failed: {type(e).__name__}: {e}"
            return (None, f"error:{type(e).__name__}")

    def cancel(self, order_id: str) -> bool:
        if self.dry_run or not self.connected or self.client is None:
            return True
        try:
            self.client.cancel(order_id=order_id)
            return True
        except Exception as e:
            self.last_error = f"cancel failed: {type(e).__name__}: {e}"
            return False

    def fetch_open_orders(self) -> list:
        if self.dry_run or not self.connected or self.client is None:
            return []
        try:
            return self.client.get_orders() or []
        except Exception as e:
            self.last_error = f"fetch_orders failed: {type(e).__name__}: {e}"
            return []


class DualResearchLogger:
    def __init__(self, data_dir: str = DATA_DIR) -> None:
        self.data_dir = data_dir
        self.paths: Dict[str, str] = {}

    def clear_and_init(self) -> None:
        if os.path.exists(self.data_dir):
            shutil.rmtree(self.data_dir)
        os.makedirs(self.data_dir, exist_ok=True)
        self.paths = {
            "events": os.path.join(self.data_dir, "events.csv"),
            "bot40_signals": os.path.join(self.data_dir, "bot40_signals.csv"),
            "bot120_signals": os.path.join(self.data_dir, "bot120_signals.csv"),
            "bot40_virtual_buys": os.path.join(self.data_dir, "bot40_virtual_buys.csv"),
            "bot120_virtual_buys": os.path.join(self.data_dir, "bot120_virtual_buys.csv"),
            "bot40_settlements": os.path.join(self.data_dir, "bot40_settlements.csv"),
            "bot120_settlements": os.path.join(self.data_dir, "bot120_settlements.csv"),
            "bot40_research": os.path.join(self.data_dir, "bot40_research.csv"),
            "bot120_research": os.path.join(self.data_dir, "bot120_research.csv"),
            "trade_outcomes": os.path.join(self.data_dir, "trade_outcomes.csv"),
        }
        headers = {
            "events": ["ts", "slug", "event", "detail"],
            "signals": [
                "ts", "bot", "slug", "market_suffix", "sec_from_start", "window_open",
                "up_best_bid", "up_best_ask", "down_best_bid", "down_best_ask",
                "btc_price", "target_price", "distance_to_target", "decision", "note",
            ],
            "virtual_buys": [
                "ts", "bot", "slug", "sec_from_start", "side", "mode", "price_limit", "spent_usd", "filled_qty",
                "avg_fill_price", "best_ask", "best_bid_now", "available_notional_le_threshold",
                "available_qty_le_threshold", "btc_price", "target_price", "distance_to_target",
                "limit_031_filled", "limit_fill_sec", "fallback_used", "fallback_fill_sec", "fallback_fill_price", "note",
            ],
            "settlements": [
                "ts", "bot", "slug", "winner_side", "btc_price", "target_price", "spent_total",
                "payout_total", "pnl_total", "up_qty", "down_qty", "result",
            ],
            "bot40_research": [
                "ts", "slug", "sec_from_start", "price_level", "btc_price", "target_price", "distance_to_target",
                "flow_side", "up_best_ask", "down_best_ask", "up_qty_le_level", "up_notional_le_level",
                "down_qty_le_level", "down_notional_le_level", "eligible_side", "note",
            ],
            "bot120_research": [
                "ts", "slug", "sec_from_start", "distance_level", "btc_price", "target_price", "distance_to_target",
                "flow_side", "flow_best_ask", "flow_best_bid", "flow_total_ask_qty", "flow_total_ask_notional",
                "would_trigger", "note",
            ],
            "trade_outcomes": [
                "entry_ts", "settle_ts", "bot", "slug", "sec_from_start", "buy_side", "winner_side", "result",
                "spent_usd", "qty", "avg_fill_price", "payout", "pnl",
                "entry_btc_price", "entry_target_price", "entry_distance_signed", "entry_flow_side", "entry_with_flow",
                "entry_best_ask", "entry_best_bid", "settle_btc_price", "settle_target_price",
            ],
        }
        self._init_csv(self.paths["events"], headers["events"])
        self._init_csv(self.paths["bot40_signals"], headers["signals"])
        self._init_csv(self.paths["bot120_signals"], headers["signals"])
        self._init_csv(self.paths["bot40_virtual_buys"], headers["virtual_buys"])
        self._init_csv(self.paths["bot120_virtual_buys"], headers["virtual_buys"])
        self._init_csv(self.paths["bot40_settlements"], headers["settlements"])
        self._init_csv(self.paths["bot120_settlements"], headers["settlements"])
        self._init_csv(self.paths["bot40_research"], headers["bot40_research"])
        self._init_csv(self.paths["bot120_research"], headers["bot120_research"])
        self._init_csv(self.paths["trade_outcomes"], headers["trade_outcomes"])

    @staticmethod
    def _init_csv(path: str, headers: List[str]) -> None:
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(headers)

    @staticmethod
    def _append_csv(path: str, row: List) -> None:
        with open(path, "a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)

    @staticmethod
    def _ts() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def log_event(self, slug: str, event: str, detail: str) -> None:
        self._append_csv(self.paths["events"], [self._ts(), slug, event, detail])

    def log_signal(self, bot: BotState, slug: str, market_suffix: int, sec: int,
                   up_bid: Optional[float], up_ask: Optional[float], down_bid: Optional[float], down_ask: Optional[float],
                   btc_price: Optional[float], target_price: Optional[float], distance: Optional[float]) -> None:
        path = self.paths[f"{bot.name.lower()}_signals"]
        self._append_csv(path, [
            self._ts(), bot.name, slug, market_suffix, sec,
            int(bot.start_sec <= sec <= bot.end_sec),
            up_bid, up_ask, down_bid, down_ask,
            btc_price, target_price, distance, bot.last_decision, bot.last_note,
        ])

    def log_virtual_buy(self, bot: BotState, slug: str, sec: int, side: str, mode: str, price_limit: float,
                        spent: float, qty: float, avg_fill: float, best_ask: Optional[float], best_bid_now: Optional[float],
                        avail_notional: Optional[float], avail_qty: Optional[float], btc_price: Optional[float],
                        target_price: Optional[float], distance: Optional[float],
                        limit_031_filled: int, limit_fill_sec: Optional[int], fallback_used: int,
                        fallback_fill_sec: Optional[int], fallback_fill_price: Optional[float], note: str) -> None:
        path = self.paths[f"{bot.name.lower()}_virtual_buys"]
        self._append_csv(path, [
            self._ts(), bot.name, slug, sec, side, mode, price_limit, round(spent, 6), round(qty, 6), round(avg_fill, 6),
            best_ask, best_bid_now, avail_notional, avail_qty, btc_price, target_price, distance,
            limit_031_filled, limit_fill_sec, fallback_used, fallback_fill_sec, fallback_fill_price, note,
        ])

    def log_settlement(self, bot: BotState, slug: str, winner_side: str, btc_price: Optional[float], target_price: Optional[float],
                       spent_total: float, payout_total: float, pnl_total: float, up_qty: float, down_qty: float, result: str) -> None:
        path = self.paths[f"{bot.name.lower()}_settlements"]
        self._append_csv(path, [
            self._ts(), bot.name, slug, winner_side, btc_price, target_price,
            round(spent_total, 6), round(payout_total, 6), round(pnl_total, 6),
            round(up_qty, 6), round(down_qty, 6), result,
        ])

    def log_bot40_research(self, slug: str, sec: int, price_level: float, btc_price: Optional[float], target_price: Optional[float],
                           distance: Optional[float], flow_side: Optional[str], up_best_ask: Optional[float], down_best_ask: Optional[float],
                           up_qty: Optional[float], up_notional: Optional[float], down_qty: Optional[float], down_notional: Optional[float],
                           eligible_side: Optional[str], note: str) -> None:
        self._append_csv(self.paths["bot40_research"], [
            self._ts(), slug, sec, price_level, btc_price, target_price, distance, flow_side,
            up_best_ask, down_best_ask, up_qty, up_notional, down_qty, down_notional, eligible_side, note,
        ])

    def log_bot120_research(self, slug: str, sec: int, distance_level: float, btc_price: Optional[float], target_price: Optional[float],
                            distance: Optional[float], flow_side: Optional[str], flow_best_ask: Optional[float], flow_best_bid: Optional[float],
                            flow_total_qty: Optional[float], flow_total_notional: Optional[float], would_trigger: int, note: str) -> None:
        self._append_csv(self.paths["bot120_research"], [
            self._ts(), slug, sec, distance_level, btc_price, target_price, distance, flow_side,
            flow_best_ask, flow_best_bid, flow_total_qty, flow_total_notional, would_trigger, note,
        ])

    def log_trade_outcome(self, bot_name: str, slug: str, pos: "VirtualPosition", winner_side: str,
                          result: str, payout: float, pnl: float,
                          settle_btc_price: Optional[float], settle_target_price: Optional[float]) -> None:
        self._append_csv(self.paths["trade_outcomes"], [
            pos.entry_ts or self._ts(), self._ts(), bot_name, slug, pos.sec, pos.side, winner_side, result,
            round(pos.spent, 6), round(pos.qty, 6), round(pos.avg_fill, 6), round(payout, 6), round(pnl, 6),
            pos.entry_btc_price, pos.entry_target_price, pos.entry_distance,
            pos.entry_flow_side, pos.entry_with_flow,
            pos.entry_best_ask, pos.entry_best_bid, settle_btc_price, settle_target_price,
        ])


class Polymarket5mDualBot:
    def __init__(self, binance_engine: BinanceEngine, logger: DualResearchLogger):
        self.binance = binance_engine
        self.logger = logger
        # Live trading wiring (set from main() after construction).
        # When dry_run=True (default), V3's virtual fill simulation runs as before.
        # When dry_run=False, _try_execute_bot_buy calls self.wallet.place_buy().
        self.dry_run: bool = True
        self.wallet: Optional["Wallet"] = None
        # Daily loss tracking (enforced when dry_run=False).
        self.daily_realized_pnl: float = 0.0
        self.daily_realized_date: str = ""
        self.killed_for_daily_loss: bool = False
        self.current = {
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
        self.bot40 = BotState(name="BOT40", start_sec=0, end_sec=BOT40_MAX_SEC)
        self.bot120 = BotState(name="BOT120", start_sec=BOT120_MIN_SEC, end_sec=BOT120_MAX_SEC)
        self._render_task: Optional[asyncio.Task] = None

    @staticmethod
    def now_local_str() -> str:
        return datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    @staticmethod
    def safe_float(x):
        try:
            v = float(x)
            if math.isfinite(v):
                return v
            return None
        except Exception:
            return None

    @staticmethod
    def fmt(x, digits=3):
        if x is None:
            return "-"
        try:
            return f"{float(x):.{digits}f}"
        except Exception:
            return str(x)

    @staticmethod
    def clear_screen() -> None:
        print("\033[2J\033[H", end="")

    def parse_initial_url(self, url: str) -> Tuple[str, str, int]:
        url = url.strip()
        if "/event/" not in url:
            raise ValueError("URL must contain /event/")
        slug = url.split("/event/")[1].strip().strip("/")
        suffix = slug.split("-")[-1]
        if not suffix.isdigit():
            raise ValueError("Bad slug format")
        prefix = slug[:-(len(suffix))]
        return slug, prefix, int(suffix)

    def build_slug_from_suffix(self, prefix: str, suffix: int) -> str:
        return f"{prefix}{suffix}"

    def build_url_from_slug(self, slug: str) -> str:
        return f"https://polymarket.com/event/{slug}"

    def seconds_from_market_start(self) -> int:
        if self.current["current_suffix"] is None:
            return 0
        return max(0, int(time.time()) - int(self.current["current_suffix"]))

    def current_phase(self) -> str:
        sec = self.seconds_from_market_start()
        if 0 <= sec <= BOT40_MAX_SEC:
            return "BOT40"
        if BOT120_MIN_SEC <= sec <= BOT120_MAX_SEC:
            return "BOT120"
        return "CLOSED"

    def extract_target_from_question(self, question: str) -> Optional[float]:
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
        """Plain HTTP fetch + regex (legacy fallback). Try __NEXT_DATA__ first via
        extract_target_from_next_data ג€” that's the reliable browser-free method.
        """
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
        """Browser-free target extraction. Polymarket pages embed all market
        metadata in a <script id="__NEXT_DATA__"> JSON tag. We fetch the page
        with plain HTTP, parse the JSON, find the event whose slug matches our
        market, and return its eventMetadata.priceToBeat.

        This avoids Playwright entirely. Works on Windows + Python 3.14 where
        Playwright currently has subprocess issues.

        Returns (target_price_or_None, source_label).
        """
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
                    if 10000 <= x <= 500000:
                        return x
            return None

        # 1) exact slug match ג€” preferred
        for path, ev in events:
            if ev.get("slug") == slug:
                target = read_price_to_beat(ev)
                if target is not None:
                    return target, "next_data_slug_match"

        # 2) suffix match ג€” slug ends with same numeric epoch
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

    def ensure_target_price(self, slug: str, url: str, question: str, current_target: Optional[float], market_obj: Optional[dict] = None) -> Tuple[Optional[float], Optional[str], Optional[float], Optional[float], Optional[float], Optional[float], Optional[float]]:
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

            event_meta_target = self.safe_float(event_meta.get("priceToBeat"))
            line_target = self.safe_float(market_obj.get("line"))
            strike_target = self.safe_float(market_obj.get("strikePrice"))

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
                self.logger.log_event(slug, "TARGET_FROM_PAGE", f"target_price={page_target:.2f}")
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

    def _capture_binance_open_target(self) -> None:
        if self.current.get("target_binance_open") is None and self.binance.price is not None:
            self.current["target_binance_open"] = float(self.binance.price)

    def _capture_binance_prev_5m_close_target(self) -> None:
        boundary_epoch = self.current.get("current_suffix")
        close_px = self.binance.close_for_boundary(boundary_epoch)
        if close_px is not None:
            self.current["target_binance_prev_5m_close"] = float(close_px)

    async def _capture_rendered_page_target(self) -> None:
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
                self.logger.log_event(self.current.get("slug") or "-", "TARGET_RENDERED_PAGE", f"target_price={rendered_target:.2f} | source={source}")
            except Exception:
                pass
        else:
            self.current["render_retry_status"] = "retry_wait"

    async def _maybe_retry_rendered_target(self, sec: int) -> None:
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
        self._kickoff_render_target()

    def _resolve_target_in_use(self) -> Tuple[Optional[float], Optional[str]]:
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

    def _kickoff_render_target(self) -> None:
        if self.current.get("target_rendered_page") is not None:
            return
        if self._render_task is not None and not self._render_task.done():
            return
        self._render_task = asyncio.create_task(self._capture_rendered_page_target())

    async def load_initial_market_from_user(self) -> None:
        print("׳”׳“׳‘׳§ ׳›׳×׳•׳‘׳× ׳©׳•׳§ 5 ׳“׳§׳•׳×")
        url = input().strip()
        await self.load_initial_market_from_url(url)

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
            raise RuntimeError(f"׳׳ ׳ ׳׳¦׳ ׳©׳•׳§ ׳¢׳‘׳•׳¨ slug: {slug}")
        self.current.update(market)
        self.current["market_loaded_at"] = time.time()
        self._capture_binance_prev_5m_close_target()
        self._capture_binance_open_target()
        await self._capture_rendered_page_target()
        self.logger.log_event(slug, "INIT", f"initial slug={slug} up={market['yes_token']} down={market['no_token']}")

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
        bid = self.safe_float(msg.get("best_bid"))
        ask = self.safe_float(msg.get("best_ask"))
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
        px = self.safe_float(msg.get("price"))
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
                p = self.safe_float(x.get("price"))
                s = self.safe_float(x.get("size") or x.get("amount"))
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

    def _spread(self, side: str) -> Optional[float]:
        bid = self.prices[side]["best_bid"]
        ask = self.prices[side]["best_ask"]
        if bid is None or ask is None:
            return None
        return round(ask - bid, 6)

    def _best_ask_qty(self, side: str) -> Tuple[Optional[float], Optional[float]]:
        asks = self.prices[side]["asks"]
        if not asks:
            return None, None
        best = asks[0]
        if best.get("size") is None:
            return None, None
        qty = float(best["size"])
        return qty, round(qty * float(best["price"]), 6)

    def _qty_notional_le(self, side: str, level: float) -> Tuple[Optional[float], Optional[float]]:
        asks = self.prices[side]["asks"]
        if not asks:
            return None, None
        qty = 0.0
        notional = 0.0
        found = False
        for row in asks:
            if row["price"] <= level and row.get("size") is not None:
                found = True
                qty += float(row["size"])
                notional += float(row["size"]) * float(row["price"])
        if not found:
            return 0.0, 0.0
        return round(qty, 6), round(notional, 6)

    def _distance_fields(self) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        btc = self.binance.price
        target, source = self._resolve_target_in_use()
        self.current["target_price"] = target
        self.current["target_source"] = source
        if btc is None or target is None:
            return btc, target, None
        return btc, target, round(btc - target, 6)

    @staticmethod
    def _flow_side_from_distance(distance: Optional[float]) -> Optional[str]:
        if distance is None:
            return None
        if distance > 0:
            return "UP"
        if distance < 0:
            return "DOWN"
        return None

    def _total_ask_qty_notional(self, side: str) -> Tuple[Optional[float], Optional[float]]:
        asks = self.prices[side]["asks"]
        if not asks:
            return None, None
        qty = 0.0
        notional = 0.0
        for row in asks:
            size = self.safe_float(row.get("size"))
            price = self.safe_float(row.get("price"))
            if size is None or price is None:
                continue
            qty += size
            notional += size * price
        return round(qty, 6), round(notional, 6)

    def _bot40_level_and_mode(self, sec: int) -> Tuple[Optional[float], Optional[str]]:
        """V3-derived helper: in phase 1 we'll buy at any price <= the highest maker
        level (0.30) to feed downstream logic; in phase 2 we use 0.35 as before.
        The actual maker placement uses BOT40_MAKER_LEVELS in _setup_bot40_maker_orders.
        """
        if sec < 0 or sec > BOT40_MAX_SEC:
            return None, None
        if sec <= BOT40_LIMIT_END_SEC:
            return max(BOT40_MAKER_LEVELS), "MAKER_PHASE1"
        return BOT40_FALLBACK_PRICE, "FALLBACK_035"

    def _simulate_fill_up_to_cap(self, side: str, cap_usd: float, level: float) -> Tuple[float, float, Optional[float]]:
        asks = self.prices[side]["asks"] or []
        spent = 0.0
        qty = 0.0
        for row in asks:
            price = self.safe_float(row.get("price"))
            size = self.safe_float(row.get("size"))
            if price is None or size is None or price > level or size <= 0:
                continue
            remaining = cap_usd - spent
            if remaining <= 1e-12:
                break
            max_qty_here = remaining / price
            take_qty = min(size, max_qty_here)
            if take_qty <= 0:
                continue
            qty += take_qty
            spent += take_qty * price
        if qty <= 1e-12 or spent <= 1e-12:
            return 0.0, 0.0, None
        avg = spent / qty
        return round(spent, 6), round(qty, 6), round(avg, 6)

    def _side_has_fresh_ask_for_current_market(self, side: str) -> bool:
        ask = self.prices[side]["best_ask"]
        updated_at = self.prices[side]["updated_at"]
        loaded_at = float(self.current.get("market_loaded_at") or 0.0)
        return ask is not None and updated_at is not None and updated_at >= loaded_at

    def _choose_bot40_side(self, sec: int) -> Optional[str]:
        level, _mode = self._bot40_level_and_mode(sec)
        if level is None:
            return None

        btc, target, distance = self._distance_fields()
        flow_side = self._flow_side_from_distance(distance)

        if self.bot40.last_buy and self.bot40.current_market_spent < MAX_BUY_USD:
            existing_side = self.bot40.last_buy["side"]
            ask = self.prices[existing_side]["best_ask"]
            if ask is None or ask > level or not self._side_has_fresh_ask_for_current_market(existing_side):
                return None
            if distance is not None and abs(distance) >= BOT40_FLOW_DIST_THRESHOLD and flow_side is not None and existing_side != flow_side:
                return None
            return existing_side

        eligible = []
        up_ask = self.prices["UP"]["best_ask"]
        down_ask = self.prices["DOWN"]["best_ask"]
        if up_ask is not None and up_ask <= level and self._side_has_fresh_ask_for_current_market("UP"):
            eligible.append(("UP", up_ask))
        if down_ask is not None and down_ask <= level and self._side_has_fresh_ask_for_current_market("DOWN"):
            eligible.append(("DOWN", down_ask))

        if not eligible:
            return None

        if distance is not None and abs(distance) >= BOT40_FLOW_DIST_THRESHOLD and flow_side is not None:
            eligible = [x for x in eligible if x[0] == flow_side]
            if not eligible:
                return None

        eligible.sort(key=lambda x: x[1])
        return eligible[0][0]

    def _choose_bot120_side(self, sec: int) -> Optional[str]:
        if not (BOT120_MIN_SEC <= sec <= BOT120_MAX_SEC):
            return None
        btc, target, distance = self._distance_fields()
        if btc is None or target is None or distance is None:
            return None
        if abs(distance) < MIN_DIST_BOT120:
            return None
        direction_side = "UP" if distance > 0 else "DOWN"
        if self.bot120.last_buy and self.bot120.last_buy.get("side") != direction_side:
            return None
        ask = self.prices[direction_side]["best_ask"]
        if ask is None or not self._side_has_fresh_ask_for_current_market(direction_side):
            return None
        if ask > BOT120_MAX_PRICE:
            return None
        return direction_side

    def _record_bot40_research_for_second(self, sec: int) -> None:
        if sec not in BOT40_RESEARCH_SECONDS:
            return
        btc, target, distance = self._distance_fields()
        flow_side = self._flow_side_from_distance(distance)
        up_best_ask = self.prices["UP"]["best_ask"]
        down_best_ask = self.prices["DOWN"]["best_ask"]
        for price_level in BOT40_RESEARCH_PRICE_LEVELS:
            up_qty, up_notional = self._qty_notional_le("UP", price_level)
            down_qty, down_notional = self._qty_notional_le("DOWN", price_level)
            eligible = []
            if up_best_ask is not None and up_best_ask <= price_level:
                eligible.append(("UP", up_best_ask))
            if down_best_ask is not None and down_best_ask <= price_level:
                eligible.append(("DOWN", down_best_ask))
            note = "free"
            if distance is not None and abs(distance) >= BOT40_FLOW_DIST_THRESHOLD:
                note = "flow_only"
                if flow_side is not None:
                    eligible = [x for x in eligible if x[0] == flow_side]
            eligible.sort(key=lambda x: x[1])
            eligible_side = eligible[0][0] if eligible else None
            self.logger.log_bot40_research(
                slug=self.current["slug"],
                sec=sec,
                price_level=price_level,
                btc_price=btc,
                target_price=target,
                distance=distance,
                flow_side=flow_side,
                up_best_ask=up_best_ask,
                down_best_ask=down_best_ask,
                up_qty=up_qty,
                up_notional=up_notional,
                down_qty=down_qty,
                down_notional=down_notional,
                eligible_side=eligible_side,
                note=note,
            )

    def _record_bot120_research_for_second(self, sec: int) -> None:
        if sec not in BOT120_RESEARCH_SECONDS:
            return
        btc, target, distance = self._distance_fields()
        flow_side = self._flow_side_from_distance(distance)
        flow_best_ask = self.prices[flow_side]["best_ask"] if flow_side else None
        flow_best_bid = self.prices[flow_side]["best_bid"] if flow_side else None
        flow_total_qty, flow_total_notional = self._total_ask_qty_notional(flow_side) if flow_side else (None, None)
        for distance_level in BOT120_RESEARCH_DISTANCE_LEVELS:
            would_trigger = int(
                btc is not None and target is not None and distance is not None and flow_side is not None and abs(distance) >= distance_level and flow_best_ask is not None
            )
            self.logger.log_bot120_research(
                slug=self.current["slug"],
                sec=sec,
                distance_level=distance_level,
                btc_price=btc,
                target_price=target,
                distance=distance,
                flow_side=flow_side,
                flow_best_ask=flow_best_ask,
                flow_best_bid=flow_best_bid,
                flow_total_qty=flow_total_qty,
                flow_total_notional=flow_total_notional,
                would_trigger=would_trigger,
                note="dir_only_no_price_cap",
            )

    def _record_signals_for_second(self, sec: int) -> None:
        btc, target, distance = self._distance_fields()
        for bot in [self.bot40, self.bot120]:
            key = sec
            if key in bot.last_logged_sec_side:
                continue
            bot.last_logged_sec_side.add(key)
            self.logger.log_signal(
                bot=bot,
                slug=self.current["slug"],
                market_suffix=self.current["current_suffix"],
                sec=sec,
                up_bid=self.prices["UP"]["best_bid"],
                up_ask=self.prices["UP"]["best_ask"],
                down_bid=self.prices["DOWN"]["best_bid"],
                down_ask=self.prices["DOWN"]["best_ask"],
                btc_price=btc,
                target_price=target,
                distance=distance,
            )

    def _try_execute_bot_buy(self, bot: BotState, sec: int, side: Optional[str]) -> None:
        btc, target, distance = self._distance_fields()

        if bot is self.bot120:
            if distance is None or target is None or btc is None:
                bot.last_decision = "WAIT"
                bot.last_note = "missing poly target"
                return
            if abs(distance) < MIN_DIST_BOT120:
                bot.last_decision = "WAIT"
                bot.last_note = f"dist<{MIN_DIST_BOT120:.0f}"
                return

        if not (bot.start_sec <= sec <= bot.end_sec):
            bot.last_decision = "WAIT"
            bot.last_note = f"outside {bot.start_sec}-{bot.end_sec}"
            return

        if side is None:
            bot.last_decision = "WAIT"
            if bot is self.bot40:
                level, mode = self._bot40_level_and_mode(sec)
                if mode == "MAKER_PHASE1":
                    bot.last_note = f"no side at or below {max(BOT40_MAKER_LEVELS):.2f}"
                else:
                    bot.last_note = f"no side at or below {BOT40_FALLBACK_PRICE:.2f}"
            else:
                bot.last_note = "no side with direction / ask missing"
            return

        if bot.current_market_spent >= MAX_BUY_USD - 1e-9:
            bot.buy_done_for_market = True
            bot.last_decision = "WAIT"
            bot.last_note = "cap filled"
            return

        key = (sec, side)
        if key in bot.executed_virtual_sec_side:
            bot.last_decision = "WAIT"
            bot.last_note = "already checked this second"
            return
        bot.executed_virtual_sec_side.add(key)

        best_ask = self.prices[side]["best_ask"]
        mode = "MARKET_035"
        price_limit = ENTRY_THRESHOLD
        if bot is self.bot40:
            level, mode = self._bot40_level_and_mode(sec)
            price_limit = level
            if best_ask is None or best_ask > price_limit:
                bot.last_decision = "WAIT"
                bot.last_note = f"{side} ask not valid"
                return
        else:
            mode = "MARKET_ANY"
            price_limit = 1.0
            if best_ask is None:
                bot.last_decision = "WAIT"
                bot.last_note = f"{side} ask missing"
                return

        if bot.last_buy and bot.last_buy.get("side") != side:
            bot.last_decision = "WAIT"
            bot.last_note = "side locked"
            return

        avail_qty, avail_notional = self._qty_notional_le(side, price_limit)
        remaining_cap = max(0.0, MAX_BUY_USD - bot.current_market_spent)
        spend_cap = min(remaining_cap, avail_notional or 0.0)
        if spend_cap <= 0:
            bot.last_decision = "WAIT"
            bot.last_note = f"{side} no liquidity <= {price_limit:.2f}"
            return

        # ============================================================
        # LIVE vs DRY-RUN branch
        # ============================================================
        if self.dry_run:
            # V3 simulation path (unchanged)
            spent, filled_qty, avg_fill = self._simulate_fill_up_to_cap(side, spend_cap, price_limit)
            if spent <= 0 or filled_qty <= 0 or avg_fill is None:
                bot.last_decision = "WAIT"
                bot.last_note = "fill simulation failed"
                return
            live_order_id = None
        else:
            # LIVE path: enforce daily loss cap, refuse if killed, then place real order.
            if self._check_and_update_daily_kill():
                bot.last_decision = "BLOCKED"
                bot.last_note = f"daily loss cap hit (${MAX_DAILY_LOSS_USD:.0f})"
                return
            if self.wallet is None or not self.wallet.connected:
                bot.last_decision = "BLOCKED"
                bot.last_note = "wallet not connected"
                return
            # Refuse if wallet balance is over the safety cap (shouldn't be funded that high).
            bal = self.wallet.get_usdc_balance()
            if bal is not None and bal > MAX_WALLET_USD:
                bot.last_decision = "BLOCKED"
                bot.last_note = f"wallet ${bal:.2f} > cap ${MAX_WALLET_USD:.0f}"
                return
            # Compute order parameters: place at price_limit for spend_cap dollars.
            order_price = round(min(price_limit, 0.99), 4)  # never above 0.99
            order_shares = round(spend_cap / order_price, 4)
            if order_shares < 5.0:  # Polymarket CLOB minimum is ~5 shares
                bot.last_decision = "WAIT"
                bot.last_note = f"order size {order_shares:.2f} below minimum (5)"
                return
            token_id = self.current.get("yes_token") if side == "UP" else self.current.get("no_token")
            if not token_id:
                bot.last_decision = "WAIT"
                bot.last_note = f"{side} token_id missing ג€” cannot place live order"
                return
            order_id, status = self.wallet.place_buy(str(token_id), order_price, order_shares)
            if not order_id or not status.startswith(("placed", "dry_run")):
                bot.last_decision = "REJECTED"
                bot.last_note = f"live order rejected: {status}"
                self.logger.log_event(self.current.get("slug") or "-", "LIVE_ORDER_REJECTED",
                                       f"side={side} price={order_price} size={order_shares} status={status}")
                return
            # Optimistically record fill at the limit price. (True fill price could be
            # lower; we'll reconcile later via order status polling in V2.)
            spent = round(order_shares * order_price, 6)
            filled_qty = order_shares
            avg_fill = order_price
            live_order_id = order_id
            self.logger.log_event(self.current.get("slug") or "-", "LIVE_ORDER_PLACED",
                                   f"side={side} order_id={order_id} price={order_price} size={order_shares}")
            print(f"{ANSI_GREEN}[LIVE] {bot.name} BUY {side} @{order_price:.4f} x{order_shares:.2f} (${spent:.2f}) order_id={order_id}{ANSI_RESET}")

        entry_flow_side = self._flow_side_from_distance(distance)
        entry_with_flow = int(side == entry_flow_side) if entry_flow_side is not None else None
        pos = VirtualPosition(
            sec=sec,
            side=side,
            spent=spent,
            qty=filled_qty,
            avg_fill=avg_fill,
            entry_best_ask=best_ask,
            entry_best_bid=self.prices[side]["best_bid"],
            entry_btc_price=btc,
            entry_target_price=target,
            entry_distance=distance,
            entry_flow_side=entry_flow_side,
            entry_with_flow=entry_with_flow,
            entry_ts=DualResearchLogger._ts(),
        )
        bot.positions[side].append(pos)
        bot.current_market_buy_count += 1
        bot.current_market_spent += spent
        bot.last_buy = {
            "sec": sec,
            "side": side,
            "spent": spent,
            "qty": filled_qty,
            "avg_fill": avg_fill,
            "avail_notional": avail_notional,
        }
        bot.virtual_buy_count += 1
        bot.virtual_spent_total += spent
        bot.buy_done_for_market = bot.current_market_spent >= MAX_BUY_USD - 1e-9
        bot.triggers_seen += 1
        bot.last_decision = f"BUY_{side}"
        bot.last_note = f"{mode} {side} sec={sec} spent=${spent:.2f} total=${bot.current_market_spent:.2f}"

        limit_031_filled = 1 if (bot is self.bot40 and mode == "LIMIT_031") else 0
        limit_fill_sec = sec if limit_031_filled else None
        fallback_used = 1 if (bot is self.bot40 and mode == "FALLBACK_035") else 0
        fallback_fill_sec = sec if fallback_used else None
        fallback_fill_price = avg_fill if fallback_used else None

        self.logger.log_virtual_buy(
            bot=bot,
            slug=self.current["slug"],
            sec=sec,
            side=side,
            mode=mode,
            price_limit=price_limit,
            spent=spent,
            qty=filled_qty,
            avg_fill=avg_fill,
            best_ask=best_ask,
            best_bid_now=self.prices[side]["best_bid"],
            avail_notional=avail_notional,
            avail_qty=avail_qty,
            btc_price=btc,
            target_price=target,
            distance=distance,
            limit_031_filled=limit_031_filled,
            limit_fill_sec=limit_fill_sec,
            fallback_used=fallback_used,
            fallback_fill_sec=fallback_fill_sec,
            fallback_fill_price=fallback_fill_price,
            note=bot.last_note,
        )

    def _position_mark(self, bot: BotState, side: str) -> Tuple[float, float, Optional[float], Optional[float]]:
        positions = bot.positions[side]
        if not positions:
            return 0.0, 0.0, None, None
        total_spent = sum(p.spent for p in positions)
        total_qty = sum(p.qty for p in positions)
        best_bid = self.prices[side]["best_bid"]
        if best_bid is None:
            return total_spent, total_qty, None, None
        mark_value = total_qty * best_bid
        pnl = mark_value - total_spent
        return total_spent, total_qty, round(mark_value, 6), round(pnl, 6)

    def _open_pnl_total(self, bot: BotState) -> Tuple[float, float, float]:
        total_spent = 0.0
        total_mark = 0.0
        for side in ["UP", "DOWN"]:
            spent, _qty, mark_value, _pnl = self._position_mark(bot, side)
            total_spent += spent
            total_mark += mark_value or 0.0
        return round(total_spent, 6), round(total_mark, 6), round(total_mark - total_spent, 6)

    def _update_daily_pnl(self, pnl_delta: float) -> None:
        """Accumulate today's realized P&L. Resets at calendar day boundary."""
        today = datetime.now().strftime("%Y-%m-%d")
        if self.daily_realized_date != today:
            self.daily_realized_date = today
            self.daily_realized_pnl = 0.0
            self.killed_for_daily_loss = False
        self.daily_realized_pnl += pnl_delta

    def _check_and_update_daily_kill(self) -> bool:
        """Returns True if daily kill is active (bot must NOT trade).
        Updates the killed_for_daily_loss flag. Only enforces in live mode."""
        if self.dry_run:
            return False
        # day rollover handled inside _update_daily_pnl, but call once with 0 to ensure date is current
        today = datetime.now().strftime("%Y-%m-%d")
        if self.daily_realized_date != today:
            self.daily_realized_date = today
            self.daily_realized_pnl = 0.0
            self.killed_for_daily_loss = False
        if self.killed_for_daily_loss:
            return True
        if self.daily_realized_pnl <= -MAX_DAILY_LOSS_USD:
            self.killed_for_daily_loss = True
            self.logger.log_event(self.current.get("slug") or "-", "KILL_DAILY_LOSS",
                                   f"daily_pnl=${self.daily_realized_pnl:.2f} cap=${MAX_DAILY_LOSS_USD:.2f}")
            print(f"{ANSI_RED}{ANSI_BOLD}KILL SWITCH: daily loss cap hit. PnL=${self.daily_realized_pnl:.2f}. Bot will NOT place new orders today.{ANSI_RESET}")
            return True
        return False

    def _settle_bot_positions(self, bot: BotState) -> None:
        target = self.current.get("target_price")
        if target is None:
            target, source, event_meta_target, line_target, strike_target, question_target, rendered_page_target = self.ensure_target_price(
                self.current.get("slug") or "-",
                self.current.get("url") or "",
                self.current.get("question") or "",
                target,
            )
            if target is not None:
                self.current["target_price"] = target
                self.current["target_source"] = source
                self.current["target_event_meta"] = event_meta_target
                self.current["target_line"] = line_target
                self.current["target_strike"] = strike_target
                self.current["target_question"] = question_target
                self.current["target_rendered_page"] = rendered_page_target
            else:
                self._capture_binance_prev_5m_close_target()
                target = self.current.get("target_binance_prev_5m_close")
        btc = self.binance.price
        slug = self.current.get("slug") or "-"
        total_spent = 0.0
        total_qty_up = 0.0
        total_qty_down = 0.0
        for pos in bot.positions["UP"]:
            total_spent += pos.spent
            total_qty_up += pos.qty
        for pos in bot.positions["DOWN"]:
            total_spent += pos.spent
            total_qty_down += pos.qty
        if total_spent <= 0:
            return
        if target is None or btc is None:
            bot.last_note = f"SETTLE SKIPPED {bot.name} | missing target or binance"
            return
        if btc > target:
            winner_side = "UP"
            payout = total_qty_up
        elif btc < target:
            winner_side = "DOWN"
            payout = total_qty_down
        else:
            winner_side = "PUSH"
            payout = total_spent
        pnl = payout - total_spent
        bot.realized_payout_total += payout
        bot.realized_pnl_total += pnl
        bot.settled_markets += 1
        # Daily P&L tracking ג€” only matters for live mode but we accumulate always.
        self._update_daily_pnl(pnl)
        if pnl > 1e-9:
            bot.wins += 1
            result = "WIN"
        elif pnl < -1e-9:
            bot.losses += 1
            result = "LOSS"
        else:
            bot.pushes += 1
            result = "PUSH"
        self.logger.log_settlement(
            bot=bot,
            slug=slug,
            winner_side=winner_side,
            btc_price=btc,
            target_price=target,
            spent_total=total_spent,
            payout_total=payout,
            pnl_total=pnl,
            up_qty=total_qty_up,
            down_qty=total_qty_down,
            result=result,
        )
        for side_name in ["UP", "DOWN"]:
            for pos in bot.positions[side_name]:
                if winner_side == "PUSH":
                    pos_payout = pos.spent
                elif pos.side == winner_side:
                    pos_payout = pos.qty
                else:
                    pos_payout = 0.0
                pos_pnl = pos_payout - pos.spent
                if pos_pnl > 1e-9:
                    pos_result = "WIN"
                elif pos_pnl < -1e-9:
                    pos_result = "LOSS"
                else:
                    pos_result = "PUSH"
                self.logger.log_trade_outcome(
                    bot_name=bot.name,
                    slug=slug,
                    pos=pos,
                    winner_side=winner_side,
                    result=pos_result,
                    payout=pos_payout,
                    pnl=pos_pnl,
                    settle_btc_price=btc,
                    settle_target_price=target,
                )
        bot.last_note = f"SETTLED {bot.name} winner={winner_side} spent=${total_spent:.2f} payout=${payout:.2f} pnl=${pnl:.2f}"

    async def move_to_next_market(self) -> None:
        old_slug = self.current["slug"]
        old_suffix = self.current["current_suffix"]
        self._settle_bot_positions(self.bot40)
        self._settle_bot_positions(self.bot120)
        self.current["current_suffix"] = int(self.current["current_suffix"]) + 300
        self.current["slug"] = self.build_slug_from_suffix(self.current["prefix"], self.current["current_suffix"])
        self.current["url"] = self.build_url_from_slug(self.current["slug"])
        market = self.fetch_market_by_slug(self.current["slug"])
        if not market:
            raise RuntimeError(f"׳׳ ׳ ׳׳¦׳ ׳©׳•׳§ ׳¢׳‘׳•׳¨ slug ׳”׳‘׳: {self.current['slug']}")
        self.current.update(market)
        self.current["market_loaded_at"] = time.time()
        self._render_task = None
        self._capture_binance_prev_5m_close_target()
        self._capture_binance_open_target()
        self._kickoff_render_target()
        for side in ["UP", "DOWN"]:
            self.prices[side] = {"best_bid": None, "best_ask": None, "last_trade": None, "updated_at": 0.0, "asks": []}
        self.bot40.reset_market()
        self.bot120.reset_market()
        self.meta["markets_scanned"] += 1
        self.meta["last_rollover"] = f"{old_suffix} -> {self.current['current_suffix']}"
        self.logger.log_event(old_slug or "-", "ROLLOVER", f"to {self.current['slug']}")

    def _format_bot_panel(self, bot: BotState) -> List[str]:
        open_spent, open_mark, open_pnl = self._open_pnl_total(bot)
        market_profit = open_pnl
        active_buy = str(bot.last_decision).startswith("BUY")
        decision_text = colorize_decision(bot.last_decision, active=active_buy)
        blink_profit = abs(open_pnl) > 1e-9
        role_line = bot.last_buy["side"] if bot.last_buy else "NONE"
        return [
            f"DEC:{decision_text}  TRIG:{bot.triggers_seen}  MKTS:{self.meta['markets_scanned']}",
            f"BUYS:{bot.virtual_buy_count}  SETTLED:{bot.settled_markets}",
            f"THIS:buys={bot.current_market_buy_count} spent=${bot.current_market_spent:,.2f}",
            f"OPEN:$ / PNL:{color_money(open_mark, False)} / {color_money(open_pnl, blink_profit)}",
            f"PROFIT:{color_money(market_profit)}",
            f"W:{bot.wins}  L:{bot.losses}  P:{bot.pushes}",
            f"POS:{role_line}  LAST:{bot.last_note}",
            f"WIN:{'ACTIVE' if bot.start_sec <= self.seconds_from_market_start() <= bot.end_sec else 'CLOSED'}  REASON:{bot.last_note}",
        ]

    def print_status(self) -> None:
        self.clear_screen()
        width = 118
        btc = self.binance.snapshot()
        updated = datetime.fromtimestamp(btc['updated_at']).strftime('%Y-%m-%d %H:%M:%S') if btc['updated_at'] else '-'
        btc_price = btc['price']

        target_used, target_source = self._resolve_target_in_use()
        self.current["target_price"] = target_used
        self.current["target_source"] = target_source
        dist = None if btc_price is None or target_used is None else abs(btc_price - target_used)

        mode_label = (f"{ANSI_RED}{ANSI_BOLD}LIVE TRADING{ANSI_RESET}" if not self.dry_run
                      else f"{ANSI_CYAN}DRY-RUN (simulation){ANSI_RESET}")
        print(f"{ANSI_BOLD}LIVE_BTC_5M_V1{ANSI_RESET}   mode={mode_label}")
        print("=" * width)
        print(f"LOCAL TIME   : {self.now_local_str()}")
        # show daily kill / cap status when in live mode
        if not self.dry_run:
            kill_state = (f"{ANSI_RED}{ANSI_BOLD}KILL ACTIVE{ANSI_RESET}" if self.killed_for_daily_loss
                          else f"{ANSI_GREEN}OK{ANSI_RESET}")
            print(f"DAILY        : pnl=${self.daily_realized_pnl:+.2f}  cap=${MAX_DAILY_LOSS_USD:.0f}  state={kill_state}")
            if self.wallet:
                bal = self.wallet.get_usdc_balance()
                bal_str = f"{bal:.2f}" if bal is not None else "?"
                addr_str = self.wallet.address[:8] + "..." if self.wallet.address else "-"
                print(f"WALLET       : ${bal_str}  cap=${MAX_WALLET_USD:.0f}  addr={addr_str}")
        print(f"SLUG         : {self.current['slug'] or '-'}")
        print(f"URL          : {self.current['url'] or '-'}")
        print(f"QUESTION     : {self.current['question'] or '-'}")
        print(f"MARKET END   : {self.current['end_date'] or '-'}")
        print(f"SEC FROM STRT: {self.seconds_from_market_start()}")
        print(f"ENTRY RULE   : bot40 0-{BOT40_LIMIT_END_SEC}s maker@{BOT40_MAKER_LEVELS} (size=${BOT40_MAKER_SIZE_USD:.0f}/lvl) then {BOT40_MAX_SEC}s<={BOT40_FALLBACK_PRICE:.2f} | if dist>={BOT40_FLOW_DIST_THRESHOLD:.0f} only with flow | bot120 {BOT120_MIN_SEC}-{BOT120_MAX_SEC}s dir-only dist>={MIN_DIST_BOT120:.0f} cap<={BOT120_MAX_PRICE:.2f} | cap=${MAX_BUY_USD:.0f}")
        print(f"BINANCE BTC  : {self.fmt(btc_price, 2)} | updated: {updated}")
        print(f"TARGET USED  : {self.fmt(target_used, 2)} | source: {target_source or '-'}")
        print(f"TGT RENDERED : {self.fmt(self.current.get('target_rendered_page'), 2)}")
        print(f"TGT MODE     : rendered retry 0-{RENDER_RETRY_WINDOW_SEC}s every {RENDER_RETRY_INTERVAL_SEC}s | no fallback for trading")
        print(f"TGT RETRIES  : attempts={self.current.get('render_retry_attempts') or 0} | last_sec={self.current.get('render_retry_last_sec') if self.current.get('render_retry_last_sec') is not None else '-'} | status={self.current.get('render_retry_status') or '-'}")
        print(f"TGT SOURCE   : {self.current.get('render_retry_last_source') or '-'}")
        print(f"TGT ERR      : {self.current.get('render_retry_last_error') or '-'}")
        print(f"TGT BIN CLOS : {self.fmt(self.current.get('target_binance_prev_5m_close'), 2)}")
        print(f"TGT BIN OPEN : {self.fmt(self.current.get('target_binance_open'), 2)}")
        print(f"DIST TARGET  : {self.fmt(dist, 2)} | BOT120 MIN DIST: {MIN_DIST_BOT120:.2f}")
        print("-" * width)
        print(f"{'SIDE':<8}{'BEST BID':<12}{'BEST ASK':<12}{'LAST TRADE':<12}{'SPREAD':<10}{'STALE':<8}{'QTY<=' + str(ENTRY_THRESHOLD):<18}{'USD<=' + str(ENTRY_THRESHOLD):<18}")
        for side in ("UP", "DOWN"):
            best_bid = self.prices[side]["best_bid"]
            best_ask = self.prices[side]["best_ask"]
            spread = None
            if best_bid is not None and best_ask is not None:
                spread = best_ask - best_bid
            stale = "YES" if ((time.time() - self.prices[side]["updated_at"]) > STALE_AFTER_SEC if self.prices[side]["updated_at"] else True) else "NO"
            print(
                f"{side:<8}"
                f"{self.fmt(best_bid):<12}"
                f"{self.fmt(best_ask):<12}"
                f"{self.fmt(self.prices[side]['last_trade']):<12}"
                f"{self.fmt(spread):<10}"
                f"{stale:<8}"
                f"{self.fmt(self._qty_notional_le(side, ENTRY_THRESHOLD)[0]):<18}"
                f"{self.fmt(self._qty_notional_le(side, ENTRY_THRESHOLD)[1]):<18}"
            )
        print("=" * width)

        left = self._format_bot_panel(self.bot40)
        right = self._format_bot_panel(self.bot120)
        panel_width = 56
        print(ANSI_CYAN + trim_cell("BOT40", panel_width) + ANSI_RESET + " | " + ANSI_CYAN + trim_cell("BOT120", panel_width) + ANSI_RESET)
        print("-" * (panel_width * 2 + 3))
        for l, r in zip(left, right):
            print(trim_cell(l, panel_width) + " | " + trim_cell(r, panel_width))
        print("-" * (panel_width * 2 + 3))
        combined_trades = self.bot40.virtual_buy_count + self.bot120.virtual_buy_count
        bot40_open_spent, bot40_open_mark, bot40_open_pnl = self._open_pnl_total(self.bot40)
        bot120_open_spent, bot120_open_mark, bot120_open_pnl = self._open_pnl_total(self.bot120)
        bot40_total_profit = self.bot40.realized_pnl_total + bot40_open_pnl
        bot120_total_profit = self.bot120.realized_pnl_total + bot120_open_pnl
        combined_profit = bot40_total_profit + bot120_total_profit
        print(f"BOT40 TOTAL_PROFIT : {color_money(bot40_total_profit)}")
        print(f"BOT120 TOTAL_PROFIT: {color_money(bot120_total_profit)}")
        print(f"COMBINED: TRADES={combined_trades}   TOTAL_PROFIT={color_money(combined_profit)}")
        print("Ctrl+C ׳›׳“׳™ ׳׳¢׳¦׳•׳¨")

    async def stream_current_market(self) -> None:
        ssl_ctx = ssl.create_default_context()
        while True:
            try:
                async with websockets.connect(
                    POLY_WS_URL,
                    ssl=ssl_ctx,
                    ping_interval=None,
                    close_timeout=5,
                    max_size=2**20,
                ) as ws:
                    await ws.send(json.dumps(self.build_subscribe_payload()))
                    hb_task = asyncio.create_task(self.ws_heartbeat(ws))
                    self.logger.log_event(self.current["slug"], "WS_SUBSCRIBE", self.current["slug"])
                    last_print = 0.0
                    last_logged_sec = -1
                    try:
                        while True:
                            if time.time() >= int(self.current["current_suffix"]) + 300:
                                await self.move_to_next_market()
                                self.print_status()
                                break
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                                self.handle_ws_message(raw)
                            except asyncio.TimeoutError:
                                pass
                            self._capture_binance_prev_5m_close_target()
                            self._capture_binance_open_target()
                            sec = self.seconds_from_market_start()
                            if sec != last_logged_sec:
                                last_logged_sec = sec
                                await self._maybe_retry_rendered_target(sec)
                                self._record_bot40_research_for_second(sec)
                                self._record_bot120_research_for_second(sec)
                                self._record_signals_for_second(sec)
                                bot40_side = self._choose_bot40_side(sec)
                                if bot40_side is not None:
                                    self._try_execute_bot_buy(self.bot40, sec, bot40_side)
                                else:
                                    self._try_execute_bot_buy(self.bot40, sec, None)
                                bot120_side = self._choose_bot120_side(sec)
                                if bot120_side is not None:
                                    self._try_execute_bot_buy(self.bot120, sec, bot120_side)
                                else:
                                    self._try_execute_bot_buy(self.bot120, sec, None)
                            if time.time() - last_print >= SCREEN_REFRESH_EVERY_SEC:
                                last_print = time.time()
                                self.print_status()
                    finally:
                        hb_task.cancel()
            except KeyboardInterrupt:
                raise
            except Exception as e:
                self.bot40.last_decision = "ERROR"
                self.bot120.last_decision = "ERROR"
                self.bot40.last_note = f"[WS ERROR] {e}"
                self.bot120.last_note = f"[WS ERROR] {e}"
                self.logger.log_event(self.current.get("slug") or "-", "ERROR", str(e))
                self.print_status()
                await asyncio.sleep(3)


def parse_cli_args() -> "argparse.Namespace":
    import argparse
    p = argparse.ArgumentParser(
        description="LIVE_BTC_5M_V1 ג€” Polymarket BTC 5-min trading bot.",
    )
    p.add_argument("--live", action="store_true",
                   help="Enable LIVE trading (real orders, real money). Default is dry-run simulation.")
    p.add_argument("--url", default=None,
                   help="Initial Polymarket 5-min event URL (skip the input prompt).")
    return p.parse_args()


def confirm_live_mode() -> bool:
    """Loud, hard-to-mistake confirmation before enabling live trading."""
    print()
    print(f"{ANSI_RED}{ANSI_BOLD}{'=' * 70}{ANSI_RESET}")
    print(f"{ANSI_RED}{ANSI_BOLD}  LIVE TRADING MODE  -  REAL MONEY ON POLYMARKET  {ANSI_RESET}")
    print(f"{ANSI_RED}{ANSI_BOLD}{'=' * 70}{ANSI_RESET}")
    print(f"{ANSI_YELLOW}This bot will place REAL buy orders on Polymarket using the wallet")
    print(f"private key in /root/.env (or local .env).{ANSI_RESET}")
    print()
    print(f"  Per-trade size:     ${MAX_BUY_USD:.2f}")
    print(f"  BOT40 maker levels: {BOT40_MAKER_LEVELS}  (size ${BOT40_MAKER_SIZE_USD:.2f}/level)")
    print(f"  BOT120 dist >=:     {MIN_DIST_BOT120:.0f}, price cap {BOT120_MAX_PRICE:.2f}")
    print(f"  Daily loss cap:     ${MAX_DAILY_LOSS_USD:.2f}  (bot stops itself)")
    print(f"  Wallet cap:         ${MAX_WALLET_USD:.2f}  (refuse trade if exceeded)")
    print()
    answer = input("Type the words 'go live' to confirm, anything else cancels: ").strip().lower()
    if answer != "go live":
        print(f"{ANSI_YELLOW}Live mode NOT confirmed. Exiting.{ANSI_RESET}")
        return False
    print(f"{ANSI_GREEN}Confirmed. Live trading enabled.{ANSI_RESET}")
    return True


async def main() -> None:
    args = parse_cli_args()
    dry_run = not args.live
    if args.live:
        if not confirm_live_mode():
            return

    # initialize wallet (no-op in dry-run)
    wallet = Wallet(dry_run=dry_run)
    if not dry_run:
        print("Connecting to Polymarket CLOB...")
        if not wallet.connect():
            print(f"{ANSI_RED}CLOB connect failed: {wallet.last_error}{ANSI_RESET}")
            return
        bal = wallet.get_usdc_balance()
        if bal is None:
            print(f"{ANSI_YELLOW}Warning: could not read wallet balance ({wallet.last_error}). Continuing anyway.{ANSI_RESET}")
        else:
            print(f"Wallet USDC balance: ${bal:.2f}")
            if bal > MAX_WALLET_USD:
                print(f"{ANSI_RED}Wallet balance ${bal:.2f} exceeds cap ${MAX_WALLET_USD:.2f}. Refusing to trade.{ANSI_RESET}")
                print(f"{ANSI_RED}Withdraw funds first or raise MAX_WALLET_USD in code.{ANSI_RESET}")
                return
            if bal < MAX_BUY_USD:
                print(f"{ANSI_YELLOW}Warning: wallet balance ${bal:.2f} is less than per-trade size ${MAX_BUY_USD:.2f}. Trades may fail.{ANSI_RESET}")

    print(f"\nMode: {'LIVE TRADING' if not dry_run else 'DRY-RUN (simulation)'}")
    logger = DualResearchLogger()
    logger.clear_and_init()
    binance = BinanceEngine()
    binance_task = asyncio.create_task(binance.run())
    bot = Polymarket5mDualBot(binance, logger)
    # attach mode + wallet to bot so _try_execute_bot_buy can route accordingly
    bot.dry_run = dry_run
    bot.wallet = wallet
    try:
        if args.url:
            await bot.load_initial_market_from_url(args.url)
        else:
            await bot.load_initial_market_from_user()
        bot.print_status()
        await bot.stream_current_market()
    finally:
        binance.stop()
        binance_task.cancel()
        try:
            await binance_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass


if __name__ == "__main__":
    # NOTE: do NOT set WindowsSelectorEventLoopPolicy on Windows ג€” that breaks
    # Playwright's subprocess spawn. The default ProactorEventLoop is correct.
    # (V3 originally set Selector; we leave it on default.)
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nstopped.")
