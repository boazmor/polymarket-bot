#!/usr/bin/env python3
"""V5 — outlier-continuation LIMIT bot.

Logic:
  1. When window N just closed: check outcomes of all 4 platforms
  2. If exactly 1 platform was outlier (opposite of other 3), check signal config
  3. If matches: queue LIMIT order for window N+1 at cheap price
  4. During window N+1: monitor target platform's ask
  5. Fill if ask <= limit_price
  6. At end of N+1: resolve outcome
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


def snap_pred(sec):
    return snapshot_at(tail_rows(PRED_DATA), "market_open_epoch", "sec_from_open", sec, WIN_HALF, {
        "up": "yes_ask", "down": "no_ask_implied", "target": "strike", "binance": "binance_now",
    })


def snap_poly(sec):
    return snapshot_at(tail_rows(POLY_DATA), "market_epoch", "sec_from_start", sec, WIN_HALF, {
        "up": "up_ask", "down": "down_ask", "target": "target_price", "binance": "binance_price",
    })


def snap_okx(sec):
    return snapshot_at(tail_rows(OKX_DATA), "market_open_epoch", "sec_from_open", sec, WIN_HALF, {
        "up": "up_ask", "down": "down_ask", "target": "target_price", "binance": "binance_now",
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
        "target": med("target_price"), "binance": med("binance_now"),
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
    if plat == "okx":
        rows = tail_rows(OKX_DATA, n_bytes=400_000)
        last_bn = target = None
        for r in rows:
            try: ep = int(r.get("market_open_epoch") or 0)
            except: continue
            if ep != window_epoch: continue
            bn = f(r.get("binance_now")); tg = f(r.get("target_price"))
            if bn is not None: last_bn = bn
            if tg is not None: target = tg
        if last_bn is None or target is None: return None
        return "UP" if last_bn > target else "DOWN"
    return None


def get_anchor_max_abs_distance(window_epoch):
    """For consensus platforms (poly, lim, okx), get max abs distance at sec ~240."""
    max_dist = 0
    for plat in ["poly","lim","okx"]:
        if plat == "poly": s = snap_poly(240)
        elif plat == "lim": s = snap_lim(window_epoch, 240)
        elif plat == "okx": s = snap_okx(240)
        if not s: continue
        if plat != "lim" and s.get("ep") != window_epoch: continue
        bn = s.get("binance"); tg = s.get("target")
        if bn is None or tg is None: continue
        d = abs(bn - tg)
        if d > max_dist: max_dist = d
    return max_dist


def ensure_csv(path, header):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline="") as fh:
            csv.writer(fh).writerow(header)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="/root/live/v5")
    p.add_argument("--signals-json", default="/root/live/v5/v5_signals.json")
    p.add_argument("--invest-usd", type=float, default=2.0)
    p.add_argument("--live", action="store_true")
    args = p.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    log_path = os.path.join(args.out_dir, "v5.log")
    detections_path = os.path.join(args.out_dir, "v5_detections.csv")
    orders_path = os.path.join(args.out_dir, "v5_orders.csv")
    fills_path = os.path.join(args.out_dir, "v5_fills.csv")
    outcomes_path = os.path.join(args.out_dir, "v5_outcomes.csv")

    ensure_csv(detections_path, [
        "ts_utc","detection_window","signal_name",
        "outlier_outcome","consensus_outcome","anchor_max_distance",
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
        log("ERROR: V5 --live not wired yet", log_path); sys.exit(1)

    with open(args.signals_json) as fh:
        cfg = json.load(fh)
    signals = cfg["signals"]
    log(f"V5 starting (DRY-RUN). invest=${args.invest_usd} signals={len(signals)}", log_path)

    # Pending: {target_window: {signal, target, side, limit_price}}
    pending = {}
    active_fills = {}

    last_detection_window = None
    last_outcome_window = None

    while True:
        try:
            now = time.time()
            window_epoch = int((now // 300) * 300)
            sec_now = int(now - window_epoch)
            prev_window = window_epoch - 300

            # === OUTCOME PASS for fills ===
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
                            f"target={info['target']} side={info['side']} "
                            f"fill={info['fill_price']:.3f} outcome={outcome} won={won} pnl=${pnl:+.3f}",
                            log_path)
                last_outcome_window = prev_window

                # === DETECTION PASS: check if prev_window had outlier outcome ===
                if last_detection_window != prev_window:
                    last_detection_window = prev_window
                    outs = {}
                    for plat in ["poly","pred","lim","okx"]:
                        outs[plat] = derive_outcome(plat, prev_window)
                    if all(o in ("UP","DOWN") for o in outs.values()):
                        up_count = sum(1 for o in outs.values() if o == "UP")
                        outlier = None; consensus = None
                        if up_count == 3:
                            outlier_plat = [p for p,o in outs.items() if o == "DOWN"][0]
                            outlier = outs[outlier_plat]; consensus = "UP"
                        elif up_count == 1:
                            outlier_plat = [p for p,o in outs.items() if o == "UP"][0]
                            outlier = outs[outlier_plat]; consensus = "DOWN"
                        else: outlier_plat = None
                        if outlier_plat:
                            anchor_dist = get_anchor_max_abs_distance(prev_window)
                            with open(detections_path, "a", newline="") as fh:
                                csv.writer(fh).writerow([
                                    now_iso(), prev_window, f"outlier_{outlier_plat}",
                                    outlier, consensus, round(anchor_dist, 2),
                                ])
                            # Match against signals
                            for sig in signals:
                                if sig["outlier_platform"] != outlier_plat: continue
                                if sig["consensus_direction"] != consensus: continue
                                if anchor_dist < sig["min_anchor_distance_abs"]: continue
                                # Queue limit for current window (window_epoch)
                                target_w = window_epoch
                                pending[target_w] = {
                                    "signal_name": sig["name"],
                                    "target": sig["target"], "side": sig["side"],
                                    "limit_price": sig["limit_price"],
                                }
                                with open(orders_path, "a", newline="") as fh:
                                    csv.writer(fh).writerow([
                                        now_iso(), prev_window, target_w, sig["name"],
                                        sig["target"], sig["side"], sig["limit_price"],
                                    ])
                                log(f"DETECT prev_window={prev_window} outlier={outlier_plat} "
                                    f"consensus={consensus} dist={anchor_dist:.1f} → "
                                    f"queue LIMIT for window={target_w} target={sig['target']} "
                                    f"side={sig['side']} @ {sig['limit_price']:.3f}", log_path)
                                break

            # === FILL CHECK for pending orders in CURRENT window ===
            if window_epoch in pending:
                p = pending[window_epoch]
                target = p["target"]
                if target == "poly": tsnap = snap_poly(sec_now)
                elif target == "pred": tsnap = snap_pred(sec_now)
                elif target == "lim": tsnap = snap_lim(window_epoch, sec_now)
                elif target == "okx": tsnap = snap_okx(sec_now)
                else: tsnap = None
                if tsnap:
                    if target != "lim" and tsnap.get("ep") != window_epoch: tsnap = None
                if tsnap:
                    ask = tsnap.get("down") if p["side"] == "DOWN" else tsnap.get("up")
                    if ask is not None and ask <= p["limit_price"]:
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
