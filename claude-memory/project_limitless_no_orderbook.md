---
name: Limitless NO outcome recorded (CTF complement) 12/05
description: LIMITLESS_RECORDER now writes no_best_ask, no_best_ask_size_usd, no_best_bid, no_best_bid_size_usd to latest.json and combined_per_second.csv. arb_3way_live picks all 6 candidates including B_LIM and PolyUP_LimDN.
type: project
originSessionId: 45cf2c48-b122-47c9-927a-f7ebcad47182
---
12/05/2026 ~22:30 Israel. The Limitless market is a CTF (Conditional Token Framework) binary outcome — YES and NO tokens always sum to $1. So the NO orderbook is a mathematical complement of the YES orderbook recorded already:

    no_best_ask = 1 - yes_best_bid       (someone willing to BUY yes at $X = someone offering NO at $1-X)
    no_best_bid = 1 - yes_best_ask
    liquidity carries over to the opposite side

Verified by probing all Limitless `/markets/{slug}/orderbook` URL variants — there is ONE orderbook per market that already encodes both sides via the CTF math.

**What changed (committed c41a76d):**
- `LIMITLESS_RECORDER.py` writes 6 new fields per snapshot in both CSV and latest.json: no_best_ask, no_best_ask_size_usd, no_best_ask_shares, no_best_bid, no_best_bid_size_usd, no_best_bid_shares
- Restarted recorders: rec_limitless_15m on Helsinki, rec_limitless_15m + rec_limitless_1h on Hetzner (old CSVs backed up to .bak.<epoch>)
- `arb_3way_live.py` builds full 6-candidate list (added B_LIM and PolyUP_LimDN). Restarted on Helsinki.

**Per-platform NO ordering — when bot fires it:**
- Limitless NO BUY → `place_fak_buy` with `token_id = market.tokens.no`. CTF matching transparently handles the YES_bid ↔ NO_ask cross-fill.

**Expected impact:**
Virtual V5_3WAY data showed B_LIM = 99 trades, +$11,699 (57% of 23h PnL). PolyUP_LimDN = 58 trades, +$1,154 (6%). Adding them is expected to roughly double v1's live opportunity count.

**Outstanding:**
- The existing combined_per_second.csv header on each server still has 17 columns; new rows now write 23. DictReader handles missing-old fields as None, but analyses may need pandas read_csv with `usecols` filtering or a header re-write migration.
- Limitless min_size is still empirically unconfirmed for $1.20 FAK orders.
- ConditionalTokens approval still pending; needed before any Limitless SELL leg works (only relevant for emergency-sell path).
