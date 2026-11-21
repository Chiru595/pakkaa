"""Money helpers. All amounts are integer paise; these functions never use floats for stored values."""
from __future__ import annotations

import re
from decimal import Decimal, InvalidOperation

MAX_AMOUNT_PAISE = 10_000_000_000  # ₹10 crore: a sane cap for one deal or payout in this product

_AMOUNT = re.compile(
    r"(?<![\w.])(?P<cur>₹|rs\.?|inr)?\s*(?P<num>\d{1,3}(?:,\d{2,3})+|\d+(?:\.\d+)?)\s*"
    r"(?P<unit>k\b|thousand\b|l\b|lakhs?\b|lacs?\b|cr\b|crores?\b|/-)?",
    re.I,
)
_UNITS = {"k": 1_000, "thousand": 1_000, "l": 100_000, "lakh": 100_000, "lakhs": 100_000, "lac": 100_000,
          "lacs": 100_000, "cr": 10_000_000, "crore": 10_000_000, "crores": 10_000_000}


def rupees_to_paise(rupees: Decimal | int | str) -> int:
    try:
        value = Decimal(str(rupees))
    except InvalidOperation as exc:
        raise ValueError("not a number") from exc
    paise = int((value * 100).to_integral_value())
    if paise <= 0 or paise > MAX_AMOUNT_PAISE:
        raise ValueError("amount out of range")
    return paise


def format_inr(paise: int) -> str:
    """Indian digit grouping: 4800000 paise -> ₹48,000 (paise shown only when non-zero)."""
    neg = paise < 0
    rupees, rem = divmod(abs(paise), 100)
    s = str(rupees)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        s = ",".join(groups) + "," + tail
    out = f"₹{s}" + (f".{rem:02d}" if rem else "")
    return f"-{out}" if neg else out


def amounts_in_text(text: str, min_rupees: int = 100) -> list[int]:
    """Money amounts mentioned in free text, in paise. Bare small numbers (days, %, counts) are ignored."""
    found: list[int] = []
    for m in _AMOUNT.finditer(text):
        num, unit, cur = m.group("num"), (m.group("unit") or "").lower(), m.group("cur")
        tail = text[m.end():m.end() + 2]
        if tail.startswith("%") or (not unit and not cur and re.match(r"\s*(days?|weeks?|months?|hrs?|hours?)\b",
                                                                      text[m.end():m.end() + 8], re.I)):
            continue
        try:
            value = Decimal(num.replace(",", "")) * _UNITS.get(unit, 1)
        except InvalidOperation:
            continue
        if value < min_rupees and not cur:
            continue
        if value <= 0 or value * 100 > MAX_AMOUNT_PAISE:
            continue
        found.append(int(value * 100))
    return found
