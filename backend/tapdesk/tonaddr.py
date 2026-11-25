"""TON address forms (pure). Friendly addresses (EQ…/UQ…) are decoded locally with their CRC,
so a typo is caught before anything is polled. APIs answer with the raw form "0:<hex>"."""

from __future__ import annotations

import base64
import re

_RAW = re.compile(r"^(-1|0):([0-9a-fA-F]{64})$")
_FRIENDLY = re.compile(r"^[A-Za-z0-9_\-+/]{48}$")


class AddressError(ValueError):
    pass


def crc16(data: bytes) -> int:
    """CRC-16/XMODEM, as used by friendly TON addresses."""
    crc = 0
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) if crc & 0x8000 else crc << 1
            crc &= 0xFFFF
    return crc


def to_raw(address: str) -> str:
    """Any mainnet address form → lowercase raw "wc:hex". Raises AddressError with a human message."""
    a = (address or "").strip()
    m = _RAW.match(a)
    if m:
        return f"{m.group(1)}:{m.group(2).lower()}"
    if not _FRIENDLY.match(a):
        raise AddressError("That doesn't look like a TON address (48 characters, starting with EQ or UQ)")
    try:
        data = base64.urlsafe_b64decode(a.replace("+", "-").replace("/", "_"))
    except ValueError:
        raise AddressError("That TON address isn't valid base64") from None
    if len(data) != 36 or crc16(data[:34]) != int.from_bytes(data[34:], "big"):
        raise AddressError("That TON address has a typo (checksum doesn't match)")
    flags, wc = data[0], data[1]
    if flags & 0x80:
        raise AddressError("That's a testnet address; TapDesk watches mainnet")
    if flags & 0x7F not in (0x11, 0x51):
        raise AddressError("That TON address has unknown flags")
    workchain = -1 if wc == 0xFF else wc
    if workchain not in (0, -1):
        raise AddressError("Unknown TON workchain")
    return f"{workchain}:{data[2:34].hex()}"


def to_friendly(raw: str, bounceable: bool = False) -> str:
    wc, _, hexpart = to_raw(raw).partition(":")
    body = bytes([0x11 if bounceable else 0x51, 0xFF if wc == "-1" else 0]) + bytes.fromhex(hexpart)
    return base64.urlsafe_b64encode(body + crc16(body).to_bytes(2, "big")).decode()


def short(address: str) -> str:
    """For the glasses: "UQAb…x9Zk"."""
    a = address.strip()
    return a if len(a) <= 10 else f"{a[:4]}…{a[-4:]}"


USDT_MASTER = "EQCxE6mUtQJKFnGfaROTKOt1lZbDiiX1kCixRv7Nw2Id_sDs"
USDT_MASTER_RAW = to_raw(USDT_MASTER)
