"""Term sheet rules: which terms exist, how each value is validated, and the canonical hash both sides sign."""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from app.money import MAX_AMOUNT_PAISE, amounts_in_text, format_inr

TermKey = Literal["scope", "deliverables", "price_paise", "advance_pct", "timeline_days", "revisions",
                  "payment_due_days", "ip_transfer", "cancellation"]


@dataclass(frozen=True)
class TermDef:
    key: str
    label: str
    kind: Literal["text", "money", "percent", "days", "count"]
    required: bool = False
    max_len: int = 500
    min_value: int = 0
    max_value: int = 0


TERM_DEFS: dict[str, TermDef] = {d.key: d for d in [
    TermDef("scope", "Scope of work", "text", required=True),
    TermDef("deliverables", "Deliverables", "text"),
    TermDef("price_paise", "Total price", "money", required=True, min_value=100, max_value=MAX_AMOUNT_PAISE),
    TermDef("advance_pct", "Advance", "percent", required=True, min_value=0, max_value=100),
    TermDef("timeline_days", "Timeline", "days", required=True, min_value=1, max_value=730),
    TermDef("revisions", "Revision rounds", "count", min_value=0, max_value=20),
    TermDef("payment_due_days", "Balance due after delivery", "days", min_value=0, max_value=180),
    TermDef("ip_transfer", "IP / ownership", "text", max_len=300),
    TermDef("cancellation", "Cancellation", "text", max_len=300),
]}


class TermValueError(ValueError):
    pass


_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def coerce_value(key: str, raw: Any) -> str | int:
    """Validates a term value from a person or the Scribe. Money arrives in paise (int) or as text like '45k'."""
    d = TERM_DEFS.get(key)
    if d is None:
        raise TermValueError(f"Unknown term {key!r}")
    if d.kind == "text":
        if not isinstance(raw, str):
            raise TermValueError(f"{d.label} must be text")
        value = " ".join(raw.split())
        if not value or len(value) > d.max_len or _CONTROL.search(value):
            raise TermValueError(f"{d.label} must be 1 to {d.max_len} characters")
        return value
    if d.kind == "money" and isinstance(raw, str):
        found = amounts_in_text(raw, min_rupees=1)
        if len(found) != 1:
            raise TermValueError(f"{d.label} must be a single amount")
        raw = found[0]
    if isinstance(raw, bool):
        raise TermValueError(f"{d.label} must be a whole number")
    if isinstance(raw, str):
        digits = raw.strip().rstrip("%").strip()
        digits = re.sub(r"\s*(days?|rounds?|revisions?)$", "", digits, flags=re.I)
        if not digits.isdigit():
            raise TermValueError(f"{d.label} must be a whole number")
        raw = int(digits)
    if isinstance(raw, float):
        if not raw.is_integer():
            raise TermValueError(f"{d.label} must be a whole number")
        raw = int(raw)
    if not isinstance(raw, int) or not d.min_value <= raw <= d.max_value:
        raise TermValueError(f"{d.label} must be between {d.min_value} and {d.max_value}")
    return raw


def display(key: str, value: Any) -> str:
    d = TERM_DEFS[key]
    if value is None:
        return "—"
    if d.kind == "money":
        return format_inr(int(value))
    if d.kind == "percent":
        return f"{value}%"
    if d.kind == "days":
        return f"{value} day{'s' if value != 1 else ''}"
    if d.kind == "count":
        return f"{value} round{'s' if value != 1 else ''}"
    return str(value)


def values(terms: dict[str, Any]) -> dict[str, Any]:
    return {k: v.get("value") for k, v in terms.items() if isinstance(v, dict) and k in TERM_DEFS}


def sheet_hash(room_id: str, version: int, terms: dict[str, Any]) -> str:
    """SHA-256 over the canonical JSON of the agreed values: what both people sign."""
    body = json.dumps({"room_id": room_id, "version": version, "terms": values(terms)}, sort_keys=True,
                      separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode()).hexdigest()


def advance_paise(terms: dict[str, Any]) -> int | None:
    v = values(terms)
    price, pct = v.get("price_paise"), v.get("advance_pct")
    if price is None or pct is None:
        return None
    return (int(price) * int(pct) + 50) // 100


@dataclass
class ReviewItem:
    level: Literal["missing", "risk", "info"]
    term_key: str | None
    message: str


def review(terms: dict[str, Any], pending_proposals: int = 0) -> list[ReviewItem]:
    """Checks before locking. Deterministic: the same sheet always gets the same review."""
    v = values(terms)
    items: list[ReviewItem] = []
    for d in TERM_DEFS.values():
        if d.required and v.get(d.key) in (None, ""):
            items.append(ReviewItem("missing", d.key, f"{d.label} is not agreed yet"))
    pct = v.get("advance_pct")
    if pct is not None and pct < 25:
        items.append(ReviewItem(
            "risk", "advance_pct",
            f"Advance is {pct}%. With a new client, at least 25% upfront protects you if they go quiet"))
    if v.get("revisions") is None:
        items.append(ReviewItem(
            "risk", "revisions", "No cap on revision rounds. Unlimited changes are the usual source of scope creep"))
    due = v.get("payment_due_days")
    if due is None:
        items.append(ReviewItem("risk", "payment_due_days", "No due date for the balance payment"))
    elif due > 45:
        items.append(ReviewItem("info", "payment_due_days",
                                "Balance due after more than 45 days. For Udyam-registered micro and small suppliers, "
                                "the MSMED Act 2006 (s.15) caps agreed payment terms at 45 days"))
    if not v.get("ip_transfer"):
        items.append(ReviewItem("risk", "ip_transfer", "Ownership is not stated. A common term is: rights transfer "
                                                       "on full payment"))
    if not v.get("cancellation"):
        items.append(ReviewItem("info", "cancellation", "No cancellation term. Say what happens to the advance if the "
                                                         "project stops"))
    if pending_proposals:
        items.append(ReviewItem(
            "info", None, f"{pending_proposals} Scribe suggestion(s) are still waiting for a decision"))
    return items
