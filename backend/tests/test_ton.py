import asyncio
import base64
import json
from pathlib import Path

import httpx

from tapdesk.store import Store
from tapdesk.ton import Event, TonWatcher, advance, parse_toncenter, parse_tonapi_events
from tapdesk.tonaddr import USDT_MASTER_RAW

ADDR = "EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2N"
ME = "0:83dfd552e63729b472fcbcc8c45ebcc6691702558b68ec7527e1ba403a0f31a8"
OTHER = "0:0f1722307df4d92d35fedc6e8d5b288f3023dc9703ad76eb34be00ffc6cdf679"
T = 1_800_000_000  # the test clock's start
FAKE_JETTON = "0:e8a882a439ebd566e9267f52fc9c685c8e585ae2c7ef825f06211a538ff6f9dc"


def ton_event(eid, ts, amount, *, to=ME, frm=OTHER, scam=False, status="ok", in_progress=False):
    return {"event_id": eid, "timestamp": ts, "is_scam": scam, "in_progress": in_progress, "lt": ts * 1000,
            "actions": [{"type": "TonTransfer", "status": status, "TonTransfer": {
                "sender": {"address": frm}, "recipient": {"address": to}, "amount": amount,
                "comment": "never shown"}}]}


def jetton_event(eid, ts, amount, *, master=USDT_MASTER_RAW, symbol="USD₮"):
    return {"event_id": eid, "timestamp": ts, "is_scam": False, "in_progress": False, "lt": ts * 1000,
            "actions": [{"type": "JettonTransfer", "status": "ok", "JettonTransfer": {
                "sender": {"address": OTHER}, "recipient": {"address": ME}, "amount": str(amount),
                "jetton": {"address": master, "symbol": symbol, "decimals": 6}}}]}


def test_parse_tonapi_keeps_only_real_incoming_ton_and_usdt():
    events = parse_tonapi_events({"events": [
        ton_event("a", 100, 2_000_000_000),
        ton_event("b", 101, 1, scam=True),
        ton_event("c", 102, 5_000_000_000, to=OTHER, frm=ME),  # outgoing
        ton_event("d", 103, 5_000_000_000, status="failed"),
        jetton_event("e", 104, 1_000_000),
        jetton_event("f", 105, 9_000_000, master=FAKE_JETTON, symbol="USDT"),  # a token merely named USDT
    ]}, ME)
    assert [(e.id, e.items) for e in events] == [
        ("a", [("TON", 2.0)]), ("b", []), ("c", []), ("d", []), ("e", [("USDT", 1.0)]), ("f", [])]


def b64(hex_id: str) -> str:
    return base64.b64encode(bytes.fromhex(hex_id)).decode()


TRACE_A, TRACE_B, TRACE_C = "aa" * 32, "bb" * 32, "cc" * 32


def test_parse_toncenter_names_events_by_trace_and_skips_returned_and_notifications():
    txs = {"transactions": [
        {"hash": "h1", "trace_id": b64(TRACE_A), "now": 100, "description": {"aborted": False},
         "in_msg": {"source": OTHER.upper(), "destination": ME.upper(), "value": "3000000000", "opcode": None}},
        {"hash": "h2", "now": 101, "description": {"aborted": True},
         "in_msg": {"source": OTHER, "destination": ME, "value": "5000000000", "bounce": True}},  # sent back
        {"hash": "h2b", "now": 101, "description": {"aborted": True},
         "in_msg": {"source": OTHER, "destination": ME, "value": "1", "bounce": False}},  # credited anyway
        {"hash": "h3", "trace_id": b64(TRACE_B), "now": 102, "description": {"aborted": False},
         "in_msg": {"source": OTHER, "destination": ME, "value": "50000000", "opcode": "0x7362d09c"}},
        {"hash": "h4", "now": 103, "description": {}, "in_msg": {"source": None, "value": None}},
    ]}
    jettons = {"jetton_transfers": [
        {"transaction_hash": "j1", "trace_id": b64(TRACE_B), "transaction_now": 104, "transaction_aborted": False,
         "destination": ME.upper(), "jetton_master": USDT_MASTER_RAW.upper(), "amount": "250000000"},
        {"transaction_hash": "j2", "trace_id": b64(TRACE_C), "transaction_now": 105, "transaction_aborted": False,
         "destination": ME, "jetton_master": FAKE_JETTON, "amount": "1"},
    ]}
    events = {e.id: (e.ts, e.items) for e in parse_toncenter(txs, jettons, ME)}
    assert events == {
        TRACE_A: (100, [("TON", 3.0)]),
        "h2": (101, []),
        "h2b": (101, [("TON", 1e-9)]),  # dust: dropped later by the watcher
        TRACE_B: (102, [("USDT", 250.0)]),  # the jetton notification and the transfer are one event
        "h4": (103, []),
        TRACE_C: (105, []),
    }


