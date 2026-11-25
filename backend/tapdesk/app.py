"""HTTP + WebSocket API for the glasses plugin.

The plugin pairs once with a 6-digit code printed by the backend, then sends its device
token as a Bearer header (HTTP) or in the first WebSocket message.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
from typing import Awaitable, Callable

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import commands, stt
from .alerts import Alerts
from .paper import Paper, PaperError
from .prices import VENUE_NAME, Prices
from .settings import Settings, SettingsError, clean_symbol
from .store import Store
from .ton import TonWatcher
from .tonaddr import short

Transcriber = Callable[[bytes], Awaitable[str]]

log = logging.getLogger("tapdesk.app")

TAGLINE = ("TapDesk: paper trading on Even G2 with public market data. "
           "Unofficial; not affiliated with any exchange. Paper only. No financial advice.")
HELLO_TIMEOUT_S = 10
ARRIVAL_BUY = "BTC"
ARRIVAL_FIXED_USD = 250.0


class PairBody(BaseModel):
    code: str


def _positive(v) -> float | None:
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
        return None
    return float(v)


class Hub:
    """State shared by every glasses connection: sockets and the data sources."""

    def __init__(self, store: Store, prices: Prices, ton: TonWatcher, paper: Paper, alerts: Alerts,
                 transcribe: Transcriber):
        self.store, self.prices, self.ton, self.paper, self.alerts = store, prices, ton, paper, alerts
        self.transcribe = transcribe
        self.sockets: set[WebSocket] = set()

    # views ------------------------------------------------------------------------
    def wallet(self) -> dict | None:
        w = self.ton.wallet
        if w is None:
            return None
        px = self.prices.best("TON")
        value = None
        if w.get("ton") is not None and px:
            value = w["ton"] * px["bid"] + (w.get("usdt") or 0.0)
        return {**w, "address": short(w["address"]), "valueUsd": value,
                "tonPrice": px["bid"] if px else None, "priceStale": bool(px and px["stale"])}

    def session(self) -> dict:
        prefs = self.store.settings()
        wallet = self.wallet()
        paper = self.paper.summary(self.prices.best)
        watch = [self.prices.best(s) or {"symbol": s, "missing": True} for s in prefs.watchlist]
        total = paper["equity"] + ((wallet or {}).get("valueUsd") or 0.0)
        return {"type": "session", "total": total, "wallet": wallet, "watch": watch, "paper": paper,
                "alerts": self.alerts.active()}

    def arrival_card(self, a: dict) -> dict:
        value = a["amount"]
        if a["symbol"] == "TON":
            px = self.prices.fresh("TON")
            value = a["amount"] * px["bid"] if px else None
        return {"type": "card", "kind": "arrival", "id": f"arrival:{a['id']}", "arrivalId": a["id"],
                "symbol": a["symbol"], "amount": a["amount"], "valueUsd": value, "test": a["test"],
                "buySymbol": ARRIVAL_BUY, "fixedUsd": ARRIVAL_FIXED_USD}

    @staticmethod
    def alert_card(a: dict) -> dict:
        return {"type": "card", "kind": "alert", "id": f"alert:{a['id']}", "symbol": a["symbol"], "op": a["op"],
                "level": a["level"], "price": a["price"]}

    def cards(self) -> list[dict]:
        return [self.arrival_card(a) for a in self.ton.pending()] + [self.alert_card(a) for a in self.alerts.fired()]

    def dismiss(self, card_id: str) -> None:
        kind, _, ident = str(card_id or "").partition(":")
        if kind == "arrival" and ident:
            arrival = self.ton.arrival(ident)
            if arrival and arrival["state"] == "new":
                self.ton.set_state(ident, "ignored")
        elif kind == "alert" and ident.isdigit():
            self.alerts.dismiss(int(ident))

    # pushes -----------------------------------------------------------------------
    async def broadcast(self, msg: dict) -> None:
        text = json.dumps(msg)
        for ws in list(self.sockets):
            try:
                await ws.send_text(text)
            except Exception:
                self.sockets.discard(ws)

    async def push_session(self) -> None:
        if self.sockets:
            await self.broadcast(self.session())

    async def on_arrival(self, arrival: dict) -> None:
        await self.broadcast(self.arrival_card(arrival))
        await self.push_session()

    async def on_tick(self) -> None:
        for fired in self.alerts.check(self.prices.best):
            log.info("alert fired: %s %s", fired["symbol"], fired["op"])
            await self.broadcast(self.alert_card(fired))
        await self.push_session()

    # orders -----------------------------------------------------------------------
    def place(self, msg: dict, client_id: str) -> dict:
        def fail(human: str) -> dict:
            return {"type": "order_result", "clientMsgId": client_id, "ok": False, "human": human}

        def done(fill) -> dict:
            return {"type": "order_result", "clientMsgId": client_id, "ok": True, "fill": fill.to_json(),
                    "venueName": VENUE_NAME.get(fill.venue, fill.venue)}

        prior = self.paper.previous(client_id)
        if prior:
            return done(prior)  # a retry: answer with the first fill, never fill twice
        side = msg.get("side")
        if side not in ("buy", "sell"):
            return fail("Buy or sell?")
        try:
            symbol = clean_symbol(msg.get("symbol"))
        except SettingsError as exc:
            return fail(str(exc))
        qty, usd, fraction = _positive(msg.get("qty")), _positive(msg.get("usd")), _positive(msg.get("fraction"))
        arrival_id = str(msg.get("arrivalId") or "")[:128] or None
        credit = 0.0
        if arrival_id:
            arrival = self.ton.arrival(arrival_id)
            if side != "buy" or not arrival:
                return fail("That transfer isn't known")
            if arrival["state"] != "new" or self.paper.arrival_used(arrival_id):
                return fail("That transfer was already handled")
            if arrival["symbol"] == "TON":
                px = self.prices.fresh("TON")
                if not px:
                    return fail("No fresh TON price right now. Try again.")
                credit = arrival["amount"] * px["bid"]
            else:
                credit = arrival["amount"]
            qty, usd, fraction = None, credit, None
        best = self.prices.fresh(symbol)
        if not best:
            return fail(f"No fresh {symbol} price right now. Try again.")
        try:
            fill = self.paper.market(client_id, side, symbol, best, qty=qty, usd=usd, fraction=fraction,
                                     credit=credit, arrival_id=arrival_id)
        except PaperError as exc:
            return fail(str(exc))
        if arrival_id:
            self.ton.set_state(arrival_id, "used")
        if msg.get("cardId"):
            self.dismiss(str(msg["cardId"]))
        log.info("paper %s %s filled on %s", side, symbol, fill.venue)
        return done(fill)


class Session:
    """One glasses connection: routes its messages."""

    def __init__(self, hub: Hub, ws: WebSocket):
        self.hub, self.ws = hub, ws
        self.recorder = stt.Recorder()
        self.dictation_id = ""
        self.tasks: set[asyncio.Task] = set()
        self.current: dict = {}

    async def audio(self, chunk: bytes) -> None:
        self.recorder.add(chunk)

    async def reply(self, msg: dict) -> None:
        if msg.get("type") == "error":
            msg = {**self.current, **msg}  # which request failed, so the plugin can match it
        await self.ws.send_text(json.dumps(msg))

    def close(self) -> None:
        for task in self.tasks:
            task.cancel()
        self.recorder.cancel()

    async def handle(self, msg: dict) -> None:
        kind = msg.get("type")
        self.current = {"for": kind}
        hub = self.hub
        if kind == "open_session":
            await self.reply(hub.session())
        elif kind == "confirm_order":
            client_id = str(msg.get("clientMsgId") or "")[:64]
            if not client_id:
                await self.reply({"type": "error", "code": "bad_request", "human": "Order without an id"})
                return
            result = hub.place(msg, client_id)
            await self.reply(result)
            if result["ok"]:
                await hub.push_session()
        elif kind == "set_alert":
            client_id = str(msg.get("clientMsgId") or "")[:64]
            try:
                alert = hub.alerts.add(msg, client_id or None)
            except SettingsError as exc:
                await self.reply({"type": "alert_result", "clientMsgId": client_id, "ok": False, "human": str(exc)})
                return
            await self.reply({"type": "alert_result", "clientMsgId": client_id, "ok": True, "alert": alert})
            await hub.push_session()
        elif kind == "cancel":
            hub.dismiss(str(msg.get("cardId") or ""))
        elif kind == "audio_begin":
            self.dictation_id = str(msg.get("id") or "")[:64]
            self.recorder.begin()
        elif kind == "audio_cancel":
            self.recorder.cancel()
        elif kind == "audio_end":
            pcm = self.recorder.end()
            dictation = self.dictation_id
            if pcm is None:
                await self.reply({"type": "stt", "id": dictation, "text": "", "command": None,
                                  "human": "Too short. Hold while you speak."})
                return
            # Transcribe off the receive loop so the next recording's audio isn't stuck behind it.
            task = asyncio.create_task(self._transcribe(dictation, pcm))
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)

    async def _transcribe(self, dictation: str, pcm: bytes) -> None:
        try:
            text = await self.hub.transcribe(pcm)
        except Exception as exc:
            log.warning("speech-to-text failed: %s", type(exc).__name__)
            await self.reply({"type": "stt", "id": dictation, "text": "", "command": None,
                              "human": "Speech-to-text is offline"})
            return
        hub = self.hub
        known = set(hub.store.settings().watchlist) | set(hub.paper.symbols())
        # Parsing only: nothing runs until the glasses confirm with a tap.
        command = commands.parse(text, known, lambda s: (hub.prices.best(s) or {}).get("mid")) if text else None
        human = "" if command else "Didn't catch that" if text else "Didn't hear anything"
        await self.reply({"type": "stt", "id": dictation, "text": text, "command": command, "human": human,
                          "examples": [] if command else commands.EXAMPLES})


def create_app(hub: Hub, test_arrivals: bool = False) -> FastAPI:
    app = FastAPI(title="TapDesk backend", docs_url=None, redoc_url=None, openapi_url=None)
    # The plugin runs in a WebView whose origin differs per platform; access is
    # controlled by the device token, not by origin, and no cookies are used.
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET", "POST", "PUT"],
                       allow_headers=["Authorization", "Content-Type"])
    app.state.hub = hub
    store = hub.store

    def device(authorization: str | None = Header(default=None)) -> None:
        token = authorization[7:] if authorization and authorization.startswith("Bearer ") else None
        if not store.is_device(token):
            raise HTTPException(status_code=401, detail="not paired")

    def settings_view() -> dict:
        paper = hub.paper.summary(hub.prices.best)
        return {**store.settings().to_json(), "alerts": hub.alerts.active(), "wallet": hub.wallet(),
                "paper": {k: paper[k] for k in ("cash", "equity", "pnl", "pnlPct")}}

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"ok": True, "tagline": TAGLINE}

    @app.post("/pair")
    async def pair(body: PairBody) -> dict:
        token = store.redeem_pairing_code(body.code)
        if token is None:
            raise HTTPException(status_code=401, detail="wrong or expired code")
        log.info("device paired")
        return {"token": token}

    @app.get("/settings", dependencies=[Depends(device)])
    async def get_settings() -> dict:
        return settings_view()

    @app.put("/settings", dependencies=[Depends(device)])
    async def put_settings(body: dict) -> dict:
        """Watched address, watchlist, the alert list (replaced when given) and `resetPaper`."""
        try:
            prefs = Settings.from_json({**store.settings().to_json(), **body})
            if "alerts" in body:
                hub.alerts.replace(body["alerts"])
        except SettingsError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        store.set_settings(prefs)
        if body.get("resetPaper") is True:
            hub.paper.reset()
            log.info("paper account reset")
        await hub.push_session()
        return {"ok": True, **settings_view()}

    @app.get("/cards", dependencies=[Depends(device)])
    async def get_cards() -> dict:
        """Cards still waiting for an answer on the glasses (used by scripts/demo.py to wait for an arrival)."""
        return {"cards": hub.cards()}

    if test_arrivals:
        @app.post("/dev/test-arrival", dependencies=[Depends(device)])
        async def test_arrival(body: dict) -> dict:
            """Off unless TAPDESK_TEST_ARRIVALS=1. The card is labelled as a test on the glasses."""
            symbol = str(body.get("symbol") or "USDT").upper()
            amount = _positive(body.get("amount")) or 1.0
            if symbol not in ("USDT", "TON") or amount > 100_000:
                raise HTTPException(status_code=400, detail="USDT or TON, up to 100,000")
            arrival = hub.ton.add_test_arrival(symbol, amount)
            await hub.on_arrival(arrival)
            return {"ok": True, "arrival": arrival}

    @app.websocket("/ws")
    async def ws_endpoint(ws: WebSocket) -> None:
        await ws.accept()
        session: Session | None = None
        try:
            hello = json.loads(await asyncio.wait_for(ws.receive_text(), HELLO_TIMEOUT_S))
            if not isinstance(hello, dict) or hello.get("type") != "hello" or not store.is_device(hello.get("token")):
                await ws.send_text(json.dumps({"type": "error", "code": "unpaired", "human": "Pair again on your phone"}))
                await ws.close(code=4401)
                return
            hub.sockets.add(ws)
            session = Session(hub, ws)
            await ws.send_text(json.dumps(hub.session()))
            for card in hub.cards():  # what arrived or fired while the app was closed
                await ws.send_text(json.dumps(card))
            while True:
                frame = await ws.receive()
                if frame["type"] == "websocket.disconnect":
                    break
                if frame.get("bytes") is not None:  # PCM audio for a recording in progress
                    await session.audio(frame["bytes"])
                    continue
                try:
                    msg = json.loads(frame.get("text") or "null")
                except json.JSONDecodeError:
                    continue
                try:
                    await session.handle(msg if isinstance(msg, dict) else {})
                except (WebSocketDisconnect, asyncio.CancelledError):
                    raise
                except Exception as exc:
                    log.warning("request failed: %s", type(exc).__name__)
                    await session.reply({"type": "error", "code": "failed", "human": "Something went wrong"})
        except (WebSocketDisconnect, asyncio.TimeoutError, json.JSONDecodeError):
            pass
        finally:
            hub.sockets.discard(ws)
            if session is not None:
                session.close()

    return app
