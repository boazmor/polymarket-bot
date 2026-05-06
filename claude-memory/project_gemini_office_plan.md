---
name: Gemini API key — office plan 06/05/2026
description: User going to office to register on Gemini, get API key, save it, and upload. Continue conversation from office.
type: project
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
## What user is going to do at office (06/05/2026 morning)

1. Sign up at https://www.gemini.com/ from Israel (verify Israel access)
2. Complete KYC (photo ID, selfie, proof of address) — may take 1-3 days for full approval
3. Generate API key (Account Settings → API → New Key)
4. Save key to server (NEVER commit to git)
5. Upload bot/recorder using the key

## What we need from Gemini API key

Two strings:
- **API Key (public-ish identifier)** — like `account-XXXXX`
- **API Secret (PRIVATE)** — base64-encoded secret used for signing requests

Permissions needed for our use case:
- `Trader` permission — for placing prediction-market orders
- `Auditor` permission — for reading positions and order history
- (NOT needed: Fund Manager / withdrawals)

## Where to save on Hetzner Germany

Following our existing pattern (CLAUDE.md says `.env` is gitignored):
```
/root/.env  (or /root/polybot_repo/.env)
```
Add lines:
```
GEMINI_API_KEY=account-...
GEMINI_API_SECRET=...
```
NEVER print these to logs or commit to git.

## What I should be ready to do once user gives the key

1. Verify the key works: small authenticated GET (e.g., `/v1/account` or `/v1/balances`)
2. Check balance and supported predictions instruments
3. Build a Wallet class for Gemini (mirror existing Polymarket/Kalshi wallet classes)
4. Add Gemini buy/sell methods that respect `outcome=yes|no` parameter
5. Update arb_virtual_bot to support 3-platform mode (only when user approves going beyond paper trading)

## Reference for tomorrow

- Gemini Predictions API order endpoint: `POST /v1/prediction-markets/order`
- Required body: `symbol, orderType, side (buy/sell), quantity, price, outcome (yes/no), timeInForce`
- Min: limit orders only, price 0.01-0.99, no margin/leverage
- Auth: HMAC-SHA384 of base64-encoded JSON payload using API secret

## Status of overnight stuff while user travels

- arb_virtual_bot V2 still running (Poly+Kalshi, virtual)
- gemini_btc recorder still running (data only, NOT in bot)
- All recorders running normally
- Server low on memory (111MB free of 3.8GB) — flagged but not urgent

## DO NOT

- Don't add Gemini to the live bot before user approval
- Don't print the API key in any output even when debugging
- Don't commit .env or any file containing the key
