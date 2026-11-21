from __future__ import annotations

import hmac

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db, utcnow
from app.deps import get_llm_dep, get_nexconn
from app.domain import terms as T
from app.domain.deal import current_hash, get_sheet, require_deal_room, require_participant, signatures
from app.domain.rooms import post_card
from app.integrations.nexconn import NexconnClient
from app.llm import LLM
from app.memory.signals import advance_paid
from app.models import (
    TEAM_ROLES,
    Org,
    PaymentLink,
    PaymentStatus,
    Room,
    RoomSide,
    TermSheet,
    TermSheetSignature,
    TermSheetStatus,
    User,
)
from app.money import format_inr
from app.payments import PaymentProvider, get_payment_provider
from app.routers.auth import otp_provider
from app.routers.deals import DealOut, PaymentLinkOut, deal_out, link_out
from app.schemas import OtpCode
from app.security import audit
from app.security.deps import current_user, rate_limit, room_access
from app.security.otp import OtpError, request_otp, verify_otp

router = APIRouter(prefix="/api", tags=["signing"])


class SignTarget(BaseModel):
    version: int = Field(ge=1)
    hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class SignRequestOut(SignTarget):
    phone_last4: str
    test_mode: bool
    test_code: str | None = None


class SignIn(SignTarget):
    code: OtpCode


class PayPage(BaseModel):
    org_name: str
    room_title: str
    purpose: str
    amount_paise: int
    amount_display: str
    status: str
    provider: str
    simulated: bool


class SimulateIn(BaseModel):
    outcome: str = Field(pattern=r"^(paid|failed)$")


def _purpose(sheet_id: str, version: int) -> str:
    return f"sign:{sheet_id}:{version}"


def _check_target(sheet: TermSheet, body: SignTarget) -> None:
    if body.version != sheet.version or not hmac.compare_digest(body.hash, current_hash(sheet)):
        raise HTTPException(status.HTTP_409_CONFLICT, "The terms changed. Review the latest version before signing")


@router.post("/rooms/{room_id}/term-sheet/sign/request", response_model=SignRequestOut,
             dependencies=[Depends(rate_limit("sign-otp", 10, 600))])
def request_signature(room_id: str, body: SignTarget, user: User = Depends(current_user),
                      db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> SignRequestOut:
    room, _, member = room_access(db, user, room_id)
    require_deal_room(room)
    require_participant(member)
    sheet = get_sheet(db, room)
    _check_target(sheet, body)
    missing = [i.message for i in T.review(sheet.terms) if i.level == "missing"]
    if missing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Agree these first: {'; '.join(missing)}")
    if any(s.user_id == user.id for s in signatures(db, sheet)):
        raise HTTPException(status.HTTP_409_CONFLICT, "You already signed this version")
    try:
        issued = request_otp(db, user.phone_e164, settings.otp_pepper, otp_provider, _purpose(sheet.id, sheet.version))
    except OtpError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    test_mode = settings.demo_mode or not settings.is_production
    return SignRequestOut(version=sheet.version, hash=current_hash(sheet), phone_last4=user.phone_last4,
                          test_mode=test_mode, test_code=issued.code if test_mode else None)


@router.post("/rooms/{room_id}/term-sheet/sign", response_model=DealOut,
             dependencies=[Depends(rate_limit("sign", 20, 600))])
def sign(room_id: str, body: SignIn, user: User = Depends(current_user), db: Session = Depends(get_db),
         settings: Settings = Depends(get_settings), nexconn: NexconnClient = Depends(get_nexconn)) -> DealOut:
    room, _, member = room_access(db, user, room_id)
    require_deal_room(room)
    member = require_participant(member)
    sheet = get_sheet(db, room)
    _check_target(sheet, body)
    try:
        verify_otp(db, user.phone_e164, body.code, settings.otp_pepper, _purpose(sheet.id, sheet.version))
    except OtpError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    sheet = get_sheet(db, room, lock=True)
    _check_target(sheet, body)
    if any(s.user_id == user.id for s in signatures(db, sheet)):
        raise HTTPException(status.HTTP_409_CONFLICT, "You already signed this version")
    db.add(TermSheetSignature(term_sheet_id=sheet.id, version=sheet.version, sheet_hash=body.hash, user_id=user.id,
                              side=member.side))
    audit.record(db, "term_sheet.signed", actor_user_id=user.id, org_id=room.org_id, subject_type="term_sheet",
                 subject_id=sheet.id, payload={"version": sheet.version, "hash": body.hash, "side": member.side,
                                               "method": "otp"})
    db.flush()
    sides = {s.side for s in signatures(db, sheet)}
    locked = {RoomSide.PROVIDER.value, RoomSide.CLIENT.value} <= sides
    if locked:
        sheet.status, sheet.locked_hash, sheet.locked_at = TermSheetStatus.LOCKED, body.hash, utcnow()
        audit.record(db, "term_sheet.locked", actor_user_id=user.id, org_id=room.org_id, subject_type="term_sheet",
                     subject_id=sheet.id, payload={"version": sheet.version, "hash": body.hash})
    db.commit()
    if locked:
        title = f"Term sheet v{sheet.version} locked by both sides. SHA-256 {body.hash[:12]}…"
    else:
        other = "client" if member.side == RoomSide.PROVIDER else "freelancer"
        title = f"{user.name} signed v{sheet.version} with OTP. Waiting for the {other}"
    post_card(db, nexconn, settings, room, {"kind": "signature", "v": 1, "version": sheet.version,
                                            "hash": body.hash, "locked": locked, "title": title})
    return deal_out(db, room, member)


def _pay_url(settings: Settings, link: PaymentLink) -> str:
    return f"{settings.public_url}/pay/{link.public_token}"


@router.post("/rooms/{room_id}/advance-link", response_model=PaymentLinkOut, status_code=201,
             dependencies=[Depends(rate_limit("paylink", 10, 600))])
def create_advance_link(room_id: str, user: User = Depends(current_user), db: Session = Depends(get_db),
                        settings: Settings = Depends(get_settings), nexconn: NexconnClient = Depends(get_nexconn),
                        provider: PaymentProvider = Depends(get_payment_provider)) -> PaymentLinkOut:
    room, membership, member = room_access(db, user, room_id)
    require_deal_room(room)
    if member is None or member.side != RoomSide.PROVIDER or membership.role not in TEAM_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the freelancer's team can request the advance")
    sheet = get_sheet(db, room, lock=True)
    if sheet.status != TermSheetStatus.LOCKED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Both sides must lock the term sheet first")
    amount = T.advance_paise(sheet.terms)
    if not amount:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "The agreed advance is zero")
    existing = db.execute(select(PaymentLink).where(
        PaymentLink.room_id == room.id, PaymentLink.term_version == sheet.version,
        PaymentLink.status.in_([PaymentStatus.PENDING, PaymentStatus.PAID]))).scalar_one_or_none()
    if existing:
        return link_out(existing, _pay_url(settings, existing))
    created = provider.create_link(amount, reference=f"{room.id}:v{sheet.version}",
                                   description=f"Advance: {room.title}")
    link = PaymentLink(org_id=room.org_id, room_id=room.id, term_sheet_id=sheet.id, term_version=sheet.version,
                       purpose="advance", amount_paise=amount, provider=created.provider,
                       provider_ref=created.provider_ref, public_token=created.public_token, created_by=user.id)
    db.add(link)
    db.flush()
    audit.record(db, "payment_link.created", actor_user_id=user.id, org_id=room.org_id, subject_type="payment_link",
                 subject_id=link.id, payload={"amount_paise": amount, "version": sheet.version,
                                              "provider": created.provider, "provider_ref": created.provider_ref})
    db.commit()
    url = _pay_url(settings, link)
    post_card(db, nexconn, settings, room, {
        "kind": "payment_link", "v": 1, "link_id": link.id, "url": url, "amount_display": format_inr(amount),
        "status": link.status, "simulated": True,
        "title": f"Advance of {format_inr(amount)} requested (simulated payment link)"})
    return link_out(link, url)


