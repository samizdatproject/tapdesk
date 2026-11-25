"""User settings: the watched TON address and the price watchlist (pure, unit-tested)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from .tonaddr import AddressError, to_raw

DEFAULT_WATCHLIST = ["BTC", "ETH", "TON", "SOL"]
MAX_WATCH = 8
_SYMBOL = re.compile(r"^[A-Z0-9]{2,10}$")


class SettingsError(ValueError):
    pass


def clean_symbol(raw: object) -> str:
    s = str(raw or "").strip().upper()
    if not _SYMBOL.match(s):
        raise SettingsError(f"'{s[:12]}' isn't a ticker symbol (2-10 letters or digits)")
    if s in ("USDT", "USD", "USDC"):
        raise SettingsError(f"{s} is the quote currency; pick a coin to watch")
    return s


@dataclass(frozen=True)
class Settings:
    ton_address: str = ""
    watchlist: list[str] = field(default_factory=lambda: list(DEFAULT_WATCHLIST))

    @property
    def ton_raw(self) -> str:
        return to_raw(self.ton_address) if self.ton_address else ""

    def to_json(self) -> dict:
        return {"tonAddress": self.ton_address, "watchlist": list(self.watchlist)}

    @classmethod
    def from_json(cls, data: dict | None) -> "Settings":
        """Validates input from the phone screen; raises SettingsError with a human message."""
        data = data or {}
        address = str(data.get("tonAddress") or "").strip()
        if address:
            try:
                to_raw(address)
            except AddressError as exc:
                raise SettingsError(str(exc)) from None
        raw = data.get("watchlist", DEFAULT_WATCHLIST)
        if not isinstance(raw, list):
            raise SettingsError("watchlist must be a list")
        watch: list[str] = []
        for item in raw:
            s = clean_symbol(item)
            if s not in watch:
                watch.append(s)
        if not watch:
            raise SettingsError("Watch at least one coin")
        if len(watch) > MAX_WATCH:
            raise SettingsError(f"Watch at most {MAX_WATCH} coins")
        return cls(ton_address=address, watchlist=watch)
