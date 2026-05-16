#!/usr/bin/env python3
"""Build a single BOT_REVIEW.md file containing the request + all source."""

import os

ROOT = r"C:\Users\user\polymarket-bot"
OUT = r"C:\Users\user\Desktop\BTC_5Min_Trading\BOT_REVIEW.md"

HEADER = """# CODE REVIEW REQUEST — 3-PLATFORM CRYPTO BINARY ARB BOT

## INSTRUCTIONS FOR YOU, AI REVIEWER

Below is (1) a request for a full line-by-line code review, then (2) the
entire source of a live arbitrage bot, ~2200 lines across 7 files. Please
read everything and answer the questions in sections A-F. Be specific and
cite line numbers / function names. Where the code is good, say so. Where
it is wrong, say so directly.

---

## CONTEXT — WHAT THE BOT DOES

Three-platform arbitrage on BTC binary up/down prediction markets:
  - Polymarket  : Polygon chain, Chainlink oracle, CLOB
  - Predict.fun : BNB chain, Binance oracle, CLOB
  - Limitless   : Base chain, Chainlink oracle, CLOB

It hunts for moments when buying YES on one platform and NO on another sums
to less than $1, locking in profit on settlement. Two deployments:
  - V5_3WAY: 15-minute markets, on a Hetzner VPS in Helsinki
  - V6_3WAY: 1-hour  markets, on a Hetzner VPS in Germany

Position size: $1.20 base notional on the cheaper leg, $7 per-side cap.
Real money, ~$300 wallet.

The bot subscribes directly to all 3 WebSocket feeds, maintains a
thread-safe SharedState, and runs a detect-fire-hedge loop per window.
Stop-on-loss safety: any window closing with negative PnL writes a
.stopped file that the watchdog respects, so no auto-restart after a loss.

## RECENT CHANGE — A FRESHNESS-MODEL REWRITE WAS JUST COMPLETED

Previously the freshness gate measured `now - last_received_message_time`.
We found via 24h diagnostic this was wrong: when the orderbook is quiet,
no messages are sent, but the data is NOT stale, it is simply unchanged.
The new model trusts silence and gates instead on:
  1. Heartbeat: any message received in the last 60 seconds catches dead
     connections.
  2. Transit: the most recent message's server-timestamp-to-local-receive
     delta is below 300 ms per platform.

The bot decides "data is fresh" if both conditions hold, regardless of how
long the orderbook has been quiet.

Server timestamps exist in every platform payload:
  - Polymarket : `timestamp` field, Unix ms as string, in book / price_change
  - Predict.fun: `data.updateTimestampMs` Unix ms float.
                  NOTE: can be stale after a reconnect snapshot, so transit
                  is computed only if (now - server_ts) < 10 seconds.
  - Limitless  : ISO 8601 string `timestamp` on every orderbookUpdate

Empirical transit-latency p99 measured 13/05:
  - Limitless from Helsinki : 34 ms, median 21 ms
  - Limitless from US-East  : 78 ms, median 73 ms   (Helsinki is FASTER)
  - Polymarket from Helsinki: ~50 ms one-sample
  - Predict.fun             : ~60-80 ms one-sample
  - Polymarket geoblocks US server IPs at order endpoint, so we cannot
    relocate to US-East.

## PLEASE EVALUATE EACH OF THE FOLLOWING

### A) Correctness — line by line

Walk through the code and call out any:
  - Race conditions, off-by-ones, missing thread-safety
  - Float precision risks in price/size arithmetic
  - Order types or API parameters that are not idiomatic for each venue
  - Reconciliation gaps where one leg fills and the other fails
  - Async/sync mixing problems between WS threads and the main sync loop
  - Edge cases: market rollover, WS reconnect during fire, decimal rounding
    on FAK orders, settlement window timing

### B) Speed and competitiveness

This competes against latency-arb bots running for years. With direct WS
and direct order placement, what is the realistic detect-to-fire latency
I should expect? Where would a professional HFT firm crush us?
  - Polling rate POLL_SEC=0.2 — too slow?
  - check_freshness inside the loop — hidden cost?
  - Sequential thin-side-first vs parallel — is parallel_depth_multiplier=4x
    the right threshold?
  - Order placement async vs sync: Polymarket uses sync py-clob-client-v2
    calls; Predict and Limitless are async via wrapper. Is the sync call
    the bottleneck?

### C) The new freshness model

Critically evaluate the "trust silence" design.
  - 60-second heartbeat: too generous? Could the WS layer silently fail
    without raising disconnect, leaving the bot trading on dead data?
  - 300 ms transit cap: too tight or too loose given measured p99 of ~80 ms?
  - The "ignore server_ts when (now - server_ts) > 10s" guard for the
    Predict.fun post-reconnect stale snapshot — right approach or cleaner
    alternative?
  - The skew gate, max age difference between legs, was removed. Was that
    correct? Could the bot now fire on legs that drift apart in age?

### D) Strategy and risk

  - $7 per-side cap on ~$300 wallet: right sizing?
  - Single-trade-per-window V5 and 2-per-window V6: leaving money on the table?
  - 5% emergency-sell threshold and shortfall top-up: right hedge-completion
    strategy?
  - Cross-oracle vs same-oracle pairs are not treated differently.
    Should they be?
  - Stop-on-loss exits the process after the first losing window. Too
    conservative or about right for live capital?

### E) Improvements with outsized impact

Pick the top three changes that would most likely:
  - Increase trade frequency without raising bad-fill risk
  - Reduce time from opportunity to fill
  - Reduce missed arb edges

### F) Honest verdict

  - Is this bot competitive against fast professional arb operators on the
    same markets? If not, why, and is the gap closable?
  - Should the strategy be reframed for slower edges, 5-minute decay rather
    than 50-ms reaction?
  - Architectural changes (different runtime, Rust port, etc) that would
    matter at this strategy scale?

---

## SOURCE CODE

Sizes per file, total ~2200 lines:

  - arb_v5_3way_live.py     1311 lines  entry point for 15-min bot
  - arb_v6_3way_live.py     1312 lines  1-hour bot, 95% identical to V5
                                        (WINDOW_SEC=3600 + slug from date
                                        string) — OMITTED to save tokens.
                                        Review V5 and assume V6 same logic.
  - ws_feeds/state.py       127  lines  thread-safe SharedState with
                                        freshness fields
  - ws_feeds/poly_ws.py     197  lines  Polymarket WS client
  - ws_feeds/predict_ws.py  179  lines  Predict.fun WS client
  - ws_feeds/limitless_ws.py 173 lines  Limitless WS client, Socket.IO via
                                        limitless-sdk
  - ws_feeds/runner.py      41   lines  starts the 3 WS threads
  - check_live_bots.sh      91   lines  supervisor / watchdog with .stopped
                                        file convention

Architecture summary:

```
main thread bot
   |
   +-- starts 3 daemon WS threads via ws_feeds.runner
   |       poly_ws        ->  STATE.update("poly", ...)
   |       predict_ws     ->  STATE.update("predict", ...)
   |       limitless_ws   ->  STATE.update("lim", ...)
   |
   +-- per-window loop:
         1. fetch current market metadata for all 3 platforms in parallel
         2. inside POLL_SEC tight loop:
            - read snapshots from STATE
            - check_freshness on candidate legs
            - build cross-platform arb candidates
            - on shareable price + sufficient depth: fire orders
              - parallel fire if BOTH legs have at least 4x depth
              - else sequential thin-side-first
              - top-up from 3rd platform on shortfall
              - emergency sell excess if greater than 10%
         3. window close: snapshot wealth, log PnL, stop-on-loss if negative
```

---

"""

