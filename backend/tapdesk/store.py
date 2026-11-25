"""Small SQLite store: settings, paired device tokens and the current pairing code.

The paper ledger, alerts and wallet arrivals keep their own tables in the same file
(see paper.py, alerts.py, ton.py). Tokens and pairing codes are stored as SHA-256 hashes.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from .settings import Settings

PAIR_TTL_S = 300
PAIR_MAX_ATTEMPTS = 5


def _h(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class Store:
    def __init__(self, path: Path | str, clock=time.time):
        self.clock = clock
        self.lock = threading.RLock()
        path = Path(path)
        path.touch(mode=0o600, exist_ok=True)
        os.chmod(path, 0o600)
        self.db = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self.db.executescript(
            """
            CREATE TABLE IF NOT EXISTS devices (token_hash TEXT PRIMARY KEY, created REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS pairing (id INTEGER PRIMARY KEY CHECK (id = 1),
                code_hash TEXT NOT NULL, expires REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """
        )

    # key/value (JSON) -------------------------------------------------------------
    def get(self, key: str, default=None):
        with self.lock:
            row = self.db.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def put(self, key: str, value) -> None:
        with self.lock:
            self.db.execute("INSERT OR REPLACE INTO kv(key, value) VALUES (?, ?)", (key, json.dumps(value)))

    # settings ---------------------------------------------------------------------
    def settings(self) -> Settings:
        raw = self.get("settings")
        return Settings.from_json(raw) if raw else Settings()

    def set_settings(self, settings: Settings) -> None:
        self.put("settings", settings.to_json())

    # pairing ----------------------------------------------------------------------
    def new_pairing_code(self) -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        with self.lock:
            self.db.execute(
                "INSERT OR REPLACE INTO pairing(id, code_hash, expires, attempts) VALUES (1, ?, ?, 0)",
                (_h(code), self.clock() + PAIR_TTL_S),
            )
        return code

    def redeem_pairing_code(self, code: str) -> str | None:
        """Returns a new device token if the code is right; the code is single use."""
        with self.lock:
            row = self.db.execute("SELECT code_hash, expires, attempts FROM pairing WHERE id = 1").fetchone()
            if row is None:
                return None
            code_hash, expires, attempts = row
            if self.clock() > expires or attempts >= PAIR_MAX_ATTEMPTS:
                self.db.execute("DELETE FROM pairing WHERE id = 1")
                return None
            if not hmac.compare_digest(code_hash, _h(code.strip())):
                self.db.execute("UPDATE pairing SET attempts = attempts + 1 WHERE id = 1")
                return None
            self.db.execute("DELETE FROM pairing WHERE id = 1")
            token = secrets.token_urlsafe(32)
            self.db.execute("INSERT INTO devices(token_hash, created) VALUES (?, ?)", (_h(token), self.clock()))
            return token

    # devices ----------------------------------------------------------------------
    def is_device(self, token: str | None) -> bool:
        if not token:
            return False
        with self.lock:
            return self.db.execute("SELECT 1 FROM devices WHERE token_hash = ?", (_h(token),)).fetchone() is not None

    def forget_devices(self) -> None:
        with self.lock:
            self.db.execute("DELETE FROM devices")
