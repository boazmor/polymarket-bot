---
name: Hetzner Helsinki server (provisioned 2026-05-02)
description: Connection details for the new Helsinki VPS used for live trading (replaces Germany which is geoblocked)
type: reference
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
Provisioned 2026-05-02 to bypass Polymarket's German IP geoblock. Polymarket does NOT block Finland — verified working.

**Connection:**
- IP: `62.238.26.145`
- SSH alias: `ssh helsinki` (defined in `C:\Users\user\.ssh\config`)
- Auth: SSH key at `C:\Users\user\.ssh\id_ed25519_hetzner`
- User: root

**System:**
- OS: Ubuntu 24.04.3 LTS
- Python: 3.12.3
- Type: Hetzner CPX22 (4 vCPU, 8GB RAM, 80GB disk)
- Cost: ~7€/month

**Pre-installed (as of 2026-05-02):**
- py-clob-client-v2 v1.0.0 (the V2-aware fork — NOT the broken py-clob-client)
- python-dotenv, websockets, requests, websocket-client, screen
- All bot dependencies

**Layout:**
- `/root/live/btc_5m/` — bot code + .env (chmod 600)
  - `LIVE_BTC_5M_V1_TEST5.py`
  - `smoke_test_v2.py`
  - `.env` (private key + EOA address)
- `/root/research/multi_coin/` — recorder code
  - `MULTI_COIN_RECORDER.py`
- `/root/data_<coin>_5m_research/` — recorder output, one dir per coin
- `/root/germany_archive/` — empty (planned for archived Germany data, not yet copied)
- `/root/start_helsinki.sh` — starts 7 coin recorders under screen
- `/root/start_bot_helsinki.sh` — starts the live bot under screen (NOT YET RUN)

**Currently running (as of 2026-05-02 evening):**
- 7 multi-coin recorders under `screen` (one per coin: rec_btc, rec_eth, rec_sol, rec_xrp, rec_doge, rec_bnb, rec_hype)
- Bot is NOT yet running here — still on user's home PC. Will migrate when stable.

**To check status:**
```bash
ssh helsinki "screen -ls"           # list active screens
ssh helsinki "tail /root/rec_btc.log"  # peek at one recorder
```

**To attach to a screen (interactively view):**
```bash
ssh helsinki
screen -r rec_btc
# Ctrl+A then D to detach without killing
```

**Disk capacity at provisioning: 70GB free.** Recording rate ~19MB/hour for 7 coins → 5+ months capacity.

## Why this server (not Germany)
- Hetzner Germany (178.104.134.228) is BLOCKED by Polymarket — orders return HTTP 403 "Trading restricted in your region"
- Hetzner Helsinki (this server) is NOT blocked — verified by HTTP 200 from /clob/time and from the live order placement test
- Both servers are on the same Hetzner account, same SSH key

## Plan for the two servers (as of end of session 2026-05-02)
- **Helsinki**: bot + 5min recorders (current)
- **Germany**: planned to host 15min + 1h + 4h + daily recorders (NOT YET DEPLOYED — recorder code needs `--window` flag)
- Once Germany is repurposed, the duplicate 5min recorders there will be stopped
