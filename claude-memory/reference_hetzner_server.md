---
name: Hetzner production server (Germany)
description: Connection details and layout of the Hetzner server in Germany used for the Polymarket bot
type: reference
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
The user runs the Polymarket bot on a Hetzner VPS in Germany (chosen for low latency to Polymarket infrastructure).

**Connection:**
- IP: `178.104.134.228`
- SSH: `ssh root@178.104.134.228`
- Auth: password (no SSH key set up yet)
- User: root (everything runs as root in `/root/`)

**System:**
- OS: Ubuntu (likely 24.04, not fully confirmed)
- Python: 3.12 (path `/usr/lib/python3.12/...`)

**Filesystem layout (verified 2026-04-29):**
- `/root/` — code lives here, MANY bot generations side-by-side (50+ files including ARCHITECT_V*, BITKO*, polibot*, MULTI_5M_*, ws_binance_poly_recorder_fix_real2.py being the latest recorder at 44KB from 25/04). Some Hebrew-named files have corrupted UTF-8 encoding on the server.
- `/root/data_ws_binance_poly_research/` — Binance + Polymarket order-book recordings. Last data: 26/04 23:57. Sizes: combined_per_second.csv 62MB, binance_ticks.csv 137MB, poly_book_ticks.csv 1.4GB, raw_poly_messages.jsonl **40GB**, plus markets/events/market_outcomes (small). Total 41GB.
- `/root/data_ws_chainlink_research/` — Polymarket-side BTC price (Chainlink RTDS via `wss://ws-live-data.polymarket.com`, topic `crypto_prices_chainlink`, symbol `btc/usd`). Last data: **only 24/04**, ran for ~10.5 hours. Files: per_second.csv 8.7MB, rtds_ticks.csv 4.7MB, raw_messages.jsonl 8.7MB.
- Many other `/root/data_*` dirs from other experiments: data_5m_dual, data_5m_research, data_edge_rare, data_eth, data_v30, data_target_render_retry, data_target_timing, etc.

**Important:** the two recorders (Binance+Poly vs Chainlink) **never ran simultaneously** in the recorded data — Chainlink ran 24/04, Binance+Poly ran 25–26/04. Cannot do a direct two-source price comparison without a fresh joint run.

**Currently no requirements.txt, no systemd unit, no cron** — bots are run manually inside an open SSH session via `python3 <filename>.py`. When the SSH session closes the process dies. **As of 2026-04-29 nothing is running** (last data is from 26/04).

**Auth (as of 2026-04-29):** SSH key authentication is set up. The home machine has an Ed25519 key at `C:\Users\user\.ssh\id_ed25519_hetzner` (no passphrase) whose public key is in `/root/.ssh/authorized_keys` on the server. SSH config alias `hetzner` is defined in `C:\Users\user\.ssh\config` — connect with `ssh hetzner` (no password). Password login still works as a fallback. **The office machine will need its own key generated and added to authorized_keys when we set it up there.**
