"""Speech-to-text for voice commands, through a whisper.cpp server on the same machine.

The glasses send raw PCM (16 kHz, 16-bit little-endian, mono). Audio is buffered in memory,
wrapped as WAV and posted to whisper-server; it is never written to disk or sent elsewhere.
"""

from __future__ import annotations

import os
import struct

import httpx

SAMPLE_RATE = 16_000
BYTES_PER_SECOND = SAMPLE_RATE * 2
MAX_SECONDS = 10
MIN_SECONDS = 0.3
DEFAULT_URL = "http://127.0.0.1:8080/inference"
# Biases whisper towards the words the command parser knows. A word list, not sample commands:
# whisper can echo its prompt on silence, and a list of words never parses as a command.
PROMPT = "Vocabulary: bitcoin, BTC, ether, ETH, toncoin, TON, solana, SOL, alert, above, below, PnL."

# whisper prints these for silence or noise; they are not dictation.
_NOISE = {"", "[blank_audio]", "(silence)", "[silence]", "[music]", "(music)", "[noise]", "you"}


def wav(pcm: bytes, rate: int = SAMPLE_RATE) -> bytes:
    data = pcm[: len(pcm) - len(pcm) % 2]
    return b"RIFF" + struct.pack("<I", 36 + len(data)) + b"WAVEfmt " + struct.pack(
        "<IHHIIHH", 16, 1, 1, rate, rate * 2, 2, 16) + b"data" + struct.pack("<I", len(data)) + data


def clean(text: str) -> str:
    t = " ".join((text or "").split())
    return "" if t.lower().strip(" .") in _NOISE else t


class Recorder:
    """One recording at a time per connection; caps the length at MAX_SECONDS."""

    def __init__(self) -> None:
        self.buf: bytearray | None = None

    @property
    def active(self) -> bool:
        return self.buf is not None

    def begin(self) -> None:
        self.buf = bytearray()

    def add(self, chunk: bytes) -> None:
        if self.buf is not None and len(self.buf) < MAX_SECONDS * BYTES_PER_SECOND:
            self.buf += chunk[: MAX_SECONDS * BYTES_PER_SECOND - len(self.buf)]

    def end(self) -> bytes | None:
        pcm, self.buf = self.buf, None
        if pcm is None or len(pcm) < MIN_SECONDS * BYTES_PER_SECOND:
            return None
        return bytes(pcm)

    def cancel(self) -> None:
        self.buf = None


async def transcribe(pcm: bytes, url: str | None = None) -> str:
    url = url or os.environ.get("TAPDESK_WHISPER_URL", DEFAULT_URL)
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(url, files={"file": ("audio.wav", wav(pcm), "audio/wav")},
                              data={"response_format": "json", "temperature": "0.0", "prompt": PROMPT})
        r.raise_for_status()
        return clean(r.json().get("text", ""))
