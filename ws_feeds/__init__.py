"""ws_feeds — direct WebSocket subscriptions for the 3 arb platforms.

Each platform's WS client runs in its own thread with its own asyncio event
loop and writes orderbook updates into the SharedState dict in state.py.

The main arb bot reads from SharedState (sync, thread-safe via RLock) and
never touches asyncio or WebSocket code itself.
"""
