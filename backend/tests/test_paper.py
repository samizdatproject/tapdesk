import pytest

from tapdesk.paper import START_CASH, Paper, PaperError
from tapdesk.store import Store

BTC = {"symbol": "BTC", "bid": 58_000.0, "bidVenue": "bybit", "ask": 58_391.0, "askVenue": "coinbase", "stale": False}


@pytest.fixture
def paper(tmp_path, clock):
    return Paper(Store(tmp_path / "db.sqlite", clock=clock), clock=clock)


def prices(**symbols):
    return lambda s: symbols.get(s)


def test_starts_with_10k_paper_usdt(paper):
    s = paper.summary(prices())
    assert s["cash"] == START_CASH and s["equity"] == START_CASH and s["pnl"] == 0 and s["positions"] == []


def test_buy_usd_fills_at_best_ask_and_records_venue(paper):
    f = paper.market("o1", "buy", "BTC", BTC, usd=250)
    assert (f.side, f.price, f.venue, f.usd) == ("buy", 58_391.0, "coinbase", 250)
    assert f.qty == pytest.approx(250 / 58_391)
    s = paper.summary(prices(BTC=BTC))
    assert s["cash"] == pytest.approx(9_750)
    pos = s["positions"][0]
    assert pos["symbol"] == "BTC" and pos["avg"] == pytest.approx(58_391)
    assert pos["value"] == pytest.approx(f.qty * 58_000)  # valued at the best bid
    assert s["pnl"] == pytest.approx(pos["upnl"]) and s["pnl"] < 0


def test_same_client_id_never_fills_twice(paper):
    a = paper.market("o1", "buy", "BTC", BTC, usd=100)
    b = paper.market("o1", "buy", "BTC", {**BTC, "ask": 1.0}, usd=100)
    assert a == b and paper.summary(prices())["cash"] == pytest.approx(9_900)
    assert paper.previous("o1") == a and paper.previous("nope") is None


def test_buy_qty_and_refuses_more_than_cash(paper):
    f = paper.market("o1", "buy", "BTC", BTC, qty=0.01)
    assert f.usd == pytest.approx(583.91)
    with pytest.raises(PaperError, match="Not enough paper cash"):
        paper.market("o2", "buy", "BTC", BTC, usd=10_000)
    with pytest.raises(PaperError, match=r"\$1 or more"):
        paper.market("o3", "buy", "BTC", BTC, usd=0.5)
    assert paper.previous("o2") is None  # refused orders leave no trace, so the id may retry


def test_sell_fraction_realises_pnl(paper):
    paper.market("o1", "buy", "BTC", BTC, usd=1_000)
    up = {**BTC, "bid": 60_000.0, "bidVenue": "okx"}
    f = paper.market("o2", "sell", "BTC", up, fraction=0.5)
    assert f.venue == "okx" and f.price == 60_000.0
    s = paper.summary(prices(BTC=up))
    assert s["realised"] == pytest.approx(500 * (60_000 / 58_391) - 500)
    paper.market("o3", "sell", "BTC", up, fraction=1.0)
    s = paper.summary(prices(BTC=up))
    assert s["positions"] == [] and s["pnl"] == pytest.approx(s["realised"])


def test_sell_refusals(paper):
    with pytest.raises(PaperError, match="No paper BTC"):
        paper.market("o1", "sell", "BTC", BTC, fraction=1.0)
    paper.market("o2", "buy", "BTC", BTC, usd=100)
    with pytest.raises(PaperError, match="hold only"):
        paper.market("o3", "sell", "BTC", BTC, qty=1)
    with pytest.raises(PaperError, match="how much to buy"):
        paper.market("o4", "buy", "BTC", BTC, fraction=0.5)


def test_buy_with_a_received_transfer_credits_it_once(paper):
    f = paper.market("o1", "buy", "BTC", BTC, usd=1.0, credit=1.0, arrival_id="ev1")
    assert f.credited == 1.0 and f.usd == 1.0
    s = paper.summary(prices(BTC=BTC))
    assert s["cash"] == pytest.approx(START_CASH) and s["deposits"] == 1.0
    assert s["pnl"] == pytest.approx(s["equity"] - START_CASH - 1.0)  # a deposit isn't profit
    with pytest.raises(PaperError, match="already used"):
        paper.market("o2", "buy", "BTC", BTC, usd=1.0, credit=1.0, arrival_id="ev1")
    small = paper.market("o3", "buy", "BTC", BTC, usd=0.46, credit=0.46, arrival_id="ev2")
    assert small.usd == 0.46  # a small transfer is still bought with in full


def test_reset(paper):
    paper.market("o1", "buy", "BTC", BTC, usd=100)
    paper.reset()
    assert paper.summary(prices())["cash"] == START_CASH and paper.symbols() == []
    assert paper.market("o1", "buy", "BTC", BTC, usd=50).usd == 100  # old id still answers its first fill
