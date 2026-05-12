---
name: Live-bot watchdog
description: check_live_bots.sh on both servers monitors 5 live + virtual arb bots; auto-restarts when process dies OR log file goes stale beyond per-bot threshold. Cron every 5 min. Log at /root/bot_watchdog.log on each host.
type: reference
originSessionId: 45cf2c48-b122-47c9-927a-f7ebcad47182
---
Deployed 12/05/2026 21:50 Israel after a 38-min silent stall caused by Python stdout buffering masked the bots' activity. Three fixes baked into the watchdog:

1. **All restarts use `python3 -u`** so stdout flushes per line under `screen ... > log` redirection. Without `-u` the log can look frozen even while the bot trades normally.
2. **pgrep pattern** is `"python3.* $script"` not `"python3 -u $script"` — matches bots that were launched without -u so the watchdog still recognizes them and (on restart) upgrades them to -u.
3. **Per-bot stale thresholds** match window cadence so quiet idle periods don't trigger false restarts.

| host | bot | stale threshold |
|---|---|---|
| helsinki | arb_v5_live (15min markets) | 600s |
| helsinki | arb_v7_live (5min markets) | 600s |
| hetzner | arb_v6_live (1h markets) | 1800s |
| hetzner | arb_v5_3way (virtual 15min) | 900s |
| hetzner | arb_v6_3way (virtual 1h) | 1800s |

**Cron entries:**
- helsinki: `*/5 * * * * /root/check_live_bots.sh helsinki > /dev/null 2>&1`
- hetzner:  `*/5 * * * * /root/check_live_bots.sh hetzner > /dev/null 2>&1`

**Restart side effects:**
- Kills via `pkill -f "python3.* $script"` — be careful if you ever run two copies of the same script for different params (the pkill is `-f` so it matches by command line; safest: each bot has a unique script filename).
- Archives the old log to `${logfile}.bak.${epoch}` before launching. Disk grows linearly with restart count; if many restarts pile up, rotate manually.
- Restart uses hard-coded args matching the V5/V6/V7 live + 3way invocations. To change args, edit the script not the cron.

**To extend** for new bots (e.g. arb_3way_live when built): add `check_bot name script.py "args" log.log stale_sec` in the right `case` block, scp to both hosts, no cron change needed.
