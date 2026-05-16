#!/usr/bin/env python3
"""Analyze SKIP_SIZE_OVER_CAP events: timing in window + outcome correlation.

Tests user hypothesis: 0.01 ask is the LOSING side, so buying the OTHER side
(expensive side) at the end of the window would be a profitable directional bet.

Inputs:
  - /root/arb_v5_3way_live_orders.csv   (Helsinki, SKIP events)
  - /root/data_btc_15m_research/market_outcomes.csv  (Helsinki -> winners)
Outputs CSV: detailed table + summary buckets.
"""

import csv
import sys
from datetime import datetime, timezone
from collections import defaultdict


SKIP_FILE = "/root/arb_v5_3way_live_orders.csv"
OUTCOMES_FILE = "/root/outcomes_fresh.csv"
WINDOW_SEC = 900

# Direction -> (cheap-platform-if-YES-hypothesis, expensive-outcome)
# Each direction has two legs; under "cheap leg is the YES/UP side" hypothesis,
# the cheap side is on a specific platform/outcome and the expensive side
# tells us which outcome we'd be betting on if we bought only that leg.
DIRECTION_MAPPING = {
    "A_POLY":       ("poly",  "DOWN"),  # legs: poly UP + predict NO  -> cheap=poly,    expensive bets DOWN
    "B_POLY":       ("poly",  "UP"),    # legs: poly DOWN + predict YES -> cheap=poly,  expensive bets UP
    "A_LIM":        ("lim",   "DOWN"),  # legs: lim YES + predict NO   -> cheap=lim,   expensive bets DOWN
    "LimUP_PolyDN": ("lim",   "DOWN"),  # legs: lim YES + poly DOWN    -> cheap=lim,   expensive bets DOWN
    "B_LIM":        ("lim",   "UP"),    # legs: lim NO + predict YES   -> cheap=lim,   expensive bets UP
    "PolyUP_LimDN": ("poly",  "DOWN"),  # legs: poly UP + lim NO       -> cheap=poly,  expensive bets DOWN
}


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_outcomes():
    """epoch -> winner_side (UP/DOWN/UNKNOWN)"""
    out = {}
    with open(OUTCOMES_FILE) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                epoch = int(r["market_epoch"])
                winner = r["winner_side"]
                out[epoch] = winner
            except (KeyError, ValueError):
                pass
    return out


def load_skip_events():
    """Returns list of dicts."""
    events = []
    with open(SKIP_FILE) as f:
        rd = csv.reader(f)
        next(rd)  # skip header
        for r in rd:
            if len(r) < 5 or r[1] != "SKIP_SIZE_OVER_CAP":
                continue
            try:
                ts = parse_ts(r[0])
                direction = r[2]
                min_p = float(r[3])
                max_p = float(r[4])
                events.append({
                    "ts": ts, "direction": direction,
                    "min_p": min_p, "max_p": max_p,
                })
            except (ValueError, IndexError):
                continue
    return events


