#!/usr/bin/env python3
"""V6 — next-window LIMIT bot.

Logic:
  1. Watch window N for double-extreme pair-spread events
  2. When detected, queue a LIMIT order for window N+1 at a cheap price
  3. During window N+1, check target platform's ask at each second
  4. If ask <= limit_price, simulate FILL at limit_price
  5. At end of window N+1, resolve outcome based on target platform

DRY only.
"""
import argparse, csv, json, os, sys, time
from collections import defaultdict
from datetime import datetime, timezone

POLY_DATA     = "/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv"
POLY_OUTCOMES = "/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv"
PRED_DATA     = "/root/data_predict_btc_5m/combined_per_second.csv"
LIM_DATA      = "/root/data_limitless_btc_5m/combined_per_second.csv"
LIM_MK        = "/root/data_limitless_btc_5m/markets.csv"
OKX_DATA      = "/root/data_okx_btc_5m/combined_per_second.csv"

POLL_SEC = 1.0
WIN_HALF = 3
TAIL_BYTES = 200_000


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


def tail_rows(path, n_bytes=TAIL_BYTES):
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as fh:
            if size > n_bytes:
                fh.seek(-n_bytes, 2); fh.readline()
            data = fh.read().decode("utf-8", errors="ignore")
    except (FileNotFoundError, OSError):
        return []
    lines = data.splitlines()
    if not lines: return []
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


