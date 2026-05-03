# -*- coding: utf-8 -*-
"""
strategy.py — per-coin trading strategy.

Stage-2 split: the original BOT40 strategy is exposed as TWO clearly-named
phase methods over one shared BotState:

    bot_30_choose_side()   — sec 0-30, MAKER pattern at maker levels
    bot_40_choose_side()   — sec 30-40, TAKER fallback at fallback price

Both phases share one BotState (named "BOT40" for CSV schema continuity), so
the spent budget cap (MAX_BUY_USD) accumulates across phases — same as before.

BOT_120 is a parallel strategy with its own BotState. Identical to original.

One Strategy instance per coin. The master controller spawns N strategies and
ticks each one as new seconds tick by.
"""
import math
import time
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from bot_config import (
    BOT40_MAX_SEC, BOT40_LIMIT_END_SEC,
    BOT40_MAKER_LEVELS, BOT40_FALLBACK_PRICE, BOT40_FLOW_DIST_THRESHOLD,
    BOT40_RESEARCH_PRICE_LEVELS, BOT40_RESEARCH_SECONDS,
    BOT120_MIN_SEC, BOT120_MAX_SEC,
    BOT120_RESEARCH_SECONDS, BOT120_RESEARCH_DISTANCE_LEVELS,
    MIN_DIST_BOT120, BOT120_MAX_PRICE, BOT120_LIMIT_PRICE,
    MAX_BUY_USD, BOT40_MAKER_SIZE_USD,
    MAX_DAILY_LOSS_USD, MAX_WALLET_USD,
    ENTRY_THRESHOLD,
)
from bot_engine.state import BotState, VirtualPosition


def _safe_float(x):
    try:
        v = float(x)
        if math.isfinite(v):
            return v
        return None
    except Exception:
        return None


def _now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _flow_side_from_distance(distance: Optional[float]) -> Optional[str]:
    if distance is None:
        return None
    if distance > 0:
        return "UP"
    if distance < 0:
        return "DOWN"
    return None


