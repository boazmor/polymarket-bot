---
name: End of 05/05/2026 evening — handoff to office tomorrow morning
description: Day's work: V2 arb formula deployed, Gemini Predictions integrated as 3rd platform. Tomorrow at office, work on triangular arb. Data snapshot in repo.
type: project
originSessionId: bc5f3a4a-b82e-47e9-a5d4-d893c405e913
---
User left for evening 05/05/2026. Office session tomorrow morning needs to pick up exactly where we left off.

## What got built today

### 1. arb_virtual_bot V2 — dynamic sizing (commit d1271fd)
- Target $100/side (was $50)
- If smaller-side depth < $100: trade size = depth/2 per side
- Min: $5/side
- Cap: 15 trades per 15min market
- Cooldown: 5s between opens on same (direction, market)
- Single cost threshold ≤ 0.90 (replaced T1/T2/T3 tiers)

**Performance V2:** ~$240-300/hour virtual ROI on a 4-9 hour sample. Need 24h+ to confirm.
- Snapshot saved at: `snapshots/2026-05-05/arb_v2_results.csv` (136 trades)

### 2. Gemini Predictions integration (NEW today)
- Built `research/multi_coin/GEMINI_RECORDER.py` (NOT yet committed at handoff time)
- Recorder running in `screen -S gemini_btc` on Hetzner Germany
- Output: `/root/data_gemini_btc_15m/combined_per_second.csv`
- Captures YES bid/ask + sizes + USD depth + implied NO prices + NO depth
- Snapshot at: `snapshots/2026-05-05/gemini_btc_15m_partial.csv`

**Key finding from Gemini integration:**
Gemini binary contracts have ONE order book (YES side). Buying NO is done via `outcome=no` parameter — internally matches against yes_bid (NO ask = 1 - yes_bid). Both directions ARE buyable.

**3-platform pricing snapshot at 20:07 UTC for the same 15min window:**
| | Polymarket | Kalshi | Gemini |
|---|---|---|---|
| UP/YES ask | 0.16 | 0.26 | 0.09 |
| DOWN/NO ask | 0.85 | 0.75 | 0.92 |
| Strike | $81,610 | $81,612 | $81,634 |

**ARB opportunities at that moment:**
- PolyUP + KalshiNO = 0.91 (+9%)
- PolyDOWN + GeminiYES = 0.94 (+6%)
- **KalshiNO + GeminiYES = 0.84 (+19%)** ← biggest
- PolyUP + GeminiNO = 1.08 (loss, skip)

The Kalshi+Gemini pair is huge — disagreement on UP probability (Kalshi 26%, Gemini 9%) creates large arb window.

## TOMORROW MORNING TODO (priority order)

### 1. Pull overnight V2 results (5 min)
```
ssh hetzner "screen -S arb_virtual -X hardcopy /tmp/snap.txt && cat /tmp/snap.txt"
```
Filter trades with `open_ts >= '2026-05-05 13:48:00'` for V2-only stats.

### 2. Pull overnight Gemini data (5 min)
```
ssh hetzner "wc -l /root/data_gemini_btc_15m/combined_per_second.csv"
```
Should have 50-60k rows by 8am Israel.

### 3. **Build triangular arb analysis** — main task tomorrow
- Same time-aligned scan across 3 platforms (Polymarket + Kalshi + Gemini, BTC15M)
- Compute all 6 cross-platform pairs every second
- Find frequency of opportunities ≥10% profit
- Compare to current 2-platform (Poly+Kalshi) baseline
- Decide: add Gemini as 3rd leg in arb_virtual_bot? Or build dedicated triangular bot?

### 4. Gemini account registration (when user is ready)
- **CONFIRMED Israel-friendly** for the main exchange (Asia: Israel listed in supported countries)
- **Predictions product specifically** — geo-restriction unclear. Best test: try signing up.
- KYC: photo ID, selfie, proof of address. Verification 1-3 days.
- Even without account, our recorder works (read-only, no auth) — so trading-side question can wait.
- Sign-up link: https://www.gemini.com/

## Code state at end of day

**Committed and pushed:**
- a108564 — depth check
- d1271fd — dynamic sizing V2

**NOT yet committed (need to commit + push at handoff):**
- `research/multi_coin/GEMINI_RECORDER.py` (NEW file)
- `snapshots/2026-05-05/arb_v2_results.csv` (data)
- `snapshots/2026-05-05/gemini_btc_15m_partial.csv` (data)
- `claude-memory/project_*.md` files added today

## Running on Hetzner Germany

- `screen -S arb_virtual` — V2 arb bot, virtual mode, $100/side
- `screen -S gemini_btc` — NEW Gemini recorder for BTC 15m
- `screen -S brm` — BRM bot (live trading) — still running
- `screen -S rec_btc_15m` — Polymarket BTC 15m recorder
- `screen -S kalshi_btc` — Kalshi BTC 15m recorder
- + 35 other recorders (multi-coin, multi-window, multi-platform)

## Open user-facing questions

1. **Israel registration on Gemini** — open. Search confirmed Israel allowed for main exchange; unclear for Predictions. User to attempt sign-up when ready.
2. **Triangular vs paired arb** — design decision pending tomorrow's analysis.
3. **When to go LIVE on arb_virtual_bot** — still virtual; no live conversion until V2 has 48h+ stable data.
