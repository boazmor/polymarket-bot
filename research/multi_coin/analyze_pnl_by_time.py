"""analyze_pnl_by_time.py — segment a trade_outcomes.csv by NYC hour, day of
week, weekday/weekend. Designed to be run on ANY bot's trade log.

Usage:
    python3 analyze_pnl_by_time.py /root/data_5m_dual/trade_outcomes.csv
"""

import csv, sys
from datetime import datetime, timedelta
from collections import defaultdict

NYC_OFFSET_HOURS = -4   # ET in May (EDT). UTC->NYC. Server timestamps assumed UTC.

# Recognized US holidays in 2026 (when crypto markets behave differently due to lower US flow)
HOLIDAYS_2026 = {
    "2026-01-01": "New Year's Day",
    "2026-01-19": "MLK Day",
    "2026-02-16": "Presidents Day",
    "2026-04-03": "Good Friday",
    "2026-05-25": "Memorial Day",
    "2026-06-19": "Juneteenth",
    "2026-07-03": "Independence Day (observed)",
    "2026-09-07": "Labor Day",
    "2026-11-26": "Thanksgiving",
    "2026-11-27": "Black Friday",
    "2026-12-25": "Christmas",
}

DAYNAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

def main(path):
    with open(path) as f:
        rows = list(csv.DictReader(f))

    if not rows:
        print("no trades"); return

    # parse + normalize
    trades = []
    for x in rows:
        try:
            ts_utc = datetime.strptime(x["entry_ts"], "%Y-%m-%d %H:%M:%S")
        except Exception:
            continue
        ts_nyc = ts_utc + timedelta(hours=NYC_OFFSET_HOURS)
        trades.append({
            "ts_utc": ts_utc,
            "ts_nyc": ts_nyc,
            "pnl": float(x.get("pnl") or 0),
            "spent": float(x.get("spent_usd") or 0),
            "result": x.get("result"),
            "bot": x.get("bot"),
        })

    print(f"=== {path} ===")
    print(f"trades: {len(trades)}, range {trades[0]['ts_utc']} ... {trades[-1]['ts_utc']} UTC")

    # ------------------------------------------------------------ BY HOUR (NYC)
    print("\n=== BY NYC HOUR ===")
    by_hour = defaultdict(lambda: {"n":0, "wins":0, "losses":0, "pnl":0.0, "spent":0.0})
    for t in trades:
        h = t["ts_nyc"].hour
        by_hour[h]["n"] += 1
        by_hour[h]["pnl"] += t["pnl"]
        by_hour[h]["spent"] += t["spent"]
        if t["result"] == "WIN": by_hour[h]["wins"] += 1
        elif t["result"] == "LOSS": by_hour[h]["losses"] += 1

    print(f"{'NYCh':>4s} {'#':>4s} {'win%':>6s} {'pnl':>10s} {'pnl/trade':>10s}")
    for h in sorted(by_hour.keys()):
        b = by_hour[h]
        wr = 100*b["wins"]/(b["wins"]+b["losses"]) if (b["wins"]+b["losses"]) else 0
        avg = b["pnl"]/b["n"] if b["n"] else 0
        print(f"{h:>3d}h {b['n']:>4d} {wr:>5.1f}% ${b['pnl']:>+8,.0f} ${avg:>+8,.2f}")

    # ------------------------------------------------------------ BY DAY OF WEEK
    print("\n=== BY DAY OF WEEK (NYC date) ===")
    by_dow = defaultdict(lambda: {"n":0, "wins":0, "losses":0, "pnl":0.0, "days":set()})
    for t in trades:
        dow = t["ts_nyc"].weekday()
        by_dow[dow]["n"] += 1
        by_dow[dow]["pnl"] += t["pnl"]
        by_dow[dow]["days"].add(t["ts_nyc"].strftime("%Y-%m-%d"))
        if t["result"] == "WIN": by_dow[dow]["wins"] += 1
        elif t["result"] == "LOSS": by_dow[dow]["losses"] += 1

    print(f"{'day':>4s} {'#trades':>8s} {'win%':>6s} {'#dates':>7s} {'pnl':>10s} {'pnl/trade':>10s}")
    for dow in sorted(by_dow.keys()):
        b = by_dow[dow]
        wr = 100*b["wins"]/(b["wins"]+b["losses"]) if (b["wins"]+b["losses"]) else 0
        avg = b["pnl"]/b["n"] if b["n"] else 0
        print(f"{DAYNAMES[dow]:>4s} {b['n']:>8d} {wr:>5.1f}% {len(b['days']):>7d} ${b['pnl']:>+8,.0f} ${avg:>+8,.2f}")

    # ------------------------------------------------------------ WEEKDAY vs WEEKEND
    print("\n=== WEEKDAY vs WEEKEND ===")
    weekday_pnl = sum(t["pnl"] for t in trades if t["ts_nyc"].weekday() < 5)
    weekday_n = sum(1 for t in trades if t["ts_nyc"].weekday() < 5)
    weekend_pnl = sum(t["pnl"] for t in trades if t["ts_nyc"].weekday() >= 5)
    weekend_n = sum(1 for t in trades if t["ts_nyc"].weekday() >= 5)
    if weekday_n:
        print(f"  weekday: {weekday_n} trades, ${weekday_pnl:+,.0f} pnl, ${weekday_pnl/weekday_n:+.2f}/trade")
    if weekend_n:
        print(f"  weekend: {weekend_n} trades, ${weekend_pnl:+,.0f} pnl, ${weekend_pnl/weekend_n:+.2f}/trade")

    # ------------------------------------------------------------ HOLIDAY flag
    holidays_today = [t for t in trades if t["ts_nyc"].strftime("%Y-%m-%d") in HOLIDAYS_2026]
    if holidays_today:
        print(f"\n=== HOLIDAY DAYS ({len(holidays_today)} trades) ===")
        for d, name in HOLIDAYS_2026.items():
            day_trades = [t for t in trades if t["ts_nyc"].strftime("%Y-%m-%d") == d]
            if day_trades:
                p = sum(t["pnl"] for t in day_trades)
                print(f"  {d} ({name}): {len(day_trades)} trades, ${p:+,.0f} pnl")

    # ------------------------------------------------------------ NYC HOUR x WEEKDAY/WEEKEND
    print("\n=== NYC HOUR — WEEKDAY vs WEEKEND ===")
    print(f"{'NYCh':>4s} {'wkday n':>8s} {'wkday pnl/trade':>16s} {'wkend n':>8s} {'wkend pnl/trade':>16s}")
    for h in range(24):
        wkd = [t for t in trades if t["ts_nyc"].weekday() < 5 and t["ts_nyc"].hour == h]
        wke = [t for t in trades if t["ts_nyc"].weekday() >= 5 and t["ts_nyc"].hour == h]
        if not wkd and not wke: continue
        wkd_avg = sum(t["pnl"] for t in wkd)/len(wkd) if wkd else 0
        wke_avg = sum(t["pnl"] for t in wke)/len(wke) if wke else 0
        print(f"{h:>3d}h {len(wkd):>8d} ${wkd_avg:>+13,.2f}   {len(wke):>8d} ${wke_avg:>+13,.2f}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 analyze_pnl_by_time.py <trade_outcomes.csv>")
        sys.exit(1)
    main(sys.argv[1])
