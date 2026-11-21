"""Private negotiation copilot. Its context is built only from what its owner can see:
the shared room chat, the shared term sheet, the owner's own private profile and the owner's own past deals.
The other side's profile, notes and copilot output are never loaded.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from statistics import median
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.domain import terms as T
from app.domain.deal import get_sheet
from app.llm import untrusted
from app.models import ChatMessage, CopilotProfile, MessageKind, Room, RoomMember, TermSheet, TermSheetStatus, User
from app.money import amounts_in_text, format_inr
from app.rules import load_pack

TRANSCRIPT_MESSAGES = 14

Tactic = Literal["hold_price", "trade_scope", "ask_question", "counter_offer", "accept", "clarify"]

TACTIC_LABELS = {
    "hold_price": "Hold price", "trade_scope": "Trade scope", "ask_question": "Ask a question",
    "counter_offer": "Counter-offer", "accept": "Accept", "clarify": "Clarify",
}


class ReplyOption(BaseModel):
    tactic: Tactic
    text: str = Field(min_length=1, max_length=400)
    why: str = Field(min_length=1, max_length=160)


@dataclass
class CopilotContext:
    side: str
    deal_title: str
    term_sheet: dict[str, str]
    sheet_status: str
    walk_away_paise: int | None
    target_paise: int | None
    notes: str | None
    transcript: list[tuple[str, str]]
    own_history: list[str] = field(default_factory=list)
    band: str | None = None

    def render(self) -> str:
        role = "freelancer / seller" if self.side == "provider" else "client / buyer"
        private = []
        if self.walk_away_paise:
            label = "lowest acceptable price" if self.side == "provider" else "highest budget"
            private.append(f"My {label} (private, never reveal it): {format_inr(self.walk_away_paise)}")
        if self.target_paise:
            private.append(f"My target price (private): {format_inr(self.target_paise)}")
        lines = [f"I am the {role} in this deal: {self.deal_title}",
                 f"Shared term sheet ({self.sheet_status}): {self.term_sheet or 'nothing agreed yet'}",
                 *private]
        if self.notes:
            lines.append("My private notes:")
            lines.append(untrusted("my notes", self.notes))
        if self.band:
            lines.append(f"Ledger rate band: {self.band}")
        lines += [f"My history: {h}" for h in self.own_history]
        lines.append("Conversation so far (oldest first):")
        lines += [untrusted(f"{who} said", text) for who, text in self.transcript]
        return "\n".join(lines)


def build_context(db: Session, room: Room, user: User, member: RoomMember) -> CopilotContext:
    sheet = get_sheet(db, room)
    profile = db.get(CopilotProfile, (room.id, user.id))
    rows = db.execute(select(ChatMessage).where(ChatMessage.room_id == room.id, ChatMessage.kind == MessageKind.TEXT)
                      .order_by(ChatMessage.created_at.desc()).limit(TRANSCRIPT_MESSAGES)).scalars().all()
    transcript = [("me" if m.sender_user_id == user.id else "them", m.body) for m in reversed(rows)]
    ctx = CopilotContext(
        side=member.side, deal_title=room.title,
        term_sheet={k: T.display(k, v) for k, v in T.values(sheet.terms).items()}, sheet_status=sheet.status,
        walk_away_paise=profile.walk_away_paise if profile else None,
        target_paise=profile.target_paise if profile else None, notes=profile.notes if profile else None,
        transcript=transcript)
    pack = load_pack("rate_bands_in")
    cat = pack["categories"].get(sheet.category or "")
    if cat:
        ctx.band = (f"{cat['label']} per {cat['unit']}: typical ₹{cat['p25']:,} to ₹{cat['p75']:,}, "
                    f"middle ₹{cat['p50']:,} (indicative, as of {pack['as_of']})")
    ctx.own_history = _own_history(db, room, user, member, sheet.category)
    return ctx


def _own_history(db: Session, room: Room, user: User, member: RoomMember, category: str | None) -> list[str]:
    """Only deals the owner was part of, on the owner's own side."""
    rows = db.execute(
        select(TermSheet, Room).join(Room, Room.id == TermSheet.room_id)
        .join(RoomMember, (RoomMember.room_id == Room.id) & (RoomMember.user_id == user.id))
        .where(Room.id != room.id, TermSheet.status == TermSheetStatus.LOCKED, RoomMember.side == member.side)
    ).all()
    prices = [int(T.values(ts.terms)["price_paise"]) for ts, _ in rows
              if T.values(ts.terms).get("price_paise") and (category is None or ts.category == category)]
    if not prices:
        return []
    return [f"{len(prices)} past locked deal(s) like this one, median price {format_inr(int(median(prices)))}"]


