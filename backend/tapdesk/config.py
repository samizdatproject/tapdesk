"""Settings come from environment variables, optionally loaded from an env file.

TAPDESK_ENV          path to an env file (KEY=value lines). Default: ~/.config/tapdesk/tapdesk.env
TAPDESK_TON_ADDRESS  the TON wallet to watch (read-only; can also be set on the phone screen)
TAPDESK_DATA         directory for the SQLite file. Default: the env file's directory.
TAPDESK_WHISPER_URL  whisper.cpp server inference URL. Default: http://127.0.0.1:8080/inference
TAPDESK_TEST_ARRIVALS  "1" enables POST /dev/test-arrival (cards are labelled "test"). Off by default.

No API keys are needed: every data source is public.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_ENV = "~/.config/tapdesk/tapdesk.env"


def _read_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


@dataclass(frozen=True)
class Config:
    data_dir: Path
    ton_address: str
    test_arrivals: bool

    @property
    def db_file(self) -> Path:
        return self.data_dir / "tapdesk.sqlite"


def load() -> Config:
    env_path = Path(os.environ.get("TAPDESK_ENV", DEFAULT_ENV)).expanduser()
    values = _read_env_file(env_path)
    values.update({k: v for k, v in os.environ.items() if k.startswith("TAPDESK_")})
    if values.get("TAPDESK_WHISPER_URL"):
        os.environ["TAPDESK_WHISPER_URL"] = values["TAPDESK_WHISPER_URL"]
    data_dir = Path(values.get("TAPDESK_DATA") or env_path.parent).expanduser()
    return Config(
        data_dir=data_dir,
        ton_address=values.get("TAPDESK_TON_ADDRESS", "").strip(),
        test_arrivals=values.get("TAPDESK_TEST_ARRIVALS") == "1",
    )
