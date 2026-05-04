# -*- coding: utf-8 -*-
"""
screen.py — display rendering helpers + multi-coin TUI.

Two modes:
  * single_coin — drop-in replacement for the original print_status, used by
    LIVE_BTC_5M_V1_TEST5.py-style runs.
  * multi_coin — combined view: one summary header + a compact row per coin.

Render strategy is unchanged from the original: stdout is redirected to a
StringIO buffer, ONE write goes to the terminal — eliminates flicker.
"""
import io
import sys
import time
from datetime import datetime
from typing import List, Optional

ANSI_RESET = "\033[0m"
ANSI_RED = "\033[31m"
ANSI_GREEN = "\033[32m"
ANSI_YELLOW = "\033[33m"
ANSI_BOLD = "\033[1m"
ANSI_CYAN = "\033[36m"
ANSI_WHITE = "\033[37m"
ANSI_DIM = "\033[2m"
ANSI_BLINK = "\033[5m"


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


def fmt(x, digits: int = 3) -> str:
    if x is None:
        return "-"
    try:
        return f"{float(x):.{digits}f}"
    except Exception:
        return str(x)


def now_local_str() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def render_buffered(render_fn) -> None:
    """Run `render_fn` while redirecting stdout to a buffer, then write the
    buffer to the real stdout in ONE go. Eliminates flicker on slow terminals.
    """
    saved = sys.stdout
    buf = io.StringIO()
    sys.stdout = buf
    try:
        render_fn()
    finally:
        sys.stdout = saved
    # Full clear + home + hide cursor before each frame.
    # \033[2J = clear entire screen, \033[H = cursor home, \033[?25l = hide cursor.
    # Using full clear (not just clear-to-end) prevents duplicate output when the
    # terminal is resized or scrolled.
    sys.stdout.write("\033[2J\033[H\033[?25l" + buf.getvalue())
    sys.stdout.flush()


def format_bot_panel(bot, open_spent: float, open_mark: float, open_pnl: float,
                     markets_scanned: int) -> List[str]:
    """Returns the 8-line vertical panel for one strategy (BOT40 or BOT120).
    Caller has already computed open_spent/open_mark/open_pnl via
    strategy.open_pnl_total(bot, prices)."""
    market_profit = open_pnl
    active_buy = str(bot.last_decision).startswith("BUY")
    decision_text = colorize_decision(bot.last_decision, active=active_buy)
    blink_profit = abs(open_pnl) > 1e-9
    role_line = bot.last_buy["side"] if bot.last_buy else "NONE"
    return [
        f"DEC:{decision_text}  TRIG:{bot.triggers_seen}  MKTS:{markets_scanned}",
        f"BUYS:{bot.virtual_buy_count}  SETTLED:{bot.settled_markets}",
        f"THIS:buys={bot.current_market_buy_count} spent=${bot.current_market_spent:,.2f}",
        f"OPEN:$ / PNL:{color_money(open_mark, False)} / {color_money(open_pnl, blink_profit)}",
        f"PROFIT:{color_money(market_profit)}",
        f"W:{bot.wins}  L:{bot.losses}  P:{bot.pushes}",
        f"POS:{role_line}  LAST:{bot.last_note}",
    ]


def format_multi_coin_summary_row(coin: str, total_profit: float, trades: int,
                                  daily_pnl: float, last_note: str = "") -> str:
    """One-line summary per coin, used in the multi-coin master TUI."""
    return (
        f"{trim_cell(coin, 6)} | "
        f"PNL:{color_money(total_profit, False):<24} | "
        f"DAILY:{color_money(daily_pnl, False):<22} | "
        f"TRADES:{trades:<5} | "
        f"{trim_cell(last_note, 50)}"
    )


def cursor_home_hide() -> None:
    """Print escape codes that move cursor to top-left and hide it. Used at the
    very beginning of each frame so subsequent writes overwrite the previous frame.
    """
    print("\033[H\033[?25l", end="")
