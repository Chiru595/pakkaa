from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents import paylane_ai as AI
from app.config import Settings, get_settings
from app.db import get_db
from app.deps import get_llm_dep, get_nexconn
from app.domain import paylane as P
from app.domain import terms as T
from app.domain.policy import TUNABLES, effective_risk, set_threshold
from app.domain.policy import history as policy_history
from app.domain.risk import IST, name_collisions, policy
from app.domain.rooms import post_card
from app.integrations.nexconn import NexconnClient
from app.llm import LLM
from app.memory import signals as memory_signals
from app.models import (
    TEAM_ROLES,
    CardState,
    Membership,
    Payee,
    PaymentCard,
    Payout,
    RiskSignal,
    Room,
    RoomType,
    TermSheet,
    TermSheetStatus,
    User,
)
from app.money import MAX_AMOUNT_PAISE, format_inr
from app.payments import PaymentProvider, get_payment_provider
from app.routers.auth import otp_provider
from app.schemas import ObjectId, OtpCode, bounded_text
from app.security import audit
from app.security.deps import current_user, membership_for, rate_limit, require_org_role, room_access
from app.security.otp import (
    MAX_PER_HOUR,
    TEST_MODE_MAX_PER_HOUR,
    OtpError,
    request_otp,
    verify_otp,
)

router = APIRouter(prefix="/api", tags=["paylane"])


class PayeeRef(BaseModel):
    id: str
    name: str
    masked: str
    gstin: str | None
    details_changed_at: datetime | None


class PersonRef(BaseModel):
    id: str
    name: str
    role: str | None
    phone_last4: str
    verified: bool
    name_collisions: list[dict[str, str]]


class SignalOut(BaseModel):
    rule_id: str
    weight: int
    message: str
    evidence: dict[str, Any]


class ApprovalOut(BaseModel):
    name: str
    role: str
    at: datetime


class ViewerOut(BaseModel):
    can_approve: bool
    can_edit: bool
    can_payout: bool
    can_step_up: bool
    can_reject: bool
    is_approver: bool
    blocked_reason: str | None


class CardOut(BaseModel):
    id: str
    number: int
    room_id: str
    state: str
    version: int
    amount_paise: int
    amount_display: str
    reason: str
    source: str
    payee: PayeeRef | None
    payee_name_claimed: str | None
    deal: dict[str, Any] | None
    requester: PersonRef | None
    risk_score: int
    risk_level: str
    signals: list[SignalOut]
    required_approvals: int
    owner_approval_required: bool
    approvals: list[ApprovalOut]
    step_up_required: bool
    step_up_done: bool
    step_up_at: datetime | None
    card_hash: str
    payout: dict[str, Any] | None
    viewer: ViewerOut
    created_at: datetime


class CardIn(BaseModel):
    payee_id: ObjectId
    amount_paise: int = Field(gt=0, le=MAX_AMOUNT_PAISE)
    reason: bounded_text(2, 300)  # type: ignore[valid-type]
    deal_room_id: ObjectId | None = None
    request_text: bounded_text(0, 400, multiline=True) | None = None  # type: ignore[valid-type]


class CardEdit(BaseModel):
    amount_paise: int | None = Field(default=None, gt=0, le=MAX_AMOUNT_PAISE)
    payee_id: str | None = Field(default=None, max_length=32)
    reason: str | None = Field(default=None, min_length=2, max_length=300, pattern=r"^[^\x00-\x1f\x7f]+$")


class ParseIn(BaseModel):
    text: bounded_text(3, 400)  # type: ignore[valid-type]


class ParseOut(BaseModel):
    payee_name: str
    amount_paise: int | None
    amount_display: str | None
    reason: str
    payees: list[PayeeRef]
    deals: list[dict[str, Any]]
    used_ai: bool


class ApproveIn(BaseModel):
    version: int = Field(ge=1)
    override_reason: bounded_text(0, 300) | None = None  # type: ignore[valid-type]


class RejectIn(BaseModel):
    note: bounded_text(0, 200) = ""  # type: ignore[valid-type]
    suspected_fraud: bool = False


