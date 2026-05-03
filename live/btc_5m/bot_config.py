# -*- coding: utf-8 -*-
"""
bot_config.py
=============
External params file for LIVE_BTC_5M_V1_TEST5.py (Task #13 stage 1, 2026-05-02).

Single source of truth for every tunable constant. The bot imports from here,
so adjusting strategy / safety / timing requires editing ONLY this file —
the main bot file stays untouched during normal tuning.

Groups:
  * URL / endpoint constants
  * Timing constants (heartbeat, screen refresh, phase windows)
  * BOT40 strategy params (maker levels, fallback, research grid)
  * BOT120 strategy params (distance threshold, limit price, research grid)
  * Safety / live-trading caps
  * Wallet / CLOB identity (Safe address, signature type)
  * Storage paths
"""

# ============================================================================
# URL / endpoint constants
# ============================================================================
GAMMA_MARKETS_BY_SLUG = "https://gamma-api.polymarket.com/markets"
POLY_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"
BINANCE_WS_URL = "wss://stream.binance.com:9443/ws/btcusdt@trade"
CLOB_HOST = "https://clob.polymarket.com"
POLYGON_CHAIN_ID = 137

# ============================================================================
# Timing constants
# ============================================================================
HEARTBEAT_EVERY_SEC = 10
STALE_AFTER_SEC = 20
SCREEN_REFRESH_EVERY_SEC = 2   # CHANGED 02/05 — bumped from 1 to 2 to reduce flicker on PowerShell

# Phase windows (seconds since 5-min market start)
BOT40_MAX_SEC = 40
BOT120_MIN_SEC = 0           # CHANGED FROM V3 (was 41) — V4-style, full window
BOT120_MAX_SEC = 120

# Render-retry pacing (target page extraction)
RENDER_RETRY_WINDOW_SEC = 20
RENDER_RETRY_INTERVAL_SEC = 2

# ============================================================================
# Generic strategy thresholds
# ============================================================================
ENTRY_THRESHOLD = 0.35

# ============================================================================
# BOT40 strategy params
# ============================================================================
# Maker model: BOT40 phase 1 places THREE simultaneous limit orders at these
# price levels. Each level gets BOT40_MAKER_SIZE_USD of capital reserved.
# Total in book = 3 * BOT40_MAKER_SIZE_USD. Cap on actual fills = MAX_BUY_USD.
BOT40_MAKER_LEVELS = [0.28, 0.29, 0.30]
BOT40_LIMIT_END_SEC = 30
BOT40_FALLBACK_PRICE = 0.35
BOT40_FLOW_DIST_THRESHOLD = 25.0

# BOT40 research-only grid (CSV reporting, not real trading)
BOT40_RESEARCH_PRICE_LEVELS = [round(x / 100.0, 2) for x in range(28, 43)]
BOT40_RESEARCH_SECONDS = list(range(32, 43))

# ============================================================================
# BOT120 strategy params
# ============================================================================
MIN_DIST_BOT120 = 60.0           # threshold to PLACE limit @ 0.50 maker order
BOT120_LIMIT_PRICE = 0.50        # limit order price for the maker buy
BOT120_MARKET_BUY_DIST = 68.0    # NEW 03/05 — additional MARKET buy when distance hits this
BOT120_MARKET_MAX_PRICE = 0.80   # NEW 03/05 — market buy fires only if ask <= this
BOT120_MAX_PRICE = 0.80          # legacy — kept for backwards compat in print_status

# BOT120 research-only grid
BOT120_RESEARCH_SECONDS = [55, 57, 59, 62, 64]
BOT120_RESEARCH_DISTANCE_LEVELS = [55.0, 57.0, 59.0, 62.0, 64.0]

# ============================================================================
# LIVE TRADING — strategy and safety constants
# ============================================================================
# Production size — same for virtual (dry-run) testing and for live trading.
# Virtual uses no real money so size is risk-free for testing.
MAX_BUY_USD = 2.0       # TEST FILE — $2/trade (was $1; floating-point made $1 fail Polymarket > $1 minimum)
BOT40_MAKER_SIZE_USD = 2.0   # TEST FILE      # $2 per maker order level (was $1; same float issue)

