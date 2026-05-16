#!/usr/bin/env python3
"""Build a single EVENT_DRIVEN_REQUEST.md file containing:
   1. A detailed code-writing request: rewrite the bot's main polling loop
      into an event-driven asyncio architecture.
   2. The full current source of the bot so the AI has everything to work with.
"""

import os

ROOT = r"C:\Users\user\polymarket-bot"
OUT = r"C:\Users\user\Desktop\BTC_5Min_Trading\EVENT_DRIVEN_REQUEST.md"

HEADER = """# CODE-WRITING REQUEST — REPLACE POLLING WITH EVENT-DRIVEN ASYNCIO

## WHAT I WANT FROM YOU

I want a complete, working rewrite of my arbitrage bot's main loop and
the 3 WebSocket clients so that the bot is fully **event-driven** instead
of polling-based. Please **write the actual code**, not just describe it.

The deliverables I expect from your reply, in order:

1. A short 1-paragraph description of the architecture you chose
   (which option among the alternatives below, and why).
2. A list of files you will produce, with their purpose.
3. **The complete, ready-to-run source** of each replacement file.
   Drop-in replacements that match the existing file structure when
   possible, so I can `scp` them onto the server and restart the bot.
4. A short "what changed and what to watch for" section at the end.

I am a non-developer relying on you to deliver runnable code. Aim for
correctness and clarity over cleverness. Match the existing patterns in
the code (function names, import style, error handling style) unless the
event-driven rewrite makes them obsolete.

---

## CURRENT ARCHITECTURE (problem to solve)

The bot trades 3-platform BTC binary arbitrage across:
  - Polymarket  (Polygon, Chainlink oracle, CLOB, py-clob-client-v2 SYNC)
  - Predict.fun (BNB, Binance oracle, CLOB, predict_sdk ASYNC)
  - Limitless   (Base, Chainlink oracle, CLOB, limitless-sdk ASYNC,
                 Socket.IO transport)

Three WebSocket client modules already exist in `ws_feeds/`, each running
in its own thread with its own asyncio event loop:
  - `ws_feeds/poly_ws.py`       (raw `websockets` library)
  - `ws_feeds/predict_ws.py`    (raw `websockets` library)
  - `ws_feeds/limitless_ws.py`  (Socket.IO via `limitless_sdk`)

They update a thread-safe shared `STATE` (`ws_feeds/state.py`) on every
inbound message. The main bot — `arb_v5_3way_live.py` (15-min) and the
nearly-identical `arb_v6_3way_live.py` (1-hour) — runs a sync polling
loop that wakes every `POLL_SEC = 0.05` seconds (50 ms), reads STATE,
runs `check_freshness` + `build_candidates` + `pick_best`, and fires
orders if conditions match.

**The problem:** polling at 50 ms means the bot adds 0-50 ms of dead
time between an orderbook update arriving on the WS thread and the bot
noticing it. Professional latency-arb bots have effectively zero delay.

**The goal:** when a WS message arrives, the bot should evaluate the
arb candidates **immediately**, not at the next polling tick.

---

## CONSTRAINTS AND CONTEXT

1. **All 3 platforms must remain integrated.** The WS feed and order
   placement for each platform must keep working.
2. **Polymarket's order client (`py-clob-client-v2`) is synchronous.**
   The call `poly_client.create_and_post_order(...)` blocks the
   calling thread for 100-300 ms. If you use asyncio, you must wrap
   it with `asyncio.to_thread()` (or `loop.run_in_executor()`) so it
   does not block the event loop. The other two clients
   (`PredictTrader`, `LimitlessTrader`) already expose async APIs.
3. **Stop-on-loss must keep working.** When a window closes with
   negative PnL, the bot writes `/root/<script-without-.py>.stopped`
   and exits. The watchdog respects this file.
4. **Freshness model unchanged.** A leg is fresh iff:
     - heartbeat: last_update_ms within 30 000 ms
     - transit: last_transit_ms <= 300 ms per platform
   See `HEARTBEAT_MAX_MS` and `TRANSIT_MAX_MS` in the bot. Keep this.
5. **Same 6 arb candidates.** See `build_candidates(p, pr, lim, ...)`
   in `arb_v5_3way_live.py`. Do not change which directions are built.
6. **Same risk caps.** `BASE_NOTIONAL_USD = 1.20`, `MAX_SIDE_USD = 7.0`,
   `EXCESS_SELL_PCT = 0.05`, `PARALLEL_DEPTH_MULTIPLIER = 2.0`.
7. **Window timing unchanged.** V5 is 15 min, V6 is 1 h. Wealth snapshot
   on open and close. Stop-on-loss on close.

---

## DESIGN ALTERNATIVES (pick one, justify in your reply)

Option A — **One asyncio event loop, everything inside it.**
  - Convert all 3 WS clients to live on the same loop.
  - The bot main is `async def main()`. Each WS client calls back into
    a single `evaluate_now()` coroutine on book change.
  - Polymarket order placement wrapped with `asyncio.to_thread()`.
  - **Pros:** truly event-driven; no GIL friction; single mental model.
  - **Cons:** biggest refactor; need to keep `limitless_sdk`'s Socket.IO
    happy alongside `websockets`.

Option B — **Keep WS threads, add a Condition/Event for signalling.**
  - Each WS thread, when a book actually changes (not just heartbeat),
    calls `arb_signal.set()` on a `threading.Event`.
  - Main thread does `arb_signal.wait(timeout=POLL_SEC)` instead of
    `time.sleep(POLL_SEC)`. On wake, run the same evaluate logic.
  - **Pros:** minimal refactor; existing thread isolation preserved.
  - **Cons:** the GIL still serialises; you save the polling 50 ms but
    not the GIL contention during order placement.

Option C — **WS threads push events to an asyncio queue.**
  - Each WS thread, on book change, calls
    `asyncio.run_coroutine_threadsafe(queue.put(event), main_loop)`.
  - Main thread is an `async def main()` that reads from the queue.
  - **Pros:** clean separation; clear event flow.
  - **Cons:** the WS threads still own their own loops; need to bridge
    cleanly.

I do not have a strong preference. Pick the one you can deliver as
working code with the lowest risk of regression. Be explicit about
which option you chose and why.

---

## NON-NEGOTIABLE ACCEPTANCE CRITERIA

Your code must:

1. Start cleanly with the same CLI invocation:
   `python3 arb_v5_3way_live.py --max-trades-per-window 1 --invest 7.0`
2. Subscribe to all 3 platform feeds and start firing within ~2 seconds.
3. On book change, evaluate arb candidates within **< 5 ms of message
   receive**, not at the next 50 ms tick.
4. Keep the stop-on-loss `.stopped` file behaviour.
5. Keep the wealth-snapshot output and per-window CSV logging
   (same headers).
6. Not deadlock when Polymarket's sync client call takes 300 ms.
7. Handle the V6 case too — produce a corresponding `arb_v6_3way_live.py`
   that is the 1-hour variant (only `WINDOW_SEC` and the Poly market
   slug format change; everything else is identical).

---

## DELIVERABLE FORMAT

In your reply:

1. **Architecture choice paragraph.**
2. **File list** (e.g. `arb_v5_3way_live.py` rewritten, `ws_feeds/state.py`
   minor changes if needed, etc.).
3. For each file, **the full source inside one fenced code block**,
   ready to copy onto the server.
4. **What changed + what to watch for** at the end — 5-10 bullets.

If you genuinely cannot produce working code without more information,
list the precise missing piece(s) — do not hand-wave or omit code.

---

## SOURCE CODE (current working version)

Sizes per file, ~2200 lines total:

  - arb_v5_3way_live.py     1316 lines  15-min entry point
  - arb_v6_3way_live.py     1316 lines  1-hour version (OMITTED — assume
                                        same logic, V5 is the template)
  - ws_feeds/state.py       128  lines  thread-safe SharedState
  - ws_feeds/poly_ws.py     197  lines  Polymarket WS client
  - ws_feeds/predict_ws.py  179  lines  Predict.fun WS client
  - ws_feeds/limitless_ws.py 173 lines  Limitless WS client (Socket.IO)
  - ws_feeds/runner.py      41   lines  starts the 3 WS threads
  - check_live_bots.sh      92   lines  supervisor / watchdog

Architecture today:

```
main thread (sync polling)
   |
   +-- starts 3 daemon WS threads via ws_feeds.runner
   |       poly_ws       ->  STATE.update("poly", ...)
   |       predict_ws    ->  STATE.update("predict", ...)
   |       limitless_ws  ->  STATE.update("lim", ...)
   |
   +-- per-window loop:
         1. fetch current market metadata for all 3 platforms in parallel
         2. inside POLL_SEC tight loop (50 ms):
            - read snapshots from STATE
            - check_freshness on candidate legs
            - build cross-platform arb candidates
            - on shareable price + sufficient depth: fire orders
              - parallel fire if BOTH legs have >= 2x depth
              - else sequential thin-side-first
              - top-up from 3rd platform on shortfall
              - emergency sell excess if > 5%
         3. window close: snapshot wealth, log PnL, stop-on-loss if negative
```

Target architecture: **same per-window logic, but step 2 is triggered by
WS message arrival, not by a timer.**

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
    f.write("END OF SOURCE. Please now produce the event-driven rewrite as described above.\n")

size = os.path.getsize(OUT)
with open(OUT, encoding="utf-8") as f:
    lines = sum(1 for _ in f)
print(f"wrote {OUT}\nsize: {size:,} bytes\nlines: {lines:,}")
