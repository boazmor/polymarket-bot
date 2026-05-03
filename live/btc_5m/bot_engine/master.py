# -*- coding: utf-8 -*-
"""
master.py — multi-coin orchestrator.

The master:
  1. Reads COIN_PARAMS from bot_config and figures out which coins are enabled.
  2. For each enabled coin, builds: BinanceEngine + MarketManager + Strategy
     (logger is per-coin too).
  3. Shares ONE Wallet across all coins.
  4. Runs N concurrent asyncio tasks: one Polymarket WS stream per coin, one
     Binance feed per coin, plus a render loop showing combined status.
  5. Enforces a GLOBAL daily kill switch (sum of per-coin daily PnL) on top
     of the per-coin daily caps already enforced by Strategy.

This module is COMPLETE but PARKED — it is not invoked by the running BTC bot.
It will be activated only after the per-coin params are calibrated and the
user gives explicit go-live approval.
"""
import asyncio
import json
import os
import ssl
import time
from typing import Dict, List, Optional

import websockets

from bot_config import (
    COIN_PARAMS,
    POLY_WS_URL,
    SCREEN_REFRESH_EVERY_SEC,
    HEARTBEAT_EVERY_SEC,
)
from bot_engine.binance import BinanceEngine
from bot_engine.market_manager import MarketManager
from bot_engine.reports import CoinResearchLogger
from bot_engine.strategy import Strategy
from bot_engine.wallet import Wallet
from bot_engine import screen as scr


class CoinRuntime:
    """Bundle of everything one coin needs at runtime."""
    def __init__(self, coin: str, params: dict, wallet: Wallet):
        self.coin = coin
        self.params = params
        self.wallet = wallet

        self.binance = BinanceEngine(symbol=params.get("binance_symbol", f"{coin.lower()}usdt"))
        self.logger = CoinResearchLogger(
            data_dir=params.get("data_dir", f"data_live_{coin.lower()}_5m"),
            coin=coin,
        )
        self.market = MarketManager(coin=coin, binance_engine=self.binance,
                                     log_event=self._log_event_closure())
        self.strategy = Strategy(coin=coin, market_mgr=self.market,
                                 wallet=wallet, logger=self.logger,
                                 params=params)

        self.binance_task: Optional[asyncio.Task] = None
        self.poly_ws_task: Optional[asyncio.Task] = None

    def _log_event_closure(self):
        # Closure so MarketManager can log without circular import on Strategy
        def _log(slug, event, detail):
            try:
                self.logger.log_event(slug, event, detail)
            except Exception:
                pass
        return _log

    def init_logs(self) -> None:
        self.logger.clear_and_init()

    def set_dry_run(self, dry_run: bool) -> None:
        self.strategy.dry_run = dry_run


