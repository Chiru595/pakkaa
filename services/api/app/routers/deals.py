from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db, utcnow
from app.deps import get_llm_dep, get_nexconn
from app.domain import terms as T
from app.domain.deal import (
    current_hash,
    decide_proposal,
    get_sheet,
    require_deal_room,
    require_participant,
    set_term,
    signatures,
)
from app.domain.fair_price import assess
from app.domain.rooms import post_card
from app.integrations.nexconn import NexconnClient
from app.llm import LLM
from app.memory import signals as memory_signals
from app.models import (
    TEAM_ROLES,
    ChatMessage,
    CopilotProfile,
    DealOutcome,
    PaymentLink,
    ProposalStatus,
    Room,
    RoomMember,
    RoomSide,
    TermProposal,
    TermSheet,
    TermSheetStatus,
    User,
)
from app.money import MAX_AMOUNT_PAISE, format_inr
from app.rules import load_pack
from app.schemas import bounded_text
from app.security import audit
from app.security.deps import current_user, membership_for, rate_limit, room_access

router = APIRouter(prefix="/api/rooms", tags=["deals"])


class TermOut(BaseModel):
    key: str
    label: str
    kind: str
    required: bool
    value: Any = None
    display: str
    via: str | None = None
    source_message_ids: list[str] = []


class SignatureOut(BaseModel):
    side: str
    name: str
    signed_at: datetime


class ProposalOut(BaseModel):
    id: str
    term_key: str
    label: str
    value: Any
    display: str
    quote: str
    confidence_pct: int
    source_message_id: str | None


class PaymentLinkOut(BaseModel):
    id: str
    amount_paise: int
    amount_display: str
    status: str
    provider: str
    url: str | None = None
    created_at: datetime
    paid_at: datetime | None


class TermSheetOut(BaseModel):
    id: str
    version: int
    status: str
    category: str | None
    hash: str
    locked_hash: str | None
    locked_at: datetime | None
    terms: list[TermOut]
    advance_paise: int | None
    advance_display: str | None
    signatures: list[SignatureOut]


class OutcomeOut(BaseModel):
    outcome: str
    first_ask_display: str | None
    final_display: str | None
    note: str | None
    recorded_at: datetime


class DealOut(BaseModel):
    room_id: str
    my_side: str
    term_sheet: TermSheetOut
    proposals: list[ProposalOut]
    advance_link: PaymentLinkOut | None
    disclaimer: str
    counterparty_notes: list[str] = []
    outcome: OutcomeOut | None = None


class OutcomeIn(BaseModel):
    outcome: Literal["won", "lost"]
    note: bounded_text(0, 300) | None = None  # type: ignore[valid-type]


class TermUpdate(BaseModel):
    key: T.TermKey
    value: str | int = Field(union_mode="left_to_right")


class ProfileIn(BaseModel):
    walk_away_paise: int | None = Field(default=None, gt=0, le=MAX_AMOUNT_PAISE)
    target_paise: int | None = Field(default=None, gt=0, le=MAX_AMOUNT_PAISE)
    notes: bounded_text(0, 1000, multiline=True) | None = None  # type: ignore[valid-type]


class ProfileOut(ProfileIn):
    side: str


class FairPriceOut(BaseModel):
    category: str | None
    category_label: str | None
    categories: dict[str, str]
    offer_paise: int | None
    band: dict[str, int] | None
    position: str | None
    verdict: str
    comparisons: list[dict[str, Any]]
    source: str
    as_of: str
    disclaimer: str


class ReviewItemOut(BaseModel):
    level: str
    term_key: str | None
    message: str


DISCLAIMER = "Pakkaa suggests; you decide. Not legal or financial advice."


def _context(db: Session, user: User, room_id: str) -> tuple[Room, RoomMember]:
    room, _, member = room_access(db, user, room_id)
    require_deal_room(room)
    return room, require_participant(member)


def _message_ref(db: Session, ids: list[str]) -> list[str]:
    """Maps our message ids to the ids clients see (Nexconn message ids) so the UI can link to the source."""
    if not ids:
        return []
    rows = db.execute(select(ChatMessage.id, ChatMessage.nexconn_message_id).where(ChatMessage.id.in_(ids))).all()
    lookup = {r[0]: r[1] or r[0] for r in rows}
    return [lookup.get(i, i) for i in ids]


