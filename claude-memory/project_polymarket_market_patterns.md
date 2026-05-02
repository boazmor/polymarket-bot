---
name: Polymarket BTC/ETH/etc Up-or-Down market URL patterns (5 windows)
description: Slug formats for the 5 time-window markets Polymarket offers (5m, 15m, 1h, 4h, daily) — needed by the recorder
type: project
originSessionId: fe8ca768-786a-4168-98ed-e8180d93ac92
---
Discovered 2026-05-02 by probing gamma-api.polymarket.com with various slug formats.

## Five window types

| Window | Slug pattern | Example | Coin name style | Time element |
|---|---|---|---|---|
| 5 min | `{short}-updown-5m-{epoch}` | `btc-updown-5m-1777748700` | short (btc, eth) | epoch ÷ 300 |
| 15 min | `{short}-updown-15m-{epoch}` | `btc-updown-15m-1777738500` | short | epoch ÷ 900 |
| 1 hour | `{long}-up-or-down-{month}-{day}-{year}-{hour}{am/pm}-et` | `bitcoin-up-or-down-may-2-2026-12pm-et` | long (bitcoin, ethereum) | calendar+hour |
| 4 hours | `{short}-updown-4h-{epoch}` | `btc-updown-4h-1777737600` | short | epoch ÷ 14400 |
| Daily | `{long}-up-or-down-on-{month}-{day}-{year}` | `bitcoin-up-or-down-on-may-2-2026` | long | calendar date |

## Coin name mapping

| Symbol | Short (5m/15m/4h) | Long (1h/daily) |
|---|---|---|
| BTC | btc | bitcoin |
| ETH | eth | ethereum |
| SOL | sol | solana |
| XRP | xrp | xrp |
| DOGE | doge | dogecoin |
| BNB | bnb | bnb |
| HYPE | hype | hype |

## Notes
- The 1-hour and daily slugs use ENGLISH calendar names (lowercase month: jan, feb, mar, apr, may, jun, jul, aug, sep, oct, nov, dec).
- Day-of-month has NO leading zero (1, 2, 3 ... 31)
- Hour in 1-hour slug uses 12-hour format with am/pm (no leading zero on hour)
- Timezone is `et` (Eastern Time, US)
- Polymarket's "Daily" page on the UI ironically shows MONTH-spanning markets, but the slug format is per-day.

## Recording plan (as of 2026-05-02)
- All 7 coins × all 5 windows = 35 recorders eventually
- Helsinki currently runs 7 × 5min
- Germany planned for 7 × (15min + 1h + 4h + daily) = 28 recorders, once recorder code adds `--window` flag

## Recorder code requires update
`research/multi_coin/MULTI_COIN_RECORDER.py` currently hardcodes the 5m pattern. Needs a `--window` CLI flag and per-window logic for slug computation. Estimate: ~30 lines of changes. Status as of end of 2026-05-02 session: NOT YET DONE.
