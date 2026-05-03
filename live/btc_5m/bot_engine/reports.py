# -*- coding: utf-8 -*-
"""
reports.py — CSV logger for the multi-coin bot.

Each coin gets its OWN data directory (e.g. data_live_btc_5m/, data_live_eth_5m/).
The schema is identical across coins so cross-coin analysis can read all dirs.

Each run wipes its own data dir on startup (per project rule #7).
"""
import csv
import os
import shutil
from datetime import datetime
from typing import Dict, List, Optional

from bot_engine.state import BotState, VirtualPosition


class CoinResearchLogger:
    """Per-coin CSV writer. data_dir is unique per coin, so multiple loggers
    can run side by side without collision.
    """

    def __init__(self, data_dir: str, coin: str = "?") -> None:
        self.data_dir = data_dir
        self.coin = coin
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
            "events": ["ts", "coin", "slug", "event", "detail"],
            "signals": [
                "ts", "coin", "bot", "slug", "market_suffix", "sec_from_start", "window_open",
                "up_best_bid", "up_best_ask", "down_best_bid", "down_best_ask",
                "btc_price", "target_price", "distance_to_target", "decision", "note",
            ],
            "virtual_buys": [
                "ts", "coin", "bot", "slug", "sec_from_start", "side", "mode", "price_limit",
                "spent_usd", "filled_qty", "avg_fill_price", "best_ask", "best_bid_now",
                "available_notional_le_threshold", "available_qty_le_threshold",
                "btc_price", "target_price", "distance_to_target",
                "limit_031_filled", "limit_fill_sec", "fallback_used",
                "fallback_fill_sec", "fallback_fill_price", "note",
            ],
            "settlements": [
                "ts", "coin", "bot", "slug", "winner_side", "btc_price", "target_price",
                "spent_total", "payout_total", "pnl_total", "up_qty", "down_qty", "result",
            ],
            "bot40_research": [
                "ts", "coin", "slug", "sec_from_start", "price_level", "btc_price",
                "target_price", "distance_to_target", "flow_side",
                "up_best_ask", "down_best_ask",
                "up_qty_le_level", "up_notional_le_level",
                "down_qty_le_level", "down_notional_le_level", "eligible_side", "note",
            ],
            "bot120_research": [
                "ts", "coin", "slug", "sec_from_start", "distance_level", "btc_price",
                "target_price", "distance_to_target", "flow_side",
                "flow_best_ask", "flow_best_bid",
                "flow_total_ask_qty", "flow_total_ask_notional",
                "would_trigger", "note",
            ],
            "trade_outcomes": [
                "entry_ts", "settle_ts", "coin", "bot", "slug", "sec_from_start", "buy_side",
                "winner_side", "result", "spent_usd", "qty", "avg_fill_price", "payout", "pnl",
                "entry_btc_price", "entry_target_price", "entry_distance_signed",
                "entry_flow_side", "entry_with_flow",
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
        self._append_csv(self.paths["events"], [self._ts(), self.coin, slug, event, detail])

    def log_signal(self, bot: BotState, slug: str, market_suffix: int, sec: int,
                   up_bid: Optional[float], up_ask: Optional[float],
                   down_bid: Optional[float], down_ask: Optional[float],
                   btc_price: Optional[float], target_price: Optional[float],
                   distance: Optional[float]) -> None:
        path = self.paths[f"{bot.name.lower()}_signals"]
        self._append_csv(path, [
            self._ts(), self.coin, bot.name, slug, market_suffix, sec,
            int(bot.start_sec <= sec <= bot.end_sec),
            up_bid, up_ask, down_bid, down_ask,
            btc_price, target_price, distance, bot.last_decision, bot.last_note,
        ])

    def log_virtual_buy(self, bot: BotState, slug: str, sec: int, side: str, mode: str,
                        price_limit: float, spent: float, qty: float, avg_fill: float,
                        best_ask: Optional[float], best_bid_now: Optional[float],
                        avail_notional: Optional[float], avail_qty: Optional[float],
                        btc_price: Optional[float], target_price: Optional[float],
                        distance: Optional[float], limit_031_filled: int,
                        limit_fill_sec: Optional[int], fallback_used: int,
                        fallback_fill_sec: Optional[int],
                        fallback_fill_price: Optional[float], note: str) -> None:
        path = self.paths[f"{bot.name.lower()}_virtual_buys"]
        self._append_csv(path, [
            self._ts(), self.coin, bot.name, slug, sec, side, mode, price_limit,
            round(spent, 6), round(qty, 6), round(avg_fill, 6),
            best_ask, best_bid_now, avail_notional, avail_qty,
            btc_price, target_price, distance,
            limit_031_filled, limit_fill_sec, fallback_used,
            fallback_fill_sec, fallback_fill_price, note,
        ])

    def log_settlement(self, bot: BotState, slug: str, winner_side: str,
                       btc_price: Optional[float], target_price: Optional[float],
                       spent_total: float, payout_total: float, pnl_total: float,
                       up_qty: float, down_qty: float, result: str) -> None:
        path = self.paths[f"{bot.name.lower()}_settlements"]
        self._append_csv(path, [
            self._ts(), self.coin, bot.name, slug, winner_side, btc_price, target_price,
            round(spent_total, 6), round(payout_total, 6), round(pnl_total, 6),
            round(up_qty, 6), round(down_qty, 6), result,
        ])

    def log_bot40_research(self, slug: str, sec: int, price_level: float,
                           btc_price: Optional[float], target_price: Optional[float],
                           distance: Optional[float], flow_side: Optional[str],
                           up_best_ask: Optional[float], down_best_ask: Optional[float],
                           up_qty: Optional[float], up_notional: Optional[float],
                           down_qty: Optional[float], down_notional: Optional[float],
                           eligible_side: Optional[str], note: str) -> None:
        self._append_csv(self.paths["bot40_research"], [
            self._ts(), self.coin, slug, sec, price_level, btc_price, target_price,
            distance, flow_side, up_best_ask, down_best_ask,
            up_qty, up_notional, down_qty, down_notional, eligible_side, note,
        ])

    def log_bot120_research(self, slug: str, sec: int, distance_level: float,
                            btc_price: Optional[float], target_price: Optional[float],
                            distance: Optional[float], flow_side: Optional[str],
                            flow_best_ask: Optional[float], flow_best_bid: Optional[float],
                            flow_total_qty: Optional[float],
                            flow_total_notional: Optional[float],
                            would_trigger: int, note: str) -> None:
        self._append_csv(self.paths["bot120_research"], [
            self._ts(), self.coin, slug, sec, distance_level, btc_price, target_price,
            distance, flow_side, flow_best_ask, flow_best_bid,
            flow_total_qty, flow_total_notional, would_trigger, note,
        ])

    def log_trade_outcome(self, bot_name: str, slug: str, pos: VirtualPosition,
                          winner_side: str, result: str, payout: float, pnl: float,
                          settle_btc_price: Optional[float],
                          settle_target_price: Optional[float]) -> None:
        self._append_csv(self.paths["trade_outcomes"], [
            pos.entry_ts or self._ts(), self._ts(), self.coin, bot_name, slug,
            pos.sec, pos.side, winner_side, result,
            round(pos.spent, 6), round(pos.qty, 6), round(pos.avg_fill, 6),
            round(payout, 6), round(pnl, 6),
            pos.entry_btc_price, pos.entry_target_price, pos.entry_distance,
            pos.entry_flow_side, pos.entry_with_flow,
            pos.entry_best_ask, pos.entry_best_bid,
            settle_btc_price, settle_target_price,
        ])
