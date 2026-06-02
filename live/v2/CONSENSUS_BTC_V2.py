#!/usr/bin/env python3
"""V2 = V1 (414 combos) + POLY EXTREME overlay.

Uses the same 414 proven-100% combos as V1, but adds an extra filter:
  poly's chosen-side ask must be >= poly_extreme_threshold.

This restricts V1's fires to only the cases where Polymarket is at extreme
price for the chosen direction. The hypothesis: V1 is already strong, adding
poly extreme should push accuracy even higher (target 95%+).

Rich logging: every trade records ALL snapshots so the report-generator can
slice by neighboring parameter values without rebuilding the dataset.
"""
import argparse, csv, json, os, sys, time
from collections import defaultdict
from datetime import datetime, timezone

POLY_DATA     = "/root/research/multi_coin/data_btc_5m_research/combined_per_second.csv"
POLY_OUTCOMES = "/root/research/multi_coin/data_btc_5m_research/market_outcomes.csv"
PRED_DATA     = "/root/data_predict_btc_5m/combined_per_second.csv"
LIM_DATA      = "/root/data_limitless_btc_5m/combined_per_second.csv"
LIM_MK        = "/root/data_limitless_btc_5m/markets.csv"
KAL_DATA      = "/root/data_kalshi_btc_15m/combined_per_second.csv"
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


def snap_poly(sec_now):
    return snapshot_at(tail_rows(POLY_DATA), "market_epoch", "sec_from_start", sec_now, WIN_HALF, {
        "up": "up_ask", "down": "down_ask", "target": "target_price", "binance": "binance_price",
    })


def snap_pred(sec_now):
    return snapshot_at(tail_rows(PRED_DATA), "market_open_epoch", "sec_from_open", sec_now, WIN_HALF, {
        "up": "yes_ask", "down": "no_ask_implied", "target": "strike", "binance": "binance_now",
    })


