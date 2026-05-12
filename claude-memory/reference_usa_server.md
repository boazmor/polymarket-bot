---
name: USA server (Ashburn VA) — live bots host
description: Hetzner Cloud cpx21 in Ashburn VA, runs V5_3WAY + V6_3WAY + V7 LIVE since 13/05. SSH alias usa, IP 178.156.203.239. Replaces Helsinki/Hetzner as the live-trading host.
type: reference
originSessionId: 45cf2c48-b122-47c9-927a-f7ebcad47182
---
Provisioned 13/05/2026 00:35 Israel time via Hetzner Cloud API.

**Connection:**
- SSH alias: `usa` (in `C:\Users\user\.ssh\config`)
- IP: `178.156.203.239`
- User: root
- Key: `C:\Users\user\.ssh\id_ed25519_hetzner` (same as Helsinki/Hetzner)
- OS: Ubuntu 24.04 LTS
- Spec: cpx21 = 3 vCPU, 4 GB RAM, 80 GB disk, $13.99/mo

**What runs here (13/05 onward):**
- arb_v5_3way_live — 15min markets
- arb_v6_3way_live — 1h markets
- arb_v7_live — 5min markets (still 2-platform Poly+Predict)
- 7 recorders: poly 5m/15m/1h, predict 5m/15m/1h, limitless 15m (WS), limitless 1h (WS)
- watchdog cron */5 minutes with `check_live_bots.sh usa`

**Latency tested 13/05 vs Helsinki (TTFB to API):**
- Predict.fun: US 136ms vs Helsinki 264ms — **US wins by 128ms**
- Limitless: US 105ms vs Helsinki 102ms — tied
- Polymarket: US 127ms vs Helsinki 89ms — Helsinki wins by 37ms (Cloudflare edge)

Net effect of migration is positive because Predict.fun arb is a large chunk of opportunities and the 128ms saving on each Predict order outweighs the 37ms loss on Polymarket.

**Known constraint — Binance geo-blocks US:**
The MULTI_COIN_RECORDER on this server cannot fetch Binance WebSocket (HTTP 451). Columns 6-10 in `combined_per_second.csv` (binance_price, distance_signed, distance_abs, etc.) are empty. **Does NOT break the bots** — they read `target_chainlink_at_open` (column 13) and orderbook columns (16+), all of which still populate.

**Hetzner API token (used during setup):**
Token `claude-setup` was created in project `polymarket-us` on 13/05. **Should be revoked after the migration is verified stable.** Token value: 4KtQUSbKdXmUtbLvULFFzy8aStZkdt94vIrLwPtk0JlDHEFJ2w2WMQQU2NfHOzuC — DO NOT commit this string to git.

**What stays on Helsinki and Hetzner:**
- Helsinki: recorders only (multi-coin 5m, limitless 15m, predict 15m+1h, multi-window). Live bot processes all migrated to US.
- Hetzner: virtual 3WAY bots (arb_v5_3way, arb_v6_3way, arb_v6_eth_1h, arb_v6_bnb_1h, etc.) PLUS legacy recorders. Live bot processes all migrated to US.

**Watchdog reorg:**
`check_live_bots.sh` now has three hosts: `usa` checks the 3 live bots, `helsinki` is empty (will be removed in a future cleanup), `hetzner` keeps the 2 virtual 3WAY bots.
