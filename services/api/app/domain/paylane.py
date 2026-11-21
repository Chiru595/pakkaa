"""Pay Lane state machine. Every money-moving step re-checks role, state, version, freeze and idempotency here,
in code, regardless of what any message or AI output says."""
from __future__ import annotations

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.domain import risk as R
from app.models import (
    TEAM_ROLES,
    CardApproval,
    CardState,
    Membership,
    OrgControl,
    Payee,
    PaymentCard,
    Payout,
    RiskSignal,
    Room,
    RoomType,
    User,
    VerifyCall,
)
from app.payments import PaymentProvider
from app.security import audit

APPROVER_ROLES = frozenset({"owner", "finance"})


def control(db: Session, org_id: str, *, lock: bool = False) -> OrgControl:
    q = select(OrgControl).where(OrgControl.org_id == org_id)
    if lock:
        q = q.with_for_update().execution_options(populate_existing=True)
    row = db.execute(q).scalar_one_or_none()
    if row is None:
        row = OrgControl(org_id=org_id, frozen=False, unfreeze_votes=[], card_seq=0)
        db.add(row)
        db.flush()
        if lock:
            row = db.execute(q).scalar_one()
    return row


def require_not_frozen(db: Session, org_id: str) -> None:
    c = control(db, org_id)
    if c.frozen:
        raise HTTPException(status.HTTP_423_LOCKED, f"Payments are frozen: {c.reason or 'kill switch on'}. Two "
                                                    "approvers must unfreeze first")


def _apply_assessment(db: Session, card: PaymentCard, a: R.Assessment) -> None:
    card.risk_score, card.risk_level = a.score, a.level
    card.verify_call_required = a.verify_call_required
    card.required_approvals, card.owner_approval_required = R.approval_band(card.amount_paise)
    for s in a.signals:
        db.add(RiskSignal(card_id=card.id, card_version=card.version, rule_id=s.rule_id, weight=s.weight,
                          message=s.message, evidence=s.evidence))


def assess_card(db: Session, card: PaymentCard, requester_role: str | None) -> R.Assessment:
    """Risk words are checked in the reason and in the original request text the person typed."""
    return R.assess(db, org_id=card.org_id, card_id=card.id, payee_id=card.payee_id, amount_paise=card.amount_paise,
                    reason=card.reason, requester_id=card.requester_id, requester_role=requester_role,
                    extra_text=card.request_text or "", deal_room_id=card.deal_room_id)


def create_card(db: Session, *, room: Room, requester: User | None, requester_role: str | None,
                payee: Payee | None, payee_name_claimed: str | None, amount_paise: int, reason: str,
                deal_room_id: str | None, source: str, state: CardState, source_message_id: str | None = None,
                extra_text: str = "") -> PaymentCard:
    if deal_room_id:
        deal = db.get(Room, deal_room_id)
        if deal is None or deal.org_id != room.org_id or deal.type != RoomType.DEAL:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "That deal is not in this workspace")
    ctl = control(db, room.org_id, lock=True)
    ctl.card_seq += 1
    card = PaymentCard(org_id=room.org_id, room_id=room.id, number=ctl.card_seq, payee_id=payee.id if payee else None,
                       payee_name_claimed=payee_name_claimed, deal_room_id=deal_room_id, amount_paise=amount_paise,
                       reason=reason, request_text=extra_text[:1000] or None, state=state, source=source,
                       source_message_id=source_message_id, requester_id=requester.id if requester else None,
                       version=1)
    db.add(card)
    db.flush()
    _apply_assessment(db, card, assess_card(db, card, requester_role))
    audit.record(db, "card.create", actor_user_id=requester.id if requester else None, org_id=room.org_id,
                 subject_type="payment_card", subject_id=card.id,
                 payload={"number": card.number, "amount_paise": amount_paise, "payee_id": card.payee_id,
                          "state": state.value, "source": source, "risk": card.risk_level, "score": card.risk_score})
    return card


def approvals(db: Session, card: PaymentCard) -> list[CardApproval]:
    return list(db.execute(select(CardApproval).where(
        CardApproval.card_id == card.id, CardApproval.card_version == card.version)).scalars())


