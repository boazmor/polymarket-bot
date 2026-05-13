"""Limitless Exchange WebSocket client.

Uses limitless-sdk's WebSocketClient (Socket.IO under the hood) to subscribe
to the orderbook channel for one market slug. Updates SharedState on every
orderbookUpdate event.

Why the SDK and not raw websockets:
  - Limitless uses Socket.IO protocol, not raw WS
  - The SDK handles the handshake, reconnect, and event-name mapping
  - Our limitless_trader.py already depends on the SDK, so no new dependency

Threading:
  Limitless trader uses its own asyncio loop for order placement. The WS
  client here uses a SEPARATE loop in its own thread. They never share
  the loop — order placement runs on the main thread, WS on its own.
"""

import asyncio
import time

from limitless_sdk.websocket import WebSocketClient, WebSocketConfig


async def limitless_ws_main(slug_provider, state):
    """Main WS loop with reconnect and resubscribe on market rollover.

    slug_provider: callable returning the current market slug (so we can
    re-subscribe when the 15-min/1h market rolls over).
    """
    current_slug = None
    backoff = 1.0
    max_backoff = 30.0

    while True:
        try:
            ws = WebSocketClient(WebSocketConfig(auto_reconnect=True))

            @ws.on("orderbookUpdate")
            async def on_ob(data):
                try:
                    msg_slug = data.get("marketSlug", "") if isinstance(data, dict) else ""
                    if current_slug and msg_slug and msg_slug != current_slug:
                        return  # event for a different market
                    ob = data.get("orderbook") or {} if isinstance(data, dict) else {}
                    bids = ob.get("bids") or []
                    asks = ob.get("asks") or []

                    # parse_size_to_usd-style normalization: API may return
                    # raw size_units (shares * 1e6) or already-decimal shares.
                    def to_shares(s):
                        try:
                            v = float(s)
                        except (TypeError, ValueError):
                            return 0.0
                        return v / 1e6 if v > 1e4 else v

                    if bids:
                        best_bid = float(bids[0].get("price") or 0)
                        best_bid_shares = to_shares(bids[0].get("size"))
                        best_bid_usd = best_bid * best_bid_shares
                        total_bid_usd = sum(
                            float(b.get("price") or 0) * to_shares(b.get("size"))
                            for b in bids
                        )
                    else:
                        best_bid = 0.0
                        best_bid_usd = 0.0
                        total_bid_usd = 0.0
                    if asks:
                        best_ask = float(asks[0].get("price") or 0)
                        best_ask_shares = to_shares(asks[0].get("size"))
                        best_ask_usd = best_ask * best_ask_shares
                        total_ask_usd = sum(
                            float(a.get("price") or 0) * to_shares(a.get("size"))
                            for a in asks
                        )
                    else:
                        best_ask = 0.0
                        best_ask_usd = 0.0
                        total_ask_usd = 0.0

                    no_best_ask = round(1.0 - best_bid, 4) if best_bid > 0 else 0
                    no_ask_depth = best_bid_usd  # same liquidity, complement price

                    now_ms = int(time.time() * 1000)
                    state.update("lim",
                                 best_bid=best_bid, best_ask=best_ask,
                                 bid_depth_usd=round(best_bid_usd, 4),
                                 ask_depth_usd=round(best_ask_usd, 4),
                                 no_best_ask=no_best_ask,
                                 no_ask_depth_usd=round(no_ask_depth, 4),
                                 ts_ms=now_ms,
                                 slug=msg_slug or current_slug or "",
                                 connected=True)
                except Exception as e:
                    print(f"[lim_ws] event handler error: {type(e).__name__}: {e}")

            await ws.connect()
            print("[lim_ws] connected")
            current_slug = slug_provider()
            if current_slug:
                await ws.subscribe("subscribe_market_prices",
                                   {"marketSlugs": [current_slug]})

            backoff = 1.0

            while True:
                await asyncio.sleep(5)
                # Check for market rollover
                new_slug = slug_provider()
                if new_slug and new_slug != current_slug:
                    if current_slug:
                        try:
                            await ws.unsubscribe("subscribe_market_prices",
                                                 {"marketSlugs": [current_slug]})
                        except Exception:
                            pass
                    current_slug = new_slug
                    await ws.subscribe("subscribe_market_prices",
                                       {"marketSlugs": [current_slug]})
                    state.update("lim", slug=current_slug)

                # Zombie detection: if last update > 30s ago, force reconnect.
                snap = state.get("lim")
                now_ms = int(time.time() * 1000)
                if snap.last_update_ms and (now_ms - snap.last_update_ms > 30000):
                    print("[lim_ws] zombie detected (no update >30s), reconnecting")
                    raise ConnectionError("zombie_no_data")

        except Exception as e:
            print(f"[lim_ws] error: {type(e).__name__}: {e}; reconnect in {backoff}s")
            state.mark_disconnected("lim")
            try:
                await ws.disconnect()
            except Exception:
                pass
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, max_backoff)


def run_in_thread(slug_provider, state):
    import threading

    def target():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(limitless_ws_main(slug_provider, state))
        finally:
            loop.close()

    t = threading.Thread(target=target, daemon=True, name="lim_ws")
    t.start()
    return t


if __name__ == "__main__":
    import sys
    from state import STATE
    if len(sys.argv) < 2:
        print("usage: limitless_ws.py <market_slug>")
        sys.exit(1)
    slug = sys.argv[1]
    asyncio.run(limitless_ws_main(lambda: slug, STATE))
