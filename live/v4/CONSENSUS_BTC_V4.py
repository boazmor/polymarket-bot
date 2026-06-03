#!/usr/bin/env python3
"""V4 unified — double-extreme pair-spread bot.

At each fire_sec, scans all 6 pair-spreads (poly-pred, poly-lim, poly-okx,
pred-lim, pred-okx, lim-okx). Identifies pairs where current spread is
extreme (z-score >= 2.0 from typical median).

Fires when EXACTLY 2 pairs are extreme. Categorizes:
  - all_above: both extremes above typical → bet DOWN on chosen target
  - all_below: both extremes below typical → noisy, skip
  - mixed (specific patterns): per signal config

DRY only for now. Rich logging.
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


def snap_poly(sec_now):
    return snapshot_at(tail_rows(POLY_DATA), "market_epoch", "sec_from_start", sec_now, WIN_HALF, {
        "up": "up_ask", "down": "down_ask", "target": "target_price",
    })


def snap_pred(sec_now):
    return snapshot_at(tail_rows(PRED_DATA), "market_open_epoch", "sec_from_open", sec_now, WIN_HALF, {
        "up": "yes_ask", "down": "no_ask_implied", "target": "strike",
    })


def snap_okx(sec_now):
    return snapshot_at(tail_rows(OKX_DATA), "market_open_epoch", "sec_from_open", sec_now, WIN_HALF, {
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
    """Outcome from binance vs target for pred/lim/okx; poly uses authoritative."""
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
        mmap = _lim_map()
        rows = tail_rows(LIM_DATA, n_bytes=400_000)
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


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="/root/live/v4")
    p.add_argument("--pair-stats-json", default="/root/live/v4/v4_pair_stats.json")
    p.add_argument("--signals-json", default="/root/live/v4/v4_signals.json")
    p.add_argument("--invest-usd", type=float, default=2.0)
    p.add_argument("--fire-secs", type=str, default="60,120,180,240")
    p.add_argument("--live", action="store_true")
    args = p.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    log_path = os.path.join(args.out_dir, "v4.log")
    trades_path = os.path.join(args.out_dir, "v4_trades.csv")
    outcomes_path = os.path.join(args.out_dir, "v4_outcomes.csv")
    decisions_path = os.path.join(args.out_dir, "v4_decisions.csv")
    windows_fired_path = os.path.join(args.out_dir, "v4_windows_fired.log")

    ensure_csv(trades_path, [
        "ts_utc","window_epoch","sec_now","signal_name",
        "category","target","side","buy_price","shares","invest_usd",
        "poly_target","pred_target","lim_target","okx_target",
        "spread_poly_pred","spread_poly_lim","spread_poly_okx",
        "spread_pred_lim","spread_pred_okx","spread_lim_okx",
        "extreme_pair_1","extreme_dir_1","extreme_pair_2","extreme_dir_2",
        "mode",
    ])
    ensure_csv(outcomes_path, [
        "ts_utc","window_epoch","signal_name","target","side","buy_price",
        "target_outcome","won","pnl_usd",
    ])
    ensure_csv(decisions_path, [
        "ts_utc","window_epoch","sec_now",
        "n_extremes","category","matched_signal","fired","reason",
    ])

    if args.live:
        log("ERROR: V4 --live not wired yet", log_path); sys.exit(1)

    with open(args.pair_stats_json) as fh:
        pair_cfg = json.load(fh)
    pair_stats = {tuple(k.split("-")): tuple(v) for k, v in pair_cfg["pairs"].items()}
    with open(args.signals_json) as fh:
        sig_cfg = json.load(fh)
    signals = sig_cfg["signals"]
    fire_secs = sorted(set(int(s) for s in args.fire_secs.split(",")))

    log(f"V4 starting (DRY-RUN). invest=${args.invest_usd} fire_secs={fire_secs} "
        f"pairs={len(pair_stats)} signals={len(signals)}", log_path)

    windows_fired = set()
    try:
        with open(windows_fired_path) as fh:
            for line in fh:
                w = line.strip().split(",")[0]
                if w.isdigit(): windows_fired.add(int(w))
    except (FileNotFoundError, OSError): pass

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
                if prev_window in live_trades:
                    info = live_trades[prev_window]
                    outcome = derive_outcome(info["target"], prev_window)
                    if outcome is None and sec_now < 30:
                        time.sleep(POLL_SEC); continue
                    live_trades.pop(prev_window, None)
                    if outcome in ("UP","DOWN"):
                        won = (outcome == info["side"])
                        shares = args.invest_usd / info["price"]
                        pnl = (shares - args.invest_usd) if won else -args.invest_usd
                        with open(outcomes_path, "a", newline="") as fh:
                            csv.writer(fh).writerow([
                                now_iso(), prev_window, info["signal"], info["target"],
                                info["side"], info["price"], outcome, won, round(pnl, 4),
                            ])
                        log(f"OUTCOME window={prev_window} signal={info['signal']} "
                            f"side={info['side']} price={info['price']:.3f} "
                            f"outcome={outcome} won={won} pnl=${pnl:+.3f}", log_path)
                last_outcome_window = prev_window

            target_sec = None
            for s in fire_secs:
                if abs(sec_now - s) <= 1:
                    target_sec = s; break
            if target_sec is None:
                time.sleep(POLL_SEC); continue
            if window_epoch in windows_fired:
                time.sleep(POLL_SEC); continue

            # Get all targets
            poly = snap_poly(target_sec)
            pred = snap_pred(target_sec)
            lim = snap_lim(window_epoch, target_sec)
            okx = snap_okx(target_sec)
            if not poly or poly.get("ep") != window_epoch: poly = None
            if not pred or pred.get("ep") != window_epoch: pred = None
            if not okx or okx.get("ep") != window_epoch: okx = None
            snaps = {"poly":poly,"pred":pred,"lim":lim,"okx":okx}

            # Compute pair spreads + extremes
            extremes = []
            spreads = {}
            for (p1, p2), (med, sd) in pair_stats.items():
                s1 = snaps.get(p1); s2 = snaps.get(p2)
                if not s1 or not s2: continue
                t1 = s1.get("target"); t2 = s2.get("target")
                if t1 is None or t2 is None: continue
                sp = t1 - t2
                spreads[(p1,p2)] = sp
                cl = classify_extreme(sp, med, sd)
                if cl: extremes.append(((p1,p2), cl, sp))

            if len(extremes) != 2:
                with open(decisions_path, "a", newline="") as fh:
                    csv.writer(fh).writerow([
                        now_iso(), window_epoch, sec_now,
                        len(extremes), "wrong_count", None, False,
                        f"have_{len(extremes)}_extremes",
                    ])
                time.sleep(POLL_SEC); continue

            # Match against configured signals
            statuses = [s for _, s, _ in extremes]
            category = None
            if all(s == "ABOVE" for s in statuses): category = "all_above"
            elif all(s == "BELOW" for s in statuses): category = "all_below"
            else: category = "mixed"

            matched_sig = None
            for sig in signals:
                if sig["name"] == "all_above_pred_DOWN" and category == "all_above":
                    matched_sig = sig; break
                # Mixed signals: check specific pair+dir combinations
                if "extremes" in sig and isinstance(sig["extremes"], list):
                    needed = sig["extremes"]
                    if any("any_2_pairs_above" in n for n in needed): continue
                    # check both required extremes present
                    match = True
                    for need in needed:
                        if "pair" not in need: continue
                        pr = tuple(need["pair"].split("-"))
                        dr = need["dir"]
                        found = False
                        for (xp1,xp2), xdr, _ in extremes:
                            if (xp1,xp2) == pr and xdr == dr:
                                found = True; break
                        if not found: match = False; break
                    if match: matched_sig = sig; break

            if not matched_sig:
                with open(decisions_path, "a", newline="") as fh:
                    csv.writer(fh).writerow([
                        now_iso(), window_epoch, sec_now, len(extremes),
                        category, None, False, "no_signal_match",
                    ])
                time.sleep(POLL_SEC); continue

            target_plat = matched_sig["target"]
            side = matched_sig["side"]
            t_snap = snaps.get(target_plat)
            if not t_snap:
                time.sleep(POLL_SEC); continue
            buy_price = t_snap.get("down") if side == "DOWN" else t_snap.get("up")
            if buy_price is None or buy_price <= 0:
                with open(decisions_path, "a", newline="") as fh:
                    csv.writer(fh).writerow([
                        now_iso(), window_epoch, sec_now, len(extremes),
                        category, matched_sig["name"], False, "no_buy_price",
                    ])
                time.sleep(POLL_SEC); continue

            shares = args.invest_usd / buy_price
            windows_fired.add(window_epoch)
            try:
                with open(windows_fired_path, "a") as fh:
                    fh.write(f"{window_epoch},{matched_sig['name']},{target_plat},{side},{round(buy_price,3)}\n")
            except OSError: pass
            live_trades[window_epoch] = {
                "signal": matched_sig["name"], "target": target_plat,
                "side": side, "price": buy_price,
            }

            def sg(plat):
                s = snaps.get(plat)
                return s.get("target") if s else None

            ex1 = extremes[0]; ex2 = extremes[1]

            with open(trades_path, "a", newline="") as fh:
                csv.writer(fh).writerow([
                    now_iso(), window_epoch, sec_now, matched_sig["name"],
                    category, target_plat, side, round(buy_price, 4),
                    round(shares, 4), round(args.invest_usd, 2),
                    sg("poly"), sg("pred"), sg("lim"), sg("okx"),
                    spreads.get(("poly","pred")), spreads.get(("poly","lim")),
                    spreads.get(("poly","okx")), spreads.get(("pred","lim")),
                    spreads.get(("pred","okx")), spreads.get(("lim","okx")),
                    f"{ex1[0][0]}-{ex1[0][1]}", ex1[1],
                    f"{ex2[0][0]}-{ex2[0][1]}", ex2[1],
                    "DRY",
                ])
            with open(decisions_path, "a", newline="") as fh:
                csv.writer(fh).writerow([
                    now_iso(), window_epoch, sec_now, len(extremes),
                    category, matched_sig["name"], True, "FIRE",
                ])
            log(f"FIRE window={window_epoch} sec={sec_now} signal={matched_sig['name']} "
                f"target={target_plat} side={side} @ {buy_price:.3f}", log_path)

            time.sleep(POLL_SEC)

        except KeyboardInterrupt:
            log("interrupted", log_path); break
        except Exception as e:
            log(f"loop error: {type(e).__name__}: {e}", log_path)
            time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
