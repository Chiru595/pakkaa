"""Deal Room state: the term sheet, who is on which side, and every change to agreed terms."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain import terms as T
from app.domain.fair_price import detect_category
from app.models import (
    ProposalStatus,
    Room,
    RoomMember,
    RoomSide,
    RoomType,
    TermProposal,
    TermSheet,
    TermSheetSignature,
    TermSheetStatus,
    User,
)
from app.security import audit


def require_deal_room(room: Room) -> None:
    if room.type != RoomType.DEAL:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")


def require_participant(member: RoomMember | None) -> RoomMember:
    if member is None or member.side not in (RoomSide.PROVIDER, RoomSide.CLIENT):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Join the deal room first")
    return member


def get_sheet(db: Session, room: Room, *, lock: bool = False) -> TermSheet:
    q = select(TermSheet).where(TermSheet.room_id == room.id)
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    sheet = db.execute(q).scalar_one_or_none()
    if sheet is None:
        sheet = TermSheet(room_id=room.id, version=1, terms={}, category=detect_category(room.title))
        db.add(sheet)
        db.flush()
    return sheet


def current_hash(sheet: TermSheet) -> str:
    return T.sheet_hash(sheet.room_id, sheet.version, sheet.terms)


def set_term(db: Session, room: Room, sheet: TermSheet, key: str, raw: Any, actor: User, *, via: str,
             source_message_ids: list[str] | None = None) -> bool:
    """Validates and applies one term. Any change bumps the version and voids an earlier lock."""
    value = T.coerce_value(key, raw)
    existing = sheet.terms.get(key) or {}
    if existing.get("value") == value:
        return False
    was_locked = sheet.status == TermSheetStatus.LOCKED
    sheet.terms = {**sheet.terms, key: {
        "value": value, "source_message_ids": source_message_ids or [], "updated_by": actor.id, "via": via,
        "updated_at": utcnow().isoformat(),
    }}
    sheet.version += 1
    sheet.status = TermSheetStatus.DRAFT
    sheet.locked_hash = None
    sheet.locked_at = None
    sheet.updated_at = utcnow()
    if key == "scope" and not sheet.category:
        sheet.category = detect_category(room.title, str(value))
    audit.record(db, "term.set", actor_user_id=actor.id, org_id=room.org_id, subject_type="term_sheet",
                 subject_id=sheet.id, payload={"key": key, "value": value, "version": sheet.version, "via": via,
                                               "voided_lock": was_locked, "sources": source_message_ids or []})
    return True


def decide_proposal(db: Session, room: Room, sheet: TermSheet, proposal: TermProposal, actor: User,
                    accept: bool) -> None:
    if proposal.status != ProposalStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "This suggestion was already handled")
    proposal.decided_by, proposal.decided_at = actor.id, utcnow()
    if accept:
        try:
            set_term(db, room, sheet, proposal.term_key, proposal.value, actor, via="scribe",
                     source_message_ids=[proposal.source_message_id] if proposal.source_message_id else [])
        except T.TermValueError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
        proposal.status = ProposalStatus.ACCEPTED
    else:
        proposal.status = ProposalStatus.REJECTED
    audit.record(db, "scribe.proposal_decided", actor_user_id=actor.id, org_id=room.org_id,
                 subject_type="term_proposal", subject_id=proposal.id,
                 payload={"accepted": accept, "key": proposal.term_key, "value": proposal.value})


def signatures(db: Session, sheet: TermSheet) -> list[TermSheetSignature]:
    return list(db.execute(select(TermSheetSignature).where(
        TermSheetSignature.term_sheet_id == sheet.id, TermSheetSignature.version == sheet.version)).scalars())
