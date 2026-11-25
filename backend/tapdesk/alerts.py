"""Price alerts: symbol, above/below, level. Checked on every price tick, fired once.

A fired alert waits as a card until the glasses dismiss it. G2 can't show anything while
the app is closed, so cards that fire meanwhile appear when it is opened again.
"""

from __future__ import annotations

import time

from .settings import SettingsError, clean_symbol
from .store import Store

MAX_ACTIVE = 20
OPS = ("above", "below")


def clean_alert(raw: dict) -> tuple[str, str, float]:
    """Validates an alert from the phone or the glasses; raises SettingsError."""
    if not isinstance(raw, dict):
        raise SettingsError("An alert needs a symbol, above or below, and a level")
    symbol = clean_symbol(raw.get("symbol"))
    op = str(raw.get("op") or "").lower()
    if op not in OPS:
        raise SettingsError("An alert is either above or below a level")
    try:
        level = float(raw.get("level"))
    except (TypeError, ValueError):
        raise SettingsError("The alert level must be a number") from None
    if not 0 < level < 1e9:
        raise SettingsError("The alert level must be above zero")
    return symbol, op, level


class Alerts:
    def __init__(self, store: Store, clock=time.time):
        self.store, self.clock = store, clock
        with store.lock:
            store.db.execute(
                """CREATE TABLE IF NOT EXISTS alerts (id INTEGER PRIMARY KEY AUTOINCREMENT,
                   symbol TEXT NOT NULL, op TEXT NOT NULL, level REAL NOT NULL, created REAL NOT NULL,
                   fired_at REAL, price REAL, dismissed INTEGER NOT NULL DEFAULT 0, client_id TEXT UNIQUE)"""
            )

    @staticmethod
    def _row(r) -> dict:
        return {"id": r[0], "symbol": r[1], "op": r[2], "level": r[3], "firedAt": r[4], "price": r[5]}

    def _select(self, where: str, args=()) -> list[dict]:
        with self.store.lock:
            rows = self.store.db.execute(
                f"SELECT id, symbol, op, level, fired_at, price FROM alerts WHERE {where} ORDER BY id", args)
            return [self._row(r) for r in rows]

    def active(self) -> list[dict]:
        return self._select("fired_at IS NULL")

    def fired(self) -> list[dict]:
        """Fired and not dismissed yet: shown as cards."""
        return self._select("fired_at IS NOT NULL AND dismissed = 0")

    def get(self, alert_id: int) -> dict | None:
        found = self._select("id = ?", (alert_id,))
        return found[0] if found else None

    def add(self, raw: dict, client_id: str | None = None) -> dict:
        symbol, op, level = clean_alert(raw)
        with self.store.lock:
            if client_id:
                again = self._select("client_id = ?", (client_id,))
                if again:
                    return again[0]
            if len(self.active()) >= MAX_ACTIVE:
                raise SettingsError(f"At most {MAX_ACTIVE} alerts")
            cur = self.store.db.execute(
                "INSERT INTO alerts(symbol, op, level, created, client_id) VALUES (?, ?, ?, ?, ?)",
                (symbol, op, level, self.clock(), client_id))
            return self.get(cur.lastrowid) or {}

    def replace(self, raws: list) -> list[dict]:
        """The phone screen's list becomes the set of active alerts (unchanged ones keep their id)."""
        if not isinstance(raws, list):
            raise SettingsError("alerts must be a list")
        wanted = list(dict.fromkeys(clean_alert(r) for r in raws))
        if len(wanted) > MAX_ACTIVE:
            raise SettingsError(f"At most {MAX_ACTIVE} alerts")
        with self.store.lock:
            for a in self.active():
                key = (a["symbol"], a["op"], a["level"])
                if key in wanted:
                    wanted.remove(key)
                else:
                    self.store.db.execute("DELETE FROM alerts WHERE id = ?", (a["id"],))
            for symbol, op, level in wanted:
                self.store.db.execute("INSERT INTO alerts(symbol, op, level, created) VALUES (?, ?, ?, ?)",
                                      (symbol, op, level, self.clock()))
        return self.active()

    def symbols(self) -> list[str]:
        return list(dict.fromkeys(a["symbol"] for a in self.active()))

    def check(self, price_of) -> list[dict]:
        """Fires every active alert whose level the mid price has crossed; each fires once."""
        fired = []
        for a in self.active():
            best = price_of(a["symbol"])
            if not best or best.get("stale"):
                continue
            mid = best["mid"]
            if (a["op"] == "above" and mid >= a["level"]) or (a["op"] == "below" and mid <= a["level"]):
                with self.store.lock:
                    cur = self.store.db.execute(
                        "UPDATE alerts SET fired_at = ?, price = ? WHERE id = ? AND fired_at IS NULL",
                        (self.clock(), mid, a["id"]))
                if cur.rowcount:
                    fired.append({**a, "firedAt": self.clock(), "price": mid})
        return fired

    def dismiss(self, alert_id: int) -> None:
        with self.store.lock:
            self.store.db.execute("UPDATE alerts SET dismissed = 1 WHERE id = ?", (alert_id,))
