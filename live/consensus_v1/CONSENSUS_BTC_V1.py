#!/usr/bin/env python3
"""CONSENSUS_BTC_V1 — Poly+Predict consensus bot (BTC 5-min).

STRATEGY
  At sec=90 of each 5-min market window:
    - Read median Poly UP/DOWN ask in [sec 80, 100]
    - Read median Predict YES/NO ask in [sec 80, 100]
    - If both Poly_UP >= THR and Predict_UP >= THR: buy UP on cheaper platform
    - Elif both Poly_DN >= THR and Predict_DN >= THR: buy DOWN on cheaper
    - Else: skip window
    - Hold to expiry. Outcome resolved by each platform's own oracle.

PARALLEL RECORDING
  At every decision moment, append a Limitless snapshot to a separate CSV
  so we can later study whether Limitless adds a signal that improves win
  rate, and whether Limitless prices ever beat the chosen platform.

MODES
  --dry-run (default): no orders. Logs decisions only. Safe to run forever.
  --live: places real orders via py_clob_client_v2 / PredictTrader.
          Requires .env with MY_PRIVATE_KEY, MY_ADDRESS, PREDICT_API_KEY.
          NOT IMPLEMENTED IN THIS REVISION — flip on after dry-run proves out.

FILES WRITTEN (relative to script dir or --out-dir)
  consensus_v1_decisions.csv  one row per 5-min window analyzed
  consensus_v1_trades.csv     one row per simulated/live trade
  consensus_v1_limitless.csv  Limitless snapshot at every decision moment
  consensus_v1.log            stdout (run with python3 -u for live tailing)

DOES NOT modify or touch any recorder data dir.
"""
import argparse
import csv
import os
import statistics
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

# ----- file paths (read-only, from running recorders) -----
POLY_DATA = "/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv"
POLY_MK = "/root/research/multi_coin/data_btc_5m_research/markets.csv"
PRED_DATA = "/root/data_predict_btc_5m/combined_per_second.csv"
PRED_MK = "/root/data_predict_btc_5m/markets.csv"
LIM_DATA = "/root/data_limitless_btc_5m/combined_per_second.csv"
LIM_MK = "/root/data_limitless_btc_5m/markets.csv"
GEM_DATA = "/root/data_gemini_btc_5m/combined_per_second.csv"
GEM_MK = "/root/data_gemini_btc_5m/markets.csv"
KAL_DATA = "/root/data_kalshi_btc_15m/combined_per_second.csv"
KAL_MK = "/root/data_kalshi_btc_15m/markets.csv"

# ----- strategy params (defaults match backtest sweet spot) -----
DEFAULT_REF_SEC = 90
DEFAULT_THR = 0.60
DEFAULT_INVEST_USD = 2.0  # Poly min $1 net of 2% commission -> $2 floor
DEFAULT_WIN_HALF = 10     # use median over [REF-10, REF+10]

# ----- decision window: only fire if real time is in this band -----
FIRE_SEC_MIN = 85   # start eligibility a bit before REF to allow data warmup
FIRE_SEC_MAX = 100  # last chance to fire

# ----- final-snapshot window: capture each platform's outcome -----
FINAL_SEC_MIN = 295
FINAL_SEC_MAX = 315

POLL_SEC = 0.5

# ----- output files -----
OUT_DECISIONS = "consensus_v1_decisions.csv"
OUT_TRADES = "consensus_v1_trades.csv"
OUT_LIMITLESS = "consensus_v1_limitless.csv"
OUT_GEMINI = "consensus_v1_gemini.csv"
OUT_KALSHI = "consensus_v1_kalshi.csv"
OUT_OUTCOMES = "consensus_v1_outcomes.csv"


def log(msg):
    print(f"{datetime.now(tz=timezone.utc).strftime('%H:%M:%S')}  {msg}", flush=True)


def ensure_csv(path, header):
    new = not os.path.exists(path)
    if new:
        with open(path, "a", newline="") as f:
            csv.writer(f).writerow(header)


