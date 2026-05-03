# -*- coding: utf-8 -*-
"""
bot_engine — modular components for the multi-coin Polymarket trading bot.

Layout:
  state.py          dataclasses (VirtualPosition, BotState)
  binance.py        Binance WebSocket price fetcher (per-symbol)
  wallet.py         Polymarket CLOB wallet wrapper (shared across coins)
  reports.py        CSV logger (per-coin data dir)
  strategy.py       BOT_30 / BOT_40 / BOT_120 logic + execution + settlement
  market_manager.py Polymarket market: slug, target, WS, rollover (per-coin)
  screen.py         Display rendering (single + multi-coin)
  master.py         Multi-coin orchestrator

The currently-running bot (LIVE_BTC_5M_V1_TEST5.py) does NOT use this package.
This is a parallel, parked architecture ready for future activation.
"""
__version__ = "0.1.0-stage4"
