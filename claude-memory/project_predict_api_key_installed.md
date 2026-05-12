---
name: Predict.fun API key installed on Helsinki
description: PREDICT_API_KEY added to /root/live/btc_5m/.env on Helsinki on 2026-05-10. Live trading on Predict.fun is now unblocked from the credentials side.
type: project
originSessionId: 45cf2c48-b122-47c9-927a-f7ebcad47182
---
PREDICT_API_KEY=... was added to `/root/live/btc_5m/.env` on Helsinki on 2026-05-10 evening (Israel time). Sits alongside the existing Polymarket creds (MY_PRIVATE_KEY, MY_ADDRESS, POLYGON_RPC_URL).

**Why:** The user received the API key via Predict.fun Discord (had requested several days earlier). Without it, REST writes to Predict.fun were blocked, so live cross-platform arbitrage couldn't actually fire — the bot only simulated.

**How to apply:**
- Read with `os.environ['PREDICT_API_KEY']` (after dotenv load) when adding the writer module to the live bot.
- File mode 600, gitignored. Backup of the prior 3-key version is at `.env.bak.1778443216`.
- A typo episode happened during editing: nano session got stuck and saved as `REDICT_API_KEY` (missing P). Was rescued from `.env.save` after killing nano, sed-fixed, and reinstalled. If similar nano-stuck issues recur, look for `..env.swp` and a still-running nano PID.
- Still TODO before live Predict trading: wallet/chain setup (BNB Chain or Base, TBD), USDC funding on that chain, writer module modeled on the Polymarket Wallet class, sandbox/$1-test before real size.