FILES = [
    ("arb_v5_3way_live.py — 15-min bot entry point", "arb_v5_3way_live_v3.py", "python"),
    ("ws_feeds/state.py", "ws_feeds/state.py", "python"),
    ("ws_feeds/poly_ws.py", "ws_feeds/poly_ws.py", "python"),
    ("ws_feeds/predict_ws.py", "ws_feeds/predict_ws.py", "python"),
    ("ws_feeds/limitless_ws.py", "ws_feeds/limitless_ws.py", "python"),
    ("ws_feeds/runner.py", "ws_feeds/runner.py", "python"),
    ("check_live_bots.sh", "check_live_bots.sh", "bash"),
]

with open(OUT, "w", encoding="utf-8", newline="\n") as f:
    f.write(HEADER)
    for i, (title, path, lang) in enumerate(FILES, 1):
        f.write(f"## FILE {i} of {len(FILES)}: {title}\n\n```{lang}\n")
        with open(os.path.join(ROOT, path), encoding="utf-8") as src:
            f.write(src.read())
        f.write("\n```\n\n---\n\n")
    f.write("END OF SOURCE. Please now provide your full review per sections A-F above.\n")

size = os.path.getsize(OUT)
with open(OUT, encoding="utf-8") as f:
    lines = sum(1 for _ in f)
print(f"wrote {OUT}\nsize: {size:,} bytes\nlines: {lines:,}")
