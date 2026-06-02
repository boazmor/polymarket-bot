#!/usr/bin/env python3
"""V1 super-bot: combine multiple proven 100%-winrate parameter combos.

Reads /root/live/v1/v1_combos.json with the list of (thr, gap, sec, third_mode).
At each poll second, checks all combos whose sec matches sec_now; fires the
first match per window (dedup) on the cheapest of poly/pred. Reads authoritative
poly outcome from market_outcomes.csv (uses the fix from 31/05).

Defaults to DRY mode. Pass --live to actually place orders (not implemented yet
for this bot; raises until you wire it).
"""
import argparse
import csv
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone

# ----- file paths (read-only, from running recorders) -----
POLY_DATA     = "/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv"
POLY_OUTCOMES = "/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv"
POLY_MK       = "/root/research/multi_coin/data_btc_5m_research/markets.csv"
PRED_DATA     = "/root/data_predict_btc_5m/combined_per_second.csv"
PRED_MK       = "/root/data_predict_btc_5m/markets.csv"
LIM_DATA      = "/root/data_limitless_btc_5m/combined_per_second.csv"
LIM_MK        = "/root/data_limitless_btc_5m/markets.csv"
KAL_DATA      = "/root/data_kalshi_btc_15m/combined_per_second.csv"

POLL_SEC = 1.0
WIN_HALF = 5     # median over [sec-5, sec+5] for snapshot
TAIL_BYTES = 200_000  # how much of each csv to read for snapshots


def f(v):
    if v in (None, "", "None"): return None
    try: return float(v)
    except: return None


def now_iso():
    return datetime.now(tz=timezone.utc).isoformat()


def log(msg, path=None):
    line = f"{datetime.now(tz=timezone.utc).strftime('%H:%M:%S')}  {msg}"
    print(line, flush=True)
    if path:
        with open(path, "a") as fh: fh.write(line + "\n")


# ----- tail-reader: read last N bytes, parse rows -----
def tail_rows(path, n_bytes=TAIL_BYTES):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > n_bytes:
                fh.seek(-n_bytes, 2)
                # skip first partial line
                fh.readline()
            data = fh.read().decode("utf-8", errors="ignore")
    except (FileNotFoundError, OSError):
        return []
    lines = data.splitlines()
    if not lines: return []
    # Need header: re-read just first line
    try:
        with open(path) as fh:
            header = next(fh).strip()
    except (FileNotFoundError, OSError):
        return []
    cols = header.split(",")
    rows = []
    for line in lines:
        if not line or line.startswith(cols[0] + ","): continue
        parts = line.split(",")
        if len(parts) != len(cols): continue
        rows.append(dict(zip(cols, parts)))
    return rows


def read_poly_outcome(window_epoch):
    """Read poly winner from market_outcomes.csv. Returns 'UP'/'DOWN' or None."""
    try:
        with open(POLY_OUTCOMES, "r") as fh:
            rows = list(csv.reader(fh))
        for row in reversed(rows):
            if len(row) >= 8 and (row[2] or "").strip() == str(window_epoch):
                o = (row[7] or "").strip()
                if o in ("UP", "DOWN"): return o
    except (FileNotFoundError, OSError):
        pass
    return None


