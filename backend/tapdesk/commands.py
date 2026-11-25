"""Rule-based parser for transcribed voice commands (no LLM). Pure and unit-tested.

Nothing here executes anything: the result is shown on a confirm screen and runs only
after a tap. Understands, for example:

    alert me if BTC drops below 57k        alert when ton goes above 1.8
    buy 100 dollars of TON                 buy 0.01 bitcoin
    sell half my ETH                       sell all ton
    how am I doing / pnl                   positions
"""

from __future__ import annotations

import re

COINS = {
    "btc": "BTC", "bitcoin": "BTC", "bitcoins": "BTC", "xbt": "BTC",
    "eth": "ETH", "ether": "ETH", "ethereum": "ETH", "ethers": "ETH",
    "ton": "TON", "toncoin": "TON", "toncoins": "TON", "tons": "TON", "gram": "TON", "grams": "TON",
    "sol": "SOL", "solana": "SOL",
}

_UNITS = {
    "zero": 0, "oh": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
    "eight": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
    "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19,
}
_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80, "ninety": 90}
_SCALES = {"thousand": 1_000, "grand": 1_000, "k": 1_000, "million": 1_000_000, "m": 1_000_000}
_DIGITS = re.compile(r"^(\d+(?:\.\d+)?)(k|m)?$")

_SESSION = ("how am i doing", "how am i do", "how are we doing", "pnl", "p n l", "p and l", "profit",
            "portfolio", "session", "my balance", "how is my", "how s my")
_POSITIONS = ("positions", "position", "what do i hold", "holdings")
_ALERT = ("alert", "notify", "let me know", "tell me", "ping me", "warn me", "remind me")
_BELOW = ("below", "under", "drops", "drop", "falls", "fall", "dips", "dip", "less than", "lower than", "sinks")
_ABOVE = ("above", "over", "rises", "rise", "goes up", "more than", "higher than", "exceeds", "breaks", "tops")
_REACH = ("hits", "hit", "reaches", "reach", "touches", "gets to", "at")
_USD = ("dollars", "dollar", "usd", "usdt", "bucks", "tether", "worth")
_FRACTIONS = {"half": 0.5, "quarter": 0.25, "third": 1 / 3, "all": 1.0, "everything": 1.0, "entire": 1.0, "whole": 1.0}

EXAMPLES = ['"alert me if BTC drops below 57k"', '"buy 100 dollars of TON"', '"sell half my ETH"', '"how am I doing"']


def _words_to_number(words: list[str]) -> float:
    total, current, decimals, in_decimals = 0.0, 0.0, "", False
    for w in words:
        if w == "point":
            in_decimals = True
        elif in_decimals:
            decimals += str(_UNITS.get(w, 0))
        elif w in _UNITS:
            current += _UNITS[w]
        elif w in _TENS:
            current += _TENS[w]
        elif w == "hundred":
            current = (current or 1) * 100
        elif w in _SCALES:
            total += (current or 1) * _SCALES[w]
            current = 0
    value = total + current
    return value + float(f"0.{decimals}") if decimals else value


def tokenize(text: str) -> list[str | float]:
    """Lowercase words with numbers already turned into floats ("57k", "57,000", "fifty seven thousand")."""
    t = (text or "").lower().replace("’", "'")
    t = re.sub(r"\$\s*(\d[\d,]*(?:\.\d+)?(?:\s?[km]\b)?)", r"\1 dollars ", t)
    t = re.sub(r"(?<=\d),(?=\d{3}\b)", "", t)
    t = re.sub(r"(\d)\s*%", r"\1 percent", t)
    t = re.sub(r"[^a-z0-9.\s]", " ", t)
    t = re.sub(r"\.(?!\d)", " ", t)
    words = t.split()
    out: list[str | float] = []
    i = 0
    while i < len(words):
        w = words[i]
        m = _DIGITS.match(w)
        if m:
            value = float(m.group(1)) * (_SCALES[m.group(2)] if m.group(2) else 1)
            i += 1
            while i < len(words) and (words[i] in _SCALES or words[i] == "hundred"):
                value *= 100 if words[i] == "hundred" else _SCALES[words[i]]
                i += 1
            out.append(value)
            continue
        is_num = w in _UNITS or w in _TENS or (w == "hundred") or (w in ("thousand", "million"))
        starts = is_num or (w == "a" and i + 1 < len(words) and words[i + 1] in ("hundred", "thousand", "million"))
        if starts and w not in ("oh",):
            run = []
            while i < len(words) and (words[i] in _UNITS or words[i] in _TENS or words[i] in
                                      ("hundred", "thousand", "million", "point", "and", "a")):
                if words[i] == "point" and not (i + 1 < len(words) and words[i + 1] in _UNITS):
                    break
                if words[i] in ("and", "a") and run and not (i + 1 < len(words) and (
                        words[i + 1] in _UNITS or words[i + 1] in _TENS or words[i + 1] == "hundred")):
                    break
                run.append(words[i])
                i += 1
            nums = [r for r in run if r not in ("and", "a")]
            if nums:
                out.append(_words_to_number(nums))
            continue
        out.append(w)
        i += 1
    return out


