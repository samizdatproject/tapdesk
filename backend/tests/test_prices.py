import asyncio

import httpx

from tapdesk.prices import STALE_S, Prices


def ticker_routes(overrides=None):
    """Public ticker answers keyed by (host, symbol), shaped like the real APIs."""
    routes = {
        ("api.binance.com", "BTCUSDT"): {"bidPrice": "58390.00", "askPrice": "58392.00", "priceChangePercent": "1.20"},
        ("api.binance.com", "GRAMUSDT"): {"bidPrice": "0.00000000", "askPrice": "0.00000000", "priceChangePercent": "0"},
        ("api.bybit.com", "BTCUSDT"): {"retCode": 0, "result": {"list": [
            {"bid1Price": "58395.1", "ask1Price": "58396.0", "price24hPcnt": "0.0110"}]}},
        ("api.bybit.com", "GRAMUSDT"): {"retCode": 10001, "retMsg": "Not supported symbols", "result": {}},
        ("api.exchange.coinbase.com", "BTC-USD"): {"bid": "58300.00", "ask": "58301.00"},
        ("api.exchange.coinbase.com", "TON-USD"): {"bid": "0.9146", "ask": "0.9157"},
        ("api.exchange.coinbase.com", "USDT-USD"): {"bid": "1.0", "ask": "1.0"},
        ("www.okx.com", "BTC-USDT"): {"code": "0", "data": [
            {"bidPx": "58380", "askPx": "58399", "open24h": "57000", "last": "58390"}]},
        ("www.okx.com", "GRAM-USDT"): {"code": "51001", "data": [], "msg": "Instrument ID doesn't exist."},
    }
    routes.update(overrides or {})
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        if path.endswith("/stats"):
            return httpx.Response(200, json={"open": "0.95", "last": "0.9129"})
        symbol = (request.url.params.get("symbol") or request.url.params.get("instId")
                  or path.split("/")[-2])
        calls.append((host, symbol))
        body = routes.get((host, symbol))
        if body is None:
            if host == "api.binance.com":
                return httpx.Response(400, json={"code": -1121, "msg": "Invalid symbol."})
            if host == "api.exchange.coinbase.com":
                return httpx.Response(404, json={"message": "NotFound"})
            return httpx.Response(500)
        if isinstance(body, Exception):
            raise body
        return httpx.Response(200, json=body)

    return handler, calls


def make(handler, clock, symbols=("BTC", "TON")):
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return Prices(client, lambda: list(symbols), clock=clock)


def test_best_bid_and_ask_across_venues(clock):
    handler, _ = ticker_routes()
    p = make(handler, clock)
    asyncio.run(p.poll_once())
    b = p.best("BTC")
    assert b["bid"] == 58395.1 and b["bidVenue"] == "bybit"
    assert b["ask"] == 58301.0 and b["askVenue"] == "coinbase"
    assert b["change"] == 1.2  # Binance first
    assert b["venues"] == 4 and not b["stale"]


def test_ton_uses_gram_pairs_and_ignores_halted_books(clock):
    handler, calls = ticker_routes()
    p = make(handler, clock)
    asyncio.run(p.poll_once())
    ton = p.best("TON")
    assert ton["venues"] == 1 and ton["bidVenue"] == ton["askVenue"] == "coinbase"  # Binance answered zeros
    assert ("api.binance.com", "GRAMUSDT") in calls and ("www.okx.com", "GRAM-USDT") in calls
    assert ("bybit", "TON") in p.unsupported and ("okx", "TON") in p.unsupported
    assert ton["change"] is not None and round(ton["change"], 1) == -3.9  # from Coinbase /stats


def test_unsupported_pairs_are_not_polled_every_tick(clock):
    handler, calls = ticker_routes()
    p = make(handler, clock)
    asyncio.run(p.poll_once())
    calls.clear()
    clock.t += 3
    asyncio.run(p.poll_once())
    assert ("api.bybit.com", "GRAMUSDT") not in calls and ("api.bybit.com", "BTCUSDT") in calls


def test_coinbase_usd_is_converted_to_usdt(clock):
    handler, _ = ticker_routes({("api.exchange.coinbase.com", "USDT-USD"): {"bid": "0.9987", "ask": "0.9987"}})
    p = make(handler, clock, ["BTC"])
    asyncio.run(p.poll_once())
    q = p.quotes["BTC"]["coinbase"]
    assert round(q.ask, 2) == round(58301.0 / 0.9987, 2)


def test_a_failing_venue_is_tolerated_and_prices_go_stale(clock):
    handler, _ = ticker_routes({("api.bybit.com", "BTCUSDT"): httpx.ConnectError("down")})
    p = make(handler, clock, ["BTC"])
    asyncio.run(p.poll_once())
    assert p.best("BTC")["venues"] == 3
    clock.t += STALE_S + 1
    stale = p.best("BTC")
    assert stale["stale"] and p.fresh("BTC") is None
    assert p.best("DOGE") is None


def test_listeners_run_after_each_poll_and_failures_are_contained(clock):
    handler, _ = ticker_routes()
    p = make(handler, clock, ["BTC"])
    seen = []

    async def bad():
        raise RuntimeError("boom")

    async def good():
        seen.append(p.best("BTC")["ask"])

    p.listeners += [bad, good]
    asyncio.run(p.poll_once())
    assert seen == [58301.0]
