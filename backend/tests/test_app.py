import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from tapdesk.alerts import Alerts
from tapdesk.app import Hub, create_app
from tapdesk.paper import Paper
from tapdesk.prices import STALE_S, Prices, Quote
from tapdesk.settings import Settings
from tapdesk.store import Store
from tapdesk.ton import TonWatcher

ADDR = "EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2N"


async def fake_stt(pcm):
    if pcm[:1] == b"x":
        raise RuntimeError("offline")
    return {b"a": "Alert me if BTC drops below 57K.", b"b": "buy 100 dollars of TON"}.get(pcm[:1], "la la la")


def no_network(request):
    raise AssertionError(f"unexpected request to {request.url}")


@pytest.fixture
def env(tmp_path, clock):
    store = Store(tmp_path / "db.sqlite", clock=clock)
    store.set_settings(Settings(ton_address=ADDR, watchlist=["BTC", "ETH", "TON"]))
    client = httpx.AsyncClient(transport=httpx.MockTransport(no_network))
    prices = Prices(client, lambda: ["BTC", "TON"], clock=clock)
    prices.quotes = {
        "BTC": {"coinbase": Quote("coinbase", 58_380.0, 58_391.0, None, clock.t),
                "bybit": Quote("bybit", 58_385.0, 58_396.0, 1.2, clock.t)},
        "TON": {"coinbase": Quote("coinbase", 0.9146, 0.9157, -3.3, clock.t)},
    }
    ton = TonWatcher(client, store, lambda: store.settings().ton_address, clock=clock, host_gap=0)
    ton.wallet = {"address": ADDR, "ton": 10.0, "usdt": 5.0, "ok": True, "error": ""}
    hub = Hub(store, prices, ton, Paper(store, clock=clock), Alerts(store, clock=clock), fake_stt)
    app = create_app(hub, test_arrivals=True)
    http = TestClient(app)
    token = http.post("/pair", json={"code": store.new_pairing_code()}).json()["token"]
    return http, hub, {"Authorization": f"Bearer {token}"}, token, clock


def connect(http, token):
    ws = http.websocket_connect("/ws").__enter__()
    ws.send_json({"type": "hello", "token": token})
    first = ws.receive_json()
    assert first["type"] == "session"
    return ws, first


def recv(ws, kind):
    for _ in range(10):
        msg = ws.receive_json()
        if msg["type"] == kind:
            return msg
    raise AssertionError(f"no {kind}")


def test_healthz_says_paper_only_and_unofficial(env):
    http = env[0]
    tagline = http.get("/healthz").json()["tagline"]
    assert "Paper only" in tagline and "not affiliated" in tagline and "No financial advice" in tagline


def test_unpaired_socket_is_closed(env):
    http = env[0]
    with http.websocket_connect("/ws") as ws:
        ws.send_json({"type": "hello", "token": "nope"})
        assert ws.receive_json()["code"] == "unpaired"
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_json()
        assert exc.value.code == 4401


def test_session_has_wallet_watch_and_paper(env):
    http, hub, _, token, _ = env
    ws, s = connect(http, token)
    assert s["wallet"]["address"] == "EQCD…qB2N"
    assert s["wallet"]["valueUsd"] == pytest.approx(10 * 0.9146 + 5)
    assert [w["symbol"] for w in s["watch"]] == ["BTC", "ETH", "TON"]
    assert s["watch"][0]["ask"] == 58_391.0 and s["watch"][0]["askVenue"] == "coinbase"
    assert s["watch"][1] == {"symbol": "ETH", "missing": True}
    assert s["paper"]["cash"] == 10_000 and s["total"] == pytest.approx(10_000 + 10 * 0.9146 + 5)
    ws.close()


