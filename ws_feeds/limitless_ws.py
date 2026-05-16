"""Limitless Exchange WebSocket client.

Uses limitless-sdk's WebSocketClient (Socket.IO under the hood) to subscribe
to the orderbook channel for one market slug. Updates SharedState on every
orderbookUpdate event.

Why the SDK and not raw websockets:
  - Limitless uses Socket.IO protocol, not raw WS
  - The SDK handles the handshake, reconnect, and event-name mapping
  - Our limitless_trader.py already depends on the SDK, so no new dependency

HTTP fallback (added 2026-05-15):
  WS events stopped firing on some markets even after a successful subscribe.
  The recorder masked this with HTTP polling; the bot had no fallback and
  went silent. Now we poll /markets/{slug}/orderbook every 3 sec when WS has
  not delivered a fresh update, so trading does not block on a quiet socket.
"""

import asyncio
import json
import time
import urllib.error
import urllib.request

from limitless_sdk.websocket import WebSocketClient, WebSocketConfig

API = "https://api.limitless.exchange"
HEADERS = {"User-Agent": "arb-bot/1.0"}


def _to_shares(s):
    try:
        v = float(s)
    except (TypeError, ValueError):
        return 0.0
    return v / 1e6 if v > 1e4 else v


def _update_state_from_ob(state, ob, slug, server_ts_ms=0):
    """Parse an orderbook dict and push it to SharedState['lim'].

    Used by both the WS event handler and the HTTP fallback.
    """
    bids = (ob.get("bids") if isinstance(ob, dict) else None) or []
    asks = (ob.get("asks") if isinstance(ob, dict) else None) or []

    if bids:
        best_bid = float(bids[0].get("price") or 0)
        best_bid_shares = _to_shares(bids[0].get("size"))
        best_bid_usd = best_bid * best_bid_shares
    else:
        best_bid = 0.0
        best_bid_usd = 0.0
    if asks:
        best_ask = float(asks[0].get("price") or 0)
        best_ask_shares = _to_shares(asks[0].get("size"))
        best_ask_usd = best_ask * best_ask_shares
    else:
        best_ask = 0.0
        best_ask_usd = 0.0

    no_best_ask = round(1.0 - best_bid, 4) if best_bid > 0 else 0
    no_ask_depth = best_bid_usd  # complement side carries same liquidity

    state.update("lim",
                 best_bid=best_bid, best_ask=best_ask,
                 bid_depth_usd=round(best_bid_usd, 4),
                 ask_depth_usd=round(best_ask_usd, 4),
                 no_best_ask=no_best_ask,
                 no_ask_depth_usd=round(no_ask_depth, 4),
                 server_ts_ms=server_ts_ms,
                 slug=slug or "",
                 connected=True)


def _http_orderbook(slug, timeout=4):
    """Synchronous HTTP fetch of orderbook for one slug. Returns dict or None."""
    url = f"{API}/markets/{slug}/orderbook"
    req = urllib.request.Request(url, headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return None
            return json.loads(r.read())
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, OSError):
        return None
    except Exception:
        return None


async def limitless_ws_main(slug_provider, state):
    """Main WS loop with reconnect and resubscribe on market rollover.

    slug_provider: callable returning the current market slug (so we can
    re-subscribe when the 15-min/1h market rolls over).
    """
    current_slug = None
    backoff = 1.0
    max_backoff = 30.0
    last_http_poll_ms = 0
    HTTP_POLL_GAP_MS = 3000  # if WS quiet >3s, fall back to HTTP every 3s

    while True:
        try:
            ws = WebSocketClient(WebSocketConfig(auto_reconnect=True))

            @ws.on("orderbookUpdate")
            async def on_ob(data):
                try:
                    if not isinstance(data, dict):
                        return
                    msg_slug = data.get("marketSlug", "")
                    if current_slug and msg_slug and msg_slug != current_slug:
                        return
                    ob = data.get("orderbook") or {}
                    server_ts_ms = 0
                    iso_ts = data.get("timestamp")
                    if iso_ts:
                        try:
                            from datetime import datetime as _dt
                            server_ts_ms = int(_dt.fromisoformat(
                                iso_ts.replace("Z", "+00:00")).timestamp() * 1000)
                        except Exception:
                            server_ts_ms = 0
                    _update_state_from_ob(
                        state, ob,
                        slug=msg_slug or current_slug or "",
                        server_ts_ms=server_ts_ms,
                    )
                except Exception as e:
                    print(f"[lim_ws] event handler error: {type(e).__name__}: {e}")

            await ws.connect()
            print("[lim_ws] connected")
            current_slug = slug_provider()
            if current_slug:
                await ws.subscribe("subscribe_market_prices",
                                   {"marketSlugs": [current_slug]})

            backoff = 1.0
            loop = asyncio.get_event_loop()

            while True:
                await asyncio.sleep(1)
                # Check for market rollover (slug may also become None when
                # the bot detects no active market yet — unsubscribe so we
                # stop polling an expired slug and pump zombie reconnects).
                new_slug = slug_provider()
                if new_slug != current_slug:
                    if current_slug:
                        try:
                            await ws.unsubscribe("subscribe_market_prices",
                                                 {"marketSlugs": [current_slug]})
                        except Exception:
                            pass
                    current_slug = new_slug
                    if current_slug:
                        await ws.subscribe("subscribe_market_prices",
                                           {"marketSlugs": [current_slug]})
                        state.update("lim", slug=current_slug)
                        last_http_poll_ms = 0  # force immediate HTTP refresh
                    else:
                        # No active slug — pause HTTP polling and zombie checks
                        state.mark_disconnected("lim")

                now_ms = int(time.time() * 1000)
                snap = state.get("lim")

                # HTTP fallback: WS has not pushed fresh data within HTTP_POLL_GAP_MS
                quiet_ms = now_ms - (snap.last_update_ms or 0)
                if current_slug and quiet_ms > HTTP_POLL_GAP_MS and \
                   now_ms - last_http_poll_ms >= HTTP_POLL_GAP_MS:
                    last_http_poll_ms = now_ms
                    try:
                        ob = await loop.run_in_executor(
                            None, _http_orderbook, current_slug)
                    except Exception as e:
                        ob = None
                        print(f"[lim_ws] http poll error: {type(e).__name__}: {e}")
                    if ob:
                        _update_state_from_ob(state, ob, slug=current_slug,
                                              server_ts_ms=now_ms)

                # Zombie reconnect only if BOTH WS and HTTP are silent >60s.
                # HTTP fallback usually keeps last_update_ms fresh, so this
                # only trips on a true outage.
                snap = state.get("lim")
                quiet_ms = int(time.time() * 1000) - (snap.last_update_ms or 0)
                if snap.last_update_ms and quiet_ms > 60000:
                    print(f"[lim_ws] true outage ({quiet_ms}ms quiet), reconnecting")
                    raise ConnectionError("ws_and_http_silent")

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