def tail_rows_since(path, since_epoch):
    """Yield CSV rows whose epoch_sec >= since_epoch. Linear scan from end.
    For per-second files of ~500k rows this is still fast enough since we
    only ever need the last ~30 sec of data."""
    if not os.path.exists(path):
        return []
    try:
        size = os.path.getsize(path)
    except OSError:
        return []
    # heuristic: read last 200KB which covers ~3min of data on each recorder
    read_from = max(0, size - 200_000)
    with open(path, "rb") as f:
        f.seek(read_from)
        if read_from > 0:
            f.readline()  # drop partial line
        chunk = f.read().decode("utf-8", errors="ignore")
    lines = chunk.splitlines()
    if not lines:
        return []
    # first line is partial unless we read from 0 and got the real header
    # We need the header — read it separately:
    with open(path, "r") as f:
        header = f.readline().strip().split(",")
    out = []
    for ln in lines:
        if not ln or "," not in ln:
            continue
        vals = ln.split(",")
        if len(vals) < len(header):
            continue
        row = dict(zip(header, vals))
        try:
            ts = float(row.get("epoch_sec") or row.get("local_ts") or 0)
        except (ValueError, TypeError):
            continue
        if ts >= since_epoch:
            out.append(row)
    return out


def median(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return statistics.median(xs)


def snapshot_poly(window_epoch, ref_sec, win_half):
    """Median (up_ask, down_ask, up_bid, down_bid, target, dist) in [ref-half, ref+half]."""
    sec_lo, sec_hi = ref_sec - win_half, ref_sec + win_half
    ts_lo = window_epoch + sec_lo
    rows = tail_rows_since(POLY_DATA, ts_lo - 5)
    ua, da, ub, db, tg, bn, ds = [], [], [], [], [], [], []
    for r in rows:
        try:
            if int(r.get("market_epoch") or 0) != window_epoch:
                continue
            sec = int(r.get("sec_from_start") or -1)
            if sec < sec_lo or sec > sec_hi:
                continue
        except (ValueError, TypeError):
            continue
        try:
            v = float(r.get("up_ask") or 0)
            if v > 0: ua.append(v)
            v = float(r.get("down_ask") or 0)
            if v > 0: da.append(v)
            v = float(r.get("up_bid") or 0)
            if v > 0: ub.append(v)
            v = float(r.get("down_bid") or 0)
            if v > 0: db.append(v)
            v = float(r.get("target_price") or 0)
            if v > 0: tg.append(v)
            v = float(r.get("binance_price") or 0)
            if v > 0: bn.append(v)
            v = float(r.get("distance_signed") or 0)
            if v != 0: ds.append(v)
        except (ValueError, TypeError):
            continue
    return {
        "up_ask": median(ua), "down_ask": median(da),
        "up_bid": median(ub), "down_bid": median(db),
        "target": median(tg), "binance": median(bn),
        "distance": median(ds),
        "n_samples": len(ua),
    }


def snapshot_predict(window_epoch, ref_sec, win_half):
    sec_lo, sec_hi = ref_sec - win_half, ref_sec + win_half
    ts_lo = window_epoch + sec_lo
    rows = tail_rows_since(PRED_DATA, ts_lo - 5)
    ya, na, sk, bn, ds = [], [], [], [], []
    for r in rows:
        try:
            if int(r.get("market_open_epoch") or 0) != window_epoch:
                continue
            sec = int(r.get("sec_from_open") or -1)
            if sec < sec_lo or sec > sec_hi:
                continue
        except (ValueError, TypeError):
            continue
        try:
            v = float(r.get("yes_ask") or 0)
            if v > 0: ya.append(v)
            v = float(r.get("no_ask_implied") or 0)
            if v > 0: na.append(v)
            v = float(r.get("strike") or 0)
            if v > 0: sk.append(v)
            v = float(r.get("binance_now") or 0)
            if v > 0: bn.append(v)
            v = float(r.get("distance_signed") or 0)
            if v != 0: ds.append(v)
        except (ValueError, TypeError):
            continue
    return {"yes_ask": median(ya), "no_ask": median(na),
            "target": median(sk), "binance": median(bn),
            "distance": median(ds), "n_samples": len(ya)}


def _lim_active_market_id(window_epoch):
    """Read LIM_MK to find the 5-min market whose expirationTimestamp == window_epoch + 300."""
    if not os.path.exists(LIM_MK):
        return None, None
    expected_exp = (window_epoch + 300) * 1000
    candidate = None
    target = None
    with open(LIM_MK) as f:
        for r in csv.DictReader(f):
            try:
                exp_ms = int(r.get("expirationTimestamp") or 0)
            except (ValueError, TypeError):
                continue
            if exp_ms != expected_exp:
                continue
            candidate = r.get("market_id")
            try:
                target = float(r.get("target_price") or 0) or None
            except (ValueError, TypeError):
                pass
    return candidate, target


def snapshot_limitless(window_epoch, ref_sec, win_half):
    sec_lo, sec_hi = ref_sec - win_half, ref_sec + win_half
    ts_lo = window_epoch + sec_lo
    ts_hi = window_epoch + sec_hi + 5
    mid, target = _lim_active_market_id(window_epoch)
    if not mid:
        return {"market_id": None, "yes_ask": None, "no_ask": None,
                "target": None, "binance": None, "distance": None, "n_samples": 0}
    rows = tail_rows_since(LIM_DATA, ts_lo - 5)
    ya, na, bn, ds = [], [], [], []
    for r in rows:
        if r.get("market_id") != mid:
            continue
        try:
            ts = float(r.get("epoch_sec") or 0)
            if ts < ts_lo or ts > ts_hi:
                continue
            v = float(r.get("best_ask") or 0)
            if v > 0: ya.append(v)
            v = float(r.get("no_best_ask") or 0)
            if v > 0: na.append(v)
            v = float(r.get("binance_now") or 0)
            if v > 0: bn.append(v)
            v = float(r.get("distance_signed") or 0)
            if v != 0: ds.append(v)
        except (ValueError, TypeError):
            continue
    return {
        "market_id": mid,
        "yes_ask": median(ya),
        "no_ask": median(na),
        "target": target,
        "binance": median(bn),
        "distance": median(ds),
        "n_samples": len(ya),
    }


def snapshot_gemini(window_epoch, ref_sec, win_half):
    """Snapshot median Gemini yes/no asks in [ref-half, ref+half]."""
    sec_lo, sec_hi = ref_sec - win_half, ref_sec + win_half
    ts_lo = window_epoch + sec_lo
    ts_hi = window_epoch + sec_hi + 5
    rows = tail_rows_since(GEM_DATA, ts_lo - 5)
    mid = target = None
    ya, na, bn, ds = [], [], [], []
    for r in rows:
        try:
            mk_open = int(r.get("market_open_epoch") or 0)
            if mk_open != window_epoch:
                continue
            ts = float(r.get("epoch_sec") or 0)
            if ts < ts_lo or ts > ts_hi:
                continue
            mid = r.get("market_id") or mid
            try:
                t = float(r.get("target_price") or 0)
                if t and target is None:
                    target = t
            except (ValueError, TypeError):
                pass
            v = float(r.get("best_ask") or 0)
            if v > 0: ya.append(v)
            v = float(r.get("no_best_ask") or 0)
            if v > 0: na.append(v)
            v = float(r.get("binance_now") or 0)
            if v > 0: bn.append(v)
            v = float(r.get("distance_signed") or 0)
            if v != 0: ds.append(v)
        except (ValueError, KeyError, TypeError):
            continue
    return {"market_id": mid, "yes_ask": median(ya), "no_ask": median(na),
            "target": target, "binance": median(bn), "distance": median(ds),
            "n_samples": len(ya)}


def _kal_active_ticker(window_epoch):
    """Return Kalshi 15-min ticker whose [open, close] covers this 5-min Poly window."""
    if not os.path.exists(KAL_MK):
        return None, None, None
    candidate = None; target = None; sub_pos = None
    with open(KAL_MK) as f:
        for r in csv.DictReader(f):
            try:
                op = int(r.get("open_epoch") or 0)
                cl = int(r.get("close_epoch") or 0)
            except (ValueError, TypeError):
                continue
            if op <= window_epoch and window_epoch + 300 <= cl:
                candidate = r.get("ticker")
                try:
                    target = float(r.get("target_price") or 0) or None
                except (ValueError, TypeError):
                    pass
                sub_pos = (window_epoch - op) // 300  # 0, 1, or 2
    return candidate, target, sub_pos


def snapshot_kalshi(window_epoch, ref_sec, win_half):
    """Snapshot median Kalshi yes/no asks in [ref-half, ref+half] for the
    15-min market covering this 5-min Poly window."""
    sec_lo, sec_hi = ref_sec - win_half, ref_sec + win_half
    ts_lo = window_epoch + sec_lo
    ts_hi = window_epoch + sec_hi + 5
    ticker, target, sub_pos = _kal_active_ticker(window_epoch)
    if not ticker:
        return {"market_id": None, "yes_ask": None, "no_ask": None,
                "target": None, "binance": None, "distance": None,
                "sub_pos": None, "n_samples": 0}
    rows = tail_rows_since(KAL_DATA, ts_lo - 5)
    ya, na, bn, ds = [], [], [], []
    for r in rows:
        if r.get("market_id") != ticker:
            continue
        try:
            ts = float(r.get("epoch_sec") or 0)
            if ts < ts_lo or ts > ts_hi:
                continue
            v = float(r.get("yes_ask") or 0)
            if v > 0: ya.append(v)
            v = float(r.get("no_ask") or 0)
            if v > 0: na.append(v)
            v = float(r.get("binance_now") or 0)
            if v > 0: bn.append(v)
            v = float(r.get("distance_signed") or 0)
            if v != 0: ds.append(v)
        except (ValueError, KeyError, TypeError):
            continue
    return {"market_id": ticker, "yes_ask": median(ya), "no_ask": median(na),
            "target": target, "binance": median(bn), "distance": median(ds),
            "sub_pos": sub_pos, "n_samples": len(ya)}


def final_gemini(window_epoch):
    """End-of-window snapshot for Gemini — binance vs target."""
    rows = tail_rows_since(GEM_DATA, window_epoch + 270)
    matching = [r for r in rows
                if (r.get("market_open_epoch") or "").strip() == str(window_epoch)]
    if not matching:
        return None
    last = matching[-1]
    def f(k):
        try:
            v = float(last.get(k) or 0)
            return v if v != 0 else None
        except (ValueError, TypeError):
            return None
    target = f("target_price")
    binance = f("binance_now")
    outcome = None
    if binance and target:
        outcome = "UP" if binance > target else "DOWN"
    return {
        "outcome": outcome, "market_id": last.get("market_id"),
        "target": target, "final_binance": binance,
        "yes_ask": f("best_ask"), "no_ask": f("no_best_ask"),
    }


def final_kalshi(window_epoch):
    """End-of-window Kalshi snapshot. Note: Kalshi 15-min closes at end of
    its own window, not necessarily at end of THIS 5-min window. We record
    state as of now and flag if the Kalshi window has actually closed."""
    ticker, target, sub_pos = _kal_active_ticker(window_epoch)
    if not ticker:
        return None
    rows = tail_rows_since(KAL_DATA, window_epoch + 270)
    matching = [r for r in rows if r.get("market_id") == ticker]
    if not matching:
        return None
    last = matching[-1]
    def f(k):
        try:
            v = float(last.get(k) or 0)
            return v if v != 0 else None
        except (ValueError, TypeError):
            return None
    binance = f("binance_now")
    close_epoch = None
    try:
        close_epoch = int(last.get("close_epoch") or 0) or None
    except (ValueError, TypeError):
        pass
    kal_closed = close_epoch and close_epoch <= window_epoch + 300
    outcome = None
    if kal_closed and binance and target:
        outcome = "UP" if binance > target else "DOWN"
    return {
        "outcome": outcome, "market_id": ticker, "target": target,
        "final_binance": binance, "kal_closed_in_window": bool(kal_closed),
        "sub_pos": sub_pos, "yes_ask": f("yes_ask"), "no_ask": f("no_ask"),
    }


def final_poly(window_epoch):
    """End-of-window snapshot for Polymarket — chainlink truth + last asks."""
    rows = tail_rows_since(POLY_DATA, window_epoch + 270)
    matching = [r for r in rows
                if (r.get("market_epoch") or "").strip() == str(window_epoch)]
    if not matching:
        return None
    last = matching[-1]
    def f(k):
        try:
            v = float(last.get(k) or 0)
            return v if v != 0 else None
        except (ValueError, TypeError):
            return None
    target = f("target_price")
    binance = f("binance_price")
    chainlink = f("chainlink_price")
    outcome = None
    if chainlink and target:
        outcome = "UP" if chainlink > target else "DOWN"
    return {
        "outcome": outcome, "target": target,
        "final_binance": binance, "final_chainlink": chainlink,
        "up_ask": f("up_ask"), "down_ask": f("down_ask"),
    }


def final_predict(window_epoch):
    """End-of-window snapshot for Predict.fun — binance vs strike."""
    rows = tail_rows_since(PRED_DATA, window_epoch + 270)
    matching = [r for r in rows
                if (r.get("market_open_epoch") or "").strip() == str(window_epoch)]
    if not matching:
        return None
    last = matching[-1]
    def f(k):
        try:
            v = float(last.get(k) or 0)
            return v if v != 0 else None
        except (ValueError, TypeError):
            return None
    strike = f("strike")
    binance = f("binance_now")
    outcome = None
    if binance and strike:
        outcome = "UP" if binance > strike else "DOWN"
    return {
        "outcome": outcome, "strike": strike, "final_binance": binance,
        "yes_ask": f("yes_ask"), "no_ask": f("no_ask_implied"),
    }


def final_limitless(window_epoch):
    """End-of-window snapshot for Limitless — binance vs target."""
    mid, target = _lim_active_market_id(window_epoch)
    if not mid:
        return None
    rows = tail_rows_since(LIM_DATA, window_epoch + 270)
    matching = [r for r in rows if r.get("market_id") == mid]
    if not matching:
        return None
    last = matching[-1]
    def f(k):
        try:
            v = float(last.get(k) or 0)
            return v if v != 0 else None
        except (ValueError, TypeError):
            return None
    binance = f("binance_now")
    outcome = None
    if binance and target:
        outcome = "UP" if binance > target else "DOWN"
    return {
        "outcome": outcome, "market_id": mid, "target": target,
        "final_binance": binance,
        "yes_ask": f("best_ask"), "no_ask": f("no_best_ask"),
    }


def decide(poly, pred, lim, gem, kal, thr, min_agreements=2):
    """Return (side, platform, price, n_votes) or None.

    Counts platforms that price the same direction as likely (>= thr).
    Only fires if >= min_agreements platforms agree on one side AND
    that side has more votes than the opposite side. Buys on the
    cheapest of the TRADEABLE platforms (poly or predict)."""
    sources = [
        ("poly", (poly or {}).get("up_ask"), (poly or {}).get("down_ask"), True),
        ("predict", (pred or {}).get("yes_ask"), (pred or {}).get("no_ask"), True),
        ("limitless", (lim or {}).get("yes_ask"), (lim or {}).get("no_ask"), False),
        ("gemini", (gem or {}).get("yes_ask"), (gem or {}).get("no_ask"), False),
        ("kalshi", (kal or {}).get("yes_ask"), (kal or {}).get("no_ask"), False),
    ]
    up_votes = []
    dn_votes = []
    for name, y, n, tradeable in sources:
        if y is not None and y >= thr:
            up_votes.append((name, y, tradeable))
        if n is not None and n >= thr:
            dn_votes.append((name, n, tradeable))

    side = None
    votes = []
    if len(up_votes) >= min_agreements and len(up_votes) > len(dn_votes):
        side, votes = "UP", up_votes
    elif len(dn_votes) >= min_agreements and len(dn_votes) > len(up_votes):
        side, votes = "DOWN", dn_votes
    if side is None:
        return None
    tradeable_votes = [v for v in votes if v[2]]
    if not tradeable_votes:
        return None  # signal only from non-tradeable platforms
    cheapest = min(tradeable_votes, key=lambda v: v[1])
    return (side, cheapest[0], cheapest[1], len(votes))


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", default=True,
                   help="No real orders (default)")
    p.add_argument("--live", action="store_true",
                   help="ENABLE REAL ORDERS. Will refuse — live not wired yet")
    p.add_argument("--ref-sec", type=int, default=DEFAULT_REF_SEC)
    p.add_argument("--thr", type=float, default=DEFAULT_THR)
    p.add_argument("--invest-usd", type=float, default=DEFAULT_INVEST_USD)
    p.add_argument("--win-half", type=int, default=DEFAULT_WIN_HALF)
    p.add_argument("--out-dir", default=".")
    p.add_argument("--min-agreements", type=int, default=2,
                   help="Require >= N of the 5 platforms to agree on direction (default 2)")
    args = p.parse_args()

    if args.live:
        log("ERROR: --live not wired in this revision. Run dry-run first, then we add live.")
        sys.exit(2)

    os.makedirs(args.out_dir, exist_ok=True)
    decisions_path = os.path.join(args.out_dir, OUT_DECISIONS)
    trades_path = os.path.join(args.out_dir, OUT_TRADES)
    limitless_path = os.path.join(args.out_dir, OUT_LIMITLESS)

    ensure_csv(decisions_path, [
        "ts_utc", "window_epoch",
        "poly_up_ask", "poly_down_ask", "poly_target", "poly_binance", "poly_dist",
        "predict_yes_ask", "predict_no_ask", "predict_target", "predict_binance", "predict_dist",
        "lim_yes_ask", "lim_no_ask", "lim_target", "lim_binance", "lim_dist",
        "gem_yes_ask", "gem_no_ask", "gem_target", "gem_binance", "gem_dist",
        "kal_yes_ask", "kal_no_ask", "kal_target", "kal_binance", "kal_dist", "kal_sub_pos",
        "consensus", "side", "platform", "price", "reason"
    ])
    ensure_csv(trades_path, [
        "ts_utc", "window_epoch", "side", "platform", "price",
        "shares", "invest_usd", "mode"
    ])
    ensure_csv(limitless_path, [
        "ts_utc", "window_epoch", "lim_market_id", "lim_yes_ask",
        "lim_no_ask", "lim_target", "lim_n_samples"
    ])
    gemini_path = os.path.join(args.out_dir, OUT_GEMINI)
    ensure_csv(gemini_path, [
        "ts_utc", "window_epoch", "gem_market_id", "gem_yes_ask",
        "gem_no_ask", "gem_target", "gem_n_samples"
    ])
    kalshi_path = os.path.join(args.out_dir, OUT_KALSHI)
    ensure_csv(kalshi_path, [
        "ts_utc", "window_epoch", "kal_ticker", "kal_yes_ask",
        "kal_no_ask", "kal_target", "kal_sub_pos", "kal_n_samples"
    ])
    outcomes_path = os.path.join(args.out_dir, OUT_OUTCOMES)
    ensure_csv(outcomes_path, [
        "ts_utc", "window_epoch",
        "poly_outcome", "poly_target", "poly_final_chainlink", "poly_final_binance",
        "poly_final_up_ask", "poly_final_down_ask",
        "pred_outcome", "pred_strike", "pred_final_binance",
        "pred_final_yes_ask", "pred_final_no_ask",
        "lim_outcome", "lim_market_id", "lim_target", "lim_final_binance",
        "lim_final_yes_ask", "lim_final_no_ask",
        "gem_outcome", "gem_market_id", "gem_target", "gem_final_binance",
        "gem_final_yes_ask", "gem_final_no_ask",
        "kal_outcome", "kal_ticker", "kal_target", "kal_final_binance",
        "kal_closed_in_window", "kal_sub_pos",
        "kal_final_yes_ask", "kal_final_no_ask",
        "agree3", "agree_poly_pred", "agree_poly_lim", "agree_pred_lim",
        "agree_poly_gem", "agree_poly_kal",
    ])

    log(f"CONSENSUS_BTC_V1 starting (DRY-RUN)")
    log(f"  ref_sec={args.ref_sec} thr={args.thr} min_agreements={args.min_agreements} invest=${args.invest_usd}")
    log(f"  win_half={args.win_half} fire_band=[{FIRE_SEC_MIN},{FIRE_SEC_MAX}]")
    log(f"  out_dir={os.path.abspath(args.out_dir)}")

    last_decided_window = None
    last_outcome_window = None

    while True:
        try:
            now = time.time()
            window_epoch = int((now // 300) * 300)
            sec_now = now - window_epoch
            prev_window = window_epoch - 300

            # ----- final-snapshot pass for previous window -----
            # The end-of-window samples live in the START of the NEXT window
            # because the bot picks them up after sec=300 of prior window. So
            # check the PREVIOUS window once at the start of the new window.
            if last_outcome_window != prev_window and sec_now < 30:
                fp = final_poly(prev_window)
                fpr = final_predict(prev_window)
                fl = final_limitless(prev_window)
                fg = final_gemini(prev_window)
                fk = final_kalshi(prev_window)
                po = fp and fp.get("outcome")
                pro = fpr and fpr.get("outcome")
                lo = fl and fl.get("outcome")
                go = fg and fg.get("outcome")
                ko = fk and fk.get("outcome")
                agree3 = (po and pro and lo and po == pro == lo)
                ts_iso = datetime.now(tz=timezone.utc).isoformat()
                with open(outcomes_path, "a", newline="") as f:
                    csv.writer(f).writerow([
                        ts_iso, prev_window,
                        po, fp and fp.get("target"), fp and fp.get("final_chainlink"),
                        fp and fp.get("final_binance"),
                        fp and fp.get("up_ask"), fp and fp.get("down_ask"),
                        pro, fpr and fpr.get("strike"), fpr and fpr.get("final_binance"),
                        fpr and fpr.get("yes_ask"), fpr and fpr.get("no_ask"),
                        lo, fl and fl.get("market_id"), fl and fl.get("target"),
                        fl and fl.get("final_binance"),
                        fl and fl.get("yes_ask"), fl and fl.get("no_ask"),
                        go, fg and fg.get("market_id"), fg and fg.get("target"),
                        fg and fg.get("final_binance"),
                        fg and fg.get("yes_ask"), fg and fg.get("no_ask"),
                        ko, fk and fk.get("market_id"), fk and fk.get("target"),
                        fk and fk.get("final_binance"),
                        fk and fk.get("kal_closed_in_window"),
                        fk and fk.get("sub_pos"),
                        fk and fk.get("yes_ask"), fk and fk.get("no_ask"),
                        bool(agree3),
                        po == pro if po and pro else None,
                        po == lo if po and lo else None,
                        pro == lo if pro and lo else None,
                        po == go if po and go else None,
                        po == ko if po and ko else None,
                    ])
                log(f"outcome win {prev_window}  poly={po} pred={pro} lim={lo} gem={go} kal={ko}  agree3={bool(agree3)}")
                last_outcome_window = prev_window

            if window_epoch == last_decided_window:
                time.sleep(POLL_SEC)
                continue

            if sec_now < FIRE_SEC_MIN or sec_now > FIRE_SEC_MAX:
                time.sleep(POLL_SEC)
                continue

            poly = snapshot_poly(window_epoch, args.ref_sec, args.win_half)
            pred = snapshot_predict(window_epoch, args.ref_sec, args.win_half)
            lim = snapshot_limitless(window_epoch, args.ref_sec, args.win_half)
            gem = snapshot_gemini(window_epoch, args.ref_sec, args.win_half)
            kal = snapshot_kalshi(window_epoch, args.ref_sec, args.win_half)

            # Log side-platform snapshots regardless of consensus
            ts_iso = datetime.now(tz=timezone.utc).isoformat()
            with open(limitless_path, "a", newline="") as f:
                csv.writer(f).writerow([
                    ts_iso, window_epoch, lim.get("market_id"),
                    lim.get("yes_ask"), lim.get("no_ask"),
                    lim.get("target"), lim.get("n_samples"),
                ])
            with open(gemini_path, "a", newline="") as f:
                csv.writer(f).writerow([
                    ts_iso, window_epoch, gem.get("market_id"),
                    gem.get("yes_ask"), gem.get("no_ask"),
                    gem.get("target"), gem.get("n_samples"),
                ])
            with open(kalshi_path, "a", newline="") as f:
                csv.writer(f).writerow([
                    ts_iso, window_epoch, kal.get("market_id"),
                    kal.get("yes_ask"), kal.get("no_ask"),
                    kal.get("target"), kal.get("sub_pos"), kal.get("n_samples"),
                ])

            # Need both Poly and Predict samples to decide
            if poly["n_samples"] == 0 or pred["n_samples"] == 0:
                with open(decisions_path, "a", newline="") as f:
                    csv.writer(f).writerow([
                        ts_iso, window_epoch, poly.get("up_ask"),
                        poly.get("down_ask"), pred.get("yes_ask"),
                        pred.get("no_ask"), False, None, None, None,
                        f"no_data poly_n={poly['n_samples']} pred_n={pred['n_samples']}"
                    ])
                log(f"window {window_epoch} sec={sec_now:.0f} — no data (poly_n={poly['n_samples']} pred_n={pred['n_samples']})")
                last_decided_window = window_epoch
                time.sleep(POLL_SEC)
                continue

            decision = decide(poly, pred, lim, gem, kal, args.thr, args.min_agreements)
            consensus = decision is not None

            side = platform = price = None
            n_votes = None
            reason = "below_threshold"
            if decision:
                side, platform, price, n_votes = decision
                reason = f"{n_votes}/5 agree >= {args.thr}"

            with open(decisions_path, "a", newline="") as f:
                csv.writer(f).writerow([
                    ts_iso, window_epoch,
                    poly.get("up_ask"), poly.get("down_ask"),
                    poly.get("target"), poly.get("binance"), poly.get("distance"),
                    pred.get("yes_ask"), pred.get("no_ask"),
                    pred.get("target"), pred.get("binance"), pred.get("distance"),
                    lim.get("yes_ask"), lim.get("no_ask"),
                    lim.get("target"), lim.get("binance"), lim.get("distance"),
                    gem.get("yes_ask"), gem.get("no_ask"),
                    gem.get("target"), gem.get("binance"), gem.get("distance"),
                    kal.get("yes_ask"), kal.get("no_ask"),
                    kal.get("target"), kal.get("binance"), kal.get("distance"),
                    kal.get("sub_pos"),
                    consensus, side, platform, price, reason
                ])

            if consensus:
                shares = args.invest_usd / price
                with open(trades_path, "a", newline="") as f:
                    csv.writer(f).writerow([
                        ts_iso, window_epoch, side, platform, price,
                        round(shares, 4), args.invest_usd, "DRY"
                    ])
                log(f"window {window_epoch} sec={sec_now:.0f}  CONSENSUS {side} on {platform.upper()} @ {price:.3f}  poly_up={poly.get('up_ask')} pred_yes={pred.get('yes_ask')} poly_dn={poly.get('down_ask')} pred_no={pred.get('no_ask')}  lim_y={lim.get('yes_ask')}/n={lim.get('no_ask')}  gem_y={gem.get('yes_ask')}/n={gem.get('no_ask')}  kal_y={kal.get('yes_ask')}/n={kal.get('no_ask')} sub={kal.get('sub_pos')}")
            else:
                log(f"window {window_epoch} sec={sec_now:.0f}  no consensus  poly_up={poly.get('up_ask')} pred_yes={pred.get('yes_ask')} poly_dn={poly.get('down_ask')} pred_no={pred.get('no_ask')}  lim_y={lim.get('yes_ask')}/n={lim.get('no_ask')}  gem_y={gem.get('yes_ask')}/n={gem.get('no_ask')}  kal_y={kal.get('yes_ask')}/n={kal.get('no_ask')}")

            last_decided_window = window_epoch
            time.sleep(POLL_SEC)

        except KeyboardInterrupt:
            log("interrupted, exiting")
            break
        except Exception as e:
            log(f"ERROR {type(e).__name__}: {e}")
            time.sleep(2)


if __name__ == "__main__":
    main()
