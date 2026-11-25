"""Watches one TON address, read-only: TON and USDT balances, and new incoming transfers.

tonapi v2 (events API) first, toncenter v3 as the fallback, both keyless, at most about one
request a second per host. Only what the cards need is stored: amount, symbol, event or tx id
and time. Comments, senders and other jettons are never stored or shown. Scam-flagged events,
failed or aborted transfers and dust are ignored.

The cursor (time and ids of the newest event seen) is kept per address and source in SQLite, so
a restart never replays old transfers; the first poll of an address only sets the cursor.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Awaitable, Callable

import httpx

from .store import Store
from .tonaddr import USDT_MASTER_RAW, to_raw

log = logging.getLogger("tapdesk.ton")

TONAPI = "https://tonapi.io/v2"
TONCENTER = "https://toncenter.com/api/v3"
INTERVAL_S = 10.0
BALANCE_EVERY_S = 30.0
MAX_BACKOFF_S = 120.0
HOST_GAP_S = 1.1  # keyless limits are about 1 request a second
DUST = {"TON": 0.01, "USDT": 0.01}
CURSOR_IDS = 20
PENDING_MAX_AGE_S = 24 * 3600
# toncenter in_msg opcodes that carry TON but are not a payment: jetton notification, excesses.
_NOT_PAYMENT = {"0x7362d09c", "0xd53276db"}


@dataclass
class Event:
    id: str
    ts: int
    in_progress: bool = False
    items: list[tuple[str, float]] = field(default_factory=list)  # incoming (symbol, amount)


class RateLimited(Exception):
    pass


def _addr(obj) -> str:
    return str((obj or {}).get("address") or "").lower() if isinstance(obj, dict) else str(obj or "").lower()


def parse_tonapi_events(data: dict, raw: str) -> list[Event]:
    out = []
    for ev in data.get("events") or []:
        e = Event(id=str(ev["event_id"]), ts=int(ev["timestamp"]), in_progress=bool(ev.get("in_progress")))
        if not ev.get("is_scam"):
            for a in ev.get("actions") or []:
                if a.get("status") != "ok":
                    continue
                if a.get("type") == "TonTransfer":
                    t = a.get("TonTransfer") or {}
                    if _addr(t.get("recipient")) == raw and _addr(t.get("sender")) != raw:
                        e.items.append(("TON", int(t["amount"]) / 1e9))
                elif a.get("type") == "JettonTransfer":
                    t = a.get("JettonTransfer") or {}
                    jetton = t.get("jetton") or {}
                    # USDT is matched by its jetton master, never by symbol (anyone can call a token USDT).
                    if (_addr(t.get("recipient")) == raw and _addr(t.get("sender")) != raw
                            and _addr(jetton) == USDT_MASTER_RAW):
                        e.items.append(("USDT", int(t["amount"]) / 10 ** int(jetton.get("decimals") or 6)))
        out.append(e)
    return out


def _trace_hex(value, fallback: str) -> str:
    """toncenter's base64 trace id is tonapi's hex event id, so both sources name an event alike."""
    try:
        return base64.b64decode(value).hex() if value else fallback
    except ValueError:
        return fallback


def parse_toncenter(txs: dict, jettons: dict, raw: str) -> list[Event]:
    events: dict[str, Event] = {}

    def event(trace, fallback: str, ts: int) -> Event:
        eid = _trace_hex(trace, fallback)
        e = events.setdefault(eid, Event(id=eid, ts=ts))
        e.ts = min(e.ts, ts)
        return e

    for t in txs.get("transactions") or []:
        e = event(t.get("trace_id"), str(t["hash"]), int(t["now"]))
        m = t.get("in_msg") or {}
        # A tiny transfer can be "aborted" in the compute phase and still credit the wallet;
        # only a bounceable one that aborted was sent back.
        returned = (t.get("description") or {}).get("aborted") and m.get("bounce")
        if (not returned and m.get("source") and m.get("value") and not m.get("bounced")
                and _addr(m.get("destination")) == raw and _addr(m.get("source")) != raw
                and (m.get("opcode") or "0x00000000") not in _NOT_PAYMENT):
            e.items.append(("TON", int(m["value"]) / 1e9))
    for j in jettons.get("jetton_transfers") or []:
        e = event(j.get("trace_id"), str(j["transaction_hash"]), int(j["transaction_now"]))
        if (not j.get("transaction_aborted") and _addr(j.get("destination")) == raw
                and _addr(j.get("jetton_master")) == USDT_MASTER_RAW):
            e.items.append(("USDT", int(j["amount"]) / 1e6))
    return list(events.values())


def advance(cursor: dict | None, events: list[Event], now: float) -> tuple[dict, list[Event]]:
    """New events since `cursor`, oldest first, and the cursor after them.

    No cursor yet (first poll of an address): nothing is new. The cursor never moves past an
    event still in progress, so it is picked up once it completes.
    """
    done = [e for e in events if not e.in_progress]
    if cursor is None:
        top = max((e.ts for e in done), default=int(now))
        return {"ts": top, "ids": [e.id for e in done if e.ts == top][-CURSOR_IDS:]}, []
    limit = min((e.ts for e in events if e.in_progress), default=float("inf"))
    seen = set(cursor.get("ids") or [])
    new, ids = [], set()
    for e in sorted(done, key=lambda e: e.ts):
        if e.ts > limit or e.id in ids:
            continue
        if e.ts > cursor["ts"] or (e.ts == cursor["ts"] and e.id not in seen):
            new.append(e)
            ids.add(e.id)
    if not new:
        return cursor, []
    top = max(cursor["ts"], max(e.ts for e in new))
    kept = list(cursor.get("ids") or []) if top == cursor["ts"] else []
    kept += [e.id for e in new if e.ts == top]
    return {"ts": top, "ids": kept[-CURSOR_IDS:]}, new


class TonWatcher:
    def __init__(
        self,
        client: httpx.AsyncClient,
        store: Store,
        address: Callable[[], str],
        clock: Callable[[], float] = time.time,
        interval: float = INTERVAL_S,
        host_gap: float = HOST_GAP_S,
    ):
        self.client, self.store, self.address, self.clock = client, store, address, clock
        self.interval, self.host_gap = interval, host_gap
        self.listeners: list[Callable[[dict], Awaitable[None]]] = []
        self.wallet: dict | None = None
        self._raw = ""
        self._balance_at = 0.0
        self._last_call: dict[str, float] = {}
        self._delay = interval
        with store.lock:
            store.db.execute(
                """CREATE TABLE IF NOT EXISTS arrivals (id TEXT PRIMARY KEY, symbol TEXT NOT NULL,
                   amount REAL NOT NULL, ts REAL NOT NULL, test INTEGER NOT NULL DEFAULT 0,
                   state TEXT NOT NULL DEFAULT 'new')"""
            )

    # HTTP ---------------------------------------------------------------------------
    async def _json(self, url: str, params: dict | None = None, missing_ok: bool = False) -> dict:
        host = httpx.URL(url).host
        wait = self._last_call.get(host, 0) + self.host_gap - time.monotonic()
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_call[host] = time.monotonic()
        r = await self.client.get(url, params=params, timeout=10)
        if r.status_code == 429:
            raise RateLimited(host)
        if missing_ok and r.status_code == 404:
            return {}
        r.raise_for_status()
        return r.json()

    async def _events(self, raw: str) -> tuple[str, list[Event]]:
        """(source, events). The two sources time events differently, so each keeps its own cursor."""
        try:
            data = await self._json(f"{TONAPI}/accounts/{raw}/events", {"limit": 10})
            return "tonapi", parse_tonapi_events(data, raw)
        except (httpx.HTTPError, RateLimited, KeyError, ValueError, TypeError) as exc:
            log.info("tonapi events failed (%s); trying toncenter", type(exc).__name__)
        txs = await self._json(f"{TONCENTER}/transactions", {"account": raw, "limit": 10, "sort": "desc"})
        jettons = await self._json(f"{TONCENTER}/jetton/transfers", {
            "owner_address": raw, "jetton_master": USDT_MASTER_RAW, "direction": "in", "limit": 10, "sort": "desc"})
        return "toncenter", parse_toncenter(txs, jettons, raw)

    async def _balances(self, raw: str) -> tuple[float, float]:
        try:
            acct = await self._json(f"{TONAPI}/accounts/{raw}", missing_ok=True)
            jet = await self._json(f"{TONAPI}/accounts/{raw}/jettons", missing_ok=True)
            usdt = next((b for b in jet.get("balances") or [] if _addr(b.get("jetton")) == USDT_MASTER_RAW), None)
            dec = int(((usdt or {}).get("jetton") or {}).get("decimals") or 6)
            return int(acct.get("balance") or 0) / 1e9, int((usdt or {}).get("balance") or 0) / 10 ** dec
        except (httpx.HTTPError, RateLimited, KeyError, ValueError, TypeError) as exc:
            log.info("tonapi balance failed (%s); trying toncenter", type(exc).__name__)
        acct = await self._json(f"{TONCENTER}/account", {"address": raw}, missing_ok=True)
        jw = await self._json(f"{TONCENTER}/jetton/wallets",
                              {"owner_address": raw, "jetton_address": USDT_MASTER_RAW, "limit": 1}, missing_ok=True)
        wallets = jw.get("jetton_wallets") or []
        return int(acct.get("balance") or 0) / 1e9, int(wallets[0]["balance"]) / 1e6 if wallets else 0.0

    # polling ------------------------------------------------------------------------
    async def poll_once(self) -> list[dict]:
        address = self.address()
        if not address:
            self.wallet, self._raw = None, ""
            return []
        raw = to_raw(address)
        if raw != self._raw:
            self._raw, self._balance_at = raw, 0.0
            self.wallet = {"address": address, "ton": None, "usdt": None, "ok": False, "error": "Loading…"}
        try:
            source, events = await self._events(raw)
            key = f"ton.cursor.{source}.{raw}"
            cursor, new = advance(self.store.get(key), events, self.clock())
            self.store.put(key, cursor)
            # Both sources use the same event ids, so a transfer seen by each is recorded (and shown) once.
            arrivals = [self._record(e.id if len(e.items) == 1 else f"{e.id}:{i}", sym, amt, e.ts)
                        for e in new for i, (sym, amt) in enumerate(e.items) if amt >= DUST[sym]]
            arrivals = [a for a in arrivals if a]
            self._delay = self.interval
        except (httpx.HTTPError, RateLimited, KeyError, ValueError, TypeError) as exc:
            log.warning("TON watch failed: %s", type(exc).__name__)
            self._unavailable(exc)
            return []
        for a in arrivals:  # cards first: a slow balance call must not hold them back
            log.info("incoming %s on the watched address", a["symbol"])
            for fn in list(self.listeners):
                try:
                    await fn(a)
                except Exception as exc:
                    log.warning("arrival listener failed: %s", type(exc).__name__)
        if new or self.clock() - self._balance_at >= BALANCE_EVERY_S:
            try:
                ton, usdt = await self._balances(raw)
                self._balance_at = self.clock()
                self.wallet = {"address": address, "ton": ton, "usdt": usdt, "ok": True, "error": ""}
            except (httpx.HTTPError, RateLimited, KeyError, ValueError, TypeError) as exc:
                log.warning("TON balance failed: %s", type(exc).__name__)
                self._unavailable(exc)
        return arrivals

    def _unavailable(self, exc: Exception) -> None:
        if self.wallet is not None:
            self.wallet = {**self.wallet, "ok": False, "error": "TON data unavailable · retrying"}
        if isinstance(exc, RateLimited):
            self._delay = min(MAX_BACKOFF_S, self._delay * 2)

    async def run(self) -> None:
        while True:
            try:
                await self.poll_once()
            except Exception as exc:  # e.g. an address that no longer parses: keep the loop alive
                log.warning("TON poll error: %s", type(exc).__name__)
            await asyncio.sleep(self._delay)

    # arrivals -----------------------------------------------------------------------
    def _record(self, arrival_id: str, symbol: str, amount: float, ts: float, test: bool = False) -> dict | None:
        with self.store.lock:
            cur = self.store.db.execute(
                "INSERT OR IGNORE INTO arrivals(id, symbol, amount, ts, test) VALUES (?, ?, ?, ?, ?)",
                (arrival_id, symbol, amount, ts, int(test)))
        if cur.rowcount == 0:
            return None  # already known
        return self.arrival(arrival_id)

    def add_test_arrival(self, symbol: str, amount: float) -> dict:
        """Only reachable when TAPDESK_TEST_ARRIVALS=1; the card says "test"."""
        return self._record(f"test-{uuid.uuid4().hex[:12]}", symbol, amount, self.clock(), test=True) or {}

    def arrival(self, arrival_id: str) -> dict | None:
        with self.store.lock:
            row = self.store.db.execute(
                "SELECT id, symbol, amount, ts, test, state FROM arrivals WHERE id = ?", (arrival_id,)).fetchone()
        if not row:
            return None
        return {"id": row[0], "symbol": row[1], "amount": row[2], "ts": row[3], "test": bool(row[4]), "state": row[5]}

    def pending(self) -> list[dict]:
        """Arrivals still waiting for a choice on the glasses (the last 24 h, newest last)."""
        with self.store.lock:
            ids = [r[0] for r in self.store.db.execute(
                "SELECT id FROM arrivals WHERE state = 'new' AND ts >= ? ORDER BY ts LIMIT 10",
                (self.clock() - PENDING_MAX_AGE_S,))]
        return [a for a in (self.arrival(i) for i in ids) if a]

    def set_state(self, arrival_id: str, state: str) -> None:
        with self.store.lock:
            self.store.db.execute("UPDATE arrivals SET state = ? WHERE id = ?", (state, arrival_id))
