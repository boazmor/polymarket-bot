#!/usr/bin/env python3
"""V3 unified — handles multiple anchor→target signals from JSON config.

Each signal specifies:
  - anchor:  signal source platform (poly/pred/lim/okx)
  - target:  where to BUY (poly/pred/lim — tradeable only)
  - sec:     fire second (240, 270, etc.)
  - spread_lo/spread_hi:  spread = anchor_target - target_target ($ of BTC)
  - side:    UP or DOWN on target

Each window can fire at most once (dedup). Outcome resolved per target platform.
DRY only. Rich logging.
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


def derive_outcome_binance(window_epoch, data_file, ep_field, target_field, sec_field=None, market_filter=None):
    """Derive outcome from a recorder file by reading last binance_now vs target_price."""
    rows = tail_rows(data_file, n_bytes=400_000)
    last_bn = None; target = None
    for r in rows:
        if market_filter and not market_filter(r): continue
        try:
            ep = int(r.get(ep_field) or 0)
        except: continue
        if ep != window_epoch: continue
        bn = f(r.get("binance_now")); tg = f(r.get(target_field))
        if bn is not None: last_bn = bn
        if tg is not None: target = tg
    if last_bn is None or target is None: return None
    return "UP" if last_bn > target else "DOWN"


def derive_pred_outcome(window_epoch):
    return derive_outcome_binance(window_epoch, PRED_DATA, "market_open_epoch", "strike")


def derive_lim_outcome(window_epoch):
    mmap = _lim_map()
    rows = tail_rows(LIM_DATA, n_bytes=400_000)
    last_bn = None; target = None
    for r in rows:
        mid = r.get("market_id"); ep = mmap.get(mid)
        if ep != window_epoch: continue
        bn = f(r.get("binance_now")); tg = f(r.get("target_price"))
        if bn is not None: last_bn = bn
        if tg is not None: target = tg
    if last_bn is None or target is None: return None
    return "UP" if last_bn > target else "DOWN"


def read_outcome_for(target_plat, window_epoch):
    if target_plat == "poly":   return read_poly_outcome(window_epoch)
    if target_plat == "pred":   return derive_pred_outcome(window_epoch)
    if target_plat == "lim":    return derive_lim_outcome(window_epoch)
    return None


def ensure_csv(path, header):
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        with open(path, "w", newline="") as fh:
            csv.writer(fh).writerow(header)


def get_snap(plat, window_ep, sec):
    if plat == "poly":  s = snap_poly(sec)
    elif plat == "pred": s = snap_pred(sec)
    elif plat == "lim":  s = snap_lim(window_ep, sec)
    elif plat == "okx":  s = snap_okx(sec)
    else: return None
    if s and s.get("ep") and s.get("ep") != window_ep: return None
    return s


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--out-dir", default="/root/live/v3")
    p.add_argument("--signals-json", default="/root/live/v3/v3_signals.json")
    p.add_argument("--invest-usd", type=float, default=2.0)
    p.add_argument("--live", action="store_true")
    args = p.parse_args()

    os.makedirs(args.out_dir, exist_ok=True)
    log_path = os.path.join(args.out_dir, "v3.log")
    trades_path = os.path.join(args.out_dir, "v3_trades.csv")
    outcomes_path = os.path.join(args.out_dir, "v3_outcomes.csv")
    decisions_path = os.path.join(args.out_dir, "v3_decisions.csv")
    windows_fired_path = os.path.join(args.out_dir, "v3_windows_fired.log")

    ensure_csv(trades_path, [
        "ts_utc","window_epoch","sec_now","signal_name",
        "anchor","target","spread","side","buy_price","shares","invest_usd",
        "poly_up","poly_down","poly_target","poly_binance",
        "pred_up","pred_down","pred_target","pred_binance",
        "lim_up","lim_down","lim_target","lim_binance",
        "okx_up","okx_down","okx_target","okx_binance",
        "mode",
    ])
    ensure_csv(outcomes_path, [
        "ts_utc","window_epoch","signal_name","target","side","buy_price",
        "target_outcome","won","pnl_usd",
    ])
    ensure_csv(decisions_path, [
        "ts_utc","window_epoch","sec_now","signals_evaluated",
        "matched_signal","fired","reason",
    ])

    if args.live:
        log("ERROR: V3 --live not wired yet", log_path); sys.exit(1)

    with open(args.signals_json) as fh:
        cfg = json.load(fh)
    signals = cfg["signals"]
    signals_by_sec = defaultdict(list)
    for s in signals:
        signals_by_sec[s["sec"]].append(s)
    secs_we_check = sorted(signals_by_sec.keys())
    log(f"V3 starting (DRY-RUN). Loaded {len(signals)} signals at {len(secs_we_check)} secs: {secs_we_check}", log_path)
    log(f"  invest=${args.invest_usd}", log_path)

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
                if prev_window in live_trades:
                    info = live_trades[prev_window]
                    outcome = read_outcome_for(info["target"], prev_window)
                    if outcome is None and sec_now < 30:
                        time.sleep(POLL_SEC); continue
                    live_trades.pop(prev_window, None)
                    if outcome in ("UP","DOWN"):
                        won = (outcome == info["side"])
                        shares = args.invest_usd / info["price"]
                        pnl = (shares - args.invest_usd) if won else -args.invest_usd
                        with open(outcomes_path, "a", newline="") as fh:
                            csv.writer(fh).writerow([
                                now_iso(), prev_window, info["signal_name"],
                                info["target"], info["side"], info["price"],
                                outcome, won, round(pnl, 4),
                            ])
                        log(f"OUTCOME window={prev_window} signal={info['signal_name']} "
                            f"side={info['side']} target={info['target']} price={info['price']:.3f} "
                            f"outcome={outcome} won={won} pnl=${pnl:+.3f}", log_path)
                last_outcome_window = prev_window

            target_sec = None
            for s in secs_we_check:
                if abs(sec_now - s) <= 1:
                    target_sec = s; break
            if target_sec is None:
                time.sleep(POLL_SEC); continue
            if window_epoch in windows_fired:
                time.sleep(POLL_SEC); continue

            # Snapshot all platforms once
            poly = snap_poly(target_sec)
            pred = snap_pred(target_sec)
            lim = snap_lim(window_epoch, target_sec)
            okx = snap_okx(target_sec)
            if poly and poly.get("ep") != window_epoch: poly = None
            if pred and pred.get("ep") != window_epoch: pred = None
            if okx and okx.get("ep") != window_epoch: okx = None

            snaps = {"poly": poly, "pred": pred, "lim": lim, "okx": okx}

            # Evaluate signals at this sec
            matched_signal = None
            for sig in signals_by_sec[target_sec]:
                a_snap = snaps.get(sig["anchor"])
                t_snap = snaps.get(sig["target"])
                if not a_snap or not t_snap: continue
                at = a_snap.get("target"); tt = t_snap.get("target")
                if at is None or tt is None: continue
                spread = at - tt
                if not (sig["spread_lo"] <= spread < sig["spread_hi"]): continue
                # Match
                matched_signal = (sig, spread)
                break

            if not matched_signal:
                with open(decisions_path, "a", newline="") as fh:
                    csv.writer(fh).writerow([
                        now_iso(), window_epoch, sec_now,
                        len(signals_by_sec[target_sec]), None, False, "no_signal_match",
                    ])
                time.sleep(POLL_SEC); continue

            sig, spread = matched_signal
            t_snap = snaps[sig["target"]]
            side = sig["side"]
            buy_price = t_snap.get("down") if side == "DOWN" else t_snap.get("up")
            if buy_price is None or buy_price <= 0:
                with open(decisions_path, "a", newline="") as fh:
                    csv.writer(fh).writerow([
                        now_iso(), window_epoch, sec_now,
                        len(signals_by_sec[target_sec]), sig["name"], False, "no_buy_price",
                    ])
                time.sleep(POLL_SEC); continue

            shares = args.invest_usd / buy_price
            windows_fired.add(window_epoch)
            try:
                with open(windows_fired_path, "a") as fh:
                    fh.write(f"{window_epoch},{sig['name']},{sig['target']},{side},{round(buy_price,3)}\n")
            except OSError: pass
            live_trades[window_epoch] = {
                "signal_name": sig["name"], "target": sig["target"],
                "side": side, "price": buy_price,
            }

            def get_v(plat, field):
                s = snaps.get(plat)
                return s.get(field) if s else None

            with open(trades_path, "a", newline="") as fh:
                csv.writer(fh).writerow([
                    now_iso(), window_epoch, sec_now, sig["name"],
                    sig["anchor"], sig["target"], round(spread, 2), side,
                    round(buy_price, 4), round(shares, 4), round(args.invest_usd, 2),
                    get_v("poly","up"), get_v("poly","down"), get_v("poly","target"), get_v("poly","binance"),
                    get_v("pred","up"), get_v("pred","down"), get_v("pred","target"), get_v("pred","binance"),
                    get_v("lim","up"), get_v("lim","down"), get_v("lim","target"), get_v("lim","binance"),
                    get_v("okx","up"), get_v("okx","down"), get_v("okx","target"), get_v("okx","binance"),
                    "DRY",
                ])
            with open(decisions_path, "a", newline="") as fh:
                csv.writer(fh).writerow([
                    now_iso(), window_epoch, sec_now,
                    len(signals_by_sec[target_sec]), sig["name"], True, "FIRE",
                ])
            log(f"FIRE window={window_epoch} sec={sec_now} signal={sig['name']} "
                f"target={sig['target']} side={side} @ {buy_price:.3f} spread={spread:.1f}",
                log_path)

            time.sleep(POLL_SEC)

        except KeyboardInterrupt:
            log("interrupted", log_path); break
        except Exception as e:
            log(f"loop error: {type(e).__name__}: {e}", log_path)
            time.sleep(POLL_SEC)


if __name__ == "__main__":
    main()
