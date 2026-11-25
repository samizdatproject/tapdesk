#!/usr/bin/env python3
"""Opt-in live smoke test: prints the current best prices and the TON address status.

Uses the real public APIs once (no keys). Not part of the test suite.

    backend/.venv/bin/python scripts/live_check.py [--address EQ…|UQ…] [--symbols BTC,ETH,TON,SOL]

Without --address it uses TAPDESK_TON_ADDRESS from the env file (~/.config/tapdesk/tapdesk.env).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend"))

import httpx  # noqa: E402

from tapdesk import config  # noqa: E402
from tapdesk.prices import VENUE_NAME, Prices  # noqa: E402
from tapdesk.serve import USER_AGENT  # noqa: E402
from tapdesk.store import Store  # noqa: E402
from tapdesk.ton import TonWatcher  # noqa: E402
from tapdesk.tonaddr import AddressError, to_raw  # noqa: E402


async def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--address", default=None)
    p.add_argument("--symbols", default="BTC,ETH,TON,SOL")
    args = p.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    address = args.address if args.address is not None else config.load().ton_address

    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}) as client:
        prices = Prices(client, lambda: symbols)
        await prices.poll_once()
        print("Best prices in USDT (public tickers; Coinbase USD converted at its USDT-USD rate)")
        for s in symbols:
            b = prices.best(s)
            venues = sorted(prices.quotes.get(s, {}))
            if not b:
                print(f"  {s:5} no venue answered")
                continue
            change = f"{b['change']:+.2f}%" if b["change"] is not None else "n/a"
            print(f"  {s:5} bid {b['bid']:>12,.4f} {VENUE_NAME[b['bidVenue']]:9}  ask {b['ask']:>12,.4f} "
                  f"{VENUE_NAME[b['askVenue']]:9}  24h {change:>7}  venues: {', '.join(venues)}")
        skipped = sorted(f"{v}:{s}" for v, s in prices.unsupported)
        if skipped:
            print(f"  not listed: {', '.join(skipped)}")

        if not address:
            print("\nTON: no address (pass --address or set TAPDESK_TON_ADDRESS)")
            return
        try:
            to_raw(address)
        except AddressError as exc:
            raise SystemExit(f"\nTON address {address}: {exc}") from None
        with tempfile.TemporaryDirectory() as tmp:
            watcher = TonWatcher(client, Store(Path(tmp) / "check.sqlite"), lambda: address)
            await watcher.poll_once()
            w = watcher.wallet or {}
            print(f"\nTON address {address}")
            if w.get("ok"):
                print(f"  balance: {w['ton']:,.4f} TON, {w['usdt']:,.2f} USDT")
            else:
                print(f"  unavailable: {w.get('error')}")


if __name__ == "__main__":
    asyncio.run(main())
