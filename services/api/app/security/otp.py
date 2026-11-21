"""Phone OTP: hashed at rest, short expiry, attempt lockout and resend limits."""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Protocol

import phonenumbers
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import OtpChallenge

log = logging.getLogger(__name__)

OTP_TTL = timedelta(minutes=5)
OTP_MAX_ATTEMPTS = 5
RESEND_COOLDOWN = timedelta(seconds=30)
MAX_PER_HOUR = 6


class OtpError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


class OtpProvider(Protocol):
    def send(self, phone_e164: str, code: str) -> None: ...


class LogOtpProvider:
    """Development and demo provider: writes the code to the server log instead of sending an SMS."""

    def send(self, phone_e164: str, code: str) -> None:
        log.warning("OTP for ***%s: %s (test mode, no SMS sent)", phone_e164[-4:], code)


def normalize_phone(raw: str, region: str = "IN") -> str:
    try:
        parsed = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException as exc:
        raise OtpError("Enter a valid mobile number") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise OtpError("Enter a valid mobile number")
    return phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)


def _hash(pepper: str, phone: str, code: str) -> str:
    return hmac.new(pepper.encode(), f"{phone}:{code}".encode(), hashlib.sha256).hexdigest()


@dataclass
class IssuedOtp:
    phone_e164: str
    code: str


def request_otp(db: Session, phone_e164: str, pepper: str, provider: OtpProvider) -> IssuedOtp:
    now = utcnow()
    recent = db.execute(
        select(func.count(), func.max(OtpChallenge.created_at)).where(
            OtpChallenge.phone_e164 == phone_e164, OtpChallenge.created_at > now - timedelta(hours=1))
    ).one()
    count, latest = recent
    if latest and now - latest < RESEND_COOLDOWN:
        raise OtpError("Please wait a few seconds before asking for another code", 429)
    if count >= MAX_PER_HOUR:
        raise OtpError("Too many codes requested. Try again in an hour", 429)
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(OtpChallenge(phone_e164=phone_e164, code_hash=_hash(pepper, phone_e164, code), expires_at=now + OTP_TTL))
    db.commit()
    provider.send(phone_e164, code)
    return IssuedOtp(phone_e164, code)


def verify_otp(db: Session, phone_e164: str, code: str, pepper: str) -> None:
    now = utcnow()
    challenge = db.execute(
        select(OtpChallenge).where(OtpChallenge.phone_e164 == phone_e164, OtpChallenge.consumed_at.is_(None))
        .order_by(OtpChallenge.created_at.desc()).limit(1).with_for_update()
    ).scalar_one_or_none()
    if challenge is None or challenge.expires_at < now:
        raise OtpError("Code expired. Ask for a new one")
    if challenge.attempts >= OTP_MAX_ATTEMPTS:
        raise OtpError("Too many wrong codes. Ask for a new one", 429)
    challenge.attempts += 1
    if not hmac.compare_digest(challenge.code_hash, _hash(pepper, phone_e164, code.strip())):
        db.commit()
        raise OtpError("Wrong code")
    challenge.consumed_at = now
    db.commit()
