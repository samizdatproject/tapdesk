#!/usr/bin/env python3
"""Drives the TapDesk demo in the Even Hub simulator through the core moment, step by step.

Needs a running, paired backend (scripts/run-local.sh) and the simulator started with
--automation-port 9898 against http://localhost:5174, showing the Session screen.

    python3 scripts/demo.py --token-file devtoken --step --shots shots/

Scenes:
  1. Session: wallet value, paper PnL, best prices.
  2. Arrival: waits until USDT or TON really arrives in the watched wallet (send it now),
     or, with --test-arrival, asks the backend for a test card (backend started with
     TAPDESK_TEST_ARRIVALS=1; the card then says "(test)").
  3. One tap: "Paper-buy BTC with it", the 2 s undo, the fill with its venue, back to Session.
  4. Positions.
  5. Hold to talk: "Alert me if bitcoin drops below 57k" -> confirm -> alert set.

--step waits for Enter before each scene, so you can start or pause a screen recording.
--from N starts at scene N. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path


class Demo:
    def __init__(self, args: argparse.Namespace):
        self.backend = args.backend.rstrip("/")
        self.sim = args.sim.rstrip("/")
        self.token = Path(args.token_file).expanduser().read_text().strip()
        self.step, self.pace, self.start = args.step, args.pace, args.start
        self.test_arrival = args.test_arrival
        self.say_text = args.say
        self.shots = Path(args.shots) if args.shots else None
        self.n = 0

    # plumbing ---------------------------------------------------------------------
    def api(self, path: str, body: dict | None = None) -> dict:
        req = urllib.request.Request(
            self.backend + path,
            data=json.dumps(body).encode() if body is not None else None,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
            method="POST" if body is not None else "GET",
        )
        return json.load(urllib.request.urlopen(req, timeout=10))

    def tap(self, action: str, wait: float = 1.2) -> None:
        req = urllib.request.Request(f"{self.sim}/api/input", json.dumps({"action": action}).encode(),
                                     {"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).read()
        self.sleep(wait)

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds * self.pace)

    def shot(self, name: str) -> None:
        if not self.shots:
            return
        self.shots.mkdir(parents=True, exist_ok=True)
        self.n += 1
        data = urllib.request.urlopen(f"{self.sim}/api/screenshot/glasses", timeout=10).read()
        (self.shots / f"{self.n:02d}-{name}.png").write_bytes(data)
        print(f"   saved {self.n:02d}-{name}.png")

    def scene(self, number: int, title: str) -> bool:
        if number < self.start:
            return False
        print(f"\n== {number}. {title}")
        if self.step:
            input("   press Enter to run this scene… ")
        return True

    def speak(self, text: str) -> None:
        if sys.platform == "darwin":
            subprocess.run(["say", text], check=False)
        else:
            print(f"   (speak now: {text!r})")
            self.sleep(3)

    def wait_for_arrival(self, timeout: float = 600) -> dict:
        known = {c["id"] for c in self.api("/cards")["cards"]}
        if self.test_arrival:
            self.api("/dev/test-arrival", {"symbol": "USDT", "amount": 1.0})
        else:
            print("   Send USDT or TON to the watched address now; waiting for it (checked every ~10 s)…")
        deadline = time.time() + timeout
        while time.time() < deadline:
            for card in self.api("/cards")["cards"]:
                if card["kind"] == "arrival" and card["id"] not in known:
                    return card
            time.sleep(2)
        raise SystemExit("No arrival within the time limit.")

    # scenes -----------------------------------------------------------------------
    def run(self) -> None:
        self.api("/cards")  # fails early if the backend or token is wrong

        if self.scene(1, "Session: wallet value, paper PnL, best prices"):
            self.sleep(1)
            self.shot("session")

        if self.scene(2, "A transfer arrives in the TON wallet"):
            card = self.wait_for_arrival()
            print(f"   arrival: {card['amount']} {card['symbol']}{' (test)' if card['test'] else ''}")
            self.sleep(2.5)  # the card is pushed to the glasses at once
            self.shot("arrival")

        if self.scene(3, "One tap: paper-buy BTC with it, 2 s undo, filled at the best ask"):
            self.tap("click", 0.8)  # first option is highlighted: "Paper-buy BTC with it"
            self.shot("undo")
            self.sleep(2.2)
            self.shot("filled")
            self.sleep(3.0)  # back to Session with the new PnL
            self.shot("session-after")

        if self.scene(4, "Positions"):
            self.tap("click", 1.5)
            self.shot("positions")
            self.tap("double_click", 1.5)

        if self.scene(5, "Hold to talk: set a price alert"):
            self.tap("long_press", 0.6)
            self.speak(self.say_text)
            self.sleep(0.3)
            self.shot("listening")
            self.tap("long_press_release", 3.5)
            self.shot("parsed")
            self.tap("click", 0.8)  # confirm: the 2 s undo window starts
            self.sleep(3.0)
            self.shot("alert-set")
        print("\nDone.")


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--token-file", required=True, help="file holding a paired device token")
    p.add_argument("--backend", default="http://localhost:8788")
    p.add_argument("--sim", default="http://localhost:9898")
    p.add_argument("--test-arrival", action="store_true",
                   help="use a test card instead of waiting for a real transfer (needs TAPDESK_TEST_ARRIVALS=1)")
    p.add_argument("--say", default="Alert me if bitcoin drops below 57k", help="what scene 5 speaks")
    p.add_argument("--from", dest="start", type=int, default=1, help="start at this scene")
    p.add_argument("--pace", type=float, default=1.0, help="multiply every wait (e.g. 1.5 for a slower video)")
    p.add_argument("--step", action="store_true", help="wait for Enter before each scene")
    p.add_argument("--shots", help="save a glasses screenshot after each step into this folder")
    Demo(p.parse_args()).run()


if __name__ == "__main__":
    main()