def _link_by_token(db: Session, token: str, lock: bool = False) -> PaymentLink:
    if not 16 <= len(token) <= 64:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment link not found")
    q = select(PaymentLink).where(PaymentLink.public_token == token)
    link = db.execute(q.with_for_update() if lock else q).scalar_one_or_none()
    if link is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment link not found")
    return link


def _page(db: Session, link: PaymentLink) -> PayPage:
    room = db.get(Room, link.room_id)
    org = db.get(Org, link.org_id)
    assert room is not None and org is not None
    return PayPage(org_name=org.name, room_title=room.title, purpose=link.purpose, amount_paise=link.amount_paise,
                   amount_display=format_inr(link.amount_paise), status=link.status, provider=link.provider,
                   simulated=link.provider == "simulated")


@router.get("/pay/{token}", response_model=PayPage, dependencies=[Depends(rate_limit("pay", 60, 60))])
def pay_page(token: str, db: Session = Depends(get_db)) -> PayPage:
    return _page(db, _link_by_token(db, token))


@router.post("/pay/{token}/simulate", response_model=PayPage, dependencies=[Depends(rate_limit("pay", 60, 60))])
def simulate_payment(token: str, body: SimulateIn, db: Session = Depends(get_db),
                     settings: Settings = Depends(get_settings), nexconn: NexconnClient = Depends(get_nexconn),
                     llm: LLM = Depends(get_llm_dep)) -> PayPage:
    """Stands in for the gateway's signed callback. Idempotent: a paid link stays paid."""
    link = _link_by_token(db, token, lock=True)
    if link.provider != "simulated":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Payment link not found")
    if link.status != PaymentStatus.PENDING:
        return _page(db, link)
    link.status = PaymentStatus.PAID if body.outcome == "paid" else PaymentStatus.FAILED
    link.paid_at = utcnow() if body.outcome == "paid" else None
    audit.record(db, f"payment_link.{link.status}", org_id=link.org_id, subject_type="payment_link",
                 subject_id=link.id, payload={"amount_paise": link.amount_paise, "provider_ref": link.provider_ref})
    if link.status == PaymentStatus.PAID:
        advance_paid(db, llm, link)
    db.commit()
    room = db.get(Room, link.room_id)
    if room is not None:
        verb = "received" if link.status == PaymentStatus.PAID else "failed"
        post_card(db, nexconn, settings, room, {
            "kind": "payment_status", "v": 1, "link_id": link.id, "status": link.status, "simulated": True,
            "title": f"Advance of {format_inr(link.amount_paise)} {verb} (simulated)"})
    return _page(db, link)
