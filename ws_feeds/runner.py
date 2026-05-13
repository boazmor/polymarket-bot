"""Orchestrates the 3 WebSocket threads alongside the main sync bot.

Usage from arb_v5_3way_live_v3.py:

    from ws_feeds.state import STATE
    from ws_feeds.runner import start_all_feeds

    feeds = start_all_feeds(
        poly_up_tokens=[up_token],
        poly_down_tokens=[down_token],
        predict_market_id_provider=lambda: current_predict_market_id,
        limitless_slug_provider=lambda: current_lim_slug,
        state=STATE,
    )
    # main bot loop reads STATE.get(platform) / STATE.is_fresh(platform)
    # feeds is a list of Thread objects for diagnostics
"""

from ws_feeds.poly_ws import run_in_thread as poly_run
from ws_feeds.predict_ws import run_in_thread as predict_run
from ws_feeds.limitless_ws import run_in_thread as lim_run


def start_all_feeds(poly_tokens_provider,
                    predict_market_id_provider,
                    limitless_slug_provider,
                    state):
    """Start the 3 WS client threads. Each runs in its own asyncio loop
    and updates the shared `state` container.

    All three providers are callables. The main bot updates the underlying
    container as markets roll over; the WS clients re-read at each iteration
    and re-subscribe automatically.

    Returns a list of Thread objects (already started, daemon=True).
    """
    threads = []
    threads.append(poly_run(poly_tokens_provider, state))
    threads.append(predict_run(predict_market_id_provider, state))
    threads.append(lim_run(limitless_slug_provider, state))
    return threads
