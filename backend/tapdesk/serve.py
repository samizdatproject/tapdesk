"""Runs the backend: price poller, TON watcher and the HTTP/WebSocket API in one asyncio loop."""

from __future__ import annotations

import argparse
import asyncio
import logging

import httpx
import uvicorn

from . import config, stt
from .alerts import Alerts
from .app import ARRIVAL_BUY, TAGLINE, Hub, create_app
from .paper import Paper
from .prices import Prices
from .settings import Settings
from .store import Store
from .ton import TonWatcher

USER_AGENT = "TapDesk/0.1 (paper-trading demo; public data only)"


def open_store(cfg: config.Config) -> Store:
    cfg.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    store = Store(cfg.db_file)
    if cfg.ton_address and not store.get("settings"):
        # First run: take the address from the env file; later the phone screen owns it.
        store.set_settings(Settings.from_json({"tonAddress": cfg.ton_address}))
    return store


def build(cfg: config.Config, client: httpx.AsyncClient) -> Hub:
    store = open_store(cfg)
    paper = Paper(store)
    alerts = Alerts(store)

    def symbols() -> list[str]:
        # Always TON (wallet value) and the arrival target, plus what is watched, alerted or held.
        return list(dict.fromkeys([*store.settings().watchlist, "TON", ARRIVAL_BUY, *alerts.symbols(), *paper.symbols()]))

    prices = Prices(client, symbols)
    ton = TonWatcher(client, store, lambda: store.settings().ton_address)
    hub = Hub(store, prices, ton, paper, alerts, stt.transcribe)
    prices.listeners.append(hub.on_tick)
    ton.listeners.append(hub.on_arrival)
    return hub


async def _serve(host: str, port: int) -> None:
    cfg = config.load()
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, limits=httpx.Limits(max_connections=8)) as client:
        hub = build(cfg, client)
        app = create_app(hub, test_arrivals=cfg.test_arrivals)
        print(TAGLINE)
        print(f"Listening on http://{host}:{port}")
        address = hub.store.settings().ton_address
        print(f"Watching TON address: {address}" if address else "No TON address yet: set it on the phone screen.")
        if cfg.test_arrivals:
            print("TEST ARRIVALS ON: POST /dev/test-arrival makes a card labelled 'test'.")
        print(f"Pairing code (valid 5 minutes): {hub.store.new_pairing_code()}   New code: python -m tapdesk pair")
        server = uvicorn.Server(uvicorn.Config(app, host=host, port=port, log_level="warning"))
        tasks = [asyncio.create_task(hub.prices.run()), asyncio.create_task(hub.ton.run())]
        try:
            await server.serve()
        finally:
            for t in tasks:
                t.cancel()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m tapdesk serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8788)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    asyncio.run(_serve(args.host, args.port))


def pair_main() -> None:
    store = open_store(config.load())
    print(f"Pairing code (valid 5 minutes): {store.new_pairing_code()}")
