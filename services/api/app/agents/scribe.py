"""Scribe: reads each new Deal Room message and proposes term-sheet updates. A person must accept each one."""
from __future__ import annotations

import logging
from typing import Literal

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.domain import terms as T
from app.domain.deal import get_sheet
from app.domain.rooms import post_card
from app.integrations.nexconn import NexconnClient
from app.llm import LLM, Lane, LLMUnavailable, untrusted
from app.models import ChatMessage, MessageKind, ProposalStatus, Room, RoomMember, TermProposal
from app.security import audit

log = logging.getLogger(__name__)

MIN_CONFIDENCE = 0.55
CONTEXT_MESSAGES = 10


class ScribeProposal(BaseModel):
    term_key: Literal["scope", "deliverables", "price_paise", "advance_pct", "timeline_days", "revisions",
                      "payment_due_days", "ip_transfer", "cancellation"]
    value: str = Field(max_length=500)
    confidence: float = Field(ge=0, le=1)
    quote: str = Field(max_length=300)


class ScribeOut(BaseModel):
    proposals: list[ScribeProposal] = Field(default_factory=list, max_length=5)


TASK = """You are the Scribe for a freelance deal negotiation. Read the NEW message in the context of recent messages
and the current term sheet. Propose a term only when the NEW message states or clearly agrees to it.
Do not propose terms that are only questions, hypotheticals or one side's opening ask that the other side rejected.
Term keys and value formats:
- scope, deliverables, ip_transfer, cancellation: short plain text
- price_paise: the total project price as written, e.g. "45000" or "45k" or "1.2 lakh" (rupees)
- advance_pct: whole number percent, e.g. "40"
- timeline_days: whole number of days (convert weeks to days)
- revisions: whole number of revision rounds
- payment_due_days: days after delivery that the balance is due
Return confidence between 0 and 1 and quote the exact words you relied on (max 300 characters).
Return an empty list when nothing in the NEW message settles a term."""


def build_input(room: Room, sheet_terms: dict, history: list[tuple[str, str]], new: tuple[str, str]) -> str:
    current = {k: T.display(k, v) for k, v in T.values(sheet_terms).items()}
    lines = [f"Deal: {room.title}", f"Current term sheet: {current or 'empty'}", "Recent messages (oldest first):"]
    lines += [untrusted(f"{side} message", text) for side, text in history]
    lines += ["NEW message:", untrusted(f"{new[0]} message", new[1])]
    return "\n".join(lines)


def run(db: Session, llm: LLM, nexconn: NexconnClient, settings: Settings, room_id: str, message_id: str) -> int:
    """Returns the number of new proposals. Never changes the term sheet itself."""
    room = db.get(Room, room_id)
    msg = db.get(ChatMessage, message_id)
    if room is None or msg is None or msg.kind != MessageKind.TEXT or not llm.available:
        return 0
    sheet = get_sheet(db, room)
    members = db.execute(select(RoomMember).where(RoomMember.room_id == room.id)).scalars()
    sides = {rm.user_id: rm.side for rm in members}
    recent = db.execute(select(ChatMessage).where(
        ChatMessage.room_id == room.id, ChatMessage.kind == MessageKind.TEXT, ChatMessage.created_at < msg.created_at)
        .order_by(ChatMessage.created_at.desc()).limit(CONTEXT_MESSAGES)).scalars().all()
    history = [(sides.get(m.sender_user_id or "", "unknown"), m.body) for m in reversed(recent)]
    data = build_input(room, sheet.terms, history, (sides.get(msg.sender_user_id or "", "unknown"), msg.body))
    try:
        out = llm.structured(Lane.SMART, TASK, data, ScribeOut, max_output_tokens=800, purpose="scribe")
    except LLMUnavailable as exc:
        log.info("scribe skipped: %s", exc)
        return 0
    current = T.values(sheet.terms)
    created = 0
    for p in out.proposals:
        if p.confidence < MIN_CONFIDENCE:
            continue
        try:
            value = T.coerce_value(p.term_key, p.value)
        except T.TermValueError:
            log.info("scribe proposal dropped: invalid %s", p.term_key)
            continue
        if current.get(p.term_key) == value:
            continue
        for old in db.execute(select(TermProposal).where(
                TermProposal.room_id == room.id, TermProposal.term_key == p.term_key,
                TermProposal.status == ProposalStatus.PENDING)).scalars():
            old.status = ProposalStatus.SUPERSEDED
        db.add(TermProposal(room_id=room.id, term_key=p.term_key, value=value, quote=p.quote[:300],
                            source_message_id=msg.id, confidence_pct=round(p.confidence * 100)))
        created += 1
    if created:
        audit.record(db, "scribe.proposed", org_id=room.org_id, subject_type="room", subject_id=room.id,
                     payload={"message_id": msg.id, "count": created})
    db.commit()
    if created:
        post_card(db, nexconn, settings, room, {"kind": "scribe", "v": 1, "count": created,
                                                "title": f"Scribe noted {created} term{'s' if created > 1 else ''} "
                                                         "to confirm"})
    return created
