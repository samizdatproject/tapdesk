import pytest

from tapdesk.alerts import MAX_ACTIVE, Alerts
from tapdesk.settings import SettingsError
from tapdesk.store import Store


@pytest.fixture
def alerts(tmp_path, clock):
    return Alerts(Store(tmp_path / "db.sqlite", clock=clock), clock=clock)


def price(mid, stale=False):
    return lambda s: {"symbol": s, "mid": mid, "stale": stale} if s == "BTC" else None


def test_fires_once_when_crossed(alerts):
    a = alerts.add({"symbol": "btc", "op": "below", "level": 57_000})
    assert a["symbol"] == "BTC" and alerts.active() == [a]
    assert alerts.check(price(57_500)) == []
    assert alerts.check(price(56_900, stale=True)) == []  # never on a stale price
    fired = alerts.check(price(56_900))
    assert [(f["id"], f["price"]) for f in fired] == [(a["id"], 56_900)]
    assert alerts.check(price(56_000)) == [] and alerts.active() == []
    assert [f["id"] for f in alerts.fired()] == [a["id"]]
    alerts.dismiss(a["id"])
    assert alerts.fired() == []


def test_above(alerts):
    alerts.add({"symbol": "BTC", "op": "above", "level": 60_000})
    assert alerts.check(price(59_999)) == []
    assert len(alerts.check(price(60_000))) == 1


def test_add_is_idempotent_by_client_id(alerts):
    a = alerts.add({"symbol": "BTC", "op": "above", "level": 1}, client_id="c1")
    assert alerts.add({"symbol": "BTC", "op": "above", "level": 1}, client_id="c1") == a
    assert len(alerts.active()) == 1


@pytest.mark.parametrize("raw", [{"symbol": "BTC", "op": "near", "level": 1}, {"symbol": "BTC", "op": "above"},
                                 {"symbol": "BTC", "op": "above", "level": -5}, {"symbol": "", "op": "above", "level": 1}])
def test_invalid_alerts(alerts, raw):
    with pytest.raises(SettingsError):
        alerts.add(raw)


def test_replace_keeps_unchanged_ids_and_caps(alerts):
    keep = alerts.add({"symbol": "BTC", "op": "above", "level": 60_000})
    alerts.add({"symbol": "ETH", "op": "below", "level": 1_500})
    out = alerts.replace([{"symbol": "BTC", "op": "above", "level": 60_000}, {"symbol": "TON", "op": "above", "level": 1.8}])
    assert [a["symbol"] for a in out] == ["BTC", "TON"] and out[0]["id"] == keep["id"]
    assert alerts.symbols() == ["BTC", "TON"]
    with pytest.raises(SettingsError, match="At most"):
        alerts.replace([{"symbol": "BTC", "op": "above", "level": i + 1} for i in range(MAX_ACTIVE + 1)])