def _has(phrase_list, text: str) -> str | None:
    for p in phrase_list:
        if re.search(rf"\b{re.escape(p)}\b", text):
            return p
    return None


def _coin(tokens: list, known: set[str]) -> tuple[str, int] | tuple[None, int]:
    for i, t in enumerate(tokens):
        if isinstance(t, str):
            if t in COINS:
                return COINS[t], i
            if t.upper() in known:
                return t.upper(), i
    return None, -1


def _numbers(tokens: list) -> list[tuple[float, int]]:
    return [(t, i) for i, t in enumerate(tokens) if isinstance(t, float)]


def fmt_level(x: float) -> str:
    return f"{x:,.0f}" if x >= 1000 else f"{x:,.2f}".rstrip("0").rstrip(".") if x >= 1 else f"{x:.6g}"


def fmt_usd(x: float) -> str:
    return f"${x:,.0f}" if x == int(x) else f"${x:,.2f}"


def label(cmd: dict) -> str:
    """What the confirm screen says."""
    if cmd["type"] == "alert":
        return f"Alert: {cmd['symbol']} {cmd['op']} {fmt_level(cmd['level'])}"
    if cmd["type"] == "order":
        verb = "Paper-buy" if cmd["side"] == "buy" else "Paper-sell"
        if "usd" in cmd:
            return f"{verb} {fmt_usd(cmd['usd'])} of {cmd['symbol']}"
        if "qty" in cmd:
            return f"{verb} {cmd['qty']:.8g} {cmd['symbol']}"
        f = cmd["fraction"]
        part = "all" if f == 1 else "half" if f == 0.5 else f"{f * 100:.0f}% of"
        return f"{verb} {part} your {cmd['symbol']}"
    return {"session": "Open session", "positions": "Open positions"}.get(cmd["type"], "")


def parse(text: str, known: set[str] | None = None, price_of=None) -> dict | None:
    """A command dict, or None if it isn't understood. `price_of(symbol)` → current price, for "hits"."""
    known = {k.upper() for k in (known or set())}
    tokens = tokenize(text)
    words = " ".join(str(t) if isinstance(t, str) else "#" for t in tokens)
    coin, coin_at = _coin(tokens, known)
    nums = _numbers(tokens)

    if _has(_ALERT, words) and coin:
        if not nums:
            return None
        level = next((n for n, i in nums if i > coin_at), nums[0][0])
        if _has(_BELOW, words):
            op = "below"
        elif _has(_ABOVE, words):
            op = "above"
        elif _has(_REACH, words) and price_of and price_of(coin):
            op = "above" if level > price_of(coin) else "below"
        else:
            return None
        return _ok({"type": "alert", "symbol": coin, "op": op, "level": level})

    side = "buy" if _has(("buy", "purchase", "bye"), words) else "sell" if _has(("sell", "dump", "cell"), words) else None
    if side and coin:
        frac = None
        pct = next((n for n, i in nums if i + 1 < len(tokens) and tokens[i + 1] == "percent"), None)
        if pct is not None:
            frac = pct / 100
        else:
            frac = next((v for w, v in _FRACTIONS.items() if re.search(rf"\b{w}\b", words)), None)
        if frac is not None:
            if side == "buy" or not 0 < frac <= 1:
                return None
            return _ok({"type": "order", "side": side, "symbol": coin, "fraction": round(frac, 6)})
        for n, i in nums:
            after = [t for t in tokens[i + 1:i + 3] if isinstance(t, str)]
            if any(a in _USD for a in after):
                return _ok({"type": "order", "side": side, "symbol": coin, "usd": n})
            # "0.01 bitcoin", "5 of ton", "0.5 of a bitcoin"
            if coin_at > i and all(t in ("of", "a", "an") for t in tokens[i + 1:coin_at]) and coin_at - i <= 3:
                return _ok({"type": "order", "side": side, "symbol": coin, "qty": n})
        return None

    if _has(_POSITIONS, words):
        return {"type": "positions", "label": "Open positions"}
    if _has(_SESSION, words):
        return {"type": "session", "label": "Open session"}
    return None


def _ok(cmd: dict) -> dict | None:
    for key in ("level", "usd", "qty"):
        if key in cmd and not 0 < cmd[key] < 1e9:
            return None
    cmd["label"] = label(cmd)
    return cmd