class Strategy:
    """Per-coin strategy: BOT_30 + BOT_40 (shared budget) and BOT_120 (parallel).

    Args:
        coin: short symbol e.g. "BTC", "ETH".
        market_mgr: MarketManager instance for this coin.
        wallet: Wallet instance, shared across all coins.
        logger: CoinResearchLogger instance, per-coin.
        params: per-coin params dict (subset of COIN_PARAMS[coin]).
                Falls back to bot_config globals when a key is missing.
    """

    def __init__(self, coin: str, market_mgr, wallet, logger, params: Optional[Dict] = None):
        self.coin = coin
        self.mm = market_mgr
        self.wallet = wallet
        self.logger = logger
        self.p = self._merge_params(params or {})

        self.bot40 = BotState(name="BOT40", start_sec=0, end_sec=self.p["bot40_max_sec"])
        self.bot120 = BotState(name="BOT120",
                               start_sec=self.p["bot120_min_sec"],
                               end_sec=self.p["bot120_max_sec"])

        self.dry_run: bool = True
        self.daily_realized_pnl: float = 0.0
        self.daily_realized_date: str = ""
        self.killed_for_daily_loss: bool = False

    # ------------------------------------------------------------------
    # Param merge (per-coin overrides global)
    # ------------------------------------------------------------------
    @staticmethod
    def _merge_params(coin_params: Dict) -> Dict:
        return {
            "bot40_max_sec": coin_params.get("bot40_max_sec", BOT40_MAX_SEC),
            "bot40_limit_end_sec": coin_params.get("bot40_limit_end_sec", BOT40_LIMIT_END_SEC),
            "bot30_maker_levels": list(coin_params.get("bot30_maker_levels", BOT40_MAKER_LEVELS)),
            "bot40_fallback_price": coin_params.get("bot40_fallback_price", BOT40_FALLBACK_PRICE),
            "bot40_flow_dist_threshold": coin_params.get("bot40_flow_dist_threshold", BOT40_FLOW_DIST_THRESHOLD),
            "bot40_maker_size_usd": coin_params.get("bot40_maker_size_usd", BOT40_MAKER_SIZE_USD),
            "bot120_min_sec": coin_params.get("bot120_min_sec", BOT120_MIN_SEC),
            "bot120_max_sec": coin_params.get("bot120_max_sec", BOT120_MAX_SEC),
            "bot120_min_distance": coin_params.get("bot120_min_distance", MIN_DIST_BOT120),
            "bot120_max_price": coin_params.get("bot120_max_price", BOT120_MAX_PRICE),
            "bot120_limit_price": coin_params.get("bot120_limit_price", BOT120_LIMIT_PRICE),
            "max_buy_usd": coin_params.get("max_buy_usd", MAX_BUY_USD),
            "max_daily_loss_usd": coin_params.get("max_daily_loss_usd", MAX_DAILY_LOSS_USD),
            "max_wallet_usd": coin_params.get("max_wallet_usd", MAX_WALLET_USD),
            "blocked_nyc_hours": list(coin_params.get("blocked_nyc_hours", [])),
        }

    # ------------------------------------------------------------------
    # Liquidity / order-book helpers
    # ------------------------------------------------------------------
    def _spread(self, side: str) -> Optional[float]:
        bid = self.mm.prices[side]["best_bid"]
        ask = self.mm.prices[side]["best_ask"]
        if bid is None or ask is None:
            return None
        return round(ask - bid, 6)

    def _best_ask_qty(self, side: str) -> Tuple[Optional[float], Optional[float]]:
        asks = self.mm.prices[side]["asks"]
        if not asks:
            return None, None
        best = asks[0]
        if best.get("size") is None:
            return None, None
        qty = float(best["size"])
        return qty, round(qty * float(best["price"]), 6)

    def _qty_notional_le(self, side: str, level: float) -> Tuple[Optional[float], Optional[float]]:
        asks = self.mm.prices[side]["asks"]
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

    def _total_ask_qty_notional(self, side: str) -> Tuple[Optional[float], Optional[float]]:
        asks = self.mm.prices[side]["asks"]
        if not asks:
            return None, None
        qty = 0.0
        notional = 0.0
        for row in asks:
            size = _safe_float(row.get("size"))
            price = _safe_float(row.get("price"))
            if size is None or price is None:
                continue
            qty += size
            notional += size * price
        return round(qty, 6), round(notional, 6)

    def _distance_fields(self) -> Tuple[Optional[float], Optional[float], Optional[float]]:
        btc = self.mm.binance.price
        target, source = self.mm.resolve_target_in_use()
        self.mm.current["target_price"] = target
        self.mm.current["target_source"] = source
        if btc is None or target is None:
            return btc, target, None
        return btc, target, round(btc - target, 6)

    # ------------------------------------------------------------------
    # Phase routing for the primary (BOT_30 / BOT_40) strategy
    # ------------------------------------------------------------------
    def _bot40_level_and_mode(self, sec: int) -> Tuple[Optional[float], Optional[str]]:
        """Returns (price_level, mode) for the BOT_30/BOT_40 phase active at `sec`.
        mode is one of: "MAKER_PHASE1" (BOT_30 sec 0-30) or
                        "FALLBACK_035" (BOT_40 sec 30-40).
        """
        if sec < 0 or sec > self.p["bot40_max_sec"]:
            return None, None
        if sec <= self.p["bot40_limit_end_sec"]:
            return max(self.p["bot30_maker_levels"]), "MAKER_PHASE1"
        return self.p["bot40_fallback_price"], "FALLBACK_035"

    def _simulate_fill_up_to_cap(self, side: str, cap_usd: float, level: float) -> Tuple[float, float, Optional[float]]:
        asks = self.mm.prices[side]["asks"] or []
        spent = 0.0
        qty = 0.0
        for row in asks:
            price = _safe_float(row.get("price"))
            size = _safe_float(row.get("size"))
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

    # ------------------------------------------------------------------
    # Phase 1 of primary — BOT_30: maker 0-30s
    # ------------------------------------------------------------------
    def bot_30_choose_side(self, sec: int) -> Optional[str]:
        """BOT_30 phase: 0 ≤ sec ≤ bot40_limit_end_sec. Returns side to BUY or None."""
        if not (0 <= sec <= self.p["bot40_limit_end_sec"]):
            return None
        return self._primary_choose_side(sec)

    # ------------------------------------------------------------------
    # Phase 2 of primary — BOT_40: taker fallback 30-40s
    # ------------------------------------------------------------------
    def bot_40_choose_side(self, sec: int) -> Optional[str]:
        """BOT_40 phase: bot40_limit_end_sec < sec ≤ bot40_max_sec."""
        if not (self.p["bot40_limit_end_sec"] < sec <= self.p["bot40_max_sec"]):
            return None
        return self._primary_choose_side(sec)

    def _primary_choose_side(self, sec: int) -> Optional[str]:
        """Shared logic for BOT_30 + BOT_40 — same BotState (self.bot40),
        same budget cap. Mode comes from _bot40_level_and_mode."""
        level, _mode = self._bot40_level_and_mode(sec)
        if level is None:
            return None

        btc, target, distance = self._distance_fields()
        flow_side = _flow_side_from_distance(distance)

        # Continue an existing position if there's room left under the cap.
        if self.bot40.last_buy and self.bot40.current_market_spent < self.p["max_buy_usd"]:
            existing_side = self.bot40.last_buy["side"]
            ask = self.mm.prices[existing_side]["best_ask"]
            if ask is None or ask > level or not self.mm.side_has_fresh_ask_for_current_market(existing_side):
                return None
            if (distance is not None
                    and abs(distance) >= self.p["bot40_flow_dist_threshold"]
                    and flow_side is not None
                    and existing_side != flow_side):
                return None
            return existing_side

        # Pick the cheaper side that has a fresh ask <= level.
        eligible = []
        up_ask = self.mm.prices["UP"]["best_ask"]
        down_ask = self.mm.prices["DOWN"]["best_ask"]
        if up_ask is not None and up_ask <= level and self.mm.side_has_fresh_ask_for_current_market("UP"):
            eligible.append(("UP", up_ask))
        if down_ask is not None and down_ask <= level and self.mm.side_has_fresh_ask_for_current_market("DOWN"):
            eligible.append(("DOWN", down_ask))

        if not eligible:
            return None

        # Beyond flow-distance threshold, restrict to the flow side only.
        if (distance is not None
                and abs(distance) >= self.p["bot40_flow_dist_threshold"]
                and flow_side is not None):
            eligible = [x for x in eligible if x[0] == flow_side]
            if not eligible:
                return None

        eligible.sort(key=lambda x: x[1])
        return eligible[0][0]

    # ------------------------------------------------------------------
    # BOT_120 — parallel strategy
    # ------------------------------------------------------------------
    def bot_120_choose_side(self, sec: int) -> Optional[str]:
        if not (self.p["bot120_min_sec"] <= sec <= self.p["bot120_max_sec"]):
            return None
        btc, target, distance = self._distance_fields()
        if btc is None or target is None or distance is None:
            return None
        if abs(distance) < self.p["bot120_min_distance"]:
            return None
        direction_side = "UP" if distance > 0 else "DOWN"
        if self.bot120.last_buy and self.bot120.last_buy.get("side") != direction_side:
            return None
        if not self.mm.side_has_fresh_ask_for_current_market(direction_side):
            return None
        return direction_side

    # ------------------------------------------------------------------
    # Research recording
    # ------------------------------------------------------------------
    def record_bot40_research(self, sec: int) -> None:
        if sec not in BOT40_RESEARCH_SECONDS:
            return
        btc, target, distance = self._distance_fields()
        flow_side = _flow_side_from_distance(distance)
        up_best_ask = self.mm.prices["UP"]["best_ask"]
        down_best_ask = self.mm.prices["DOWN"]["best_ask"]
        for price_level in BOT40_RESEARCH_PRICE_LEVELS:
            up_qty, up_notional = self._qty_notional_le("UP", price_level)
            down_qty, down_notional = self._qty_notional_le("DOWN", price_level)
            eligible = []
            if up_best_ask is not None and up_best_ask <= price_level:
                eligible.append(("UP", up_best_ask))
            if down_best_ask is not None and down_best_ask <= price_level:
                eligible.append(("DOWN", down_best_ask))
            note = "free"
            if distance is not None and abs(distance) >= self.p["bot40_flow_dist_threshold"]:
                note = "flow_only"
                if flow_side is not None:
                    eligible = [x for x in eligible if x[0] == flow_side]
            eligible.sort(key=lambda x: x[1])
            eligible_side = eligible[0][0] if eligible else None
            self.logger.log_bot40_research(
                slug=self.mm.current["slug"],
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

    def record_bot120_research(self, sec: int) -> None:
        if sec not in BOT120_RESEARCH_SECONDS:
            return
        btc, target, distance = self._distance_fields()
        flow_side = _flow_side_from_distance(distance)
        flow_best_ask = self.mm.prices[flow_side]["best_ask"] if flow_side else None
        flow_best_bid = self.mm.prices[flow_side]["best_bid"] if flow_side else None
        flow_total_qty, flow_total_notional = (
            self._total_ask_qty_notional(flow_side) if flow_side else (None, None)
        )
        for distance_level in BOT120_RESEARCH_DISTANCE_LEVELS:
            would_trigger = int(
                btc is not None and target is not None and distance is not None
                and flow_side is not None and abs(distance) >= distance_level
                and flow_best_ask is not None
            )
            self.logger.log_bot120_research(
                slug=self.mm.current["slug"],
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

    def record_signals(self, sec: int) -> None:
        btc, target, distance = self._distance_fields()
        for bot in (self.bot40, self.bot120):
            key = sec
            if key in bot.last_logged_sec_side:
                continue
            bot.last_logged_sec_side.add(key)
            self.logger.log_signal(
                bot=bot,
                slug=self.mm.current["slug"],
                market_suffix=self.mm.current["current_suffix"],
                sec=sec,
                up_bid=self.mm.prices["UP"]["best_bid"],
                up_ask=self.mm.prices["UP"]["best_ask"],
                down_bid=self.mm.prices["DOWN"]["best_bid"],
                down_ask=self.mm.prices["DOWN"]["best_ask"],
                btc_price=btc,
                target_price=target,
                distance=distance,
            )

    # ------------------------------------------------------------------
    # Buy execution (dry-run + live, identical to original)
    # ------------------------------------------------------------------
    def try_execute_buy(self, bot: BotState, sec: int, side: Optional[str]) -> None:
        btc, target, distance = self._distance_fields()

        if bot is self.bot120:
            if distance is None or target is None or btc is None:
                bot.last_decision = "WAIT"
                bot.last_note = "missing poly target"
                return
            if abs(distance) < self.p["bot120_min_distance"]:
                bot.last_decision = "WAIT"
                bot.last_note = f"dist<{self.p['bot120_min_distance']:.0f}"
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
                    bot.last_note = f"no side at or below {max(self.p['bot30_maker_levels']):.2f}"
                else:
                    bot.last_note = f"no side at or below {self.p['bot40_fallback_price']:.2f}"
            else:
                bot.last_note = "no side with direction / ask missing"
            return

        if bot.current_market_spent >= self.p["max_buy_usd"] - 1e-9:
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

        best_ask = self.mm.prices[side]["best_ask"]
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
            mode = "LIMIT_050"
            price_limit = self.p["bot120_limit_price"]

        if bot.last_buy and bot.last_buy.get("side") != side:
            bot.last_decision = "WAIT"
            bot.last_note = "side locked"
            return

        avail_qty, avail_notional = self._qty_notional_le(side, price_limit)
        remaining_cap = max(0.0, self.p["max_buy_usd"] - bot.current_market_spent)
        if bot is self.bot120:
            spend_cap = remaining_cap
        else:
            spend_cap = min(remaining_cap, avail_notional or 0.0)
            if spend_cap <= 0:
                bot.last_decision = "WAIT"
                bot.last_note = f"{side} no liquidity <= {price_limit:.2f}"
                return

        if self.dry_run:
            spent, filled_qty, avg_fill = self._simulate_fill_up_to_cap(side, spend_cap, price_limit)
            if spent <= 0 or filled_qty <= 0 or avg_fill is None:
                bot.last_decision = "WAIT"
                bot.last_note = "fill simulation failed"
                return
            live_order_id = None
        else:
            if self._check_and_update_daily_kill():
                bot.last_decision = "BLOCKED"
                bot.last_note = f"daily loss cap hit (${self.p['max_daily_loss_usd']:.0f})"
                return
            if self.wallet is None or not self.wallet.connected:
                bot.last_decision = "BLOCKED"
                bot.last_note = "wallet not connected"
                return
            bal = self.wallet.get_usdc_balance()
            if bal is not None and bal > self.p["max_wallet_usd"]:
                bot.last_decision = "BLOCKED"
                bot.last_note = f"wallet ${bal:.2f} > cap ${self.p['max_wallet_usd']:.0f}"
                return
            order_price = round(min(price_limit, 0.99), 4)
            order_shares = round(spend_cap / order_price, 4)
            order_notional = order_shares * order_price
            if order_notional < 1.0:
                bot.last_decision = "WAIT"
                bot.last_note = f"order ${order_notional:.2f} below Polymarket $1 minimum"
                return
            token_id = self.mm.current.get("yes_token") if side == "UP" else self.mm.current.get("no_token")
            if not token_id:
                bot.last_decision = "WAIT"
                bot.last_note = f"{side} token_id missing — cannot place live order"
                return
            order_id, status = self.wallet.place_buy(str(token_id), order_price, order_shares)
            if not order_id or not status.startswith(("placed", "dry_run")):
                bot.last_decision = "REJECTED"
                bot.last_note = f"live order rejected: {status}"
                self.logger.log_event(self.mm.current.get("slug") or "-",
                                       "LIVE_ORDER_REJECTED",
                                       f"side={side} price={order_price} size={order_shares} status={status}")
                return
            spent = round(order_shares * order_price, 6)
            filled_qty = order_shares
            avg_fill = order_price
            live_order_id = order_id
            self.logger.log_event(self.mm.current.get("slug") or "-",
                                   "LIVE_ORDER_PLACED",
                                   f"side={side} order_id={order_id} price={order_price} size={order_shares}")
            print(f"[LIVE] {self.coin} {bot.name} BUY {side} @{order_price:.4f} "
                  f"x{order_shares:.2f} (${spent:.2f}) order_id={order_id}")

        entry_flow_side = _flow_side_from_distance(distance)
        entry_with_flow = int(side == entry_flow_side) if entry_flow_side is not None else None
        pos = VirtualPosition(
            sec=sec,
            side=side,
            spent=spent,
            qty=filled_qty,
            avg_fill=avg_fill,
            entry_best_ask=best_ask,
            entry_best_bid=self.mm.prices[side]["best_bid"],
            entry_btc_price=btc,
            entry_target_price=target,
            entry_distance=distance,
            entry_flow_side=entry_flow_side,
            entry_with_flow=entry_with_flow,
            entry_ts=_now_ts(),
        )
        bot.positions[side].append(pos)
        bot.current_market_buy_count += 1
        bot.current_market_spent += spent
        bot.last_buy = {
            "sec": sec, "side": side, "spent": spent,
            "qty": filled_qty, "avg_fill": avg_fill,
            "avail_notional": avail_notional,
        }
        bot.virtual_buy_count += 1
        bot.virtual_spent_total += spent
        bot.buy_done_for_market = bot.current_market_spent >= self.p["max_buy_usd"] - 1e-9
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
            slug=self.mm.current["slug"],
            sec=sec,
            side=side,
            mode=mode,
            price_limit=price_limit,
            spent=spent,
            qty=filled_qty,
            avg_fill=avg_fill,
            best_ask=best_ask,
            best_bid_now=self.mm.prices[side]["best_bid"],
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

    # ------------------------------------------------------------------
    # PnL / settlement
    # ------------------------------------------------------------------
    def position_mark(self, bot: BotState, side: str) -> Tuple[float, float, Optional[float], Optional[float]]:
        positions = bot.positions[side]
        if not positions:
            return 0.0, 0.0, None, None
        total_spent = sum(p.spent for p in positions)
        total_qty = sum(p.qty for p in positions)
        best_bid = self.mm.prices[side]["best_bid"]
        if best_bid is None:
            return total_spent, total_qty, None, None
        mark_value = total_qty * best_bid
        pnl = mark_value - total_spent
        return total_spent, total_qty, round(mark_value, 6), round(pnl, 6)

    def open_pnl_total(self, bot: BotState) -> Tuple[float, float, float]:
        total_spent = 0.0
        total_mark = 0.0
        for side in ("UP", "DOWN"):
            spent, _qty, mark_value, _pnl = self.position_mark(bot, side)
            total_spent += spent
            total_mark += mark_value or 0.0
        return round(total_spent, 6), round(total_mark, 6), round(total_mark - total_spent, 6)

    def _update_daily_pnl(self, pnl_delta: float) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        if self.daily_realized_date != today:
            self.daily_realized_date = today
            self.daily_realized_pnl = 0.0
            self.killed_for_daily_loss = False
        self.daily_realized_pnl += pnl_delta

    def _check_and_update_daily_kill(self) -> bool:
        if self.dry_run:
            return False
        today = datetime.now().strftime("%Y-%m-%d")
        if self.daily_realized_date != today:
            self.daily_realized_date = today
            self.daily_realized_pnl = 0.0
            self.killed_for_daily_loss = False
        if self.killed_for_daily_loss:
            return True
        if self.daily_realized_pnl <= -self.p["max_daily_loss_usd"]:
            self.killed_for_daily_loss = True
            self.logger.log_event(self.mm.current.get("slug") or "-",
                                   "KILL_DAILY_LOSS",
                                   f"daily_pnl=${self.daily_realized_pnl:.2f} "
                                   f"cap=${self.p['max_daily_loss_usd']:.2f}")
            return True
        return False

    def settle_bot_positions(self, bot: BotState) -> None:
        target = self.mm.current.get("target_price")
        if target is None:
            target, source, event_meta_target, line_target, strike_target, question_target, rendered_page_target = self.mm.ensure_target_price(
                self.mm.current.get("slug") or "-",
                self.mm.current.get("url") or "",
                self.mm.current.get("question") or "",
                target,
            )
            if target is not None:
                self.mm.current["target_price"] = target
                self.mm.current["target_source"] = source
                self.mm.current["target_event_meta"] = event_meta_target
                self.mm.current["target_line"] = line_target
                self.mm.current["target_strike"] = strike_target
                self.mm.current["target_question"] = question_target
                self.mm.current["target_rendered_page"] = rendered_page_target
            else:
                self.mm.capture_binance_prev_5m_close_target()
                target = self.mm.current.get("target_binance_prev_5m_close")
        btc = self.mm.binance.price
        slug = self.mm.current.get("slug") or "-"
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
            bot=bot, slug=slug, winner_side=winner_side,
            btc_price=btc, target_price=target,
            spent_total=total_spent, payout_total=payout, pnl_total=pnl,
            up_qty=total_qty_up, down_qty=total_qty_down, result=result,
        )
        for side_name in ("UP", "DOWN"):
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
                    bot_name=bot.name, slug=slug, pos=pos,
                    winner_side=winner_side, result=pos_result,
                    payout=pos_payout, pnl=pos_pnl,
                    settle_btc_price=btc, settle_target_price=target,
                )
        bot.last_note = (
            f"SETTLED {bot.name} winner={winner_side} "
            f"spent=${total_spent:.2f} payout=${payout:.2f} pnl=${pnl:.2f}"
        )

    def settle_all(self) -> None:
        """Called at market rollover — settle BOT_30/40 then BOT_120."""
        self.settle_bot_positions(self.bot40)
        self.settle_bot_positions(self.bot120)

    def reset_for_new_market(self) -> None:
        self.bot40.reset_market()
        self.bot120.reset_market()

    # ------------------------------------------------------------------
    # Per-tick orchestration
    # ------------------------------------------------------------------
    def tick(self, sec: int) -> None:
        """Called once per second by the master loop. Runs all 3 phase
        decisions, records research, executes any triggered buys.
        """
        self.record_bot40_research(sec)
        self.record_bot120_research(sec)
        self.record_signals(sec)

        # Primary (BOT_30 + BOT_40 share state — single execute call decides phase by sec)
        if 0 <= sec <= self.p["bot40_max_sec"]:
            primary_side = self._primary_choose_side(sec)
            self.try_execute_buy(self.bot40, sec, primary_side)
        else:
            self.try_execute_buy(self.bot40, sec, None)

        # Parallel BOT_120
        bot120_side = self.bot_120_choose_side(sec)
        if bot120_side is not None:
            self.try_execute_buy(self.bot120, sec, bot120_side)
        else:
            self.try_execute_buy(self.bot120, sec, None)
