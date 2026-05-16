"""Polymarket CLOB WebSocket client.

Subscribes to the `market` channel for specified token IDs and maintains
top-of-book state via the SharedState container.

Wire protocol (verified from py-clob-client + Polymarket docs):
  - URL: wss://ws-subscriptions-clob.polymarket.com/ws/market
  - Subscribe message:
      {"type": "MARKET", "markets": [token_id1, token_id2, ...]}
    Older docs use "assets_ids" — the v2 endpoint accepts "markets" too.
  - Event types received:
      "book"          full snapshot (on subscribe + on rollover)
      "price_change"  individual level update
      "tick_size_change" rare — recalibrate
  - No authentication required for public orderbook channel.

KNOWN BUG (silent freeze): the server occasionally accepts the connection
and subscription but never sends a book event. We detect this by timing
out on the first message after subscribe (5s) and forcing a reconnect.
"""

import asyncio
import json
import time
import websockets

POLY_WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


async def _handle_message(msg_str, token_to_side, state, platform="poly"):
    """Parse a single WS message and update SharedState.

    token_to_side maps each subscribed token_id to "up" or "down" so we know
    which side of the market the update affects.
    """
    try:
        data = json.loads(msg_str)
    except Exception:
        return

    # Polymarket sends list-wrapped messages in some channels
    if isinstance(data, list):
        for entry in data:
            await _handle_one(entry, token_to_side, state, platform)
    else:
        await _handle_one(data, token_to_side, state, platform)


async def _handle_one(entry, token_to_side, state, platform):
    event_type = entry.get("event_type")
    asset_id = entry.get("asset_id") or entry.get("market") or entry.get("token_id")
    side = token_to_side.get(str(asset_id))
    if not side:
        return  # event for a token we don't track

    server_ts = entry.get("timestamp")
    try:
        server_ts_ms = int(server_ts) if server_ts is not None else 0
    except (TypeError, ValueError):
        server_ts_ms = 0

    if event_type == "book":
        bids = entry.get("bids") or []
        asks = entry.get("asks") or []
        if side == "up":
            best_bid = float(bids[0]["price"]) if bids else 0.0
            best_ask = float(asks[0]["price"]) if asks else 0.0
            bid_depth = float(bids[0]["size"]) * best_bid if bids else 0.0
            ask_depth = float(asks[0]["size"]) * best_ask if asks else 0.0
            # DOWN side via CTF complement of UP bid: anyone willing to BUY UP
            # at price X implies someone selling DOWN at price (1 - X).
            no_best_ask = round(1.0 - best_bid, 4) if best_bid > 0 else 0.0
            state.update(platform,
                         best_bid=best_bid, best_ask=best_ask,
                         bid_depth_usd=bid_depth, ask_depth_usd=ask_depth,
                         no_best_ask=no_best_ask,
                         no_ask_depth_usd=bid_depth,
                         server_ts_ms=server_ts_ms, connected=True)

    elif event_type == "price_change":
        changes = entry.get("changes") or [entry]
        for ch in changes:
            try:
                price = float(ch.get("price", 0))
                size = float(ch.get("size", 0))
                ch_side = (ch.get("side") or "").upper()
            except (TypeError, ValueError):
                continue
            book = state.get(platform)
            if side == "up":
                if ch_side == "SELL" and abs(price - book.best_ask) < 1e-4:
                    state.update(platform,
                                 ask_depth_usd=size * price,
                                 server_ts_ms=server_ts_ms, connected=True)
                elif ch_side == "BUY" and abs(price - book.best_bid) < 1e-4:
                    new_bid_depth = size * price
                    state.update(platform,
                                 bid_depth_usd=new_bid_depth,
                                 no_ask_depth_usd=new_bid_depth,
                                 server_ts_ms=server_ts_ms, connected=True)


async def poly_ws_main(tokens_provider, state):
    """Main WS loop with reconnect and silent-freeze detection.

    tokens_provider: callable returning (up_tokens, down_tokens) tuples.
    Called at every connect AND between message receives, so when the
    15-min market rolls over we automatically re-subscribe.

    Backwards-compat: if tokens_provider is a list-pair tuple, treat it
    as static.
    """
    backoff = 1.0
    max_backoff = 30.0

    def get_tokens():
        if callable(tokens_provider):
            up, down = tokens_provider()
            return list(up or []), list(down or [])
        # Static legacy form: (up_list, down_list)
        return list(tokens_provider[0]), list(tokens_provider[1])

    while True:
        try:
            up_tokens, down_tokens = get_tokens()
            if not up_tokens or not down_tokens:
                # Markets not loaded yet — wait
                await asyncio.sleep(1)
                continue
            all_tokens = up_tokens + down_tokens
            token_to_side = {}
            for t in up_tokens:
                token_to_side[str(t)] = "up"
            for t in down_tokens:
                token_to_side[str(t)] = "down"

            async with websockets.connect(POLY_WS_URL,
                                          ping_interval=20,
                                          ping_timeout=10) as ws:
                # Official Polymarket CLOB market-channel subscribe format
                sub_msg = {
                    "assets_ids": [str(t) for t in all_tokens],
                    "type": "market",
                    "custom_feature_enabled": True,
                }
                await ws.send(json.dumps(sub_msg))
                current_up = up_tokens[0]

                # Silent-freeze guard: expect a book event within 5s.
                # If not, force reconnect.
                try:
                    first = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    await _handle_message(first, token_to_side, state)
                except asyncio.TimeoutError:
                    print("[poly_ws] silent freeze on first message, reconnecting")
                    raise ConnectionError("silent_freeze_first_msg")

                backoff = 1.0  # successful connect
                async for msg in ws:
                    await _handle_message(msg, token_to_side, state)
                    # Check for market rollover — re-subscribe if token set
                    # changed. We compare the first up_token only since the
                    # bot uses one market per window.
                    if callable(tokens_provider):
                        new_up, new_down = tokens_provider()
                        if new_up and new_up[0] != current_up:
                            print(f"[poly_ws] market rollover detected; reconnecting")
                            break  # exit inner loop, reconnect with new tokens
        except Exception as e:
            print(f"[poly_ws] error: {type(e).__name__}: {e}; reconnect in {backoff}s")
            state.mark_disconnected("poly")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)


def run_in_thread(tokens_provider, state):
    """Helper for runner.py to start this client in a dedicated thread.

    tokens_provider: callable returning (up_tokens_list, down_tokens_list).
    """
    import threading

    def target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(poly_ws_main(tokens_provider, state))
        finally:
            loop.close()

    t = threading.Thread(target=target, daemon=True, name="poly_ws")
    t.start()
    return t


if __name__ == "__main__":
    # Standalone test: pass two token IDs as CLI args
    import sys
    from state import STATE
    if len(sys.argv) < 3:
        print("usage: poly_ws.py <up_token> <down_token>")
        sys.exit(1)
    up_t, down_t = sys.argv[1], sys.argv[2]
    asyncio.run(poly_ws_main(lambda: ([up_t], [down_t]), STATE))
