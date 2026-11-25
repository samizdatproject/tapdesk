import pytest

from tapdesk.settings import DEFAULT_WATCHLIST, MAX_WATCH, Settings, SettingsError
from tapdesk.tonaddr import AddressError, USDT_MASTER, USDT_MASTER_RAW, short, to_friendly, to_raw

ADDR = "EQCD39VS5jcptHL8vMjEXrzGaRcCVYto7HUn4bpAOg8xqB2N"
RAW = "0:83dfd552e63729b472fcbcc8c45ebcc6691702558b68ec7527e1ba403a0f31a8"


def test_friendly_and_raw_forms_agree():
    assert to_raw(ADDR) == RAW
    assert to_raw(RAW.upper().replace("0X", "")) == RAW
    assert to_friendly(RAW, bounceable=True) == ADDR
    assert to_raw(to_friendly(RAW)) == RAW  # non-bounceable UQ… form
    assert to_friendly(RAW).startswith("UQ")
    assert USDT_MASTER_RAW == "0:b113a994b5024a16719f69139328eb759596c38a25f59028b146fecdc3621dfe"
    assert to_raw(USDT_MASTER) == USDT_MASTER_RAW


@pytest.mark.parametrize("bad", ["", "hello", ADDR[:-1] + "M", ADDR[:40], "0:1234"])
def test_bad_addresses_are_rejected(bad):
    with pytest.raises(AddressError):
        to_raw(bad)


def test_testnet_address_is_rejected():
    body = bytearray(__import__("base64").urlsafe_b64decode(ADDR))
    body[0] |= 0x80
    from tapdesk.tonaddr import crc16

    body[34:] = crc16(bytes(body[:34])).to_bytes(2, "big")
    with pytest.raises(AddressError, match="testnet"):
        to_raw(__import__("base64").urlsafe_b64encode(bytes(body)).decode())


def test_short_address():
    assert short(ADDR) == "EQCD…qB2N"


def test_settings_defaults_and_cleaning():
    s = Settings.from_json({})
    assert s.watchlist == DEFAULT_WATCHLIST and s.ton_address == ""
    s = Settings.from_json({"tonAddress": f" {ADDR} ", "watchlist": ["btc", "BTC", " eth "]})
    assert s.ton_address == ADDR and s.watchlist == ["BTC", "ETH"] and s.ton_raw == RAW
    assert Settings.from_json(s.to_json()) == s


@pytest.mark.parametrize("body,msg", [
    ({"tonAddress": "nope"}, "TON address"),
    ({"watchlist": []}, "at least one"),
    ({"watchlist": ["USDT"]}, "quote currency"),
    ({"watchlist": ["B!"]}, "ticker"),
    ({"watchlist": [f"C{i}" for i in range(MAX_WATCH + 1)]}, "at most"),
    ({"watchlist": "BTC"}, "list"),
])
def test_settings_errors_are_human(body, msg):
    with pytest.raises(SettingsError, match=f"(?i){msg}"):
        Settings.from_json(body)
