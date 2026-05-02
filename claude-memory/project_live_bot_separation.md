---
name: Live trading bot — separate file and data directory from research
description: Hard rule for the live-trading bot: its own filename, its own data dir, completely isolated from V3 research bot
type: project
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
The user explicitly decided on 2026-04-30 that the live-trading bot must be physically separated from the research bot (V3) to prevent data corruption and bug cross-contamination.

**Naming convention:**
- File: `LIVE_BTC_5M_V1.py` (and onwards: V2, V3...)
- Server data directory: `/root/data_live_btc_5m_v1/` (a new dir per major version, OR a single dir reused — to be decided when we build it)
- Output CSVs use a `live_` prefix: `live_trades.csv`, `live_pnl.csv`, `live_errors.csv`, `live_orders.csv`, etc.

**Hard separation rules:**
1. The live bot **never reads or writes** anything in `/root/data_5m_dual/` (V3's directory).
2. The research bot V3 **never reads or writes** anything in the live directory.
3. No "mode flag" in V3 to enable live trading — a bug in live code must not be able to break research, and vice versa.
4. The two bots run side-by-side on the same server and are unaware of each other.

**Why:** V3 calls `shutil.rmtree(data_dir)` on startup (per the strict rule "every run cleans old reports"). If the live bot wrote to the same dir, every restart of V3 would wipe live data — losing real-money trade records — and any cross-write would mix simulation with live results, making both unanalyzable.

**Status as of 2026-04-30:** The live bot does NOT exist yet. Building it is part of the weekend plan, after the wallet (Rabby + Polymarket + USDC on Polygon) is connected and a manual $1 trade through the Polymarket UI has confirmed actual fees.
