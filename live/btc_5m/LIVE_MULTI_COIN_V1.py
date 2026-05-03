# -*- coding: utf-8 -*-
"""
LIVE_MULTI_COIN_V1.py — multi-coin trading bot entry point (PARKED, NOT RUNNING).

Built 2026-05-02 as part of Task #13 stages 2-4. The currently-running BTC bot
(LIVE_BTC_5M_V1_TEST5.py) is untouched and stays in production. This file is
the future replacement, exercised only when:

  1. Per-coin params for ETH/SOL/XRP/DOGE/BNB/HYPE have been calibrated from
     the multi-coin recordings, and
  2. The user explicitly approves a transition.

Until then this script will refuse to run any non-BTC coin (because their
COIN_PARAMS entries have `enabled: False`).

Run:
  python LIVE_MULTI_COIN_V1.py                                 # dry-run, all enabled coins
  python LIVE_MULTI_COIN_V1.py --live                          # LIVE trading (will prompt)
  python LIVE_MULTI_COIN_V1.py --only BTC                      # restrict to BTC
  python LIVE_MULTI_COIN_V1.py --url BTC=https://polymarket... # initial URL per coin
"""
import argparse
import asyncio
import sys
from typing import Dict

from bot_config import COIN_PARAMS, MAX_BUY_USD, MAX_DAILY_LOSS_USD, MAX_WALLET_USD
from bot_engine.master import Master


def parse_url_args(url_args) -> Dict[str, str]:
    """Each --url is COIN=URL, e.g. --url BTC=https://polymarket.com/event/..."""
    out: Dict[str, str] = {}
    for u in url_args or []:
        if "=" not in u:
            print(f"bad --url '{u}': expected COIN=URL")
            sys.exit(2)
        coin, url = u.split("=", 1)
        out[coin.strip().upper()] = url.strip()
    return out


def parse_cli_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LIVE_MULTI_COIN_V1 — multi-coin Polymarket trading bot.")
    p.add_argument("--live", action="store_true",
                   help="Enable LIVE trading. Default is dry-run simulation.")
    p.add_argument("--only", action="append", default=None,
                   help="Restrict to these coins (repeatable, e.g. --only BTC --only ETH).")
    p.add_argument("--url", action="append", default=[],
                   help="Initial market URL per coin: COIN=URL. Repeatable.")
    return p.parse_args()


def confirm_live_mode(coins) -> bool:
    print()
    print("=" * 70)
    print("  LIVE TRADING MODE  -  REAL MONEY ON POLYMARKET")
    print("=" * 70)
    print(f"  Coins:           {', '.join(coins)}")
    print(f"  Per-trade size:  ${MAX_BUY_USD:.2f}")
    print(f"  Daily loss cap:  ${MAX_DAILY_LOSS_USD:.2f} per coin")
    print(f"  Wallet cap:      ${MAX_WALLET_USD:.2f}")
    print()
    answer = input("Type 'go live' to confirm: ").strip().lower()
    return answer == "go live"


async def main_async() -> None:
    args = parse_cli_args()
    dry_run = not args.live

    only = [c.upper() for c in args.only] if args.only else None
    initial_urls = parse_url_args(args.url)

    master = Master(dry_run=dry_run, only_coins=only)
    coins = master.select_coins()
    if not coins:
        print("No enabled coins in COIN_PARAMS. Edit bot_config.py to enable coins.")
        sys.exit(1)

    missing = [c for c in coins if c not in initial_urls]
    if missing:
        print(f"Missing --url for: {', '.join(missing)}")
        print("Provide one --url COIN=https://polymarket.com/event/... per coin.")
        sys.exit(2)

    if args.live:
        if not confirm_live_mode(coins):
            print("Live mode NOT confirmed. Exiting.")
            return

    print(f"Starting master in {'LIVE' if not dry_run else 'DRY-RUN'} mode for: {', '.join(coins)}")
    await master.run(initial_urls)


if __name__ == "__main__":
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\nstopped.")