class ThresholdIn(BaseModel):
    key: Literal["high_at", "medium_at", "baseline_multiplier", "bank_change_window_days", "no_baseline_large_rupees"]
    value: float = Field(gt=0, le=1_000_000)
    reason: bounded_text(0, 300) = ""  # type: ignore[valid-type]


class BriefOut(BaseModel):
    lines: list[str]
    evidence: list[dict[str, str]]
    ai_worded: bool
    disclaimer: str = "The brief only summarises facts. It never approves anything."


class StepUpTarget(BaseModel):
    version: int = Field(ge=1)
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class StepUpRequestOut(StepUpTarget):
    phone_last4: str
    test_mode: bool
    test_code: str | None = None


class StepUpIn(StepUpTarget):
    code: OtpCode


class PayoutIn(BaseModel):
    idempotency_key: str = Field(min_length=16, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class FreezeIn(BaseModel):
    reason: bounded_text(3, 300)  # type: ignore[valid-type]


class ControlOut(BaseModel):
    frozen: bool
    frozen_by: str | None
    frozen_at: datetime | None
    reason: str | None
    unfreeze_votes: int
    voted: bool


def _lane(db: Session, user: User, room_id: str) -> tuple[Room, Membership]:
    room, membership, _ = room_access(db, user, room_id)
    if room.type != RoomType.PAYLANE:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if membership.role not in TEAM_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the team can see or raise payments")
    return room, membership


def _card(db: Session, user: User, card_id: str) -> tuple[PaymentCard, Membership]:
    card = db.get(PaymentCard, card_id)
    m = membership_for(db, user.id, card.org_id) if card else None
    if card is None or m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if m.role not in TEAM_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the team can see payments")
    return card, m


def _person(db: Session, org_id: str, user_id: str | None) -> PersonRef | None:
    u = db.get(User, user_id) if user_id else None
    if u is None:
        return None
    m = membership_for(db, u.id, org_id)
    return PersonRef(id=u.id, name=u.name, role=m.role if m else None, phone_last4=u.phone_last4,
                     verified=u.phone_verified_at is not None and m is not None and m.role in TEAM_ROLES,
                     name_collisions=name_collisions(db, org_id, u.id, u.name))


def card_out(db: Session, card: PaymentCard, viewer: User, membership: Membership) -> CardOut:
    payee = db.get(Payee, card.payee_id) if card.payee_id else None
    deal = None
    if card.deal_room_id:
        room = db.get(Room, card.deal_room_id)
        sheet = db.execute(select(TermSheet).where(TermSheet.room_id == card.deal_room_id)).scalar_one_or_none()
        price = T.values(sheet.terms).get("price_paise") if sheet else None
        deal = {"room_id": card.deal_room_id, "title": room.title if room else "?",
                "price_display": T.display("price_paise", price) if price else None,
                "locked": bool(sheet and sheet.status == TermSheetStatus.LOCKED)}
    signals = db.execute(select(RiskSignal).where(RiskSignal.card_id == card.id,
                                                  RiskSignal.card_version == card.version)
                         .order_by(RiskSignal.weight.desc())).scalars().all()
    appr = P.approvals(db, card)
    approver_ids = [a.approver_id for a in appr]
    names = {u.id: u.name for u in db.execute(select(User).where(User.id.in_(approver_ids))).scalars()}
    payout = db.execute(select(Payout).where(Payout.card_id == card.id)).scalar_one_or_none()
    frozen = P.control(db, card.org_id).frozen
    is_approver = membership.role in P.APPROVER_ROLES
    mine = card.requester_id == viewer.id
    already = any(a.approver_id == viewer.id for a in appr)
    current_hash = P.card_hash(db, card)
    stepped_up = bool(card.step_up_hash) and card.step_up_hash == current_hash
    step_up_ok = not card.step_up_required or stepped_up
    blocked = None
    if card.state == CardState.UNVERIFIED:
        blocked = "Spotted in chat. Chat messages cannot move money; a verified team member must raise a card"
    elif frozen:
        blocked = "Payments are frozen"
    elif card.state == CardState.PENDING and mine and not step_up_ok:
        blocked = ("Step-up check: confirm this exact card with the OTP sent to your phone, then someone else "
                   "approves it")
    elif card.state == CardState.PENDING and mine:
        blocked = "You requested this payment, so someone else must approve it"
    elif card.state == CardState.PENDING and not step_up_ok:
        blocked = "Step-up check: the requester confirms this exact card with an OTP before anyone can approve it"
    can_approve = (card.state == CardState.PENDING and is_approver and not mine and not already and not frozen
                   and step_up_ok and card.payee_id is not None)
    return CardOut(
        id=card.id, number=card.number, room_id=card.room_id, state=card.state, version=card.version,
        amount_paise=card.amount_paise, amount_display=format_inr(card.amount_paise), reason=card.reason,
        source=card.source,
        payee=PayeeRef(id=payee.id, name=payee.name, masked=payee.masked, gstin=payee.gstin,
                       details_changed_at=payee.details_changed_at) if payee else None,
        payee_name_claimed=card.payee_name_claimed, deal=deal, requester=_person(db, card.org_id, card.requester_id),
        risk_score=card.risk_score, risk_level=card.risk_level,
        signals=[SignalOut(rule_id=s.rule_id, weight=s.weight, message=s.message, evidence=s.evidence)
                 for s in signals],
        required_approvals=card.required_approvals, owner_approval_required=card.owner_approval_required,
        approvals=[ApprovalOut(name=names.get(a.approver_id, "?"), role=a.role, at=a.created_at) for a in appr],
        step_up_required=card.step_up_required, step_up_done=stepped_up,
        step_up_at=card.step_up_at if stepped_up else None, card_hash=current_hash,
        payout={"provider": payout.provider, "provider_ref": payout.provider_ref, "status": payout.status,
                "at": payout.created_at, "simulated": payout.provider == "simulated"} if payout else None,
        viewer=ViewerOut(can_approve=can_approve,
                         can_edit=card.state == CardState.PENDING and (mine or is_approver),
                         can_payout=card.state == CardState.APPROVED and is_approver and not frozen,
                         can_step_up=(card.state == CardState.PENDING and card.step_up_required and mine
                                      and not stepped_up and membership.role in TEAM_ROLES
                                      and viewer.phone_verified_at is not None),
                         can_reject=card.state in (CardState.PENDING, CardState.APPROVED, CardState.UNVERIFIED)
                         and (is_approver or mine), is_approver=is_approver, blocked_reason=blocked),
        created_at=card.created_at)


def _notify(db: Session, nexconn: NexconnClient, settings: Settings, card: PaymentCard, title: str) -> None:
    room = db.get(Room, card.room_id)
    if room is not None:
        post_card(db, nexconn, settings, room, {"kind": "payment_card", "v": 1, "card_id": card.id,
                                                "number": card.number, "state": card.state, "title": title})


# ---------- control (kill switch) ----------

def _control_out(db: Session, org_id: str, user: User) -> ControlOut:
    c = P.control(db, org_id)
    return ControlOut(frozen=c.frozen, frozen_by=c.frozen_by, frozen_at=c.frozen_at, reason=c.reason,
                      unfreeze_votes=len(c.unfreeze_votes), voted=any(v["user_id"] == user.id
                                                                      for v in c.unfreeze_votes))


def _notify_lanes(db: Session, nexconn: NexconnClient, settings: Settings, org_id: str, title: str) -> None:
    for room in db.execute(select(Room).where(Room.org_id == org_id, Room.type == RoomType.PAYLANE)).scalars():
        post_card(db, nexconn, settings, room, {"kind": "control", "v": 1, "title": title})


@router.get("/orgs/{org_id}/control", response_model=ControlOut)
def get_control(org_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> ControlOut:
    require_org_role(db, user, org_id, TEAM_ROLES)
    out = _control_out(db, org_id, user)
    db.commit()
    return out


@router.post("/orgs/{org_id}/freeze", response_model=ControlOut,
             dependencies=[Depends(rate_limit("freeze", 20, 60))])
def freeze(org_id: str, body: FreezeIn, user: User = Depends(current_user), db: Session = Depends(get_db),
           settings: Settings = Depends(get_settings), nexconn: NexconnClient = Depends(get_nexconn)) -> ControlOut:
    m = require_org_role(db, user, org_id, TEAM_ROLES)
    was = P.control(db, org_id).frozen
    P.freeze(db, org_id, user, m, body.reason)
    db.commit()
    if not was:
        _notify_lanes(db, nexconn, settings, org_id, f"🧊 Payments frozen by {user.name}: {body.reason}. Two "
                                                     "approvers must unfreeze")
    return _control_out(db, org_id, user)


@router.post("/orgs/{org_id}/unfreeze", response_model=ControlOut,
             dependencies=[Depends(rate_limit("freeze", 20, 60))])
def unfreeze(org_id: str, user: User = Depends(current_user), db: Session = Depends(get_db),
             settings: Settings = Depends(get_settings), nexconn: NexconnClient = Depends(get_nexconn)) -> ControlOut:
    m = require_org_role(db, user, org_id, TEAM_ROLES)
    was = P.control(db, org_id).frozen
    c = P.vote_unfreeze(db, org_id, user, m)
    db.commit()
    if was and not c.frozen:
        _notify_lanes(db, nexconn, settings, org_id, f"▶️ Payments unfrozen (second key: {user.name})")
    elif was:
        _notify_lanes(db, nexconn, settings, org_id, f"🔑 {user.name} voted to unfreeze. One more approver needed")
    return _control_out(db, org_id, user)


# ---------- cards ----------

@router.get("/rooms/{room_id}/cards", response_model=list[CardOut])
def list_cards(room_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[CardOut]:
    room, m = _lane(db, user, room_id)
    cards = db.execute(select(PaymentCard).where(PaymentCard.room_id == room.id)
                       .order_by(PaymentCard.created_at.desc()).limit(100)).scalars().all()
    out = [card_out(db, c, user, m) for c in cards]
    db.commit()
    return out


@router.post("/rooms/{room_id}/cards/parse", response_model=ParseOut,
             dependencies=[Depends(rate_limit("card-parse", 30, 60))])
def parse_card(room_id: str, body: ParseIn, user: User = Depends(current_user), db: Session = Depends(get_db),
               llm: LLM = Depends(get_llm_dep)) -> ParseOut:
    room, _ = _lane(db, user, room_id)
    d = AI.parse_request(db, llm, room.org_id, body.text)
    return ParseOut(payee_name=d.payee_name, amount_paise=d.amount_paise,
                    amount_display=format_inr(d.amount_paise) if d.amount_paise else None, reason=d.reason,
                    payees=[PayeeRef(id=p.id, name=p.name, masked=p.masked, gstin=p.gstin,
                                     details_changed_at=p.details_changed_at) for p in d.payee_matches],
                    deals=[{"room_id": r.id, "title": r.title, "price_display": price} for r, price in d.deal_matches],
                    used_ai=d.used_ai)


@router.post("/rooms/{room_id}/cards", response_model=CardOut, status_code=201,
             dependencies=[Depends(rate_limit("card-create", 30, 60))])
def create_card(room_id: str, body: CardIn, user: User = Depends(current_user), db: Session = Depends(get_db),
                settings: Settings = Depends(get_settings), nexconn: NexconnClient = Depends(get_nexconn)) -> CardOut:
    room, m = _lane(db, user, room_id)
    P.require_not_frozen(db, room.org_id)
    payee = db.get(Payee, body.payee_id)
    if payee is None or payee.org_id != room.org_id:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pick a payee from your verified payee list")
    card = P.create_card(db, room=room, requester=user, requester_role=m.role, payee=payee, payee_name_claimed=None,
                         amount_paise=body.amount_paise, reason=" ".join(body.reason.split()),
                         deal_room_id=body.deal_room_id, source="builder", state=CardState.PENDING,
                         extra_text=" ".join((body.request_text or "").split()))
    db.commit()
    _notify(db, nexconn, settings, card, f"Card #{card.number}: {user.name} requests {format_inr(card.amount_paise)} "
                                         f"to {payee.name} · {card.risk_level} risk")
    return card_out(db, card, user, m)


@router.get("/cards/{card_id}", response_model=CardOut)
def get_card(card_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> CardOut:
    card, m = _card(db, user, card_id)
    out = card_out(db, card, user, m)
    db.commit()
    return out


@router.patch("/cards/{card_id}", response_model=CardOut,
             dependencies=[Depends(rate_limit("card-edit", 30, 60))])
def edit_card(card_id: str, body: CardEdit, user: User = Depends(current_user), db: Session = Depends(get_db),
              settings: Settings = Depends(get_settings), nexconn: NexconnClient = Depends(get_nexconn)) -> CardOut:
    card, m = _card(db, user, card_id)
    payee = None
    if body.payee_id is not None:
        payee = db.get(Payee, body.payee_id)
        if payee is None or payee.org_id != card.org_id:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Pick a payee from your verified payee list")
    had = len(P.approvals(db, card))
    changed = P.edit_card(db, card, user, m, amount_paise=body.amount_paise, payee=payee,
                          reason=" ".join(body.reason.split()) if body.reason else None)
    db.commit()
    if changed and had and not P.approvals(db, card):
        _notify(db, nexconn, settings, card, f"Card #{card.number} was edited; its {had} approval(s) were reset")
    return card_out(db, card, user, m)


@router.post("/cards/{card_id}/approve", response_model=CardOut,
             dependencies=[Depends(rate_limit("card-decide", 30, 60))])
def approve_card(card_id: str, body: ApproveIn, user: User = Depends(current_user), db: Session = Depends(get_db),
                 settings: Settings = Depends(get_settings), nexconn: NexconnClient = Depends(get_nexconn),
                 llm: LLM = Depends(get_llm_dep)) -> CardOut:
    card, m = _card(db, user, card_id)
    done = P.approve(db, card, user, m, body.version, body.override_reason)
    if card.risk_level == "high" and body.override_reason:
        memory_signals.risk_override(db, llm, card, user, " ".join(body.override_reason.split()))
    db.commit()
    have = len(P.approvals(db, card))
    _notify(db, nexconn, settings, card, f"Card #{card.number} approved by {user.name} ({have} of "
                                         f"{card.required_approvals})" + (" · ready to pay" if done else ""))
    return card_out(db, card, user, m)


@router.post("/cards/{card_id}/reject", response_model=CardOut,
             dependencies=[Depends(rate_limit("card-decide", 30, 60))])
def reject_card(card_id: str, body: RejectIn, user: User = Depends(current_user), db: Session = Depends(get_db),
                settings: Settings = Depends(get_settings), nexconn: NexconnClient = Depends(get_nexconn)) -> CardOut:
    card, m = _card(db, user, card_id)
    P.reject(db, card, user, m, " ".join(body.note.split()), body.suspected_fraud)
    db.commit()
    _notify(db, nexconn, settings, card, f"Card #{card.number} {card.state} by {user.name}")
    return card_out(db, card, user, m)


@router.get("/cards/{card_id}/brief", response_model=BriefOut, dependencies=[Depends(rate_limit("brief", 30, 60))])
def brief(card_id: str, user: User = Depends(current_user), db: Session = Depends(get_db),
          llm: LLM = Depends(get_llm_dep)) -> BriefOut:
    card, m = _card(db, user, card_id)
    out = card_out(db, card, user, m)
    evidence: list[dict[str, str]] = []
    if out.deal:
        price = f" agreed at {out.deal['price_display']}" if out.deal.get("price_display") else ""
        deal_line = f"Linked to deal “{out.deal['title']}”{price}; this card asks {out.amount_display}."
        evidence.append({"label": "Deal room", "ref": f"/rooms/{out.deal['room_id']}"})
    else:
        deal_line = f"Not linked to any deal; {out.amount_display} for “{card.reason}”."
    paid = db.execute(select(PaymentCard).where(
        PaymentCard.org_id == card.org_id, PaymentCard.payee_id == card.payee_id, PaymentCard.state == CardState.PAID,
        PaymentCard.id != card.id).order_by(PaymentCard.created_at.desc())).scalars().all() if card.payee_id else []
    if out.payee is None:
        payee_line = f"Payee “{card.payee_name_claimed or '?'}” is not on the payee list."
    elif paid:
        last = paid[0]
        payee_line = (f"{out.payee.name} was paid {len(paid)} time(s); last {format_inr(last.amount_paise)} on "
                      f"{last.created_at.astimezone(IST):%d %b %Y}.")
        evidence.append({"label": f"Card #{last.number}", "ref": f"/cards/{last.id}"})
    else:
        payee_line = f"{out.payee.name} has never been paid before ({out.payee.masked})."
    rules_line = ("Rules fired: " + "; ".join(s.message for s in out.signals[:3]) + ".") if out.signals \
        else "No risk rules fired."
    facts = AI.Brief(deal_line=deal_line[:220], payee_line=payee_line[:220], rules_line=rules_line[:220])
    worded, ai = AI.polish_brief(llm, facts)
    db.commit()
    return BriefOut(lines=[worded.deal_line, worded.payee_line, worded.rules_line], evidence=evidence, ai_worded=ai)


def _step_up_purpose(card: PaymentCard, card_hash: str) -> str:
    return f"stepup:{card.id}:{card_hash[:16]}"


@router.post("/cards/{card_id}/step-up/request", response_model=StepUpRequestOut,
             dependencies=[Depends(rate_limit("step-up-otp", 30, 600))])
def request_step_up(card_id: str, body: StepUpTarget, user: User = Depends(current_user),
                    db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> StepUpRequestOut:
    """Sends a one-time code to the requester's verified phone, scoped to this card version and its hash."""
    card, m = _card(db, user, card_id)
    target = P.check_step_up_target(db, card, user, m, body.version, body.hash)
    test_mode = settings.demo_mode or not settings.is_production
    try:
        issued = request_otp(db, user.phone_e164, settings.otp_pepper, otp_provider, _step_up_purpose(card, target),
                             max_per_hour=TEST_MODE_MAX_PER_HOUR if test_mode else MAX_PER_HOUR)
    except OtpError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    audit.record(db, "card.step_up_requested", actor_user_id=user.id, org_id=card.org_id,
                 subject_type="payment_card", subject_id=card.id, payload={"version": card.version, "hash": target})
    db.commit()
    return StepUpRequestOut(version=card.version, hash=target, phone_last4=user.phone_last4, test_mode=test_mode,
                            test_code=issued.code if test_mode else None)


@router.post("/cards/{card_id}/step-up", response_model=CardOut, dependencies=[Depends(rate_limit("step-up", 20, 600))])
def confirm_step_up(card_id: str, body: StepUpIn, user: User = Depends(current_user), db: Session = Depends(get_db),
                    settings: Settings = Depends(get_settings),
                    nexconn: NexconnClient = Depends(get_nexconn)) -> CardOut:
    card, m = _card(db, user, card_id)
    target = P.check_step_up_target(db, card, user, m, body.version, body.hash)
    try:
        verify_otp(db, user.phone_e164, body.code, settings.otp_pepper, _step_up_purpose(card, target))
    except OtpError as exc:
        audit.record(db, "card.step_up_failed", actor_user_id=user.id, org_id=card.org_id,
                     subject_type="payment_card", subject_id=card.id, payload={"version": card.version,
                                                                               "reason": str(exc)[:80]})
        db.commit()
        raise HTTPException(exc.status, str(exc)) from exc
    card = db.execute(select(PaymentCard).where(PaymentCard.id == card.id).with_for_update()
                      .execution_options(populate_existing=True)).scalar_one()
    target = P.check_step_up_target(db, card, user, m, body.version, body.hash)
    P.complete_step_up(db, card, user, target)
    db.commit()
    _notify(db, nexconn, settings, card, f"Card #{card.number}: {user.name} confirmed it with an OTP")
    return card_out(db, card, user, m)


@router.post("/cards/{card_id}/payout", response_model=CardOut, dependencies=[Depends(rate_limit("payout", 20, 60))])
def payout(card_id: str, body: PayoutIn, user: User = Depends(current_user), db: Session = Depends(get_db),
           settings: Settings = Depends(get_settings), nexconn: NexconnClient = Depends(get_nexconn),
           provider: PaymentProvider = Depends(get_payment_provider), llm: LLM = Depends(get_llm_dep)) -> CardOut:
    card = db.execute(select(PaymentCard).where(PaymentCard.id == card_id).with_for_update()
                      .execution_options(populate_existing=True)).scalar_one_or_none()
    if card is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    card, m = _card(db, user, card_id)
    row, created = P.payout(db, card, user, m, body.idempotency_key, provider)
    if created and card.payee_id:
        paid_to = db.get(Payee, card.payee_id)
        if paid_to is not None:
            memory_signals.payout_made(db, llm, card, paid_to, user)
    db.commit()
    if created:
        payee = db.get(Payee, card.payee_id) if card.payee_id else None
        _notify(db, nexconn, settings, card, f"💸 Card #{card.number}: {format_inr(card.amount_paise)} paid to "
                                             f"{payee.name if payee else '?'} (simulated, ref {row.provider_ref})")
    return card_out(db, card, user, m)


@router.get("/orgs/{org_id}/policy")
def get_policy(org_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> dict[str, Any]:
    require_org_role(db, user, org_id, TEAM_ROLES)
    pack = policy()
    risk = effective_risk(db, org_id)
    names = {u.id: u.name for u in db.execute(select(User).where(User.id.in_(
        [h.set_by for h in policy_history(db, org_id) if h.set_by]))).scalars()}
    return {"approval_bands": pack["approval_bands"], "approver_roles": pack["approver_roles"],
            "source": pack["source"], "as_of": pack["as_of"], "disclaimer": pack["disclaimer"],
            "thresholds": [{"key": k, "label": t.label, "value": risk[k], "default": pack["risk"][k],
                            "lower_is_tighter": t.lower_is_tighter, "min": t.minimum, "max": t.maximum}
                           for k, t in TUNABLES.items()],
            "changes": [{"key": h.key, "from": h.previous, "to": h.value, "direction": h.direction,
                         "source": h.source, "reason": h.reason, "by": names.get(h.set_by or ""),
                         "at": h.created_at.isoformat()} for h in policy_history(db, org_id)]}


@router.put("/orgs/{org_id}/policy/thresholds", dependencies=[Depends(rate_limit("policy", 20, 60))])
def set_policy_threshold(org_id: str, body: ThresholdIn, user: User = Depends(current_user),
                         db: Session = Depends(get_db)) -> dict[str, Any]:
    """Tightening: owner or finance. Loosening: owner only, with a reason. Every change is audited."""
    m = require_org_role(db, user, org_id, TEAM_ROLES)
    set_threshold(db, org_id, body.key, body.value, source="person", reason=body.reason, actor_id=user.id,
                  actor_role=m.role)
    db.commit()
    return get_policy(org_id, user, db)


class AskIn(BaseModel):
    question: bounded_text(3, 300)  # type: ignore[valid-type]


class AskOut(BaseModel):
    answer: str
    facts: list[dict[str, str | None]]
    ai_worded: bool
    disclaimer: str = "Answers come only from your workspace's own records."


@router.post("/orgs/{org_id}/ask", response_model=AskOut, dependencies=[Depends(rate_limit("ask", 20, 60))])
def ask(org_id: str, body: AskIn, user: User = Depends(current_user), db: Session = Depends(get_db),
        llm: LLM = Depends(get_llm_dep)) -> AskOut:
    from app.agents import ask as A

    require_org_role(db, user, org_id, TEAM_ROLES)
    result = A.answer(db, llm, org_id, " ".join(body.question.split()))
    audit.record(db, "ask.query", actor_user_id=user.id, org_id=org_id, subject_type="org", subject_id=org_id,
                 payload={"chars": len(body.question), "ai": result.ai_worded})
    db.commit()
    return AskOut(answer=result.text, facts=[{"id": f.id, "text": f.text, "link": f.link} for f in result.facts],
                  ai_worded=result.ai_worded)
