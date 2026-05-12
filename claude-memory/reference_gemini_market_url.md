---
name: דפוס קישור לשוק בודד בג'מיני פרדיקשנס
description: Gemini Predictions URL pattern for direct market deeplink. Verified 08/05.
type: reference
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
**Direct market URL pattern:**

`https://www.gemini.com/predictions/{TICKER}/{TITLE_SLUG}?categoryPath=crypto%2C15min&status=active`

**Example (BTC 15-min market starting 06:15 UTC, May 8 2026):**

`https://www.gemini.com/predictions/BTC15M2605080630/btc-price-today-at-230am-edt`

**Components:**
- `TICKER` — Internal ticker, found in our recorder's `markets.csv` under the `ticker` column. Format: `BTC15M{YY}{MMDD}{HHMM}` where HHMM is the END time of the 15-min window in UTC.
- `TITLE_SLUG` — A slugified version of the market title. Format: `btc-price-today-at-{H}{H}am-edt` or similar. The H format converts UTC end time to ET (UTC-4 in May).

**How to apply:**
- Whenever the user asks for a Gemini link, look up the current ticker from `/root/data_gemini_btc_15m/markets.csv` (column `ticker` and column `title`).
- Convert the title to a slug — lowercase, dashes for spaces, drop punctuation.
- Build the URL using the pattern above.
- The query params `?categoryPath=crypto%2C15min&status=active` are optional but match what the user gets when clicking from inside the site.
