from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db import get_db, new_id, utcnow
from app.domain import validators as V
from app.models import TEAM_ROLES, Payee, User
from app.schemas import PersonName
from app.security import audit, crypto
from app.security.deps import current_user, rate_limit, require_org_role

router = APIRouter(prefix="/api/orgs", tags=["payees"])

EDITOR_ROLES = {"owner", "finance"}


def _validate_details(m: PayeeIn | DetailsIn) -> None:
    if m.method == "upi":
        if not m.vpa or m.account_number or m.ifsc:
            raise ValueError("For UPI, give only the UPI ID")
        m.vpa = V.vpa(m.vpa)
    else:
        if not m.account_number or not m.ifsc or m.vpa:
            raise ValueError("For a bank transfer, give the account number and IFSC")
        m.account_number = V.account_number(m.account_number)
        m.ifsc = V.ifsc(m.ifsc)


class DetailsIn(BaseModel):
    method: Literal["upi", "bank"]
    vpa: str | None = Field(default=None, max_length=300)
    account_number: str | None = Field(default=None, max_length=30)
    ifsc: str | None = Field(default=None, max_length=15)

    @model_validator(mode="after")
    def _check(self) -> DetailsIn:
        _validate_details(self)
        return self


class PayeeIn(DetailsIn):
    name: PersonName
    gstin: str | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def _check_gstin(self) -> PayeeIn:
        if self.gstin:
            self.gstin = V.gstin(self.gstin)
        return self


class PayeeOut(BaseModel):
    id: str
    name: str
    gstin: str | None
    method: str
    masked: str
    ifsc: str | None
    details_changed_at: datetime | None
    created_at: datetime


def payee_out(p: Payee) -> PayeeOut:
    return PayeeOut(id=p.id, name=p.name, gstin=p.gstin, method=p.method, masked=p.masked, ifsc=p.ifsc,
                    details_changed_at=p.details_changed_at, created_at=p.created_at)


def _apply_details(p: Payee, body: PayeeIn | DetailsIn) -> str:
    if body.method == "upi":
        assert body.vpa
        p.vpa_enc, p.account_enc, p.ifsc = crypto.encrypt(body.vpa, p.id), None, None
        p.masked = f"UPI {crypto.mask(body.vpa.split('@')[0])}@{body.vpa.split('@')[1]}"
        raw = f"upi:{body.vpa}"
    else:
        assert body.account_number and body.ifsc
        p.vpa_enc, p.account_enc, p.ifsc = None, crypto.encrypt(body.account_number, p.id), body.ifsc
        p.masked = f"A/c {crypto.mask(body.account_number)} · {body.ifsc}"
        raw = f"bank:{body.account_number}:{body.ifsc}"
    p.method = body.method
    return crypto.fingerprint(raw)


@router.get("/{org_id}/payees", response_model=list[PayeeOut])
def list_payees(org_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[PayeeOut]:
    require_org_role(db, user, org_id, TEAM_ROLES)
    rows = db.execute(select(Payee).where(Payee.org_id == org_id).order_by(Payee.name)).scalars()
    return [payee_out(p) for p in rows]


@router.post("/{org_id}/payees", response_model=PayeeOut, status_code=201,
             dependencies=[Depends(rate_limit("payee", 20, 600))])
def add_payee(org_id: str, body: PayeeIn, user: User = Depends(current_user),
              db: Session = Depends(get_db)) -> PayeeOut:
    require_org_role(db, user, org_id, EDITOR_ROLES)
    if body.gstin and db.execute(select(Payee.id).where(Payee.org_id == org_id, Payee.gstin == body.gstin)).first():
        raise HTTPException(status.HTTP_409_CONFLICT, "A payee with this GSTIN already exists")
    p = Payee(id=new_id(), org_id=org_id, name=body.name, gstin=body.gstin, created_by=user.id, bank_history=[])
    p.details_fingerprint = _apply_details(p, body)
    p.bank_history = [{"fingerprint": p.details_fingerprint, "masked": p.masked, "at": utcnow().isoformat(),
                       "by": user.id}]
    db.add(p)
    audit.record(db, "payee.create", actor_user_id=user.id, org_id=org_id, subject_type="payee", subject_id=p.id,
                 payload={"name": p.name, "gstin": p.gstin, "masked": p.masked})
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "A payee with this GSTIN already exists") from exc
    return payee_out(p)


@router.put("/{org_id}/payees/{payee_id}/details", response_model=PayeeOut,
             dependencies=[Depends(rate_limit("payee", 20, 600))])
def change_details(org_id: str, payee_id: str, body: DetailsIn, user: User = Depends(current_user),
                   db: Session = Depends(get_db)) -> PayeeOut:
    """A bank or UPI change is the classic fraud move, so it is recorded and raises risk on the next payments."""
    require_org_role(db, user, org_id, EDITOR_ROLES)
    p = db.get(Payee, payee_id)
    if p is None or p.org_id != org_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    old_masked, old_fp = p.masked, p.details_fingerprint
    fp = _apply_details(p, body)
    if fp == old_fp:
        db.rollback()
        return payee_out(db.get(Payee, payee_id))  # type: ignore[arg-type]
    now = utcnow()
    p.details_fingerprint, p.details_changed_at, p.details_changed_by = fp, now, user.id
    p.bank_history = [*p.bank_history, {"fingerprint": fp, "masked": p.masked, "at": now.isoformat(), "by": user.id}]
    audit.record(db, "payee.details_changed", actor_user_id=user.id, org_id=org_id, subject_type="payee",
                 subject_id=p.id, payload={"from": old_masked, "to": p.masked})
    db.commit()
    return payee_out(p)