def snapshot_at(rows, ep_field, sec_field, sec_now, win_half, fields):
    matching = []
    for r in rows:
        try:
            ep = int(r.get(ep_field) or 0); sec = int(r.get(sec_field) or -1)
        except: continue
        if abs(sec - sec_now) > win_half: continue
        matching.append((ep, r))
    if not matching: return None
    matching.sort(key=lambda x: -x[0])
    cur_ep = matching[0][0]
    by_ep = [r for ep, r in matching if ep == cur_ep]
    out = {"ep": cur_ep, "n_samples": len(by_ep)}
    for fname, key in fields.items():
        vals = [f(r.get(key)) for r in by_ep]
        vals = [v for v in vals if v is not None]
        out[fname] = sorted(vals)[len(vals)//2] if vals else None
    return out


def snap_poly(sec):
    return snapshot_at(tail_rows(POLY_DATA), "market_epoch", "sec_from_start", sec, WIN_HALF, {
        "up": "up_ask", "down": "down_ask", "target": "target_price",
    })


def snap_pred(sec):
    return snapshot_at(tail_rows(PRED_DATA), "market_open_epoch", "sec_from_open", sec, WIN_HALF, {
        "up": "yes_ask", "down": "no_ask_implied", "target": "strike",
    })


def snap_okx(sec):
    return snapshot_at(tail_rows(OKX_DATA), "market_open_epoch", "sec_from_open", sec, WIN_HALF, {
        "up": "up_ask", "down": "down_ask", "target": "target_price",
    })


def _lim_map():
    m = {}
    try:
        with open(LIM_MK) as fh:
            for r in csv.DictReader(fh):
                try:
                    mid = r["market_id"]; exp = int(r["expirationTimestamp"])
                    m[mid] = exp // 1000 - 300
                except: pass
    except (FileNotFoundError, OSError): pass
    return m


def snap_lim(window_ep, sec):
    rows = tail_rows(LIM_DATA); mmap = _lim_map()
    matching = []
    for r in rows:
        mid = r.get("market_id"); ep = mmap.get(mid)
        if ep != window_ep: continue
        try: es = int(r.get("epoch_sec") or 0)
        except: continue
        s = es - ep
        if abs(s - sec) > WIN_HALF: continue
        matching.append(r)
    if not matching: return None
    def med(k):
        vals = [f(r.get(k)) for r in matching]
        vals = [v for v in vals if v is not None]
        return sorted(vals)[len(vals)//2] if vals else None
    return {
        "up": med("best_ask"), "down": med("no_best_ask"),
        "target": med("target_price"),
    }


def read_poly_outcome(window_epoch):
    try:
        with open(POLY_OUTCOMES, "r") as fh:
            rows = list(csv.reader(fh))
        for row in reversed(rows):
            if len(row) >= 8 and (row[2] or "").strip() == str(window_epoch):
                o = (row[7] or "").strip()
                if o in ("UP", "DOWN"): return o
    except (FileNotFoundError, OSError): pass
    return None


def derive_outcome(plat, window_epoch):
    if plat == "poly": return read_poly_outcome(window_epoch)
    if plat == "pred":
        rows = tail_rows(PRED_DATA, n_bytes=400_000)
        last_bn = target = None
        for r in rows:
            try: ep = int(r.get("market_open_epoch") or 0)
            except: continue
            if ep != window_epoch: continue
            bn = f(r.get("binance_now")); tg = f(r.get("strike"))
            if bn is not None: last_bn = bn
            if tg is not None: target = tg
        if last_bn is None or target is None: return None
        return "UP" if last_bn > target else "DOWN"
    if plat == "lim":
        mmap = _lim_map(); rows = tail_rows(LIM_DATA, n_bytes=400_000)
        last_bn = target = None
        for r in rows:
            mid = r.get("market_id"); ep = mmap.get(mid)
            if ep != window_epoch: continue
            bn = f(r.get("binance_now")); tg = f(r.get("target_price"))
            if bn is not None: last_bn = bn
            if tg is not None: target = tg
        if last_bn is None or target is None: return None
        return "UP" if last_bn > target else "DOWN"
    return None


def ensure_csv(path, header):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline="") as fh:
            csv.writer(fh).writerow(header)


def classify_extreme(spread, median, sd):
    if sd <= 0: return None
    z = (spread - median) / sd
    if z >= 2.0: return "ABOVE"
    if z <= -2.0: return "BELOW"
    return None


def get_target_ask(plat, snap, side):
    if not snap: return None
    return snap.get("down") if side == "DOWN" else snap.get("up")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="/root/live/v6")
    p.add_argument("--pair-stats-json", default="/root/live/v4/v4_pair_stats.json")
    p.add_argument("--signals-json", default="/root/live/v6/v6_signals.json")
    p.add_argument("--invest-usd", type=float, default=2.0)
    p.add_argument("--detect-secs", type=str, default="60,120,180,240",
                   help="seconds in window N to scan for double-extreme")
    p.add_argument("--live", action="store_true")
    args = p.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    log_path = os.path.join(args.out_dir, "v6.log")
    detections_path = os.path.join(args.out_dir, "v6_detections.csv")
    orders_path = os.path.join(args.out_dir, "v6_orders.csv")
    fills_path = os.path.join(args.out_dir, "v6_fills.csv")
    outcomes_path = os.path.join(args.out_dir, "v6_outcomes.csv")
    pending_path = os.path.join(args.out_dir, "v6_pending.log")

    ensure_csv(detections_path, [
        "ts_utc","detection_window","detection_sec","signal_name",
        "category","ex_pair_1","ex_dir_1","ex_pair_2","ex_dir_2",
    ])
    ensure_csv(orders_path, [
        "ts_utc","detection_window","target_window","signal_name",
        "target","side","limit_price",
    ])
    ensure_csv(fills_path, [
        "ts_utc","target_window","sec_now","signal_name","target","side",
        "fill_price","ask_at_fill","shares","invest_usd",
    ])
    ensure_csv(outcomes_path, [
        "ts_utc","target_window","signal_name","target","side","fill_price",
        "target_outcome","won","pnl_usd",
    ])

    if args.live:
        log("ERROR: V6 --live not wired yet", log_path); sys.exit(1)

    with open(args.pair_stats_json) as fh:
        pcfg = json.load(fh)
    pair_stats = {tuple(k.split("-")): tuple(v) for k, v in pcfg["pairs"].items()}
    with open(args.signals_json) as fh:
        scfg = json.load(fh)
    signals = scfg["signals"]
    detect_secs = sorted(set(int(s) for s in args.detect_secs.split(",")))

    log(f"V6 starting (DRY-RUN). invest=${args.invest_usd} detect_secs={detect_secs} "
        f"pairs={len(pair_stats)} signals={len(signals)}", log_path)

    # Pending orders: {target_window_epoch: {signal_name, target, side, limit_price, detection_ep}}
    pending = {}
    # Active fills awaiting outcome: same dict but with fill info
    active_fills = {}

    last_detection_window = None
    last_outcome_window = None

    while True:
        try:
            now = time.time()
            window_epoch = int((now // 300) * 300)
            sec_now = int(now - window_epoch)
            prev_window = window_epoch - 300

            # === OUTCOME PASS for active fills (window just ended) ===
            if last_outcome_window != prev_window and sec_now < 60:
                # Check if prev_window has an active fill awaiting outcome
                if prev_window in active_fills:
                    info = active_fills[prev_window]
                    outcome = derive_outcome(info["target"], prev_window)
                    if outcome is None and sec_now < 30:
                        time.sleep(POLL_SEC); continue
                    active_fills.pop(prev_window, None)
                    if outcome in ("UP","DOWN"):
                        won = (outcome == info["side"])
                        shares = args.invest_usd / info["fill_price"]
                        pnl = (shares - args.invest_usd) if won else -args.invest_usd
                        with open(outcomes_path, "a", newline="") as fh:
                            csv.writer(fh).writerow([
                                now_iso(), prev_window, info["signal_name"],
                                info["target"], info["side"], info["fill_price"],
                                outcome, won, round(pnl, 4),
                            ])
                        log(f"OUTCOME window={prev_window} signal={info['signal_name']} "
                            f"target={info['target']} side={info['side']} "
                            f"fill_price={info['fill_price']:.3f} outcome={outcome} won={won} pnl=${pnl:+.3f}",
                            log_path)
                last_outcome_window = prev_window

            # === FILL CHECK for pending orders in CURRENT window ===
            if window_epoch in pending:
                p = pending[window_epoch]
                target = p["target"]
                # Get current ask on target
                if target == "poly": tsnap = snap_poly(sec_now)
                elif target == "pred": tsnap = snap_pred(sec_now)
                elif target == "lim": tsnap = snap_lim(window_epoch, sec_now)
                elif target == "okx": tsnap = snap_okx(sec_now)
                else: tsnap = None
                if tsnap and tsnap.get("ep") == window_epoch:
                    ask = get_target_ask(target, tsnap, p["side"])
                    if ask is not None and ask <= p["limit_price"]:
                        # Fill at limit_price (conservative)
                        fill_price = p["limit_price"]
                        shares = args.invest_usd / fill_price
                        with open(fills_path, "a", newline="") as fh:
                            csv.writer(fh).writerow([
                                now_iso(), window_epoch, sec_now, p["signal_name"],
                                p["target"], p["side"], fill_price, round(ask, 4),
                                round(shares, 4), round(args.invest_usd, 2),
                            ])
                        active_fills[window_epoch] = {
                            "signal_name": p["signal_name"], "target": p["target"],
                            "side": p["side"], "fill_price": fill_price,
                        }
                        log(f"FILL window={window_epoch} sec={sec_now} signal={p['signal_name']} "
                            f"target={target} side={p['side']} ask={ask:.3f} fill={fill_price:.3f}",
                            log_path)
                        del pending[window_epoch]

            # === DETECTION PASS for current window ===
            target_sec = None
            for s in detect_secs:
                if abs(sec_now - s) <= 1:
                    target_sec = s; break
            if target_sec is not None and last_detection_window != window_epoch:
                # Get all 4 platform targets
                poly = snap_poly(target_sec); pred = snap_pred(target_sec)
                lim = snap_lim(window_epoch, target_sec); okx = snap_okx(target_sec)
                if poly and poly.get("ep") != window_epoch: poly = None
                if pred and pred.get("ep") != window_epoch: pred = None
                if okx and okx.get("ep") != window_epoch: okx = None
                snaps = {"poly":poly,"pred":pred,"lim":lim,"okx":okx}
                extremes = []
                for (p1, p2), (med, sd) in pair_stats.items():
                    s1 = snaps.get(p1); s2 = snaps.get(p2)
                    if not s1 or not s2: continue
                    t1 = s1.get("target"); t2 = s2.get("target")
                    if t1 is None or t2 is None: continue
                    sp = t1 - t2
                    cl = classify_extreme(sp, med, sd)
                    if cl: extremes.append(((p1,p2), cl, sp))
                if len(extremes) == 2:
                    statuses = [s for _, s, _ in extremes]
                    if all(s == "ABOVE" for s in statuses): cat = "all_above"
                    elif all(s == "BELOW" for s in statuses): cat = "all_below"
                    else: cat = "mixed"
                    # Match against signals
                    for sig in signals:
                        match = True
                        for need in sig["extremes"]:
                            if need.get("any_2_pairs_above"):
                                if cat != "all_above": match = False
                                break
                            if "pair" not in need: continue
                            pr = tuple(need["pair"].split("-"))
                            dr = need["dir"]
                            found = any((xp == pr and xd == dr) for xp, xd, _ in extremes)
                            if not found: match = False; break
                        if match:
                            # Queue limit order for NEXT window
                            next_w = window_epoch + 300
                            pending[next_w] = {
                                "signal_name": sig["name"],
                                "target": sig["target"], "side": sig["side"],
                                "limit_price": sig["limit_price"],
                                "detection_ep": window_epoch,
                            }
                            with open(detections_path, "a", newline="") as fh:
                                ex1 = extremes[0]; ex2 = extremes[1]
                                csv.writer(fh).writerow([
                                    now_iso(), window_epoch, sec_now, sig["name"],
                                    cat, f"{ex1[0][0]}-{ex1[0][1]}", ex1[1],
                                    f"{ex2[0][0]}-{ex2[0][1]}", ex2[1],
                                ])
                            with open(orders_path, "a", newline="") as fh:
                                csv.writer(fh).writerow([
                                    now_iso(), window_epoch, next_w, sig["name"],
                                    sig["target"], sig["side"], sig["limit_price"],
                                ])
                            log(f"DETECT window={window_epoch} sec={sec_now} signal={sig['name']} "
                                f"→ queue LIMIT for window={next_w} target={sig['target']} "
                                f"side={sig['side']} @ {sig['limit_price']:.3f}", log_path)
                            last_detection_window = window_epoch
                            break

            time.sleep(POLL_SEC)

        except KeyboardInterrupt:
            log("interrupted", log_path); break
        except Exception as e:
            log(f"loop error: {type(e).__name__}: {e}", log_path)
            time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