def test_real_tonapi_usdt_event_parses():
    data = json.loads((Path(__file__).parent / "fixtures" / "tonapi_usdt_event.json").read_text())
    recipient = data["events"][0]["actions"][0]["JettonTransfer"]["recipient"]["address"]
    (event,) = parse_tonapi_events(data, recipient)
    assert event.items == [("USDT", 1.755)] and not event.in_progress
    assert parse_tonapi_events(data, ME)[0].items == []  # not to us


def test_cursor_first_poll_sets_without_firing_then_finds_new():
    old = [Event("a", 100), Event("b", 90)]
    cursor, new = advance(None, old, 999)
    assert new == [] and cursor == {"ts": 100, "ids": ["a"]}
    cursor, new = advance(cursor, [Event("c", 100), *old], 999)  # same second, new id
    assert [e.id for e in new] == ["c"] and set(cursor["ids"]) == {"a", "c"}
    cursor, new = advance(cursor, [Event("d", 120), Event("c", 100), *old], 999)
    assert [e.id for e in new] == ["d"] and cursor == {"ts": 120, "ids": ["d"]}
    assert advance(cursor, [Event("d", 120)], 999) == (cursor, [])


def test_cursor_never_moves_past_an_event_in_progress():
    cursor = {"ts": 100, "ids": ["a"]}
    events = [Event("late", 130), Event("pending", 110, in_progress=True), Event("early", 105)]
    cursor, new = advance(cursor, events, 999)
    assert [e.id for e in new] == ["early"] and cursor["ts"] == 105
    events[1].in_progress = False
    cursor, new = advance(cursor, events, 999)
    assert [e.id for e in new] == ["pending", "late"] and cursor["ts"] == 130


class FakeTonApi:
    def __init__(self):
        self.events = []
        self.balance = 12_500_000_000
        self.usdt = 3_000_000
        self.toncenter_txs = []
        self.tonapi_down = False
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        host, path = request.url.host, request.url.path
        self.calls.append((host, path))
        if host == "tonapi.io":
            if self.tonapi_down:
                return httpx.Response(429, json={"error": "rate limit"})
            if path.endswith("/events"):
                return httpx.Response(200, json={"events": list(reversed(self.events))})
            if path.endswith("/jettons"):
                return httpx.Response(200, json={"balances": [
                    {"balance": "77", "jetton": {"address": FAKE_JETTON, "decimals": 9}},
                    {"balance": str(self.usdt), "jetton": {"address": USDT_MASTER_RAW, "decimals": 6}}]})
            return httpx.Response(200, json={"address": ME, "balance": self.balance})
        if path.endswith("/transactions"):
            return httpx.Response(200, json={"transactions": list(reversed(self.toncenter_txs))})
        if path.endswith("/jetton/transfers"):
            return httpx.Response(200, json={"jetton_transfers": []})
        if path.endswith("/jetton/wallets"):
            return httpx.Response(200, json={"jetton_wallets": [{"balance": str(self.usdt)}]})
        return httpx.Response(200, json={"balance": str(self.balance)})


def watcher(tmp_path, api, clock, address=ADDR):
    store = Store(tmp_path / "db.sqlite", clock=clock)
    client = httpx.AsyncClient(transport=httpx.MockTransport(api))
    return TonWatcher(client, store, lambda: address, clock=clock, host_gap=0), store


