import os
import stat

from tapdesk.settings import Settings
from tapdesk.store import PAIR_MAX_ATTEMPTS, PAIR_TTL_S, Store


def test_pairing_code_is_single_use(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    code = s.new_pairing_code()
    assert len(code) == 6 and code.isdigit()
    token = s.redeem_pairing_code(code)
    assert token and s.is_device(token)
    assert s.redeem_pairing_code(code) is None


def test_pairing_code_expires(tmp_path, clock):
    s = Store(tmp_path / "db.sqlite", clock=clock)
    code = s.new_pairing_code()
    clock.t += PAIR_TTL_S + 1
    assert s.redeem_pairing_code(code) is None


def test_pairing_locks_after_too_many_wrong_codes(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    code = s.new_pairing_code()
    wrong = "000000" if code != "000000" else "111111"
    for _ in range(PAIR_MAX_ATTEMPTS):
        assert s.redeem_pairing_code(wrong) is None
    assert s.redeem_pairing_code(code) is None


def test_tokens_are_not_stored_in_clear(tmp_path):
    path = tmp_path / "db.sqlite"
    s = Store(path)
    token = s.redeem_pairing_code(s.new_pairing_code())
    assert token.encode() not in path.read_bytes()
    assert not s.is_device(None) and not s.is_device("nope")
    s.forget_devices()
    assert not s.is_device(token)


def test_database_file_is_private(tmp_path):
    path = tmp_path / "db.sqlite"
    Store(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600


def test_settings_roundtrip(tmp_path):
    s = Store(tmp_path / "db.sqlite")
    assert s.settings() == Settings()
    s.set_settings(Settings(ton_address="", watchlist=["BTC"]))
    assert Store(tmp_path / "db.sqlite").settings().watchlist == ["BTC"]
