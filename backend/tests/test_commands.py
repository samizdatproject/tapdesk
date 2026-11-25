import pytest

from tapdesk.commands import parse, tokenize


@pytest.mark.parametrize("text,expected", [
    ("alert me if BTC drops below 57k", {"type": "alert", "symbol": "BTC", "op": "below", "level": 57_000}),
    ("Alert me if BTC drops below 57K.", {"type": "alert", "symbol": "BTC", "op": "below", "level": 57_000}),
    ("alert me if bitcoin drops below 57,000", {"type": "alert", "symbol": "BTC", "op": "below", "level": 57_000}),
    ("Alert me if Bitcoin falls under fifty seven thousand.",
     {"type": "alert", "symbol": "BTC", "op": "below", "level": 57_000}),
    ("alert when ton goes above 1.8", {"type": "alert", "symbol": "TON", "op": "above", "level": 1.8}),
    ("Notify me when toncoin goes over one point eight", {"type": "alert", "symbol": "TON", "op": "above", "level": 1.8}),
    ("buy 100 dollars of TON", {"type": "order", "side": "buy", "symbol": "TON", "usd": 100}),
    ("Buy $100 of TON.", {"type": "order", "side": "buy", "symbol": "TON", "usd": 100}),
    ("buy two hundred and fifty dollars of bitcoin", {"type": "order", "side": "buy", "symbol": "BTC", "usd": 250}),
    ("buy 0.01 bitcoin", {"type": "order", "side": "buy", "symbol": "BTC", "qty": 0.01}),
    ("Buy 0.5 of a bitcoin.", {"type": "order", "side": "buy", "symbol": "BTC", "qty": 0.5}),
    ("sell half my ETH", {"type": "order", "side": "sell", "symbol": "ETH", "fraction": 0.5}),
    ("Sell all TON.", {"type": "order", "side": "sell", "symbol": "TON", "fraction": 1.0}),
    ("sell 25% of my solana", {"type": "order", "side": "sell", "symbol": "SOL", "fraction": 0.25}),
    ("How am I doing?", {"type": "session"}),
    ("PnL", {"type": "session"}),
    ("show my positions", {"type": "positions"}),
])
def test_understood(text, expected):
    cmd = parse(text)
    assert cmd is not None and {k: cmd[k] for k in expected} == expected
    assert cmd["label"]


@pytest.mark.parametrize("text", ["", "what's the weather", "buy something", "alert me about bitcoin",
                                  "buy half my BTC", "sell bitcoin", "alert me if BTC drops below zero"])
def test_not_understood(text):
    assert parse(text) is None


def test_labels():
    assert parse("buy 100 dollars of TON")["label"] == "Paper-buy $100 of TON"
    assert parse("buy 0.01 bitcoin")["label"] == "Paper-buy 0.01 BTC"
    assert parse("sell half my ETH")["label"] == "Paper-sell half your ETH"
    assert parse("alert me if BTC drops below 57k")["label"] == "Alert: BTC below 57,000"


def test_hits_uses_the_current_price_and_watchlist_symbols():
    assert parse("alert me if btc hits 60k", price_of=lambda s: 58_000)["op"] == "above"
    assert parse("alert me if btc hits 50k", price_of=lambda s: 58_000)["op"] == "below"
    assert parse("alert me if btc hits 50k") is None
    assert parse("buy 10 dollars of doge", known={"DOGE"})["symbol"] == "DOGE"


def test_tokenize_numbers():
    assert tokenize("57k 57,000 1.5k fifty seven thousand") == [57_000, 57_000, 1_500, 57_000]
    assert tokenize("buy a hundred") == ["buy", 100]