def edit_card(db: Session, card: PaymentCard, actor: User, membership: Membership, *, amount_paise: int | None,
              payee: Payee | None, reason: str | None) -> bool:
    if card.state not in (CardState.PENDING,):
        raise HTTPException(status.HTTP_409_CONFLICT, "Only cards waiting for approval can be edited")
    if actor.id != card.requester_id and membership.role not in APPROVER_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the requester or finance can edit this card")
    changed: dict[str, Any] = {}
    if amount_paise is not None and amount_paise != card.amount_paise:
        changed["amount_paise"] = [card.amount_paise, amount_paise]
        card.amount_paise = amount_paise
    if payee is not None and payee.id != card.payee_id:
        changed["payee_id"] = [card.payee_id, payee.id]
        card.payee_id = payee.id
    if reason is not None and reason != card.reason:
        changed["reason"] = True
        card.reason = reason
    if not changed:
        return False
    voided = 0
    if "amount_paise" in changed or "payee_id" in changed:
        voided = len(approvals(db, card))
        card.version += 1
        card.verify_call_id = None
    card.updated_at = utcnow()
    requester_role = None
    if card.requester_id:
        m = db.execute(select(Membership).where(Membership.org_id == card.org_id,
                                                Membership.user_id == card.requester_id)).scalar_one_or_none()
        requester_role = m.role if m else None
    _apply_assessment(db, card, assess_card(db, card, requester_role))
    audit.record(db, "card.edit", actor_user_id=actor.id, org_id=card.org_id, subject_type="payment_card",
                 subject_id=card.id, payload={"changed": changed, "version": card.version,
                                              "approvals_reset": voided})
    return True


OVERRIDE_REASON_MIN = 10


def approve(db: Session, card: PaymentCard, actor: User, membership: Membership, version: int,
            override_reason: str | None = None) -> bool:
    """Returns True when this approval completes the policy and the card becomes approved.
    Approving a high-risk card needs a written reason, which is kept as an override the team can learn from."""
    require_not_frozen(db, card.org_id)
    if membership.role not in APPROVER_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only owners and finance can approve payments")
    if card.state != CardState.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, "This card is not waiting for approval")
    if version != card.version:
        raise HTTPException(status.HTTP_409_CONFLICT, "The card changed. Review the latest amount and payee")
    if actor.id == card.requester_id:
        audit.record(db, "card.self_approval_refused", actor_user_id=actor.id, org_id=card.org_id,
                     subject_type="payment_card", subject_id=card.id, payload={"version": version})
        db.commit()
        raise HTTPException(status.HTTP_403_FORBIDDEN, "You requested this payment, so someone else must approve it")
    if card.payee_id is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Pick a verified payee before approving")
    if card.verify_call_required:
        call = db.get(VerifyCall, card.verify_call_id) if card.verify_call_id else None
        if call is None or call.outcome != "confirmed" or call.card_version != card.version:
            raise HTTPException(status.HTTP_409_CONFLICT, "High risk: complete a verify call with the requester first")
    current = approvals(db, card)
    if any(a.approver_id == actor.id for a in current):
        raise HTTPException(status.HTTP_409_CONFLICT, "You already approved this version")
    reason = " ".join((override_reason or "").split()) or None
    if card.risk_level == "high" and (reason is None or len(reason) < OVERRIDE_REASON_MIN):
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "High risk: write why you are approving anyway (at least 10 characters)")
    db.add(CardApproval(card_id=card.id, card_version=card.version, approver_id=actor.id, role=membership.role,
                        override_reason=reason if card.risk_level == "high" else None))
    db.flush()
    current = approvals(db, card)
    owner_ok = not card.owner_approval_required or any(a.role == "owner" for a in current)
    done = len(current) >= card.required_approvals and owner_ok
    if done:
        card.state, card.decided_at = CardState.APPROVED, utcnow()
    audit.record(db, "card.approve", actor_user_id=actor.id, org_id=card.org_id, subject_type="payment_card",
                 subject_id=card.id, payload={"version": card.version, "approvals": len(current),
                                              "required": card.required_approvals, "complete": done,
                                              "override_reason": reason if card.risk_level == "high" else None})
    return done