def test_watcher_reports_new_arrivals_once_and_survives_restart(tmp_path, clock):
    api = FakeTonApi()
    api.events = [ton_event("old", T - 100, 9_000_000_000)]
    w, store = watcher(tmp_path, api, clock)
    seen = []

    async def listener(a):
        seen.append(a)

    w.listeners.append(listener)
    assert asyncio.run(w.poll_once()) == []  # the first poll never replays history
    assert w.wallet == {"address": ADDR, "ton": 12.5, "usdt": 3.0, "ok": True, "error": ""}

    api.events += [jetton_event("usdt1", T + 5, 1_000_000), ton_event("dust", T + 6, 1_000),
                   ton_event("spam", T + 7, 5, scam=True)]
    new = asyncio.run(w.poll_once())
    assert [(a["id"], a["symbol"], a["amount"], a["state"], a["test"]) for a in new] == [("usdt1", "USDT", 1.0, "new", False)]
    assert seen == new and [a["id"] for a in w.pending()] == ["usdt1"]

    w2, _ = watcher(tmp_path, api, clock)  # restart: same SQLite file
    assert asyncio.run(w2.poll_once()) == []
    w2.set_state("usdt1", "used")
    assert w2.pending() == []


def tc_tx(trace: str, ts: int, nanoton: int) -> dict:
    return {"hash": f"h-{trace[:4]}", "trace_id": b64(trace), "now": ts, "description": {"aborted": False},
            "in_msg": {"source": OTHER, "destination": ME, "value": str(nanoton)}}


def test_watcher_falls_back_to_toncenter_without_duplicates(tmp_path, clock):
    api = FakeTonApi()
    w, _ = watcher(tmp_path, api, clock)
    asyncio.run(w.poll_once())  # tonapi cursor set
    api.tonapi_down = True
    api.toncenter_txs = [tc_tx(TRACE_A, T - 50, 9_000_000_000)]
    clock.t += 60
    assert asyncio.run(w.poll_once()) == []  # first look at toncenter: only sets its own cursor
    assert w.wallet["ok"] and any(h == "toncenter.com" for h, _ in api.calls)

    api.toncenter_txs.append(tc_tx(TRACE_B, T + 100, 4_000_000_000))  # toncenter's clock runs later
    api.balance = 16_500_000_000
    new = asyncio.run(w.poll_once())
    assert [(a["id"], a["symbol"], a["amount"]) for a in new] == [(TRACE_B, "TON", 4.0)]
    assert w.wallet["ton"] == 16.5

    api.tonapi_down = False  # tonapi is back and reports the same transfer under the same id
    api.events = [ton_event(TRACE_B, T + 20, 4_000_000_000)]
    assert asyncio.run(w.poll_once()) == []
    assert [a["id"] for a in w.pending()] == [TRACE_B]


def test_cards_are_pushed_even_if_the_balance_call_fails(tmp_path, clock):
    api = FakeTonApi()
    w, _ = watcher(tmp_path, api, clock)
    asyncio.run(w.poll_once())
    seen = []

    async def listener(a):
        seen.append(a["id"])

    w.listeners.append(listener)
    api.events = [jetton_event("usdt2", T + 5, 2_000_000)]
    real = api.__call__

    def no_balance(request):
        if request.url.path.endswith("/events"):
            return real(request)
        return httpx.Response(503)

    w.client = httpx.AsyncClient(transport=httpx.MockTransport(no_balance))
    assert [a["id"] for a in asyncio.run(w.poll_once())] == ["usdt2"]
    assert seen == ["usdt2"] and w.wallet["ok"] is False


def test_watcher_marks_wallet_unavailable_when_everything_fails(tmp_path, clock):
    def down(request):
        return httpx.Response(503)

    w, _ = watcher(tmp_path, down, clock)
    assert asyncio.run(w.poll_once()) == []
    assert w.wallet["ok"] is False and "unavailable" in w.wallet["error"]


def test_no_address_means_no_wallet(tmp_path, clock):
    w, _ = watcher(tmp_path, FakeTonApi(), clock, address="")
    asyncio.run(w.poll_once())
    assert w.wallet is None


def test_test_arrivals_are_labelled(tmp_path, clock):
    w, _ = watcher(tmp_path, FakeTonApi(), clock)
    a = w.add_test_arrival("USDT", 1.0)
    assert a["test"] is True and a["id"].startswith("test-") and w.pending() == [a]
