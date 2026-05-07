#!/usr/bin/env python3
"""Merge V3 (arb_v3_3way) historical trades into the V4 (arb_v4_3way) schema.

V3 ran 06/05/2026 ~08:29-13:45 with realistic liquidity sizing — 56 trades.
We don't want to lose this historical data, so we translate V3 rows into
V4 schema and write a combined `arb_v3_v4_merged_trades.csv`.

V3 only ran SAFE direction (UP@lower_strike + DOWN@higher_strike), and
settled all legs with Polymarket's single oracle (final_binance_price).
V4 runs both safe & dangerous and uses per-platform oracles. Treat the
merged dataset accordingly — V3 rows can only show BOTH_WIN_BONUS or
single-leg patterns, never the unexpected/danger patterns.

Key column mapping (V3 → V4):
  lower_platform     → up_platform        (V3 puts UP on lower strike)
  higher_platform    → down_platform
  lower_strike       → up_strike
  higher_strike      → down_strike
  lower_up_ask       → up_ask
  higher_down_ask    → down_ask
  lower_shares       → up_shares_filled
  higher_shares      → down_shares_filled
  invest_usd         → invest_total
  cost               → cost_open
  lower_payout       → up_payout
  higher_payout      → down_payout
  lower_backup_*     → third_* (best-effort; only one of two backups kept)

Pattern remap:
  BTC<low            → DOWN_WON_ONLY
  BTC>high           → UP_WON_ONLY
  BTC_mid_BOTH_WIN   → BOTH_WIN_BONUS

Output: /root/arb_v3_v4_merged_trades.csv  with column `source_bot`.
"""
import csv

V3_PATH = "/root/arb_v3_3way_trades.csv"
V4_PATH = "/root/arb_v4_3way_trades.csv"
OUT_PATH = "/root/arb_v3_v4_merged_trades.csv"

# V4 schema (same field order as live writer) + source_bot tag at end
V4_COLS = [
    "trade_id", "open_ts", "pair_label", "direction_safety", "is_shadow",
    "poly_strike_open", "kalshi_strike_open", "gemini_strike_open",
    "up_platform", "up_market_id", "up_strike", "up_ask",
    "down_platform", "down_market_id", "down_strike", "down_ask",
    "third_platform", "third_market_id", "third_strike",
    "strike_gap", "cost_open",
    "target_shares", "up_shares_filled", "down_shares_filled",
    "third_up_shares", "third_down_shares",
    "invest_total", "completion_note",
    "close_ts", "btc_final", "winner_pattern",
    "up_payout", "down_payout", "third_up_payout", "third_down_payout",
    "total_payout", "pnl", "pnl_pct",
    "source_bot",
]

PATTERN_MAP = {
    "BTC<low": "DOWN_WON_ONLY",
    "BTC>high": "UP_WON_ONLY",
    "BTC_mid_BOTH_WIN": "BOTH_WIN_BONUS",
}


def translate_v3(r: dict) -> dict:
    out = {c: "" for c in V4_COLS}
    out["trade_id"] = r.get("trade_id", "")
    out["open_ts"] = r.get("open_ts", "")
    out["pair_label"] = r.get("pair_label", "")
    out["direction_safety"] = "safe"
    out["is_shadow"] = "0"
    out["up_platform"] = r.get("lower_platform", "")
    out["up_market_id"] = r.get("lower_market_id", "")
    out["up_strike"] = r.get("lower_strike", "")
    out["up_ask"] = r.get("lower_up_ask", "")
    out["down_platform"] = r.get("higher_platform", "")
    out["down_market_id"] = r.get("higher_market_id", "")
    out["down_strike"] = r.get("higher_strike", "")
    out["down_ask"] = r.get("higher_down_ask", "")
    # Best-effort: prefer the higher_backup (down side) for the third_* slot;
    # if not present, fall back to lower_backup.
    if r.get("higher_backup_platform"):
        out["third_platform"] = r["higher_backup_platform"]
        out["third_strike"] = r.get("higher_backup_strike", "")
    elif r.get("lower_backup_platform"):
        out["third_platform"] = r["lower_backup_platform"]
        out["third_strike"] = r.get("lower_backup_strike", "")
    out["strike_gap"] = r.get("strike_gap", "")
    out["cost_open"] = r.get("cost", "")
    # V3 didn't track target/filled separately — use lower_shares (≈higher_shares for symmetric)
    out["target_shares"] = r.get("lower_shares", "")
    out["up_shares_filled"] = r.get("lower_shares", "")
    out["down_shares_filled"] = r.get("higher_shares", "")
    out["invest_total"] = r.get("invest_usd", "")
    out["completion_note"] = "v3_no_completion"
    out["close_ts"] = r.get("close_ts", "")
    out["btc_final"] = r.get("btc_final", "")
    out["winner_pattern"] = PATTERN_MAP.get(r.get("winner_pattern", ""), r.get("winner_pattern", ""))
    out["up_payout"] = r.get("lower_payout", "")
    out["down_payout"] = r.get("higher_payout", "")
    out["total_payout"] = r.get("total_payout", "")
    out["pnl"] = r.get("pnl", "")
    out["pnl_pct"] = r.get("pnl_pct", "")
    out["source_bot"] = "V3"
    # Populate poly/kalshi/gemini_strike_open from up/down platforms when matching
    for slot, col in (("up", "up_platform"), ("down", "down_platform")):
        plat = out[col]
        strike = out[f"{slot}_strike"]
        if plat == "POLY":
            out["poly_strike_open"] = strike
        elif plat == "KALSHI":
            out["kalshi_strike_open"] = strike
        elif plat == "GEMINI":
            out["gemini_strike_open"] = strike
    return out


def passthrough_v4(r: dict) -> dict:
    out = {c: r.get(c, "") for c in V4_COLS if c != "source_bot"}
    out["source_bot"] = "V4"
    return out


def main():
    rows = []
    # V3 first (chronologically earlier)
    with open(V3_PATH) as fh:
        for r in csv.DictReader(fh):
            rows.append(translate_v3(r))
    n_v3 = len(rows)
    # Then V4
    with open(V4_PATH) as fh:
        for r in csv.DictReader(fh):
            rows.append(passthrough_v4(r))
    n_v4 = len(rows) - n_v3

    # Sort by open_ts so the timeline is coherent
    rows.sort(key=lambda r: r.get("open_ts", ""))

    with open(OUT_PATH, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=V4_COLS)
        w.writeheader()
        w.writerows(rows)

    print(f"Merged: {n_v3} V3 rows + {n_v4} V4 rows = {len(rows)} total")
    print(f"Output: {OUT_PATH}")


if __name__ == "__main__":
    main()