def payout(db: Session, card: PaymentCard, actor: User, membership: Membership, idempotency_key: str,
           provider: PaymentProvider) -> tuple[Payout, bool]:
    """Pays an approved card once. A repeated request (same key or same card) returns the first payout."""
    existing = db.execute(select(Payout).where(
        (Payout.idempotency_key == idempotency_key) | (Payout.card_id == card.id))).scalar_one_or_none()
    if existing is not None:
        if existing.card_id != card.id:
            raise HTTPException(status.HTTP_409_CONFLICT, "This idempotency key belongs to another payout")
        return existing, False
    require_not_frozen(db, card.org_id)
    if membership.role not in APPROVER_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only owners and finance can release payouts")
    if card.state != CardState.APPROVED:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only approved cards can be paid")
    current = approvals(db, card)
    if len(current) < card.required_approvals:
        raise HTTPException(status.HTTP_409_CONFLICT, "Approvals are incomplete for this version")
    payee = db.get(Payee, card.payee_id) if card.payee_id else None
    if payee is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "No verified payee")
    result = provider.payout(card.amount_paise, idempotency_key, payee.id)
    row = Payout(card_id=card.id, idempotency_key=idempotency_key, org_id=card.org_id, amount_paise=card.amount_paise,
                 provider=result.provider, provider_ref=result.provider_ref, status=result.status,
                 initiated_by=actor.id)
    db.add(row)
    card.state = CardState.PAID
    card.updated_at = utcnow()
    audit.record(db, "card.paid", actor_user_id=actor.id, org_id=card.org_id, subject_type="payment_card",
                 subject_id=card.id, payload={"amount_paise": card.amount_paise, "payee_id": payee.id,
                                              "provider": result.provider, "provider_ref": result.provider_ref,
                                              "idempotency_key": idempotency_key, "version": card.version})
    return row, True


def freeze(db: Session, org_id: str, actor: User, membership: Membership, reason: str) -> OrgControl:
    if membership.role not in TEAM_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only your team can freeze payments")
    c = control(db, org_id, lock=True)
    if c.frozen:
        return c
    c.frozen, c.frozen_by, c.frozen_at, c.reason, c.unfreeze_votes = True, actor.id, utcnow(), reason, []
    audit.record(db, "control.freeze", actor_user_id=actor.id, org_id=org_id, subject_type="org", subject_id=org_id,
                 payload={"reason": reason})
    return c


def vote_unfreeze(db: Session, org_id: str, actor: User, membership: Membership) -> OrgControl:
    if membership.role not in APPROVER_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only owners and finance can unfreeze")
    c = control(db, org_id, lock=True)
    if not c.frozen:
        return c
    votes = [v for v in c.unfreeze_votes if v["user_id"] != actor.id] + [{"user_id": actor.id,
                                                                          "at": utcnow().isoformat()}]
    c.unfreeze_votes = votes
    audit.record(db, "control.unfreeze_vote", actor_user_id=actor.id, org_id=org_id, subject_type="org",
                 subject_id=org_id, payload={"votes": len(votes)})
    if len({v["user_id"] for v in votes}) >= 2:
        c.frozen, c.reason, c.unfreeze_votes = False, None, []
        audit.record(db, "control.unfreeze", actor_user_id=actor.id, org_id=org_id, subject_type="org",
                     subject_id=org_id, payload={"voters": [v["user_id"] for v in votes]})
    return c


def reject(db: Session, card: PaymentCard, actor: User, membership: Membership, note: str,
           suspected_fraud: bool = False) -> None:
    if membership.role not in APPROVER_ROLES and actor.id != card.requester_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only approvers or the requester can reject this card")
    if card.state not in (CardState.PENDING, CardState.APPROVED, CardState.UNVERIFIED):
        raise HTTPException(status.HTTP_409_CONFLICT, "This card is already closed")
    card.state = CardState.DISMISSED if card.state == CardState.UNVERIFIED else CardState.REJECTED
    card.decided_at = utcnow()
    audit.record(db, "card.reject", actor_user_id=actor.id, org_id=card.org_id, subject_type="payment_card",
                 subject_id=card.id, payload={"note": note[:200], "state": card.state,
                                              "suspected_fraud": suspected_fraud})
    if suspected_fraud:
        if membership.role not in APPROVER_ROLES:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only owners and finance can mark a card as fraud")
        from app.domain.policy import learn_from_fraud

        learn_from_fraud(db, card, actor.id)