# Safety stops (enforced inside the bot, not just policy)
MAX_DAILY_LOSS_USD = 50.0    # TEST FILE — effectively disabled for $1 trades. Re-tighten when scaling up.
MAX_WALLET_USD = 500.0           # bot refuses to trade if wallet balance > this

# Modes
DRY_RUN_DEFAULT = True           # default; --live flag plus confirmation flips it

# ============================================================================
# Wallet / CLOB identity
# ============================================================================
# Polymarket uses Gnosis Safe proxy (signature_type=2) — discovered 2026-05-01.
# The REAL Safe address (the maker) was found 2026-05-02 by querying
# /data/trades after a manual UI order. The EOA signs, the Safe is the maker.
# MUST be passed as funder= for V2 orders. NOT 0x4cd0... (that was wrong).
SIGNATURE_TYPE_POLY_GNOSIS_SAFE = 2
SAFE_ADDRESS = "0x28Ae0B1f1e0e5a3F3eF0172CE28e0D19C197938B"

# ============================================================================
# Storage
# ============================================================================
DATA_DIR = "data_live_btc_5m_v1"

# ============================================================================
# Multi-coin configuration (Task #13 stage 4 scaffold, 2026-05-02)
# ============================================================================
# COIN_PARAMS holds per-coin overrides. The master controller looks at the
# `enabled` flag and only spawns strategies for enabled coins. BTC is the only
# calibrated coin right now — the other 6 are placeholders. They'll be filled
# in only after analyzing the multi-coin recordings (target: post-stage-4
# evaluation report).
#
# Schema (every key is optional; missing keys fall back to the globals above):
#   enabled                   bool — must be True for the master to start it
#   binance_symbol            str  — Binance pair (e.g. "btcusdt", "ethusdt")
#   poly_url                  str  — initial Polymarket /event/ URL
#   bot30_maker_levels        list[float]
#   bot40_fallback_price      float
#   bot120_min_distance       float
#   bot120_limit_price        float
#   max_buy_usd               float
#   bot40_maker_size_usd      float
#   max_daily_loss_usd        float
#   max_wallet_usd            float
#   blocked_nyc_hours         list[int]   — hours (NYC time) to skip trading
#   data_dir                  str   — per-coin CSV directory

COIN_PARAMS = {
    "BTC": {
        "enabled": True,
        "binance_symbol": "btcusdt",
        "poly_url": None,  # passed via --url for now
        "bot30_maker_levels": [0.28, 0.29, 0.30],
        "bot40_fallback_price": 0.35,
        "bot120_min_distance": 60.0,
        "bot120_limit_price": 0.50,
        "max_buy_usd": 1.0,
        "bot40_maker_size_usd": 1.0,
        "max_daily_loss_usd": 50.0,
        "blocked_nyc_hours": [5, 6, 7],  # confirmed losing in 14h dataset
        # IMPORTANT: separate data_dir from the running TEST5 bot's data_live_btc_5m_v1.
        # If both bots ever run side-by-side (e.g. for parallel A/B), they MUST NOT
        # share a data_dir — clear_and_init wipes it on startup.
        "data_dir": "data_live_btc_5m_multicoin",
    },
    # ----- placeholders, NOT calibrated, NOT enabled -----
    "ETH": {
        "enabled": False,
        "binance_symbol": "ethusdt",
        "data_dir": "data_live_eth_5m",
    },
    "SOL": {
        "enabled": False,
        "binance_symbol": "solusdt",
        "data_dir": "data_live_sol_5m",
    },
    "XRP": {
        "enabled": False,
        "binance_symbol": "xrpusdt",
        "data_dir": "data_live_xrp_5m",
    },
    "DOGE": {
        "enabled": False,
        "binance_symbol": "dogeusdt",
        "data_dir": "data_live_doge_5m",
    },
    "BNB": {
        "enabled": False,
        "binance_symbol": "bnbusdt",
        "data_dir": "data_live_bnb_5m",
    },
    "HYPE": {
        "enabled": False,
        "binance_symbol": "hypeusdt",
        "data_dir": "data_live_hype_5m",
    },
}
