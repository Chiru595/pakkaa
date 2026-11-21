"""Turns interactions into memory: Scribe corrections, deal outcomes, client payment behaviour, payouts and
risk overrides. Each function adds items to the caller's transaction; the caller commits."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain import terms as T
from app.llm import LLM
from app.memory.store import remember
from app.models import (
    AuditEvent,
    DealOutcome,
    MemoryKind,
    MemoryVisibility,
    Payee,
    PaymentCard,
    PaymentLink,
    Room,
    RoomMember,
    RoomSide,
    TermProposal,
    TermSheet,
    User,
)
from app.money import format_inr

ADVANCE_GRACE_DAYS = 3
CORRECTION_WINDOW = timedelta(hours=24)


def _client_members(db: Session, room: Room) -> list[User]:
    rows = db.execute(select(RoomMember).where(RoomMember.room_id == room.id,
                                               RoomMember.side == RoomSide.CLIENT)).scalars()
    return [rm.user for rm in rows]


def proposal_rejected(db: Session, llm: LLM | None, room: Room, proposal: TermProposal, actor: User) -> None:
    label = T.TERM_DEFS[proposal.term_key].label.lower()
    remember(db, llm, org_id=room.org_id, kind=MemoryKind.CORRECTION,
             text=f'Scribe read {label} = {T.display(proposal.term_key, proposal.value)} from "{proposal.quote}", '
                  f"but the room rejected it.",
             source_type="term_proposal", source_id=proposal.id, subject_type="room", subject_id=room.id,
             data={"term_key": proposal.term_key, "proposed": proposal.value, "quote": proposal.quote,
                   "corrected": None}, actor_user_id=actor.id)


def manual_correction(db: Session, llm: LLM | None, room: Room, key: str, value: object, actor: User) -> None:
    """A person set a term the Scribe had recently read differently: a correction worth learning from."""
    recent = db.execute(select(TermProposal).where(
        TermProposal.room_id == room.id, TermProposal.term_key == key, TermProposal.decided_at.is_not(None),
        TermProposal.decided_at > utcnow() - CORRECTION_WINDOW).order_by(TermProposal.decided_at.desc()).limit(1)
    ).scalar_one_or_none()
    if recent is None or recent.value == value:
        return
    label = T.TERM_DEFS[key].label.lower()
    remember(db, llm, org_id=room.org_id, kind=MemoryKind.CORRECTION,
             text=f'Scribe read {label} = {T.display(key, recent.value)} from "{recent.quote}"; the room set it to '
                  f"{T.display(key, value)}.",
             source_type="term_proposal", source_id=recent.id, subject_type="room", subject_id=room.id,
             data={"term_key": key, "proposed": recent.value, "quote": recent.quote, "corrected": value},
             actor_user_id=actor.id)


def first_ask_paise(db: Session, sheet: TermSheet) -> int | None:
    row = db.execute(select(AuditEvent.payload).where(
        AuditEvent.action == "term.set", AuditEvent.subject_type == "term_sheet", AuditEvent.subject_id == sheet.id,
        AuditEvent.payload["key"].astext == "price_paise").order_by(AuditEvent.seq).limit(1)).scalar_one_or_none()
    if row and row.get("value") is not None:
        return int(row["value"])
    price = T.values(sheet.terms).get("price_paise")
    return int(price) if price else None


def deal_outcome(db: Session, llm: LLM | None, room: Room, sheet: TermSheet, outcome: DealOutcome,
                 actor: User) -> None:
    clients = _client_members(db, room)
    who = ", ".join(c.name for c in clients) or "the client"
    first, final = outcome.first_ask_paise, outcome.final_paise
    detail = ""
    if first and final:
        change = round((final - first) * 100 / first)
        detail = f" Final {format_inr(final)} vs first ask {format_inr(first)} ({change:+d}%)."
    elif final:
        detail = f" Final {format_inr(final)}."
    text = f'Deal "{room.title}" with {who} was {outcome.outcome}.{detail}'
    if outcome.note:
        text += f" Note: {outcome.note}"
    remember(db, llm, org_id=room.org_id, kind=MemoryKind.PLAYBOOK, text=text, source_type="deal_outcome",
             source_id=room.id, subject_type="room", subject_id=room.id,
             data={"outcome": outcome.outcome, "first_ask_paise": first, "final_paise": final,
                   "category": sheet.category, "client_ids": [c.id for c in clients]}, actor_user_id=actor.id)
    for c in clients:
        remember(db, llm, org_id=room.org_id, kind=MemoryKind.COUNTERPARTY,
                 text=f'{c.name}: deal "{room.title}" {outcome.outcome}.{detail}', source_type="deal_outcome",
                 source_id=room.id, subject_type="user", subject_id=c.id,
                 data={"outcome": outcome.outcome, "final_paise": final}, actor_user_id=actor.id)


def advance_paid(db: Session, llm: LLM | None, link: PaymentLink) -> None:
    """Records how long the client took to pay the advance after both sides locked the terms."""
    room = db.get(Room, link.room_id)
    sheet = db.get(TermSheet, link.term_sheet_id)
    if room is None or link.paid_at is None:
        return
    start = (sheet.locked_at if sheet and sheet.locked_at else None) or link.created_at
    days = max(0, (link.paid_at - start).days)
    late = max(0, days - ADVANCE_GRACE_DAYS)
    for c in _client_members(db, room):
        timing = f"{late} days late" if late else "on time"
        remember(db, llm, org_id=room.org_id, kind=MemoryKind.COUNTERPARTY,
                 text=f'{c.name} paid the {format_inr(link.amount_paise)} advance for "{room.title}" {days} days '
                      f"after the terms were locked ({timing}).",
                 source_type="payment_link", source_id=link.id, subject_type="user", subject_id=c.id,
                 data={"days_to_pay": days, "days_late": late, "amount_paise": link.amount_paise,
                       "room_id": room.id})


def payout_made(db: Session, llm: LLM | None, card: PaymentCard, payee: Payee, actor: User) -> None:
    remember(db, llm, org_id=card.org_id, kind=MemoryKind.PAYEE,
             text=f"Paid {payee.name} {format_inr(card.amount_paise)} for {card.reason} "
                  f"(card #{card.number}, {utcnow():%d %b %Y}).",
             source_type="payment_card", source_id=card.id, subject_type="payee", subject_id=payee.id,
             data={"amount_paise": card.amount_paise, "risk_score": card.risk_score}, actor_user_id=actor.id)


def risk_override(db: Session, llm: LLM | None, card: PaymentCard, actor: User, reason: str) -> None:
    remember(db, llm, org_id=card.org_id, kind=MemoryKind.RISK_OVERRIDE,
             text=f"{actor.name} approved high-risk card #{card.number} ({format_inr(card.amount_paise)}, risk "
                  f"{card.risk_score}) because: {reason}",
             source_type="payment_card", source_id=card.id, subject_type="payment_card", subject_id=card.id,
             data={"risk_score": card.risk_score, "reason": reason, "version": card.version},
             actor_user_id=actor.id)


def counterparty_notes(db: Session, org_id: str, client_ids: list[str]) -> list[str]:
    from app.memory.store import counterparty_items

    items = counterparty_items(db, org_id, client_ids)
    late_first = sorted(items, key=lambda m: (-(m.data or {}).get("days_late", 0), -m.created_at.timestamp()))
    return [m.text for m in late_first]


__all__ = ["ADVANCE_GRACE_DAYS", "MemoryVisibility", "advance_paid", "counterparty_notes", "deal_outcome",
           "first_ask_paise", "manual_correction", "payout_made", "proposal_rejected", "risk_override"]