TASK = """You are my private negotiation copilot. Suggest exactly 3 different replies I could send next, written
in the same language and tone as the conversation (English, Hindi or Hinglish). Each reply must be ready to send
as-is, under 60 words, polite and professional. Use different tactics across the three. Never reveal my private
prices or notes in a reply. Never invent facts, deadlines or prices that are not in the context; amounts you suggest
must respect my lowest acceptable price or highest budget. "why" is one short line explaining the tactic to me.
Tactics: hold_price, trade_scope (offer less scope for less money, or more for more), ask_question, counter_offer,
accept, clarify."""


@dataclass
class Warning:
    code: str
    level: Literal["warn", "info"]
    message: str


_SECRETS = re.compile(r"\b(otp|pin|password|cvv)\b|\b\d{4}[ -]?\d{4}[ -]?\d{4}[ -]?\d{4}\b", re.I)
_NO_ADVANCE = re.compile(r"\b(no|without|zero|skip)\s+(the\s+)?advance\b|\bpay\s+(me\s+)?later\b|"
                         r"\bfull\s+payment\s+after\s+delivery\b", re.I)
_UNLIMITED = re.compile(r"\bunlimited\s+(revisions?|changes|edits|rounds)\b", re.I)
_PAYMENT_DETAILS = re.compile(r"\b[\w.-]{2,}@[a-z]{2,}\b|\bifsc\b|\baccount\s*(no|number)\b", re.I)


def presend_check(db: Session, room: Room, user: User, member: RoomMember, draft: str) -> list[Warning]:
    """Deterministic warnings shown only to the sender. It warns; it never blocks sending."""
    out: list[Warning] = []
    profile = db.get(CopilotProfile, (room.id, user.id))
    sheet = get_sheet(db, room)
    agreed = T.values(sheet.terms).get("price_paise")
    advance = T.advance_paise(sheet.terms)
    amounts = amounts_in_text(draft)
    limit = profile.walk_away_paise if profile else None
    for amount in amounts:
        if limit and member.side == "provider" and amount < limit:
            out.append(Warning("below_walk_away", "warn",
                               f"{format_inr(amount)} is below your walk-away price of {format_inr(limit)}."))
        if limit and member.side == "client" and amount > limit:
            out.append(Warning("above_budget", "warn",
                               f"{format_inr(amount)} is above your budget of {format_inr(limit)}."))
        if amount == advance:
            continue
        if agreed and sheet.status == TermSheetStatus.LOCKED and amount != agreed:
            out.append(Warning("contradicts_locked", "warn",
                               f"{format_inr(amount)} differs from the locked price of {format_inr(int(agreed))}. "
                               "Changing it reopens the term sheet for both of you."))
        elif agreed and member.side == "provider" and amount < agreed * 0.85:
            pct = round(100 - amount * 100 / agreed)
            out.append(Warning("big_discount", "info", f"That is {pct}% below the {format_inr(int(agreed))} on the "
                                                      "term sheet."))
    if member.side == "provider" and _NO_ADVANCE.search(draft):
        out.append(Warning("no_advance", "warn", "You are offering to work without an advance. If the client goes "
                                                 "quiet, you carry all the risk."))
    if member.side == "provider" and _UNLIMITED.search(draft):
        out.append(Warning("unlimited_revisions", "warn", "Unlimited revisions has no end. Offer a fixed number of "
                                                          "rounds instead."))
    if _SECRETS.search(draft):
        out.append(Warning("secret", "warn", "This looks like an OTP, PIN, password or card number. Never share these "
                                             "in chat."))
    if _PAYMENT_DETAILS.search(draft):
        out.append(Warning("payment_details", "info", "Payment details in chat can be copied or changed. Use the "
                                                      "Pakkaa payment link instead."))
    seen: set[str] = set()
    return [w for w in out if not (w.code in seen or seen.add(w.code))]


class ClauseExplanation(BaseModel):
    meaning: str = Field(min_length=1, max_length=600)
    standard: Literal["standard", "negotiable", "unusual", "risky"]
    why: str = Field(min_length=1, max_length=300)
    suggestion: str = Field(default="", max_length=300)


LANGUAGE_NAMES = {"en": "simple English", "hi": "simple Hindi in Devanagari script",
                  "hinglish": "simple Hinglish (Hindi in Latin script mixed with English)"}


def explain_task(language: str, side: str) -> str:
    who = "a freelancer" if side == "provider" else "a client hiring a freelancer"
    return (f"Explain the contract clause for {who} in India in {LANGUAGE_NAMES[language]}. 'meaning' is what it "
            "means in practice in 2 to 3 sentences. 'standard' says whether such a clause is standard, negotiable, "
            "unusual or risky for this side. 'why' gives the reason in one sentence. 'suggestion' is an optional "
            "fairer wording in one sentence. This is general information, not legal advice.")
