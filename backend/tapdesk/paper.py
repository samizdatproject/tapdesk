"""Paper trading account in SQLite. No real orders, no leverage, no user funds.

Starts with 10,000 paper USDT. Market buys fill at the best ask and sells at the best bid
across the venues, and the venue is recorded. Orders are idempotent by clientMsgId (a primary
key, so a retry after a restart can't fill twice), and a wallet arrival can be credited once.
"""

from __future__ import annotations

import time
from dataclasses import asdict, dataclass

from .store import Store

START_CASH = 10_000.0
MIN_ORDER_USD = 1.0
MAX_ORDER_USD = 10_000_000.0
EPS = 1e-9


class PaperError(ValueError):
    """Refused order; the message is shown on the glasses."""


@dataclass
class Fill:
    clientMsgId: str
    side: str
    symbol: str
    qty: float
    price: float
    venue: str
    usd: float
    credited: float
    ts: float

    def to_json(self) -> dict:
        return asdict(self)


class Paper:
    def __init__(self, store: Store, clock=time.time):
        self.store, self.clock = store, clock
        with store.lock:
            store.db.executescript(
                """
                CREATE TABLE IF NOT EXISTS paper_account (id INTEGER PRIMARY KEY CHECK (id = 1),
                    cash REAL NOT NULL, deposits REAL NOT NULL, realised REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS paper_positions (symbol TEXT PRIMARY KEY,
                    qty REAL NOT NULL, cost REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS paper_orders (client_id TEXT PRIMARY KEY, ts REAL NOT NULL,
                    side TEXT NOT NULL, symbol TEXT NOT NULL, qty REAL NOT NULL, price REAL NOT NULL,
                    venue TEXT NOT NULL, usd REAL NOT NULL, credited REAL NOT NULL DEFAULT 0,
                    arrival_id TEXT UNIQUE);
                """
            )
            store.db.execute("INSERT OR IGNORE INTO paper_account(id, cash, deposits, realised) VALUES (1, ?, 0, 0)",
                             (START_CASH,))

    def _account(self) -> tuple[float, float, float]:
        return self.store.db.execute("SELECT cash, deposits, realised FROM paper_account WHERE id = 1").fetchone()

    def holding(self, symbol: str) -> float:
        with self.store.lock:
            row = self.store.db.execute("SELECT qty FROM paper_positions WHERE symbol = ?", (symbol,)).fetchone()
        return row[0] if row else 0.0

    def symbols(self) -> list[str]:
        with self.store.lock:
            return [r[0] for r in self.store.db.execute("SELECT symbol FROM paper_positions ORDER BY symbol")]

    def previous(self, client_id: str) -> Fill | None:
        """The fill an earlier attempt with this clientMsgId already made."""
        with self.store.lock:
            row = self.store.db.execute(
                "SELECT client_id, side, symbol, qty, price, venue, usd, credited, ts FROM paper_orders WHERE client_id = ?",
                (client_id,)).fetchone()
        return Fill(*row) if row else None

    def arrival_used(self, arrival_id: str) -> bool:
        with self.store.lock:
            return self.store.db.execute("SELECT 1 FROM paper_orders WHERE arrival_id = ?",
                                         (arrival_id,)).fetchone() is not None

    def market(
        self,
        client_id: str,
        side: str,
        symbol: str,
        best: dict,
        qty: float | None = None,
        usd: float | None = None,
        fraction: float | None = None,
        credit: float = 0.0,
        arrival_id: str | None = None,
    ) -> Fill:
        """Fills a market order against `best` (from Prices.best). `credit` adds received funds first."""
        if side not in ("buy", "sell"):
            raise PaperError("Buy or sell?")
        with self.store.lock:
            done = self.previous(client_id)
            if done:
                return done
            if arrival_id and self.arrival_used(arrival_id):
                raise PaperError("That transfer was already used")
            cash, deposits, realised = self._account()
            row = self.store.db.execute("SELECT qty, cost FROM paper_positions WHERE symbol = ?", (symbol,)).fetchone()
            held, cost = row if row else (0.0, 0.0)
            if side == "buy":
                price, venue = float(best["ask"]), best["askVenue"]
                if fraction is not None:
                    raise PaperError("Say how much to buy")
                if usd is None and qty is None:
                    raise PaperError("Say how much to buy")
                amount = float(usd) if usd is not None else float(qty) * price
                fill_qty = amount / price
                # A received transfer is bought with in full, however small.
                if amount > MAX_ORDER_USD or amount <= 0 or (not credit and amount < MIN_ORDER_USD - EPS):
                    raise PaperError(f"Orders are ${MIN_ORDER_USD:.0f} or more")
                if amount > cash + credit + EPS:
                    raise PaperError(f"Not enough paper cash (${cash + credit:,.2f})")
                new_cash = cash + credit - amount
                new_held, new_cost, new_realised = held + fill_qty, cost + amount, realised
            else:
                price, venue = float(best["bid"]), best["bidVenue"]
                if held <= EPS:
                    raise PaperError(f"No paper {symbol} to sell")
                if fraction is not None:
                    if not 0 < fraction <= 1:
                        raise PaperError("Sell between 1% and all of it")
                    fill_qty = held * fraction
                elif qty is not None:
                    fill_qty = float(qty)
                elif usd is not None:
                    fill_qty = float(usd) / price
                else:
                    raise PaperError("Say how much to sell")
                if fill_qty > held * (1 + 1e-9):
                    raise PaperError(f"You hold only {held:.8g} {symbol}")
                fill_qty = min(fill_qty, held)
                amount = fill_qty * price
                if amount < MIN_ORDER_USD - EPS and fill_qty < held:
                    raise PaperError(f"Orders are ${MIN_ORDER_USD:.0f} or more")
                basis = cost * fill_qty / held
                new_cash, new_realised = cash + amount, realised + amount - basis
                new_held, new_cost = held - fill_qty, cost - basis
            if fill_qty <= 0:
                raise PaperError("Nothing to fill")
            fill = Fill(client_id, side, symbol, fill_qty, price, venue, amount, credit, self.clock())
            db = self.store.db
            db.execute("BEGIN IMMEDIATE")
            try:
                db.execute("UPDATE paper_account SET cash = ?, deposits = ?, realised = ? WHERE id = 1",
                           (new_cash, deposits + credit, new_realised))
                if new_held <= EPS:
                    db.execute("DELETE FROM paper_positions WHERE symbol = ?", (symbol,))
                else:
                    db.execute("INSERT OR REPLACE INTO paper_positions(symbol, qty, cost) VALUES (?, ?, ?)",
                               (symbol, new_held, new_cost))
                db.execute(
                    "INSERT INTO paper_orders(client_id, ts, side, symbol, qty, price, venue, usd, credited, arrival_id) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (client_id, fill.ts, side, symbol, fill_qty, price, venue, amount, credit, arrival_id))
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
            return fill

    def summary(self, price_of) -> dict:
        """Cash, positions valued at the best bid (`price_of(symbol)` → dict or None), equity and PnL."""
        with self.store.lock:
            cash, deposits, realised = self._account()
            rows = list(self.store.db.execute("SELECT symbol, qty, cost FROM paper_positions ORDER BY cost DESC"))
        positions, value_sum, upnl_sum = [], 0.0, 0.0
        for symbol, qty, cost in rows:
            best = price_of(symbol)
            price = best["bid"] if best else None
            value = qty * price if price else cost  # no price yet: carry at cost
            upnl = value - cost
            value_sum += value
            upnl_sum += upnl
            positions.append({"symbol": symbol, "qty": qty, "avg": cost / qty if qty else 0.0, "price": price,
                              "value": value, "upnl": upnl, "stale": not best or bool(best.get("stale"))})
        equity = cash + value_sum
        invested = START_CASH + deposits
        pnl = equity - invested
        return {"cash": cash, "deposits": deposits, "realised": realised, "unrealised": upnl_sum,
                "equity": equity, "pnl": pnl, "pnlPct": pnl / invested * 100 if invested else 0.0,
                "positions": positions}

    def reset(self) -> None:
        """Back to 10,000 paper USDT. Order ids are kept, so an old retry can't fill again."""
        with self.store.lock:
            self.store.db.execute("DELETE FROM paper_positions")
            self.store.db.execute("UPDATE paper_account SET cash = ?, deposits = 0, realised = 0 WHERE id = 1",
                                  (START_CASH,))