def main():
    outcomes = load_outcomes()
    events = load_skip_events()
    print(f"loaded {len(outcomes)} outcomes, {len(events)} SKIP events")

    if not events:
        print("no SKIP events to analyze")
        return

    # Filter to events with cheap_p = 0.01 (phantom hypothesis)
    phantom_events = [e for e in events if e["min_p"] == 0.01]
    print(f"phantom (min_p=0.01) events: {len(phantom_events)} of {len(events)}")

    # For each event compute window epoch + seconds into window
    rows = []
    for e in phantom_events:
        epoch_sec = int(e["ts"].timestamp())
        window_epoch = (epoch_sec // WINDOW_SEC) * WINDOW_SEC
        sec_in_window = epoch_sec - window_epoch
        winner = outcomes.get(window_epoch, "UNKNOWN")
        cheap_platform, expected_win = DIRECTION_MAPPING.get(e["direction"], ("?", "?"))
        hypothesis_correct = (winner == expected_win) if winner in ("UP", "DOWN") else None
        rows.append({
            "ts": e["ts"].isoformat(),
            "direction": e["direction"],
            "min_p": e["min_p"],
            "max_p": e["max_p"],
            "window_epoch": window_epoch,
            "sec_in_window": sec_in_window,
            "winner": winner,
            "cheap_platform_if_YES_hyp": cheap_platform,
            "expected_win_if_cheap_is_YES": expected_win,
            "hypothesis_correct": hypothesis_correct,
        })

    # === Platform that contributes the 0.01 ask ===
    print("\n=== Cheap-leg platform breakdown (assuming cheap=YES-side) ===")
    plat_counts = defaultdict(int)
    for r in rows:
        plat_counts[r["cheap_platform_if_YES_hyp"]] += 1
    for p, n in sorted(plat_counts.items(), key=lambda x: -x[1]):
        print(f"  {p:<8}  n={n:>6}  ({n/len(rows)*100:5.1f}%)")

    # === Summary by direction ===
    print("\n=== Direction breakdown (phantom events only) ===")
    by_dir = defaultdict(list)
    for r in rows:
        by_dir[r["direction"]].append(r)
    for d, rs in sorted(by_dir.items()):
        n = len(rs)
        with_winner = [r for r in rs if r["winner"] in ("UP", "DOWN")]
        hits = sum(1 for r in with_winner if r["hypothesis_correct"])
        misses = sum(1 for r in with_winner if r["hypothesis_correct"] is False)
        no_outcome = n - len(with_winner)
        rate = (hits / len(with_winner) * 100) if with_winner else 0
        print(f"  {d:<14}  n={n:>5}  resolved={len(with_winner):>5}  "
              f"hits={hits:>4} ({rate:5.1f}%)  misses={misses:>4}  no_outcome={no_outcome}")

    # === Summary by sec-in-window bucket ===
    print("\n=== Timing distribution + hit rate (all dirs combined, phantom only) ===")
    buckets = [(0, 60), (60, 180), (180, 360), (360, 540), (540, 720), (720, 840), (840, 900)]
    for lo, hi in buckets:
        in_bucket = [r for r in rows if lo <= r["sec_in_window"] < hi]
        with_winner = [r for r in in_bucket if r["winner"] in ("UP", "DOWN")]
        hits = sum(1 for r in with_winner if r["hypothesis_correct"])
        rate = (hits / len(with_winner) * 100) if with_winner else 0
        print(f"  sec {lo:>3}-{hi:<3}  n={len(in_bucket):>5}  resolved={len(with_winner):>5}  "
              f"hits={hits:>4} ({rate:5.1f}%)")

    # === Unique windows covered ===
    unique_windows = set(r["window_epoch"] for r in rows)
    resolved_windows = set(r["window_epoch"] for r in rows if r["winner"] in ("UP", "DOWN"))
    print(f"\nunique windows in phantom events: {len(unique_windows)}")
    print(f"of those with resolved outcome:    {len(resolved_windows)}")

    # === Per-window hit summary (one decision per market, not per event) ===
    print("\n=== Per-WINDOW summary (one row per market, not per scan) ===")
    per_win = defaultdict(lambda: {"n_events": 0, "directions": set(), "winner": None, "max_p_samples": []})
    for r in rows:
        w = per_win[r["window_epoch"]]
        w["n_events"] += 1
        w["directions"].add(r["direction"])
        w["winner"] = r["winner"]
        w["max_p_samples"].append(r["max_p"])
    resolved_wins = [w for w in per_win.values() if w["winner"] in ("UP", "DOWN")]
    win_hit = 0
    for w in resolved_wins:
        expected = set(DIRECTION_MAPPING[d][1] for d in w["directions"])
        if w["winner"] in expected:
            win_hit += 1
    print(f"  unique windows with phantom events: {len(per_win)}")
    print(f"  windows with resolved outcome:      {len(resolved_wins)}")
    print(f"  windows where expected-side won:    {win_hit} ({win_hit/len(resolved_wins)*100:.1f}%)")
    print(f"  windows where expected-side lost:   {len(resolved_wins)-win_hit}")

    # === Save detail CSV ===
    detail_csv = "/tmp/phantom_analysis.csv"
    with open(detail_csv, "w", newline="") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow(r)
    print(f"\ndetailed rows saved to {detail_csv}")


if __name__ == "__main__":
    main()
