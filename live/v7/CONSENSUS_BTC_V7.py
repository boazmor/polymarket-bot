#!/usr/bin/env python3
"""V7 — DOUBLE-OUTLIER LIMIT bot.

Detects: same platform was outlier in 2 consecutive 5-min windows.
Action: place LIMIT order in window N+2 to buy target platform at cheap price.

Logic:
  - Track outlier history per resolved window
  - When new outlier detected, check if prev window's outlier was same platform
  - If yes AND consensus_pattern matches → queue LIMIT for current/next window
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


def now_iso(): return datetime.now(tz=timezone.utc).isoformat()


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
    except (FileNotFoundError, OSError): return []
    lines = data.splitlines()
    if not lines: return []
    try:
        with open(path) as fh: header = next(fh).strip()
    except (FileNotFoundError, OSError): return []
    cols = header.split(",")
    rows = []
    for line in lines:
        if not line or line.startswith(cols[0] + ","): continue
        parts = line.split(",")
        if len(parts) != len(cols): continue
        rows.append(dict(zip(cols, parts)))
    return rows


def snap(rows, ep_field, sec_field, sec_now, fields):
    matching = []
    for r in rows:
        try: ep = int(r.get(ep_field) or 0); sec = int(r.get(sec_field) or -1)
        except: continue
        if abs(sec - sec_now) > WIN_HALF: continue
        matching.append((ep, r))
    if not matching: return None
    matching.sort(key=lambda x: -x[0])
    cur_ep = matching[0][0]
    by_ep = [r for ep, r in matching if ep == cur_ep]
    out = {"ep": cur_ep}
    for fname, key in fields.items():
        vals = [f(r.get(key)) for r in by_ep]
        vals = [v for v in vals if v is not None]
        out[fname] = sorted(vals)[len(vals)//2] if vals else None
    return out


def snap_pred(sec):
    return snap(tail_rows(PRED_DATA), "market_open_epoch", "sec_from_open", sec, {
        "up": "yes_ask", "down": "no_ask_implied",
    })


def snap_poly(sec):
    return snap(tail_rows(POLY_DATA), "market_epoch", "sec_from_start", sec, {
        "up": "up_ask", "down": "down_ask",
    })


def snap_okx(sec):
    return snap(tail_rows(OKX_DATA), "market_open_epoch", "sec_from_open", sec, {
        "up": "up_ask", "down": "down_ask",
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
    return {"up": med("best_ask"), "down": med("no_best_ask")}


def read_poly_outcome(we):
    try:
        with open(POLY_OUTCOMES, "r") as fh: rows = list(csv.reader(fh))
        for row in reversed(rows):
            if len(row) >= 8 and (row[2] or "").strip() == str(we):
                o = (row[7] or "").strip()
                if o in ("UP","DOWN"): return o
    except (FileNotFoundError, OSError): pass
    return None


def derive_outcome(plat, we):
    if plat == "poly": return read_poly_outcome(we)
    if plat == "pred":
        rows = tail_rows(PRED_DATA, n_bytes=400_000)
        last_bn = target = None
        for r in rows:
            try: ep = int(r.get("market_open_epoch") or 0)
            except: continue
            if ep != we: continue
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
            if ep != we: continue
            bn = f(r.get("binance_now")); tg = f(r.get("target_price"))
            if bn is not None: last_bn = bn
            if tg is not None: target = tg
        if last_bn is None or target is None: return None
        return "UP" if last_bn > target else "DOWN"
    if plat == "okx":
        rows = tail_rows(OKX_DATA, n_bytes=400_000)
        last_bn = target = None
        for r in rows:
            try: ep = int(r.get("market_open_epoch") or 0)
            except: continue
            if ep != we: continue
            bn = f(r.get("binance_now")); tg = f(r.get("target_price"))
            if bn is not None: last_bn = bn
            if tg is not None: target = tg
        if last_bn is None or target is None: return None
        return "UP" if last_bn > target else "DOWN"
    return None


def get_outlier(we):
    outs = {p: derive_outcome(p, we) for p in ["poly","pred","lim","okx"]}
    if any(o is None for o in outs.values()): return None
    up_count = sum(1 for o in outs.values() if o == "UP")
    if up_count == 3:
        return ([p for p,o in outs.items() if o == "DOWN"][0], "UP")
    if up_count == 1:
        return ([p for p,o in outs.items() if o == "UP"][0], "DOWN")
    return None


def ensure_csv(path, header):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline="") as fh: csv.writer(fh).writerow(header)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="/root/live/v7")
    p.add_argument("--signals-json", default="/root/live/v7/v7_signals.json")
    p.add_argument("--invest-usd", type=float, default=2.0)
    p.add_argument("--live", action="store_true")
    args = p.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    log_path = os.path.join(args.out_dir, "v7.log")
    detections_path = os.path.join(args.out_dir, "v7_detections.csv")
    orders_path = os.path.join(args.out_dir, "v7_orders.csv")
    fills_path = os.path.join(args.out_dir, "v7_fills.csv")
    outcomes_path = os.path.join(args.out_dir, "v7_outcomes.csv")

    ensure_csv(detections_path, [
        "ts_utc","prev_outlier_window","curr_outlier_window","signal_name",
        "outlier_plat","cons_pattern",
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
        log("ERROR: V7 --live not wired yet", log_path); sys.exit(1)

    with open(args.signals_json) as fh: cfg = json.load(fh)
    signals = cfg["signals"]
    log(f"V7 starting (DRY-RUN). invest=${args.invest_usd} signals={len(signals)}", log_path)

    pending = {}
    active_fills = {}
    outlier_history = []  # list of (ep, outlier_plat, consensus)
    last_check_window = None
    last_outcome_window = None

    while True:
        try:
            now = time.time()
            window_epoch = int((now // 300) * 300)
            sec_now = int(now - window_epoch)
            prev_window = window_epoch - 300

            # === OUTCOME PASS ===
            if last_outcome_window != prev_window and sec_now < 60:
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
                            f"outcome={outcome} won={won} pnl=${pnl:+.3f}", log_path)
                last_outcome_window = prev_window

                # === DETECTION: check prev_window's outlier and possibly double ===
                if last_check_window != prev_window:
                    last_check_window = prev_window
                    o = get_outlier(prev_window)
                    if o:
                        outlier_plat, consensus = o
                        outlier_history.append({"ep": prev_window, "plat": outlier_plat, "cons": consensus})
                        outlier_history[:] = outlier_history[-10:]
                        # Check signals for DOUBLE outlier
                        if len(outlier_history) >= 2:
                            prev_event = outlier_history[-2]
                            curr_event = outlier_history[-1]
                            # Need same platform AND consecutive (5min gap)
                            if (prev_event["plat"] == curr_event["plat"] and
                                curr_event["ep"] - prev_event["ep"] == 300):
                                # Check each signal
                                for sig in signals:
                                    if sig["outlier_platform"] != curr_event["plat"]: continue
                                    pattern = sig["consensus_pattern"]
                                    if pattern[0] != prev_event["cons"]: continue
                                    if pattern[1] != curr_event["cons"]: continue
                                    # Queue limit for current window (window_epoch = curr_event["ep"] + 300 = N+2)
                                    target_w = window_epoch
                                    pending[target_w] = {
                                        "signal_name": sig["name"],
                                        "target": sig["target"], "side": sig["side"],
                                        "limit_price": sig["limit_price"],
                                    }
                                    with open(detections_path, "a", newline="") as fh:
                                        csv.writer(fh).writerow([
                                            now_iso(), prev_event["ep"], curr_event["ep"],
                                            sig["name"], curr_event["plat"],
                                            f"{pattern[0]}→{pattern[1]}",
                                        ])
                                    with open(orders_path, "a", newline="") as fh:
                                        csv.writer(fh).writerow([
                                            now_iso(), prev_window, target_w, sig["name"],
                                            sig["target"], sig["side"], sig["limit_price"],
                                        ])
                                    log(f"DOUBLE_OUTLIER detected: {curr_event['plat']} "
                                        f"cons {pattern[0]}→{pattern[1]} → queue LIMIT window={target_w} "
                                        f"target={sig['target']} side={sig['side']} @ {sig['limit_price']:.3f}",
                                        log_path)
                                    break

            # === FILL CHECK ===
            if window_epoch in pending:
                pn = pending[window_epoch]
                target = pn["target"]
                if target == "poly": tsnap = snap_poly(sec_now)
                elif target == "pred": tsnap = snap_pred(sec_now)
                elif target == "lim": tsnap = snap_lim(window_epoch, sec_now)
                elif target == "okx": tsnap = snap_okx(sec_now)
                else: tsnap = None
                if tsnap and (target == "lim" or tsnap.get("ep") == window_epoch):
                    ask = tsnap.get("down") if pn["side"] == "DOWN" else tsnap.get("up")
                    if ask is not None and ask <= pn["limit_price"]:
                        fill_price = pn["limit_price"]
                        shares = args.invest_usd / fill_price
                        with open(fills_path, "a", newline="") as fh:
                            csv.writer(fh).writerow([
                                now_iso(), window_epoch, sec_now, pn["signal_name"],
                                pn["target"], pn["side"], fill_price, round(ask, 4),
                                round(shares, 4), round(args.invest_usd, 2),
                            ])
                        active_fills[window_epoch] = {
                            "signal_name": pn["signal_name"], "target": pn["target"],
                            "side": pn["side"], "fill_price": fill_price,
                        }
                        log(f"FILL window={window_epoch} sec={sec_now} signal={pn['signal_name']} "
                            f"ask={ask:.3f} fill={fill_price:.3f}", log_path)
                        del pending[window_epoch]

            time.sleep(POLL_SEC)

        except KeyboardInterrupt:
            log("interrupted", log_path); break
        except Exception as e:
            log(f"loop error: {type(e).__name__}: {e}", log_path)
            time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