def link_out(link: PaymentLink, url: str | None = None) -> PaymentLinkOut:
    return PaymentLinkOut(id=link.id, amount_paise=link.amount_paise, amount_display=format_inr(link.amount_paise),
                          status=link.status, provider=link.provider, url=url, created_at=link.created_at,
                          paid_at=link.paid_at)


def sheet_out(db: Session, sheet: TermSheet) -> TermSheetOut:
    terms_out = []
    for d in T.TERM_DEFS.values():
        entry = sheet.terms.get(d.key) or {}
        value = entry.get("value")
        terms_out.append(TermOut(key=d.key, label=d.label, kind=d.kind, required=d.required, value=value,
                                 display=T.display(d.key, value), via=entry.get("via"),
                                 source_message_ids=_message_ref(db, entry.get("source_message_ids") or [])))
    sigs = signatures(db, sheet)
    names = {u.id: u.name for u in db.execute(select(User).where(User.id.in_([s.user_id for s in sigs]))).scalars()}
    adv = T.advance_paise(sheet.terms)
    return TermSheetOut(id=sheet.id, version=sheet.version, status=sheet.status, category=sheet.category,
                        hash=current_hash(sheet), locked_hash=sheet.locked_hash, locked_at=sheet.locked_at,
                        terms=terms_out, advance_paise=adv, advance_display=format_inr(adv) if adv else None,
                        signatures=[SignatureOut(side=s.side, name=names.get(s.user_id, "?"), signed_at=s.signed_at)
                                    for s in sigs])


def deal_out(db: Session, room: Room, member: RoomMember) -> DealOut:
    sheet = get_sheet(db, room)
    pending = db.execute(select(TermProposal).where(
        TermProposal.room_id == room.id, TermProposal.status == ProposalStatus.PENDING)
        .order_by(TermProposal.created_at)).scalars().all()
    link = db.execute(select(PaymentLink).where(PaymentLink.room_id == room.id)
                      .order_by(PaymentLink.created_at.desc()).limit(1)).scalar_one_or_none()
    return DealOut(
        room_id=room.id, my_side=member.side, term_sheet=sheet_out(db, sheet),
        proposals=[ProposalOut(id=p.id, term_key=p.term_key, label=T.TERM_DEFS[p.term_key].label, value=p.value,
                               display=T.display(p.term_key, p.value), quote=p.quote,
                               confidence_pct=p.confidence_pct,
                               source_message_id=(_message_ref(db, [p.source_message_id]) or [None])[0]
                               if p.source_message_id else None) for p in pending],
        advance_link=link_out(link, f"{get_settings().public_url}/pay/{link.public_token}") if link else None,
        disclaimer=DISCLAIMER, counterparty_notes=_counterparty_notes(db, room, member),
        outcome=_outcome_out(db, room))


def _counterparty_notes(db: Session, room: Room, member: RoomMember) -> list[str]:
    """Only the team's own side sees what the team remembers about its client."""
    if member.side != RoomSide.PROVIDER:
        return []
    m = membership_for(db, member.user_id, room.org_id)
    if m is None or m.role not in TEAM_ROLES:
        return []
    clients = db.execute(select(RoomMember.user_id).where(RoomMember.room_id == room.id,
                                                          RoomMember.side == RoomSide.CLIENT)).scalars().all()
    return memory_signals.counterparty_notes(db, room.org_id, list(clients))


def _outcome_out(db: Session, room: Room) -> OutcomeOut | None:
    o = db.get(DealOutcome, room.id)
    if o is None:
        return None
    return OutcomeOut(outcome=o.outcome,
                      first_ask_display=format_inr(o.first_ask_paise) if o.first_ask_paise else None,
                      final_display=format_inr(o.final_paise) if o.final_paise else None, note=o.note,
                      recorded_at=o.created_at)


