"""limitless_trader.py - Limitless Exchange Base-chain trading wrapper.

Sync wrapper around the async limitless-sdk so the rest of the arb bot can stay
sync like predict_trader.py. One persistent event loop is reused across calls.

Usage:
    from limitless_trader import LimitlessTrader
    t = LimitlessTrader(api_key, api_secret, private_key)
    res = t.place_fak_buy(market_slug, token_id, price=0.55, size_usdc=1.20)
    # -> {"order_id": "...", "filled_shares": 2.1, "paid_usdc": 1.15}

Sizing rules (caller decides):
    FAK BUY:  size = USDC notional
    FAK SELL: size = shares to sell
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from typing import Optional

from eth_account import Account
from limitless_sdk import (
    HttpClient,
    MarketFetcher,
    OrderClient,
    HMACCredentials,
)
from limitless_sdk.types import Side, OrderType


class LimitlessTrader:
    def __init__(self, api_key: str, api_secret: str, private_key: str,
                 log_path: Optional[str] = None):
        self.api_key = api_key
        self.api_secret = api_secret
        self.account = Account.from_key(private_key)
        self.address = self.account.address
        self.log_path = log_path
        self._loop = asyncio.new_event_loop()
        hmac = HMACCredentials(tokenId=api_key, secret=api_secret)
        self._http = HttpClient(api_key=api_key, hmac_credentials=hmac)
        self._fetcher = MarketFetcher(self._http)
        self._orders = OrderClient(
            http_client=self._http,
            wallet=self.account,
            market_fetcher=self._fetcher,
        )
        self._market_cache = {}
        self._log("init", address=self.address)

    def _log(self, event, **kw):
        ts = datetime.now(timezone.utc).isoformat()
        line = f"[{ts[11:19]}] LIM {event}: " + json.dumps(
            {k: v for k, v in kw.items() if k != "response"}, default=str
        )[:240]
        print(line)
        if self.log_path:
            entry = {"ts": ts, "event": event, **kw}
            try:
                with open(self.log_path, "a") as f:
                    f.write(json.dumps(entry, default=str) + "\n")
            except Exception:
                pass

    def _run(self, coro):
        return self._loop.run_until_complete(coro)

    def cache_market(self, slug: str):
        if slug in self._market_cache:
            return self._market_cache[slug]
        m = self._run(self._fetcher.get_market(slug))
        self._market_cache[slug] = m
        return m

    def get_market(self, slug):
        return self.cache_market(slug)

    def _parse_fill(self, resp, side: str, price: float) -> dict:
        """Sum matched_size across maker_matches.

        For BUY: matched_size = shares acquired; paid_usdc approximated by
                 matched_size * price (FAK fills at or better than limit).
        For SELL: matched_size = shares sold; received_usdc = matched_size * price.
        """
        filled_shares = 0.0
        if resp.maker_matches:
            for m in resp.maker_matches:
                ms = m.matched_size
                if ms is None:
                    continue
                try:
                    filled_shares += float(ms)
                except (TypeError, ValueError):
                    pass
        order_id = None
        try:
            order_id = getattr(resp.order, "salt", None)
            if order_id is not None:
                order_id = str(order_id)
        except Exception:
            order_id = None
        usdc_value = filled_shares * price
        out = {"order_id": order_id, "filled_shares": filled_shares}
        if side == "BUY":
            out["paid_usdc"] = usdc_value
        else:
            out["received_usdc"] = usdc_value
        return out

    def place_fak_buy(self, market_slug: str, token_id: str,
                      price: float, size_usdc: float) -> dict:
        """Fill-And-Kill BUY. Partial fills accepted, remainder cancelled."""
        self.cache_market(market_slug)
        self._log("buy_submit", market=market_slug, price=price, size_usdc=size_usdc, token=str(token_id)[:10])
        try:
            resp = self._run(self._orders.create_order(
                token_id=str(token_id),
                side=Side.BUY,
                order_type=OrderType.FAK,
                market_slug=market_slug,
                price=float(price),
                size=float(size_usdc),
            ))
        except Exception as e:
            self._log("buy_failed", market=market_slug, error=f"{type(e).__name__}: {e}")
            return {"error": f"{type(e).__name__}: {e}", "filled_shares": 0.0, "paid_usdc": 0.0}
        res = self._parse_fill(resp, "BUY", float(price))
        self._log("buy_ok", market=market_slug, filled=res["filled_shares"], paid=res["paid_usdc"], order_id=res["order_id"])
        return res

    def place_fak_sell(self, market_slug: str, token_id: str,
                       price: float, size_shares: float) -> dict:
        """Fill-And-Kill SELL. Partial fills accepted."""
        self.cache_market(market_slug)
        self._log("sell_submit", market=market_slug, price=price, size_shares=size_shares, token=str(token_id)[:10])
        try:
            resp = self._run(self._orders.create_order(
                token_id=str(token_id),
                side=Side.SELL,
                order_type=OrderType.FAK,
                market_slug=market_slug,
                price=float(price),
                size=float(size_shares),
            ))
        except Exception as e:
            self._log("sell_failed", market=market_slug, error=f"{type(e).__name__}: {e}")
            return {"error": f"{type(e).__name__}: {e}", "filled_shares": 0.0, "received_usdc": 0.0}
        res = self._parse_fill(resp, "SELL", float(price))
        self._log("sell_ok", market=market_slug, filled=res["filled_shares"], recv=res["received_usdc"], order_id=res["order_id"])
        return res

    async def _fak_buy_async(self, market_slug, token_id, price, size_usdc):
        """Async variant for concurrent dual-platform fire."""
        await self._fetcher.get_market(market_slug) if market_slug not in self._market_cache else None
        try:
            resp = await self._orders.create_order(
                token_id=str(token_id),
                side=Side.BUY,
                order_type=OrderType.FAK,
                market_slug=market_slug,
                price=float(price),
                size=float(size_usdc),
            )
            return self._parse_fill(resp, "BUY", float(price))
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}", "filled_shares": 0.0, "paid_usdc": 0.0}

    def cancel(self, order_id: str) -> dict:
        try:
            return self._run(self._orders.cancel(order_id))
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    def cancel_all(self, market_slug: str) -> dict:
        try:
            return self._run(self._orders.cancel_all(market_slug))
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}

    def close(self):
        try:
            self._run(self._http.close())
        except Exception:
            pass
        try:
            self._loop.close()
        except Exception:
            pass


if __name__ == "__main__":
    import sys

    def load_env(path):
        out = {}
        for ln in open(path):
            ln = ln.strip()
            if not ln or ln.startswith("#") or "=" not in ln:
                continue
            k, _, v = ln.partition("=")
            out[k.strip()] = v.strip().strip('"').strip("'")
        return out

    env = load_env("/root/live/btc_5m/.env")
    t = LimitlessTrader(
        api_key=env["LIMITLESS_API_KEY"],
        api_secret=env["LIMITLESS_API_SECRET"],
        private_key=env["MY_PRIVATE_KEY"],
    )
    slug = sys.argv[1] if len(sys.argv) > 1 else "btc-up-or-down-15-min-1778526011829"
    m = t.get_market(slug)
    print(f"market id={m.id} slug={getattr(m, 'slug', '?')}")
    print(f"  venue.exchange={getattr(m.venue, 'exchange', '?') if getattr(m, 'venue', None) else '?'}")
    tokens = getattr(m, "tokens", None) or []
    for tok in tokens:
        print(f"  token: id={getattr(tok, 'token_id', '?')} outcome={getattr(tok, 'outcome', '?')}")
    t.close()
