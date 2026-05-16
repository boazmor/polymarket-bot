"""Predict.fun WebSocket client.

Subscribes to predictOrderbook/{market_id} topic on the public WS endpoint
and maintains top-of-book state via the SharedState container.

Wire protocol (verified from existing PREDICT_RECORDER_15M_V2.py which has
been running stable for weeks):
  - URL: wss://ws.predict.fun/ws
  - Subscribe:
      {"method": "subscribe", "requestId": <int>, "params": ["predictOrderbook/{market_id}"]}
  - Event format:
      {"type": "M",
       "topic": "predictOrderbook/{market_id}",
       "data": {"marketId": ..., "bids": [[price, size], ...], "asks": [[price, size], ...]}}
  - Heartbeat: handled by websockets library's TCP ping (ping_interval=20).
    The existing recorder has run for weeks without explicit message-level
    heartbeats, so this is sufficient in practice.
  - No authentication required for public orderbook channel.
"""

import asyncio
import json
import time
import websockets

PREDICT_WS_URL = "wss://ws.predict.fun/ws"


async def predict_ws_main(market_id_provider, state):
    """Main WS loop with reconnect.

    market_id_provider: a callable that returns the current Predict.fun
    market ID at any time. This lets the bot rotate markets every 15 min
    without restarting the WS thread; we re-subscribe whenever the active
    market ID changes.
    """
    backoff = 1.0
    max_backoff = 30.0
    req_id_counter = 1

    while True:
        current_market_id = None
        try:
            async with websockets.connect(PREDICT_WS_URL,
                                          open_timeout=10,
                                          ping_interval=20,
                                          ping_timeout=10) as ws:
                # Subscribe to the current market
                current_market_id = market_id_provider()
                if not current_market_id:
                    await asyncio.sleep(1)
                    continue
                topic = f"predictOrderbook/{current_market_id}"
                req_id_counter += 1
                sub_msg = {"method": "subscribe", "requestId": req_id_counter,
                           "params": [topic]}
                await ws.send(json.dumps(sub_msg))

                # Silent-freeze guard: expect a message (sub-ack or data)
                # within 5 seconds.
                try:
                    first = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    _process_msg(first, current_market_id, state)
                except asyncio.TimeoutError:
                    print("[predict_ws] silent freeze, reconnecting")
                    raise ConnectionError("silent_freeze_first_msg")

                backoff = 1.0

                while True:
                    # Periodically check if the market has rolled over.
                    # If so, re-subscribe to the new market_id.
                    new_market_id = market_id_provider()
                    if new_market_id and new_market_id != current_market_id:
                        # Unsubscribe old + subscribe new
                        try:
                            old_topic = f"predictOrderbook/{current_market_id}"
                            req_id_counter += 1
                            await ws.send(json.dumps({
                                "method": "unsubscribe",
                                "requestId": req_id_counter,
                                "params": [old_topic]
                            }))
                        except Exception:
                            pass
                        current_market_id = new_market_id
                        new_topic = f"predictOrderbook/{current_market_id}"
                        req_id_counter += 1
                        await ws.send(json.dumps({
                            "method": "subscribe",
                            "requestId": req_id_counter,
                            "params": [new_topic]
                        }))
                        state.update("predict", market_id=str(current_market_id))

                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=30.0)
                    except asyncio.TimeoutError:
                        continue
                    _process_msg(msg, current_market_id, state)

        except Exception as e:
            print(f"[predict_ws] error: {type(e).__name__}: {e}; reconnect in {backoff}s")
            state.mark_disconnected("predict")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)


def _process_msg(msg_str, expected_market_id, state):
    try:
        d = json.loads(msg_str)
    except Exception:
        return

    # Subscribe acks have no "type" field — ignore
    if d.get("type") != "M":
        return
    if not d.get("topic", "").startswith("predictOrderbook/"):
        return

    data = d.get("data") or {}
    market_id = data.get("marketId")
    if expected_market_id is not None and str(market_id) != str(expected_market_id):
        return  # event for a market we no longer track

    bids = data.get("bids") or []
    asks = data.get("asks") or []

    # Predict.fun format: [[price, size], ...]
    yes_bid = float(bids[0][0]) if bids else 0.0
    yes_bid_size = float(bids[0][1]) if bids else 0.0
    yes_ask = float(asks[0][0]) if asks else 0.0
    yes_ask_size = float(asks[0][1]) if asks else 0.0

    yes_ask_usd = sum(float(a[0]) * float(a[1]) for a in asks)
    yes_bid_usd = sum(float(b[0]) * float(b[1]) for b in bids)

    raw_server_ts = data.get("updateTimestampMs")
    try:
        server_ts_ms = int(raw_server_ts) if raw_server_ts is not None else 0
    except (TypeError, ValueError):
        server_ts_ms = 0

    state.update("predict",
                 best_bid=yes_bid,
                 best_ask=yes_ask,
                 bid_depth_usd=round(yes_bid_usd, 4),
                 ask_depth_usd=round(yes_ask_usd, 4),
                 no_best_ask=round(1.0 - yes_bid, 4) if yes_bid > 0 else 0,
                 no_ask_depth_usd=round(yes_bid_usd, 4),
                 server_ts_ms=server_ts_ms,
                 market_id=str(market_id),
                 connected=True)


def run_in_thread(market_id_provider, state):
    import threading

    def target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(predict_ws_main(market_id_provider, state))
        finally:
            loop.close()

    t = threading.Thread(target=target, daemon=True, name="predict_ws")
    t.start()
    return t


if __name__ == "__main__":
    import sys
    from state import STATE
    if len(sys.argv) < 2:
        print("usage: predict_ws.py <market_id>")
        sys.exit(1)
    mid = int(sys.argv[1])
    asyncio.run(predict_ws_main(lambda: mid, STATE))
