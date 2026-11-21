"""Pay Lane AI helpers: parse a typed payment request, and polish the approver brief. Code checks every output."""
from __future__ import annotations

import re
from dataclasses import dataclass

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import terms as T
from app.domain.risk import normalise_name
from app.llm import LLM, Lane, LLMUnavailable, untrusted
from app.models import Payee, Room, RoomMember, RoomType, TermSheet, User
from app.money import amounts_in_text


class ParsedRequest(BaseModel):
    payee_name: str = Field(default="", max_length=80)
    amount: str = Field(default="", max_length=40)
    reason: str = Field(default="", max_length=300)
    deal_hint: str = Field(default="", max_length=120)


PARSE_TASK = """Turn the team member's payment request into fields. payee_name is who should be paid, exactly as
written. amount is the amount as written (for example "40k" or "₹40,000"). reason is what the payment is for, in a
few words. deal_hint is any client or project name mentioned, else empty. Leave a field empty if it is not stated.
Do not guess."""

_REQUEST = re.compile(
    r"^\s*(?:please\s+)?(?:pay|send|transfer)\s+(?P<name>.+?)\s+(?P<amt>(?:₹|rs\.?\s*)?[\d,]+(?:\.\d+)?\s*(?:k|l|lakh|lakhs)?)"
    r"(?:\s+(?:for|towards)\s+(?P<reason>.+))?\s*$", re.I)


@dataclass
class Draft:
    payee_name: str
    amount_paise: int | None
    reason: str
    payee_matches: list[Payee]
    deal_matches: list[tuple[Room, str]]
    used_ai: bool


def parse_request(db: Session, llm: LLM, org_id: str, text: str) -> Draft:
    used_ai = False
    parsed: ParsedRequest | None = None
    if llm.available:
        try:
            parsed = llm.structured(Lane.SMART, PARSE_TASK, untrusted("payment request", text), ParsedRequest,
                                    max_output_tokens=300, purpose="card_parse")
            used_ai = True
        except LLMUnavailable:
            parsed = None
    if parsed is None:
        m = _REQUEST.match(text)
        parsed = ParsedRequest(payee_name=m.group("name") if m else "", amount=m.group("amt") if m else "",
                               reason=(m.group("reason") or "") if m else "")
    amounts = amounts_in_text(parsed.amount or "", min_rupees=1) or amounts_in_text(text, min_rupees=1)
    amount = amounts[0] if len(set(amounts)) == 1 else None
    payees = match_payees(db, org_id, parsed.payee_name)
    deals = match_deals(db, org_id, " ".join([parsed.deal_hint, parsed.reason, parsed.payee_name]))
    reason = " ".join(parsed.reason.split())[:300] or "Payment"
    return Draft(parsed.payee_name.strip()[:80], amount, reason, payees, deals, used_ai)


def match_payees(db: Session, org_id: str, name: str) -> list[Payee]:
    target = normalise_name(name)
    if not target:
        return []
    rows = db.execute(select(Payee).where(Payee.org_id == org_id)).scalars().all()
    exact = [p for p in rows if normalise_name(p.name) == target]
    return exact or [p for p in rows if target in normalise_name(p.name) or normalise_name(p.name) in target][:5]


def match_deals(db: Session, org_id: str, text: str) -> list[tuple[Room, str]]:
    words = {w for w in re.findall(r"[a-z]{3,}", text.casefold()) if w not in {"for", "the", "and", "work", "payment"}}
    if not words:
        return []
    out: list[tuple[int, Room, str]] = []
    for room in db.execute(select(Room).where(Room.org_id == org_id, Room.type == RoomType.DEAL)).scalars():
        sheet = db.execute(select(TermSheet).where(TermSheet.room_id == room.id)).scalar_one_or_none()
        names = [u.name for u in db.execute(select(User).join(RoomMember, RoomMember.user_id == User.id)
                                            .where(RoomMember.room_id == room.id)).scalars()]
        scope = str(T.values(sheet.terms).get("scope", "")) if sheet else ""
        hay = f"{room.title} {scope} {' '.join(names)}".casefold()
        score = sum(1 for w in words if w in hay)
        if score:
            price = T.values(sheet.terms).get("price_paise") if sheet else None
            out.append((score, room, T.display("price_paise", price) if price else ""))
    out.sort(key=lambda x: (-x[0], -x[1].created_at.timestamp()))
    return [(r, p) for _, r, p in out[:3]]


class Brief(BaseModel):
    deal_line: str = Field(max_length=220)
    payee_line: str = Field(max_length=220)
    rules_line: str = Field(max_length=220)


BRIEF_TASK = """Rewrite the three facts below as three short, plain lines for the person deciding whether to approve a
payment: deal_line (does it match a deal), payee_line (payee history), rules_line (risk rules that fired). Use only
the facts given; copy amounts and dates exactly. Never recommend approving or say the payment is approved, safe or
verified."""

_FORBIDDEN = re.compile(r"\b(approved?|safe to pay|go ahead|verified|legit)\b", re.I)


def polish_brief(llm: LLM, facts: Brief) -> tuple[Brief, bool]:
    """Returns the AI wording only if it keeps every amount from the facts and makes no approval claims."""
    if not llm.available:
        return facts, False
    data = f"deal: {facts.deal_line}\npayee: {facts.payee_line}\nrules: {facts.rules_line}"
    try:
        out = llm.structured(Lane.FAST, BRIEF_TASK, data, Brief, max_output_tokens=300, purpose="brief")
    except LLMUnavailable:
        return facts, False
    for mine, theirs in ((facts.deal_line, out.deal_line), (facts.payee_line, out.payee_line),
                         (facts.rules_line, out.rules_line)):
        if set(amounts_in_text(theirs, 1)) - set(amounts_in_text(mine, 1)) or _FORBIDDEN.search(theirs):
            return facts, False
    return out, True
