# -*- coding: utf-8 -*-
"""
state.py — pure dataclasses tracking strategy state.

Extracted from LIVE_BTC_5M_V1_TEST5.py lines 151-201 with no behavioral change.
"""
from dataclasses import dataclass, field
from typing import Dict, List, Optional


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
