#!/usr/bin/env python3
"""3-platform consensus analysis.

For each SKIP_SIZE_OVER_CAP event (Limitless YES = 0.01 phantom), look up
Polymarket's UP ask at the same second from combined_per_second.csv and
classify:

  CONSENSUS  : poly UP ask <= 0.05  ->  both lim AND poly say YES will lose
  SPLIT      : poly UP ask >  0.05  ->  only lim says YES will lose
  POLY_MISSING: no poly data at that second

Then cross-reference with the window outcome to test:
  - Does CONSENSUS predict the winner more reliably than SPLIT?

Inputs (on USA server):
  /root/v5_orders.csv                                 (SKIP events copied here)
  /root/data_btc_15m_research/combined_per_second.csv (poly per-second data)
  /root/data_btc_15m_research/market_outcomes.csv     (winners)
"""

import csv
import sys
from collections import defaultdict
from datetime import datetime


SKIP_FILE = "/root/v5_orders.csv"
COMBINED_FILE = "/root/data_btc_15m_research/combined_per_second.csv"
OUTCOMES_FILE = "/root/data_btc_15m_research/market_outcomes.csv"
WINDOW_SEC = 900

CONSENSUS_THRESHOLD = 0.05  # poly UP ask below this counts as "agreeing"


def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def load_outcomes():
    out = {}
    with open(OUTCOMES_FILE) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                out[int(r["market_epoch"])] = r["winner_side"]
            except (KeyError, ValueError):
                pass
    return out


def load_poly_per_second():
    """Returns dict[epoch_sec] = {up_ask, down_ask, up_bid, down_bid, slug}.
    epoch_sec is integer Unix seconds."""
    data = {}
    with open(COMBINED_FILE) as f:
        rd = csv.DictReader(f)
        for r in rd:
            try:
                es = int(r["epoch_sec"])
                up_ask = float(r["up_ask"] or 0)
                down_ask = float(r["down_ask"] or 0)
                up_bid = float(r["up_bid"] or 0)
                slug = r["market_slug"]
                data[es] = {
                    "up_ask": up_ask, "down_ask": down_ask,
                    "up_bid": up_bid, "slug": slug,
                }
            except (KeyError, ValueError):
                continue
    return data


def load_skip_phantom_events():
    events = []
    with open(SKIP_FILE) as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            if len(r) < 5 or r[1] != "SKIP_SIZE_OVER_CAP":
                continue
            try:
                ts = parse_ts(r[0])
                direction = r[2]
                min_p = float(r[3])
                max_p = float(r[4])
                if min_p != 0.01:
                    continue
                if direction != "A_LIM":
                    continue
                events.append({
                    "ts": ts, "epoch_sec": int(ts.timestamp()),
                    "direction": direction,
                    "min_p": min_p, "max_p": max_p,
                })
            except (ValueError, IndexError):
                continue
    return events


def main():
    print("loading outcomes...", file=sys.stderr)
    outcomes = load_outcomes()
    print(f"  {len(outcomes)} outcomes", file=sys.stderr)

    print("loading poly per-second data...", file=sys.stderr)
    poly = load_poly_per_second()
    print(f"  {len(poly)} per-second poly rows", file=sys.stderr)

    print("loading SKIP phantom events...", file=sys.stderr)
    events = load_skip_phantom_events()
    print(f"  {len(events)} phantom A_LIM events", file=sys.stderr)

    print()
    rows = []
    for e in events:
        window_epoch = (e["epoch_sec"] // WINDOW_SEC) * WINDOW_SEC
        sec_in_window = e["epoch_sec"] - window_epoch
        winner = outcomes.get(window_epoch, "UNKNOWN")
        p = poly.get(e["epoch_sec"])
        if p is None:
            for offset in (-1, 1, -2, 2):
                p = poly.get(e["epoch_sec"] + offset)
                if p is not None:
                    break
        if p is None:
            label = "POLY_MISSING"
            poly_up_ask = None
            poly_down_ask = None
        else:
            poly_up_ask = p["up_ask"]
            poly_down_ask = p["down_ask"]
            if poly_up_ask > 0 and poly_up_ask <= CONSENSUS_THRESHOLD:
                label = "CONSENSUS_DOWN"  # both lim+poly say YES will lose => bet DOWN
            elif poly_down_ask > 0 and poly_down_ask <= CONSENSUS_THRESHOLD:
                label = "CONSENSUS_UP"    # lim says YES loses but poly says DOWN loses => contradiction
            else:
                label = "SPLIT"

        # Expected winner if cheap=lim YES means UP is losing -> DOWN wins
        expected = "DOWN"
        hit = (winner == expected) if winner in ("UP", "DOWN") else None

        rows.append({
            "ts": e["ts"].isoformat(),
            "window_epoch": window_epoch,
            "sec_in_window": sec_in_window,
            "max_p_predict_NO": e["max_p"],
            "poly_up_ask": poly_up_ask,
            "poly_down_ask": poly_down_ask,
            "label": label,
            "winner": winner,
            "expected_DOWN_hit": hit,
        })

    # Aggregate by label
    print("=== Event-level breakdown ===")
    by_label = defaultdict(list)
    for r in rows:
        by_label[r["label"]].append(r)
    for label in ("CONSENSUS_DOWN", "SPLIT", "CONSENSUS_UP", "POLY_MISSING"):
        rs = by_label[label]
        if not rs:
            print(f"  {label:<16}  n=0")
            continue
        with_winner = [r for r in rs if r["winner"] in ("UP", "DOWN")]
        hits = sum(1 for r in with_winner if r["expected_DOWN_hit"])
        rate = (hits / len(with_winner) * 100) if with_winner else 0
        print(f"  {label:<16}  n={len(rs):>6}  resolved={len(with_winner):>6}  "
              f"DOWN-wins={hits:>5} ({rate:5.1f}%)")

    # Per-WINDOW aggregation
    print("\n=== Per-window breakdown (one decision per market) ===")
    per_win = defaultdict(lambda: {"any_consensus": False, "any_split": False, "winner": None})
    for r in rows:
        w = per_win[r["window_epoch"]]
        w["winner"] = r["winner"]
        if r["label"] == "CONSENSUS_DOWN":
            w["any_consensus"] = True
        elif r["label"] == "SPLIT":
            w["any_split"] = True

    cons_resolved = [w for w in per_win.values() if w["any_consensus"] and w["winner"] in ("UP", "DOWN")]
    cons_hits = sum(1 for w in cons_resolved if w["winner"] == "DOWN")
    print(f"  windows w/ CONSENSUS_DOWN at some point: {len(cons_resolved)}, "
          f"DOWN-won {cons_hits} ({cons_hits/max(len(cons_resolved),1)*100:.1f}%)")

    split_only = [w for w in per_win.values()
                  if w["any_split"] and not w["any_consensus"] and w["winner"] in ("UP", "DOWN")]
    split_hits = sum(1 for w in split_only if w["winner"] == "DOWN")
    print(f"  windows w/ ONLY SPLIT (no consensus):    {len(split_only)}, "
          f"DOWN-won {split_hits} ({split_hits/max(len(split_only),1)*100:.1f}%)")

    # Save detail csv
    detail_csv = "/tmp/3way_analysis.csv"
    with open(detail_csv, "w", newline="") as f:
        if rows:
            w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
            w.writeheader()
            for r in rows:
                w.writerow(r)
    print(f"\ndetail rows -> {detail_csv}")


if __name__ == "__main__":
    main()