def snap_okx(sec_now):
    return snapshot_at(tail_rows(OKX_DATA), "market_open_epoch", "sec_from_open", sec_now, WIN_HALF, {
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


def snap_lim(window_ep, sec_now):
    rows = tail_rows(LIM_DATA); mmap = _lim_map()
    matching = []
    for r in rows:
        mid = r.get("market_id"); ep = mmap.get(mid)
        if ep != window_ep: continue
        try: es = int(r.get("epoch_sec") or 0)
        except: continue
        sec = es - ep
        if abs(sec - sec_now) > WIN_HALF: continue
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


def snap_kal(window_ep, sec_now):
    rows = tail_rows(KAL_DATA, n_bytes=400_000)
    target_ts = window_ep + sec_now
    matching = []
    for r in rows:
        try:
            es = int(r.get("epoch_sec") or 0)
            oe = int(r.get("open_epoch") or 0)
            ce = int(r.get("close_epoch") or 0)
        except: continue
        if not (oe <= target_ts <= ce): continue
        if abs(es - target_ts) > WIN_HALF: continue
        matching.append(r)
    if not matching: return None
    def med(k):
        vals = [f(r.get(k)) for r in matching]
        vals = [v for v in vals if v is not None]
        return sorted(vals)[len(vals)//2] if vals else None
    return {
        "up": med("yes_ask"), "down": med("no_ask"),
        "target": med("target_price"), "binance": med("binance_now"),
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
    lim_ok = kal_ok = False
    if lim and lim.get("target") is not None:
        if abs(lim["target"] - avg_tgt) < gap and vote_of(lim, thr) == side:
            lim_ok = True
    if kal and kal.get("target") is not None:
        if abs(kal["target"] - avg_tgt) < gap and vote_of(kal, thr) == side:
            kal_ok = True
    if third == "lim_only" and not lim_ok: return None
    if third == "kal_only" and not kal_ok: return None
    if third == "lim_or_kal" and not (lim_ok or kal_ok): return None
    if third == "lim_and_kal" and not (lim_ok and kal_ok): return None
    return side


def cheap_plat(poly, pred, lim, side):
    candidates = []
    if poly:
        v = poly.get("up") if side == "UP" else poly.get("down")
        if v is not None and v > 0: candidates.append(("poly", v))
    if pred:
        v = pred.get("up") if side == "UP" else pred.get("down")
        if v is not None and v > 0: candidates.append(("pred", v))
    if lim:
        v = lim.get("up") if side == "UP" else lim.get("down")
        if v is not None and v > 0: candidates.append(("lim", v))
    if not candidates: return None
    candidates.sort(key=lambda x: x[1])
    return candidates[0]


def ensure_csv(path, header):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline="") as fh:
            csv.writer(fh).writerow(header)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="/root/live/v2")
    p.add_argument("--combos-json", default="/root/live/v1/v1_combos.json")
    p.add_argument("--invest-usd", type=float, default=2.0)
    p.add_argument("--poly-extreme", type=float, default=0.75,
                   help="V2 overlay: poly side-ask must be >= this for the chosen direction")
    p.add_argument("--live", action="store_true")
    args = p.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    log_path = os.path.join(args.out_dir, "v2.log")
    trades_path = os.path.join(args.out_dir, "v2_trades.csv")
    outcomes_path = os.path.join(args.out_dir, "v2_outcomes.csv")
    decisions_path = os.path.join(args.out_dir, "v2_decisions.csv")
    windows_fired_path = os.path.join(args.out_dir, "v2_windows_fired.log")

    ensure_csv(trades_path, [
        "ts_utc","window_epoch","sec_now","side","buy_plat","buy_price",
        "shares","invest_usd","combo_id","combo_str",
        "poly_up","poly_down","poly_target","poly_binance",
        "pred_up","pred_down","pred_target","pred_binance",
        "lim_up","lim_down","lim_target","lim_binance",
        "okx_up","okx_down","okx_target","okx_binance",
        "kal_up","kal_down","kal_target",
        "poly_side_ask","poly_extreme_used","mode",
    ])
    ensure_csv(outcomes_path, [
        "ts_utc","window_epoch","side","buy_plat","buy_price",
        "poly_outcome","won","pnl_usd",
    ])
    ensure_csv(decisions_path, [
        "ts_utc","window_epoch","sec_now","matched_combos","poly_side_ask",
        "poly_extreme_met","fired","reason",
    ])

    if args.live:
        log("ERROR: V2 --live not wired", log_path); sys.exit(1)

    with open(args.combos_json) as fh:
        cfg = json.load(fh)
    combos = cfg["combos_100"]
    combos_by_sec = defaultdict(list)
    for c in combos:
        combos_by_sec[c["sec"]].append(c)
    secs_we_check = sorted(combos_by_sec.keys())
    log(f"V2 starting (DRY-RUN). Loaded {len(combos)} V1 combos at {len(secs_we_check)} secs. "
        f"poly_extreme={args.poly_extreme}", log_path)

    windows_fired = set()
    try:
        with open(windows_fired_path) as fh:
            for line in fh:
                w = line.strip().split(",")[0]
                if w.isdigit(): windows_fired.add(int(w))
    except (FileNotFoundError, OSError): pass
    log(f"  loaded {len(windows_fired)} previously-fired windows", log_path)

    live_trades = {}
    last_outcome_window = None

    while True:
        try:
            now = time.time()
            window_epoch = int((now // 300) * 300)
            sec_now = int(now - window_epoch)
            prev_window = window_epoch - 300

            # Outcome pass
            if last_outcome_window != prev_window and sec_now < 60:
                outcome = read_poly_outcome(prev_window)
                if outcome is None and sec_now < 30:
                    time.sleep(POLL_SEC); continue
                if prev_window in live_trades:
                    info = live_trades.pop(prev_window)
                    if outcome in ("UP","DOWN"):
                        won = (outcome == info["side"])
                        shares = args.invest_usd / info["price"]
                        pnl = (shares - args.invest_usd) if won else -args.invest_usd
                        with open(outcomes_path, "a", newline="") as fh:
                            csv.writer(fh).writerow([
                                now_iso(), prev_window, info["side"], info["plat"],
                                info["price"], outcome, won, round(pnl, 4),
                            ])
                        log(f"OUTCOME window={prev_window} side={info['side']} "
                            f"plat={info['plat']} price={info['price']:.3f} outcome={outcome} "
                            f"won={won} pnl=${pnl:+.3f}", log_path)
                last_outcome_window = prev_window

            target_sec = None
            for s in secs_we_check:
                if abs(sec_now - s) <= 1:
                    target_sec = s; break
            if target_sec is None:
                time.sleep(POLL_SEC); continue
            if window_epoch in windows_fired:
                time.sleep(POLL_SEC); continue

            poly = snap_poly(target_sec)
            pred = snap_pred(target_sec)
            lim = snap_lim(window_epoch, target_sec)
            kal = snap_kal(window_epoch, target_sec)
            okx = snap_okx(target_sec)
            if not poly or not pred or poly.get("ep") != window_epoch or pred.get("ep") != window_epoch:
                time.sleep(POLL_SEC); continue
            if okx and okx.get("ep") != window_epoch: okx = None

            # Evaluate all combos at this sec
            matched = []
            for combo in combos_by_sec[target_sec]:
                side = evaluate_combo(combo, poly, pred, lim, kal)
                if side is not None:
                    matched.append((combo, side))

            if not matched:
                with open(decisions_path, "a", newline="") as fh:
                    csv.writer(fh).writerow([
                        now_iso(), window_epoch, sec_now, 0, None, None, False, "no_v1_match",
                    ])
                time.sleep(POLL_SEC); continue

            sides = set(s for _, s in matched)
            if len(sides) > 1:
                with open(decisions_path, "a", newline="") as fh:
                    csv.writer(fh).writerow([
                        now_iso(), window_epoch, sec_now, len(matched), None, None, False, "mixed_sides",
                    ])
                time.sleep(POLL_SEC); continue

            side = list(sides)[0]

            # POLY EXTREME overlay
            poly_side_ask = poly.get("up") if side == "UP" else poly.get("down")
            poly_extreme_met = poly_side_ask is not None and poly_side_ask >= args.poly_extreme
            if not poly_extreme_met:
                with open(decisions_path, "a", newline="") as fh:
                    csv.writer(fh).writerow([
                        now_iso(), window_epoch, sec_now, len(matched),
                        poly_side_ask, False, False,
                        f"poly_not_extreme_{poly_side_ask}",
                    ])
                time.sleep(POLL_SEC); continue

            # Buy cheapest of poly/pred/lim
            pick = cheap_plat(poly, pred, lim, side)
            if pick is None:
                time.sleep(POLL_SEC); continue
            plat, price = pick
            shares = args.invest_usd / price
            combo_strs = ";".join(
                f"thr{c['thr']}/gap{c['gap']}/{c['third']}" for c, _ in matched[:5]
            )
            windows_fired.add(window_epoch)
            try:
                with open(windows_fired_path, "a") as fh:
                    fh.write(f"{window_epoch},{side},{plat},{round(price,3)}\n")
            except OSError: pass
            live_trades[window_epoch] = {"side": side, "plat": plat, "price": price}

            with open(trades_path, "a", newline="") as fh:
                csv.writer(fh).writerow([
                    now_iso(), window_epoch, sec_now, side, plat, round(price, 4),
                    round(shares, 4), round(args.invest_usd, 2),
                    len(matched), combo_strs,
                    poly.get("up"), poly.get("down"), poly.get("target"), poly.get("binance"),
                    pred.get("up"), pred.get("down"), pred.get("target"), pred.get("binance"),
                    lim.get("up") if lim else None, lim.get("down") if lim else None,
                    lim.get("target") if lim else None, lim.get("binance") if lim else None,
                    okx.get("up") if okx else None, okx.get("down") if okx else None,
                    okx.get("target") if okx else None, okx.get("binance") if okx else None,
                    kal.get("up") if kal else None, kal.get("down") if kal else None,
                    kal.get("target") if kal else None,
                    poly_side_ask, args.poly_extreme, "DRY",
                ])
            with open(decisions_path, "a", newline="") as fh:
                csv.writer(fh).writerow([
                    now_iso(), window_epoch, sec_now, len(matched),
                    poly_side_ask, True, True, "FIRE",
                ])
            log(f"FIRE window={window_epoch} sec={sec_now} {side} on {plat.upper()} @ {price:.3f} "
                f"matches={len(matched)} poly_side_ask={poly_side_ask:.3f}", log_path)

            time.sleep(POLL_SEC)

        except KeyboardInterrupt:
            log("interrupted", log_path); break
        except Exception as e:
            log(f"loop error: {type(e).__name__}: {e}", log_path)
            time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