@router.get("/{room_id}/deal", response_model=DealOut)
def get_deal(room_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> DealOut:
    room, member = _context(db, user, room_id)
    out = deal_out(db, room, member)
    db.commit()
    return out


@router.patch("/{room_id}/term-sheet", response_model=DealOut,
             dependencies=[Depends(rate_limit("term-edit", 60, 60))])
def update_term(room_id: str, body: TermUpdate, user: User = Depends(current_user), db: Session = Depends(get_db),
                settings: Settings = Depends(get_settings), nexconn: NexconnClient = Depends(get_nexconn),
                llm: LLM = Depends(get_llm_dep)) -> DealOut:
    room, member = _context(db, user, room_id)
    sheet = get_sheet(db, room, lock=True)
    was_locked = sheet.status == "locked"
    try:
        changed = set_term(db, room, sheet, body.key, body.value, user, via="manual")
    except T.TermValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    if changed:
        memory_signals.manual_correction(db, llm, room, body.key, sheet.terms[body.key]["value"], user)
    db.commit()
    if changed:
        note = " The earlier lock is void; both sides must sign again." if was_locked else ""
        post_card(db, nexconn, settings, room, {
            "kind": "term_update", "v": 1, "version": sheet.version,
            "title": f"{user.name} set {T.TERM_DEFS[body.key].label.lower()} to "
                     f"{T.display(body.key, sheet.terms[body.key]['value'])} (v{sheet.version}).{note}"})
    return deal_out(db, room, member)


@router.post("/{room_id}/proposals/{proposal_id}/{decision}", response_model=DealOut,
             dependencies=[Depends(rate_limit("proposal", 60, 60))])
def decide(room_id: str, proposal_id: str, decision: str, user: User = Depends(current_user),
           db: Session = Depends(get_db), settings: Settings = Depends(get_settings),
           nexconn: NexconnClient = Depends(get_nexconn), llm: LLM = Depends(get_llm_dep)) -> DealOut:
    if decision not in ("accept", "reject"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    room, member = _context(db, user, room_id)
    proposal = db.get(TermProposal, proposal_id)
    if proposal is None or proposal.room_id != room.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    sheet = get_sheet(db, room, lock=True)
    decide_proposal(db, room, sheet, proposal, user, decision == "accept")
    if decision == "reject":
        memory_signals.proposal_rejected(db, llm, room, proposal, user)
    db.commit()
    if decision == "accept":
        post_card(db, nexconn, settings, room, {
            "kind": "term_update", "v": 1, "version": sheet.version,
            "title": f"{user.name} confirmed {T.TERM_DEFS[proposal.term_key].label.lower()}: "
                     f"{T.display(proposal.term_key, proposal.value)} (v{sheet.version})"})
    return deal_out(db, room, member)


@router.get("/{room_id}/copilot/profile", response_model=ProfileOut)
def get_profile(room_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> ProfileOut:
    room, member = _context(db, user, room_id)
    p = db.get(CopilotProfile, (room.id, user.id))
    return ProfileOut(side=member.side, walk_away_paise=p.walk_away_paise if p else None,
                      target_paise=p.target_paise if p else None, notes=p.notes if p else None)


@router.put("/{room_id}/copilot/profile", response_model=ProfileOut,
             dependencies=[Depends(rate_limit("profile", 30, 60))])
def put_profile(room_id: str, body: ProfileIn, user: User = Depends(current_user),
                db: Session = Depends(get_db)) -> ProfileOut:
    room, member = _context(db, user, room_id)
    p = db.get(CopilotProfile, (room.id, user.id)) or CopilotProfile(room_id=room.id, user_id=user.id)
    p.walk_away_paise, p.target_paise = body.walk_away_paise, body.target_paise
    p.notes = " ".join(body.notes.split()) if body.notes else None
    p.updated_at = utcnow()
    db.add(p)
    audit.record(db, "copilot.profile_set", actor_user_id=user.id, org_id=room.org_id, subject_type="room",
                 subject_id=room.id, payload={"has_walk_away": p.walk_away_paise is not None,
                                              "has_target": p.target_paise is not None})
    db.commit()
    return ProfileOut(side=member.side, walk_away_paise=p.walk_away_paise, target_paise=p.target_paise, notes=p.notes)


@router.get("/{room_id}/fair-price", response_model=FairPriceOut)
def fair_price(room_id: str, offer_paise: int | None = Query(default=None, gt=0, le=MAX_AMOUNT_PAISE),
               category: str | None = Query(default=None, max_length=40), user: User = Depends(current_user),
               db: Session = Depends(get_db)) -> FairPriceOut:
    room, _ = _context(db, user, room_id)
    sheet = get_sheet(db, room)
    pack = load_pack("rate_bands_in")
    if category is not None and category not in pack["categories"]:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown category")
    offer = offer_paise or T.values(sheet.terms).get("price_paise")
    client = db.execute(select(RoomMember.user_id).where(RoomMember.room_id == room.id, RoomMember.side == "client")
                        ).scalars().first()
    r = assess(db, org_id=room.org_id, room_id=room.id, category=category or sheet.category, offer_paise=offer,
               client_user_id=client)
    db.commit()
    return FairPriceOut(category=r.category, category_label=r.category_label,
                        categories={k: v["label"] for k, v in pack["categories"].items()}, offer_paise=r.offer_paise,
                        band=r.band, position=r.position, verdict=r.verdict,
                        comparisons=[{"label": c.label, "median_paise": c.median_paise, "count": c.count}
                                     for c in r.comparisons], source=r.source, as_of=r.as_of, disclaimer=r.disclaimer)


@router.get("/{room_id}/term-sheet/review", response_model=list[ReviewItemOut])
def lock_review(room_id: str, user: User = Depends(current_user),
                db: Session = Depends(get_db)) -> list[ReviewItemOut]:
    room, _ = _context(db, user, room_id)
    sheet = get_sheet(db, room)
    pending = db.execute(select(func.count()).select_from(TermProposal).where(
        TermProposal.room_id == room.id, TermProposal.status == ProposalStatus.PENDING)).scalar_one()
    db.commit()
    items = T.review(sheet.terms, pending)
    return [ReviewItemOut(level=i.level, term_key=i.term_key, message=i.message) for i in items]


@router.post("/{room_id}/outcome", response_model=DealOut, dependencies=[Depends(rate_limit("outcome", 20, 60))])
def record_outcome(room_id: str, body: OutcomeIn, user: User = Depends(current_user), db: Session = Depends(get_db),
                   settings: Settings = Depends(get_settings), nexconn: NexconnClient = Depends(get_nexconn),
                   llm: LLM = Depends(get_llm_dep)) -> DealOut:
    """The freelancer's team records how the deal ended, so the next negotiation can learn from it."""
    room, membership, member = room_access(db, user, room_id)
    require_deal_room(room)
    if member is None or member.side != RoomSide.PROVIDER or membership.role not in TEAM_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the freelancer's team records the outcome")
    if db.get(DealOutcome, room.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "The outcome is already recorded")
    sheet = get_sheet(db, room, lock=True)
    price = T.values(sheet.terms).get("price_paise")
    if body.outcome == "won" and sheet.status != TermSheetStatus.LOCKED:
        raise HTTPException(status.HTTP_409_CONFLICT, "A deal is won once both sides have locked the terms")
    note = " ".join(body.note.split()) if body.note else None
    outcome = DealOutcome(room_id=room.id, org_id=room.org_id, outcome=body.outcome,
                          first_ask_paise=memory_signals.first_ask_paise(db, sheet),
                          final_paise=int(price) if price and body.outcome == "won" else None, note=note or None,
                          recorded_by=user.id)
    db.add(outcome)
    db.flush()
    audit.record(db, "deal.outcome", actor_user_id=user.id, org_id=room.org_id, subject_type="room",
                 subject_id=room.id, payload={"outcome": body.outcome, "first_ask_paise": outcome.first_ask_paise,
                                              "final_paise": outcome.final_paise})
    memory_signals.deal_outcome(db, llm, room, sheet, outcome, user)
    db.commit()
    post_card(db, nexconn, settings, room, {"kind": "outcome", "v": 1, "outcome": body.outcome,
                                            "title": f"{user.name} marked this deal as {body.outcome}"},
              to_user_ids=team_side_chat_ids(db, room))
    return deal_out(db, room, member)


def team_side_chat_ids(db: Session, room: Room) -> list[str]:
    rows = db.execute(select(RoomMember).where(RoomMember.room_id == room.id,
                                               RoomMember.side == RoomSide.PROVIDER)).scalars()
    return [rm.user.chat_id for rm in rows]