def test_arrival_card_buy_with_it_end_to_end(env):
    http, hub, auth, token, _ = env
    ws, _ = connect(http, token)
    r = http.post("/dev/test-arrival", json={"symbol": "USDT", "amount": 1.0}, headers=auth)
    card = recv(ws, "card")
    assert card["kind"] == "arrival" and card["symbol"] == "USDT" and card["amount"] == 1.0 and card["test"] is True
    assert card["buySymbol"] == "BTC" and card["fixedUsd"] == 250 and card["arrivalId"] == r.json()["arrival"]["id"]

    order = {"type": "confirm_order", "clientMsgId": "m1", "side": "buy", "symbol": "BTC",
             "arrivalId": card["arrivalId"], "usd": 9999, "cardId": card["id"]}  # the server ignores a client amount
    ws.send_json(order)
    res = recv(ws, "order_result")
    assert res["ok"] and res["fill"]["usd"] == pytest.approx(1.0) and res["fill"]["credited"] == pytest.approx(1.0)
    assert res["fill"]["price"] == 58_391.0 and res["venueName"] == "Coinbase"
    s = recv(ws, "session")
    assert s["paper"]["cash"] == pytest.approx(10_000) and s["paper"]["positions"][0]["symbol"] == "BTC"

    ws.send_json(order)  # a retry with the same id: same answer, no second fill
    again = recv(ws, "order_result")
    assert again["fill"] == res["fill"]
    ws.send_json({**order, "clientMsgId": "m2"})  # a new id can't reuse the transfer
    assert recv(ws, "order_result")["human"] == "That transfer was already handled"
    assert hub.cards() == []
    ws.close()


def test_ton_arrival_is_valued_at_the_best_ton_bid(env):
    http, hub, auth, token, _ = env
    ws, _ = connect(http, token)
    http.post("/dev/test-arrival", json={"symbol": "TON", "amount": 100}, headers=auth)
    card = recv(ws, "card")
    assert card["valueUsd"] == pytest.approx(91.46)
    ws.send_json({"type": "confirm_order", "clientMsgId": "t1", "side": "buy", "symbol": "BTC", "arrivalId": card["arrivalId"]})
    assert recv(ws, "order_result")["fill"]["usd"] == pytest.approx(91.46)
    ws.close()


def test_pending_cards_are_resent_on_hello_until_handled(env):
    http, hub, auth, token, _ = env
    arrival = hub.ton.add_test_arrival("USDT", 2.0)
    ws, _ = connect(http, token)
    card = recv(ws, "card")
    assert card["arrivalId"] == arrival["id"]
    assert [c["id"] for c in http.get("/cards", headers=auth).json()["cards"]] == [card["id"]]
    assert http.get("/cards").status_code == 401
    ws.send_json({"type": "cancel", "cardId": card["id"]})
    ws.send_json({"type": "open_session"})
    recv(ws, "session")
    ws.close()
    assert hub.cards() == [] and hub.ton.arrival(arrival["id"])["state"] == "ignored"


def test_stale_price_refuses_and_the_same_id_can_retry(env):
    http, hub, _, token, clock = env
    ws, _ = connect(http, token)
    clock.t += STALE_S + 1
    order = {"type": "confirm_order", "clientMsgId": "s1", "side": "buy", "symbol": "BTC", "usd": 100}
    ws.send_json(order)
    res = recv(ws, "order_result")
    assert not res["ok"] and "No fresh BTC price" in res["human"]
    for q in hub.prices.quotes["BTC"].values():
        q.ts = clock.t
    ws.send_json(order)
    assert recv(ws, "order_result")["ok"]
    ws.close()


def test_order_validation(env):
    http, hub, _, token, _ = env
    ws, _ = connect(http, token)
    for bad, human in [({"side": "hold", "symbol": "BTC", "usd": 1}, "Buy or sell?"),
                       ({"side": "buy", "symbol": "B!", "usd": 1}, "ticker"),
                       ({"side": "buy", "symbol": "BTC", "usd": 20_000}, "Not enough paper cash"),
                       ({"side": "sell", "symbol": "BTC", "fraction": 1}, "No paper BTC"),
                       ({"side": "buy", "symbol": "DOGE", "usd": 5}, "No fresh DOGE price")]:
        ws.send_json({"type": "confirm_order", "clientMsgId": f"v{human}", **bad})
        res = recv(ws, "order_result")
        assert not res["ok"] and human in res["human"]
    ws.send_json({"type": "confirm_order", "side": "buy", "symbol": "BTC", "usd": 5})
    assert recv(ws, "error")["code"] == "bad_request"
    ws.close()