def snapshot_at(rows, ep_field, sec_field, sec_now, win_half, fields):
    """Median over rows where sec_from_start in [sec_now-win_half, sec_now+win_half]."""
    matching = []
    for r in rows:
        try:
            ep = int(r.get(ep_field) or 0)
            sec = int(r.get(sec_field) or -1)
        except (ValueError, TypeError):
            continue
        if abs(sec - sec_now) > win_half: continue
        matching.append((ep, r))
    if not matching: return None
    # use most-recent epoch (current market)
    matching.sort(key=lambda x: -x[0])
    cur_ep = matching[0][0]
    by_ep = [r for ep, r in matching if ep == cur_ep]
    out = {"ep": cur_ep, "n_samples": len(by_ep)}
    for fname, key in fields.items():
        vals = [f(r.get(key)) for r in by_ep]
        vals = [v for v in vals if v is not None]
        out[fname] = sorted(vals)[len(vals)//2] if vals else None
    return out


def snap_poly(sec_now):
    rows = tail_rows(POLY_DATA)
    return snapshot_at(rows, "market_epoch", "sec_from_start", sec_now, WIN_HALF, {
        "up": "up_ask", "down": "down_ask", "target": "target_price",
        "binance": "binance_price",
    })


def snap_pred(sec_now):
    rows = tail_rows(PRED_DATA)
    return snapshot_at(rows, "market_open_epoch", "sec_from_open", sec_now, WIN_HALF, {
        "up": "yes_ask", "down": "no_ask_implied", "target": "strike",
        "binance": "binance_now",
    })


def _lim_market_map():
    m = {}
    try:
        with open(LIM_MK) as fh:
            for r in csv.DictReader(fh):
                try:
                    mid = r["market_id"]; exp_ms = int(r["expirationTimestamp"])
                    m[mid] = exp_ms // 1000 - 300
                except (KeyError, ValueError): pass
    except (FileNotFoundError, OSError): pass
    return m


def snap_lim(window_ep, sec_now):
    """Limitless data is keyed by market_id, not epoch directly."""
    rows = tail_rows(LIM_DATA)
    mmap = _lim_market_map()
    matching = []
    for r in rows:
        mid = r.get("market_id")
        ep = mmap.get(mid)
        if ep != window_ep: continue
        try: es = int(r.get("epoch_sec") or 0)
        except (ValueError, TypeError): continue
        sec = es - ep
        if abs(sec - sec_now) > WIN_HALF: continue
        matching.append(r)
    if not matching: return None
    def med(key):
        vals = [f(r.get(key)) for r in matching]
        vals = [v for v in vals if v is not None]
        return sorted(vals)[len(vals)//2] if vals else None
    return {
        "up": med("best_ask"), "down": med("no_best_ask"),
        "target": med("target_price"), "binance": med("binance_now"),
        "n_samples": len(matching),
    }


def snap_kal(window_ep, sec_now):
    """Kalshi is 15m; find the kalshi window that contains this 5m window."""
    rows = tail_rows(KAL_DATA, n_bytes=400_000)
    target_ts = window_ep + sec_now
    matching = []
    for r in rows:
        try:
            es = int(r.get("epoch_sec") or 0)
            oe = int(r.get("open_epoch") or 0)
            ce = int(r.get("close_epoch") or 0)
        except (ValueError, TypeError):
            continue
        if not (oe <= target_ts <= ce): continue
        if abs(es - target_ts) > WIN_HALF: continue
        matching.append(r)
    if not matching: return None
    def med(key):
        vals = [f(r.get(key)) for r in matching]
        vals = [v for v in vals if v is not None]
        return sorted(vals)[len(vals)//2] if vals else None
    return {
        "up": med("yes_ask"), "down": med("no_ask"),
        "target": med("target_price"), "binance": med("binance_now"),
        "n_samples": len(matching),
    }


def vote_of(snap, thr):
    if not snap: return None
    u = snap.get("up"); d = snap.get("down")
    u_ok = u is not None and u >= thr
    d_ok = d is not None and d >= thr
    if u_ok and not d_ok: return "UP"
    if d_ok and not u_ok: return "DOWN"
    return None


def evaluate_combo(combo, poly, pred, lim, kal):
    thr = combo["thr"]; gap = combo["gap"]; third = combo["third"]
    pv = vote_of(poly, thr); prv = vote_of(pred, thr)
    if not pv or pv != prv: return None
    side = pv
    p_tgt = poly.get("target"); pr_tgt = pred.get("target")
    if p_tgt is None or pr_tgt is None: return None
    avg_tgt = (p_tgt + pr_tgt) / 2
    lim_ok = False; kal_ok = False
    if lim and lim.get("target") is not None:
        if abs(lim["target"] - avg_tgt) < gap:
            if vote_of(lim, thr) == side: lim_ok = True
    if kal and kal.get("target") is not None:
        if abs(kal["target"] - avg_tgt) < gap:
            if vote_of(kal, thr) == side: kal_ok = True
    if third == "lim_only" and not lim_ok: return None
    if third == "kal_only" and not kal_ok: return None
    if third == "lim_or_kal" and not (lim_ok or kal_ok): return None
    if third == "lim_and_kal" and not (lim_ok and kal_ok): return None
    return side


def cheap_plat(poly, pred, lim, side):
    """Pick cheapest among 3 platforms (poly, pred, lim) for the chosen side.
    Returns (plat_name, price) or None."""
    candidates = []
    if poly:
        v = poly.get("up") if side == "UP" else poly.get("down")
        if v is not None: candidates.append(("poly", v))
    if pred:
        v = pred.get("up") if side == "UP" else pred.get("down")
        if v is not None: candidates.append(("pred", v))
    if lim:
        v = lim.get("up") if side == "UP" else lim.get("down")
        if v is not None: candidates.append(("lim", v))
    if not candidates: return None
    candidates.sort(key=lambda x: x[1])
    return candidates[0]


def ensure_csv(path, header):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline="") as fh:
            csv.writer(fh).writerow(header)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="/root/live/v1")
    p.add_argument("--combos-json", default="/root/live/v1/v1_combos.json")
    p.add_argument("--invest-usd", type=float, default=2.0)
    p.add_argument("--live", action="store_true",
                   help="placeholder; live ordering NOT YET wired for V1.")
    args = p.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    log_path = os.path.join(args.out_dir, "v1.log")
    trades_path = os.path.join(args.out_dir, "v1_trades.csv")
    outcomes_path = os.path.join(args.out_dir, "v1_outcomes.csv")
    decisions_path = os.path.join(args.out_dir, "v1_decisions.csv")
    windows_fired_path = os.path.join(args.out_dir, "v1_windows_fired.log")

    ensure_csv(trades_path, ["ts_utc","window_epoch","sec_now","side","platform",
                             "price","shares","invest_usd","combo_id","combo_str","mode"])
    ensure_csv(outcomes_path, ["ts_utc","window_epoch","side","platform","price",
                               "outcome","won","pnl_usd"])
    ensure_csv(decisions_path, ["ts_utc","window_epoch","sec_now","matched_combos","fired"])

    if args.live:
        log("ERROR: --live not wired for V1 yet; refuse to start in live mode.", log_path)
        sys.exit(1)

    with open(args.combos_json) as fh:
        cfg = json.load(fh)
    combos = cfg["combos_100"]
    # Index combos by sec
    combos_by_sec = defaultdict(list)
    for c in combos:
        combos_by_sec[c["sec"]].append(c)
    secs_we_check = sorted(combos_by_sec.keys())
    log(f"V1 starting (DRY-RUN). Loaded {len(combos)} 100%-winrate combos covering {len(secs_we_check)} entry seconds.", log_path)
    log(f"  invest=${args.invest_usd} secs={secs_we_check}", log_path)

    # Persistent dedup
    windows_fired = set()
    try:
        with open(windows_fired_path) as fh:
            for line in fh:
                w = line.strip().split(",")[0]
                if w.isdigit(): windows_fired.add(int(w))
    except (FileNotFoundError, OSError): pass
    log(f"  loaded {len(windows_fired)} previously-fired windows", log_path)

    live_trades = {}  # window_epoch -> {"side","plat","price","combo_id"}
    last_outcome_window = None

    while True:
        try:
            now_ts = time.time()
            window_epoch = int((now_ts // 300) * 300)
            sec_now = int(now_ts - window_epoch)
            prev_window = window_epoch - 300

            # ----- outcome pass for previous window (retry until authoritative) -----
            if last_outcome_window != prev_window and sec_now < 60:
                outcome = read_poly_outcome(prev_window)
                if outcome is None and sec_now < 30:
                    time.sleep(POLL_SEC); continue
                if outcome is None:
                    log(f"WARN: no authoritative poly outcome for window {prev_window} after 30s", log_path)
                # Resolve our trade for this window if any
                if prev_window in live_trades:
                    info = live_trades.pop(prev_window)
                    if info["plat"] == "poly":
                        plat_outcome = outcome
                    else:
                        plat_outcome = None  # only poly is authoritative; pred outcome ignored for V1 simplicity
                    if plat_outcome in ("UP","DOWN"):
                        won = (plat_outcome == info["side"])
                        shares = args.invest_usd / info["price"]
                        pnl = (shares - args.invest_usd) if won else -args.invest_usd
                        with open(outcomes_path, "a", newline="") as fh:
                            csv.writer(fh).writerow([
                                now_iso(), prev_window, info["side"], info["plat"],
                                info["price"], plat_outcome, won, round(pnl, 4),
                            ])
                        log(f"OUTCOME window={prev_window} side={info['side']} plat={info['plat']} "
                            f"price={info['price']:.3f} outcome={plat_outcome} won={won} pnl=${pnl:+.3f}", log_path)
                last_outcome_window = prev_window

            # ----- skip if not at one of our check-secs (with +/- 1 sec tolerance) -----
            target_sec = None
            for s in secs_we_check:
                if abs(sec_now - s) <= 1:
                    target_sec = s; break
            if target_sec is None:
                time.sleep(POLL_SEC); continue

            # ----- skip if window already fired -----
            if window_epoch in windows_fired:
                time.sleep(POLL_SEC); continue

            # ----- get snapshots -----
            poly = snap_poly(target_sec)
            pred = snap_pred(target_sec)
            if not poly or not pred or poly.get("ep") != window_epoch or pred.get("ep") != window_epoch:
                time.sleep(POLL_SEC); continue
            lim = snap_lim(window_epoch, target_sec)
            kal = snap_kal(window_epoch, target_sec)

            # ----- evaluate all combos at this sec -----
            matched = []
            chosen_side = None
            for combo in combos_by_sec[target_sec]:
                side = evaluate_combo(combo, poly, pred, lim, kal)
                if side is not None:
                    matched.append((combo, side))
                    if chosen_side is None: chosen_side = side

            if not matched:
                with open(decisions_path, "a", newline="") as fh:
                    csv.writer(fh).writerow([now_iso(), window_epoch, sec_now, 0, False])
                time.sleep(POLL_SEC); continue

            # ----- only fire if ALL matched combos agree on side (safety) -----
            sides = set(s for _, s in matched)
            if len(sides) > 1:
                log(f"window {window_epoch} sec={sec_now} MIXED-SIDE matches {sides}; SKIP", log_path)
                with open(decisions_path, "a", newline="") as fh:
                    csv.writer(fh).writerow([now_iso(), window_epoch, sec_now, len(matched), False])
                time.sleep(POLL_SEC); continue

            side = chosen_side
            # buy cheapest of poly/pred/lim (3 platforms)
            pick = cheap_plat(poly, pred, lim, side)
            if pick is None:
                time.sleep(POLL_SEC); continue
            plat, price = pick
            shares = args.invest_usd / price
            combo_strs = ";".join(
                f"thr{c['thr']}/gap{c['gap']}/{c['third']}" for c, _ in matched[:5]
            )
            # mark fired (DRY only for V1 right now)
            windows_fired.add(window_epoch)
            try:
                with open(windows_fired_path, "a") as fh:
                    fh.write(f"{window_epoch},{side},{plat},{round(price,3)}\n")
            except OSError: pass
            live_trades[window_epoch] = {"side": side, "plat": plat, "price": price, "combo_id": len(matched)}
            with open(trades_path, "a", newline="") as fh:
                csv.writer(fh).writerow([
                    now_iso(), window_epoch, sec_now, side, plat, round(price, 3),
                    round(shares, 4), round(args.invest_usd, 2), len(matched),
                    combo_strs, "DRY",
                ])
            with open(decisions_path, "a", newline="") as fh:
                csv.writer(fh).writerow([now_iso(), window_epoch, sec_now, len(matched), True])
            log(f"FIRE window={window_epoch} sec={sec_now} {side} on {plat.upper()} @ {price:.3f} "
                f"matches={len(matched)} ({combo_strs[:60]}...)", log_path)

            time.sleep(POLL_SEC)
        except KeyboardInterrupt:
            log("interrupted, exiting", log_path)
            break
        except Exception as e:
            log(f"loop error: {type(e).__name__}: {e}", log_path)
            time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
