"""Best bid / best ask across Binance, Bybit, Coinbase and OKX, from public tickers (no keys).

Every few seconds each (venue, coin) pair is fetched once through one shared httpx client,
with at most a few requests in flight. A venue that fails keeps its last quote until it is
30 s old; then it no longer counts. A pair a venue doesn't list is skipped for a while.
Prices are in USDT. Coinbase quotes in USD, so its prices are converted with Coinbase's own
USDT-USD rate (USDT is usually a little under a dollar; without this Coinbase would always
look cheapest).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Awaitable, Callable

import httpx

log = logging.getLogger("tapdesk.prices")

VENUES = ("binance", "bybit", "coinbase", "okx")
VENUE_NAME = {"binance": "Binance", "bybit": "Bybit", "coinbase": "Coinbase", "okx": "OKX"}
VENUE_SHORT = {"binance": "bin", "bybit": "byb", "coinbase": "cb", "okx": "okx"}
# TON trades as GRAM on some venues.
ALIASES = {"binance": {"TON": "GRAM"}, "bybit": {"TON": "GRAM"}, "okx": {"TON": "GRAM"}}

STALE_S = 30
INTERVAL_S = 3.0
UNSUPPORTED_RETRY_S = 900
COINBASE_STATS_S = 60
USDT_RATE_S = 30
CONCURRENCY = 4


class Unsupported(Exception):
    """The venue doesn't list this pair."""


@dataclass
class Quote:
    venue: str
    bid: float
    ask: float
    change: float | None  # 24 h, percent
    ts: float


def _pair(venue: str, symbol: str) -> str:
    return ALIASES.get(venue, {}).get(symbol, symbol)


def _valid(bid: float, ask: float) -> tuple[float, float]:
    # A halted market answers with zeros; a stale last price is never used instead.
    if not (bid > 0 and ask > 0 and ask >= bid):
        raise ValueError("no live book")
    return bid, ask


