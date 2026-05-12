---
name: V6 1h virtual bot deployed and running
description: V6 is V5 BASIC's arb logic but on 1-hour BTC markets instead of 15-min. Deployed to hetzner (where 1h data lives) under screen arb_v6_1h, started 11/05 01:33 UTC.
type: project
originSessionId: 45cf2c48-b122-47c9-927a-f7ebcad47182
---
V6_1h is V5 BASIC unchanged except for data paths.

**Why:** User asked for an hour-window arb bot. V5 has only run on 15-min markets so far. Adding the 1h variant gives us a parallel sample with different settlement dynamics — fewer markets per day but potentially deeper liquidity and larger directional moves between strike and close.

**How to apply:**

Files:
- `/root/arb_virtual_bot_v6_1h.py` on hetzner — the bot itself
- `/root/arb_v6_1h_predict_trades.csv` — output trades log
- `/root/arb_v6_1h_state.json` — open-trade state
- `/root/arb_v6_1h_run.log` — stdout
- Same file in local repo: `research/multi_coin/arb_virtual_bot_v6_1h.py`

Runs under screen `arb_v6_1h` on hetzner. To check:
```
ssh hetzner "screen -ls | grep v6; tail -3 /root/arb_v6_1h_predict_trades.csv"
```

Same parameters as V5 BASIC: cost ≤ 0.90, per-leg ≤ 0.80, $100 sim invest per side. Differences only in:
- `P = /root/data_btc_1h_research/combined_per_second.csv` (Polymarket 1h)
- `PR = /root/data_predict_btc_1h/combined_per_second.csv` (Predict 1h)

Polymarket 1h slug pattern is the long calendar form: `bitcoin-up-or-down-{month}-{day}-{year}-{hour}{ampm}-et` (e.g. `bitcoin-up-or-down-may-10-2026-9pm-et`). Predict.fun uses the EXACT SAME slug for 1h markets — no separate epoch-style slug. URLs:
- https://polymarket.com/event/bitcoin-up-or-down-may-10-2026-9pm-et
- https://predict.fun/market/bitcoin-up-or-down-may-10-2026-9pm-et

To compare V5 (15m) vs V6 (1h) results: after a day of data, compare hit rate, average profit %, BOTH_LOST_DANGER frequency.

**KEY FINDING — 1h is ~15x better than 15m for arb density (backtest on 37h of joined poly+predict recordings):**
- 15m: avg cost A=1.026, avg cost B=1.028, 1,064 arb seconds (1.08% of time)
- **1h:  avg cost A=0.993, avg cost B=1.040, 11,322 arb seconds (15.91% of time)**
- Min costs on 1h: 0.470 A / 0.420 B (vs 0.560 A / 0.440 B on 15m)

Hypothesis why: 1h markets have a longer window for Predict and Polymarket pricing to drift apart. The 15m window resets so often that any divergence gets corrected quickly. The 1h window allows real arb to accumulate.

Implication: when wiring V6 to live trading, expect roughly an order of magnitude more trades than V5 LIVE — and possibly better avg profitability per trade since average cost is closer to fair.

**ETH and BNB variants added 11/05 01:47 UTC** — `arb_virtual_bot_v6_eth_1h.py` and `_bnb_1h.py` running under screens `arb_v6_eth_1h` and `arb_v6_bnb_1h` on hetzner. Predict recorders for ETH and BNB 1h were also cloned from BTC (`PREDICT_RECORDER_1H_ETH.py`, `_BNB.py`) with slug rewritten ("bitcoin-up-or-down-" → "ethereum-up-or-down-" / "bnb-up-or-down-"). Polymarket recorders started via existing `MULTI_COIN_RECORDER.py --coin ETH/BNB --window 1h`. Data dirs `/root/data_eth_1h_research`, `/root/data_predict_eth_1h`, plus BNB equivalents. Bots will start producing trade records as soon as the recorders have a few minutes of fresh data.