def test_voice_is_parsed_but_nothing_runs_until_confirmed(env):
    http, hub, _, token, _ = env
    ws, _ = connect(http, token)
    for first, expected in [(b"a", {"type": "alert", "symbol": "BTC", "op": "below", "level": 57_000.0}),
                            (b"b", {"type": "order", "side": "buy", "symbol": "TON", "usd": 100.0})]:
        ws.send_json({"type": "audio_begin", "id": "d1"})
        ws.send_bytes(first + b"\0" * 16_000)
        ws.send_json({"type": "audio_end"})
        stt = recv(ws, "stt")
        assert stt["id"] == "d1" and {k: stt["command"][k] for k in expected} == expected
    assert hub.alerts.active() == [] and hub.paper.symbols() == []  # parsing executed nothing

    ws.send_json({"type": "set_alert", "clientMsgId": "a1", "symbol": "BTC", "op": "below", "level": 57_000})
    res = recv(ws, "alert_result")
    assert res["ok"] and res["alert"]["symbol"] == "BTC"
    assert recv(ws, "session")["alerts"][0]["level"] == 57_000
    ws.close()


def test_voice_unknown_and_offline(env):
    http, hub, _, token, _ = env
    ws, _ = connect(http, token)
    for first, human in [(b"z", "Didn't catch that"), (b"x", "Speech-to-text is offline")]:
        ws.send_json({"type": "audio_begin", "id": "d"})
        ws.send_bytes(first + b"\0" * 16_000)
        ws.send_json({"type": "audio_end"})
        stt = recv(ws, "stt")
        assert stt["command"] is None and stt["human"] == human
    ws.send_json({"type": "audio_begin", "id": "short"})
    ws.send_json({"type": "audio_end"})
    assert "Too short" in recv(ws, "stt")["human"]
    ws.close()


def test_alert_fires_once_as_a_card(env):
    http, hub, _, token, clock = env
    ws, _ = connect(http, token)
    hub.alerts.add({"symbol": "BTC", "op": "below", "level": 58_000})
    hub.prices.quotes["BTC"] = {"okx": Quote("okx", 57_900.0, 57_910.0, None, clock.t)}
    asyncio.run(hub.on_tick())
    card = recv(ws, "card")
    assert card["kind"] == "alert" and card["symbol"] == "BTC" and card["op"] == "below" and card["price"] == 57_905
    asyncio.run(hub.on_tick())
    assert recv(ws, "session")["alerts"] == []
    ws.close()


def test_settings_roundtrip_and_validation(env):
    http, hub, auth, _, _ = env
    assert http.get("/settings").status_code == 401
    got = http.get("/settings", headers=auth).json()
    assert got["tonAddress"] == ADDR and got["watchlist"] == ["BTC", "ETH", "TON"] and got["alerts"] == []
    r = http.put("/settings", headers=auth, json={"tonAddress": "EQ-typo", "watchlist": ["BTC"]})
    assert r.status_code == 400 and "TON address" in r.json()["detail"]
    r = http.put("/settings", headers=auth, json={"watchlist": ["btc", "sol"],
                                                   "alerts": [{"symbol": "ton", "op": "above", "level": 1.8}]})
    assert r.status_code == 200 and r.json()["watchlist"] == ["BTC", "SOL"] and r.json()["tonAddress"] == ADDR
    assert [a["symbol"] for a in r.json()["alerts"]] == ["TON"]
    r = http.put("/settings", headers=auth, json={"alerts": [{"symbol": "ton", "op": "sideways", "level": 1}]})
    assert r.status_code == 400 and hub.alerts.active()[0]["symbol"] == "TON"  # nothing half-saved


def test_paper_reset(env):
    http, hub, auth, token, _ = env
    ws, _ = connect(http, token)
    ws.send_json({"type": "confirm_order", "clientMsgId": "r1", "side": "buy", "symbol": "BTC", "usd": 500})
    recv(ws, "order_result")
    ws.close()
    r = http.put("/settings", headers=auth, json={"resetPaper": True})
    assert r.json()["paper"]["cash"] == 10_000 and hub.paper.symbols() == []


def test_test_arrivals_are_off_by_default(env):
    http, hub, auth, _, _ = env
    plain = TestClient(create_app(hub))
    assert plain.post("/dev/test-arrival", json={}, headers=auth).status_code in (404, 405)