class Prices:
    def __init__(
        self,
        client: httpx.AsyncClient,
        symbols: Callable[[], list[str]],
        clock: Callable[[], float] = time.time,
        interval: float = INTERVAL_S,
    ):
        self.client = client
        self.symbols = symbols
        self.clock = clock
        self.interval = interval
        self.quotes: dict[str, dict[str, Quote]] = {}
        self.unsupported: dict[tuple[str, str], float] = {}
        self.listeners: list[Callable[[], Awaitable[None]]] = []
        self._cb_change: dict[str, tuple[float, float | None]] = {}
        self.usdt_usd = 1.0  # dollars per USDT, from Coinbase
        self._rate_at = 0.0
        self._sem = asyncio.Semaphore(CONCURRENCY)

    # venue fetchers: (bid, ask, 24 h change %) --------------------------------------
    async def _get(self, url: str, params: dict | None = None) -> httpx.Response:
        async with self._sem:
            return await self.client.get(url, params=params, timeout=5)

    async def _binance(self, symbol: str) -> tuple[float, float, float | None]:
        r = await self._get("https://api.binance.com/api/v3/ticker/24hr", {"symbol": f"{_pair('binance', symbol)}USDT"})
        if r.status_code == 400 and r.json().get("code") == -1121:
            raise Unsupported
        r.raise_for_status()
        d = r.json()
        return (*_valid(float(d["bidPrice"]), float(d["askPrice"])), float(d["priceChangePercent"]))

    async def _bybit(self, symbol: str) -> tuple[float, float, float | None]:
        r = await self._get("https://api.bybit.com/v5/market/tickers",
                            {"category": "spot", "symbol": f"{_pair('bybit', symbol)}USDT"})
        r.raise_for_status()
        d = r.json()
        if d.get("retCode") == 10001 or (d.get("retCode") == 0 and not d["result"].get("list")):
            raise Unsupported
        if d.get("retCode") != 0:
            raise ValueError(f"bybit {d.get('retCode')}")
        t = d["result"]["list"][0]
        change = float(t["price24hPcnt"]) * 100 if t.get("price24hPcnt") not in (None, "") else None
        return (*_valid(float(t["bid1Price"]), float(t["ask1Price"])), change)

    async def _coinbase(self, symbol: str) -> tuple[float, float, float | None]:
        product = f"{_pair('coinbase', symbol)}-USD"
        r = await self._get(f"https://api.exchange.coinbase.com/products/{product}/ticker")
        if r.status_code == 404:
            raise Unsupported
        r.raise_for_status()
        d = r.json()
        bid, ask = _valid(float(d["bid"]) / self.usdt_usd, float(d["ask"]) / self.usdt_usd)
        # The ticker has no 24 h change; /stats has the open, so ask for it only once a minute.
        at, change = self._cb_change.get(symbol, (0.0, None))
        if self.clock() - at >= COINBASE_STATS_S:
            try:
                s = await self._get(f"https://api.exchange.coinbase.com/products/{product}/stats")
                s.raise_for_status()
                st = s.json()
                change = (float(st["last"]) / float(st["open"]) - 1) * 100 if float(st["open"]) > 0 else None
            except (httpx.HTTPError, KeyError, ValueError):
                pass
            self._cb_change[symbol] = (self.clock(), change)
        return bid, ask, change

    async def _okx(self, symbol: str) -> tuple[float, float, float | None]:
        r = await self._get("https://www.okx.com/api/v5/market/ticker", {"instId": f"{_pair('okx', symbol)}-USDT"})
        r.raise_for_status()
        d = r.json()
        if d.get("code") == "51001" or (d.get("code") == "0" and not d.get("data")):
            raise Unsupported
        if d.get("code") != "0":
            raise ValueError(f"okx {d.get('code')}")
        t = d["data"][0]
        opened, last = float(t.get("open24h") or 0), float(t.get("last") or 0)
        change = (last / opened - 1) * 100 if opened > 0 and last > 0 else None
        return (*_valid(float(t["bidPx"]), float(t["askPx"])), change)

    # polling ----------------------------------------------------------------------
    async def _fetch(self, venue: str, symbol: str) -> None:
        fetch = {"binance": self._binance, "bybit": self._bybit, "coinbase": self._coinbase, "okx": self._okx}[venue]
        try:
            bid, ask, change = await fetch(symbol)
        except Unsupported:
            self.unsupported[(venue, symbol)] = self.clock() + UNSUPPORTED_RETRY_S
            self.quotes.get(symbol, {}).pop(venue, None)
            return
        except (httpx.HTTPError, KeyError, ValueError, TypeError, IndexError) as exc:
            log.debug("%s %s failed: %s", venue, symbol, type(exc).__name__)
            return  # the old quote ages out after STALE_S
        self.quotes.setdefault(symbol, {})[venue] = Quote(venue, bid, ask, change, self.clock())

    async def _usdt_rate(self) -> None:
        if self.clock() - self._rate_at < USDT_RATE_S:
            return
        try:
            r = await self._get("https://api.exchange.coinbase.com/products/USDT-USD/ticker")
            r.raise_for_status()
            d = r.json()
            bid, ask = _valid(float(d["bid"]), float(d["ask"]))
            if 0.9 < (bid + ask) / 2 < 1.1:  # anything else is a broken feed, not a rate
                self.usdt_usd, self._rate_at = (bid + ask) / 2, self.clock()
        except (httpx.HTTPError, KeyError, ValueError, TypeError) as exc:
            log.debug("USDT-USD rate failed: %s", type(exc).__name__)

    async def poll_once(self) -> None:
        await self._usdt_rate()
        now = self.clock()
        jobs = [
            self._fetch(v, s)
            for s in dict.fromkeys(self.symbols())
            for v in VENUES
            if self.unsupported.get((v, s), 0) <= now
        ]
        await asyncio.gather(*jobs)
        for fn in list(self.listeners):
            try:
                await fn()
            except Exception as exc:  # a listener must never stop the poller
                log.warning("price listener failed: %s", type(exc).__name__)

    async def run(self) -> None:
        while True:
            started = time.monotonic()
            await self.poll_once()
            await asyncio.sleep(max(0.5, self.interval - (time.monotonic() - started)))

    # reading ----------------------------------------------------------------------
    def best(self, symbol: str) -> dict | None:
        """Best bid and ask over the venues with a fresh quote. `stale` when none is fresh."""
        quotes = list(self.quotes.get(symbol, {}).values())
        if not quotes:
            return None
        now = self.clock()
        fresh = [q for q in quotes if now - q.ts <= STALE_S]
        pool = fresh or quotes
        bid = max(pool, key=lambda q: q.bid)
        ask = min(pool, key=lambda q: q.ask)
        change = next((q.change for v in ("binance", "bybit", "okx", "coinbase")
                       for q in pool if q.venue == v and q.change is not None), None)
        return {
            "symbol": symbol,
            "bid": bid.bid,
            "bidVenue": bid.venue,
            "ask": ask.ask,
            "askVenue": ask.venue,
            "mid": (bid.bid + ask.ask) / 2,
            "change": change,
            "venues": len(pool),
            "stale": not fresh,
        }

    def fresh(self, symbol: str) -> dict | None:
        b = self.best(symbol)
        return b if b and not b["stale"] else None