class Master:
    """Orchestrator for N coins running in parallel."""

    def __init__(self, dry_run: bool = True, only_coins: Optional[List[str]] = None):
        self.dry_run = dry_run
        self.only_coins = only_coins  # if set, restrict to this subset
        self.wallet = Wallet(dry_run=dry_run)
        self.runtimes: Dict[str, CoinRuntime] = {}
        self._stop = False

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------
    def select_coins(self) -> List[str]:
        coins = []
        for coin, params in COIN_PARAMS.items():
            if not params.get("enabled"):
                continue
            if self.only_coins and coin not in self.only_coins:
                continue
            coins.append(coin)
        return coins

    def build_runtimes(self) -> None:
        coins = self.select_coins()
        if not coins:
            raise RuntimeError("no enabled coins in COIN_PARAMS — cannot start master")
        for coin in coins:
            rt = CoinRuntime(coin=coin, params=COIN_PARAMS[coin], wallet=self.wallet)
            rt.init_logs()
            rt.set_dry_run(self.dry_run)
            self.runtimes[coin] = rt

    def connect_wallet(self) -> bool:
        if self.dry_run:
            return True
        if not self.wallet.connect():
            print(f"{scr.ANSI_RED}wallet connect failed: {self.wallet.last_error}{scr.ANSI_RESET}")
            return False
        bal = self.wallet.get_usdc_balance()
        if bal is not None:
            print(f"wallet USDC: ${bal:.2f}")
        return True

    # ------------------------------------------------------------------
    # Per-coin Polymarket WebSocket loop
    # ------------------------------------------------------------------
    async def _coin_poly_ws_loop(self, rt: CoinRuntime) -> None:
        ssl_ctx = ssl.create_default_context()
        last_logged_sec = -1
        while not self._stop:
            try:
                async with websockets.connect(
                    POLY_WS_URL,
                    ssl=ssl_ctx,
                    ping_interval=None,
                    close_timeout=5,
                    max_size=2**20,
                ) as ws:
                    await ws.send(json.dumps(rt.market.build_subscribe_payload()))
                    hb_task = asyncio.create_task(rt.market.ws_heartbeat(ws))
                    rt.logger.log_event(rt.market.current["slug"], "WS_SUBSCRIBE",
                                         rt.market.current["slug"])
                    try:
                        while not self._stop:
                            if time.time() >= int(rt.market.current["current_suffix"]) + 300:
                                await rt.market.rollover_to_next_market(rt.strategy.settle_all)
                                rt.strategy.reset_for_new_market()
                                # break inner loop so we re-subscribe to the new tokens
                                break
                            try:
                                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                                rt.market.handle_ws_message(raw)
                            except asyncio.TimeoutError:
                                pass
                            rt.market.capture_binance_prev_5m_close_target()
                            rt.market.capture_binance_open_target()
                            sec = rt.market.seconds_from_market_start()
                            if sec != last_logged_sec:
                                last_logged_sec = sec
                                await rt.market.maybe_retry_rendered_target(sec)
                                rt.strategy.tick(sec)
                    finally:
                        hb_task.cancel()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                rt.strategy.bot40.last_decision = "ERROR"
                rt.strategy.bot120.last_decision = "ERROR"
                rt.strategy.bot40.last_note = f"[{rt.coin} WS] {e}"
                rt.strategy.bot120.last_note = f"[{rt.coin} WS] {e}"
                rt.logger.log_event(rt.market.current.get("slug") or "-", "ERROR", str(e))
                await asyncio.sleep(3)

    # ------------------------------------------------------------------
    # Combined render loop
    # ------------------------------------------------------------------
    def _render_combined_status(self) -> None:
        scr.cursor_home_hide()
        width = 118
        mode_label = (f"{scr.ANSI_RED}{scr.ANSI_BOLD}LIVE TRADING{scr.ANSI_RESET}"
                      if not self.dry_run
                      else f"{scr.ANSI_CYAN}DRY-RUN (simulation){scr.ANSI_RESET}")
        print(f"{scr.ANSI_BOLD}LIVE_MULTI_COIN_V1{scr.ANSI_RESET}   mode={mode_label}")
        print("=" * width)
        print(f"LOCAL TIME : {scr.now_local_str()}")
        print(f"COINS      : {', '.join(sorted(self.runtimes.keys()))}")
        if not self.dry_run and self.wallet:
            bal = self.wallet.get_usdc_balance()
            bal_str = f"${bal:.2f}" if bal is not None else "?"
            addr_str = self.wallet.address[:8] + "..." if self.wallet.address else "-"
            print(f"WALLET     : {bal_str}   addr={addr_str}")
        print("-" * width)
        print(f"{'COIN':<6}{'PNL_TODAY':<14}{'TRADES':<8}{'OPEN_BOT40':<14}"
              f"{'OPEN_BOT120':<14}{'KILLED?':<10}{'NOTE'}")
        for coin in sorted(self.runtimes.keys()):
            rt = self.runtimes[coin]
            s = rt.strategy
            _, _, b40_pnl = s.open_pnl_total(s.bot40)
            _, _, b120_pnl = s.open_pnl_total(s.bot120)
            trades = s.bot40.virtual_buy_count + s.bot120.virtual_buy_count
            killed = "YES" if s.killed_for_daily_loss else "no"
            print(f"{coin:<6}"
                  f"{scr.color_money(s.daily_realized_pnl):<24}"
                  f"{trades:<8}"
                  f"{scr.color_money(b40_pnl):<24}"
                  f"{scr.color_money(b120_pnl):<24}"
                  f"{killed:<10}"
                  f"{scr.trim_cell(s.bot40.last_note, 60)}")
        print("=" * width)
        # Combined totals
        total_pnl = sum(rt.strategy.daily_realized_pnl for rt in self.runtimes.values())
        total_trades = sum(rt.strategy.bot40.virtual_buy_count
                           + rt.strategy.bot120.virtual_buy_count
                           for rt in self.runtimes.values())
        print(f"COMBINED   : trades={total_trades}   pnl_today={scr.color_money(total_pnl)}")
        print("Ctrl+C to stop.")

    async def _render_loop(self) -> None:
        while not self._stop:
            try:
                self._render_combined_status()
            except Exception as e:
                # render must never kill the master
                print(f"render error: {e}")
            await asyncio.sleep(SCREEN_REFRESH_EVERY_SEC)

    # ------------------------------------------------------------------
    # Top-level run
    # ------------------------------------------------------------------
    async def run(self, initial_urls: Dict[str, str]) -> None:
        """initial_urls maps COIN -> Polymarket /event/ URL.
        Every enabled coin must have an entry."""
        self.build_runtimes()
        if not self.connect_wallet():
            return

        # Spawn Binance feeds
        for coin, rt in self.runtimes.items():
            rt.binance_task = asyncio.create_task(rt.binance.run())

        # Load initial markets (sequential — one HTTP call each)
        for coin, rt in self.runtimes.items():
            url = initial_urls.get(coin)
            if not url:
                raise RuntimeError(f"no initial URL provided for {coin}")
            await rt.market.load_initial_market_from_url(url)

        # Spawn Polymarket WS loops
        for coin, rt in self.runtimes.items():
            rt.poly_ws_task = asyncio.create_task(self._coin_poly_ws_loop(rt))

        # Render loop in foreground
        try:
            await self._render_loop()
        finally:
            self.shutdown()

    def shutdown(self) -> None:
        self._stop = True
        for rt in self.runtimes.values():
            try:
                rt.binance.stop()
            except Exception:
                pass
            for t in (rt.binance_task, rt.poly_ws_task):
                if t and not t.done():
                    t.cancel()
