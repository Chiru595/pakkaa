from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db, utcnow
from app.models import Membership, User
from app.security import audit
from app.security.deps import current_user, rate_limit
from app.security.otp import LogOtpProvider, OtpError, normalize_phone, request_otp, verify_otp
from app.security.sessions import REFRESH_COOKIE, REFRESH_TTL, SessionError, revoke, rotate, start_session

router = APIRouter(prefix="/api/auth", tags=["auth"])
me_router = APIRouter(prefix="/api", tags=["auth"])

CONSENT_VERSION = "2026-11-21"
otp_provider = LogOtpProvider()


class OtpRequest(BaseModel):
    phone: str = Field(min_length=6, max_length=20)


class OtpRequested(BaseModel):
    phone: str
    is_new_user: bool
    needs_consent: bool
    test_mode: bool
    test_code: str | None = None


class OtpVerify(BaseModel):
    phone: str = Field(min_length=6, max_length=20)
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")
    name: str | None = Field(default=None, min_length=1, max_length=80)
    consent: bool = False


class OrgRef(BaseModel):
    org_id: str
    org_name: str
    role: str


class UserOut(BaseModel):
    id: str
    name: str
    phone_last4: str
    chat_id: str
    orgs: list[OrgRef]


class AuthOut(BaseModel):
    access_token: str
    user: UserOut


def user_out(db: Session, user: User) -> UserOut:
    ms = db.execute(select(Membership).where(Membership.user_id == user.id).order_by(Membership.created_at)).scalars()
    return UserOut(id=user.id, name=user.name, phone_last4=user.phone_last4, chat_id=user.chat_id,
                   orgs=[OrgRef(org_id=m.org_id, org_name=m.org.name, role=m.role) for m in ms])


def _set_refresh_cookie(response: Response, token: str, settings: Settings) -> None:
    response.set_cookie(REFRESH_COOKIE, token, max_age=int(REFRESH_TTL.total_seconds()), httponly=True,
                        secure=settings.api_base_url.startswith("https"), samesite="lax", path="/api/auth")


def _otp_error(exc: OtpError) -> HTTPException:
    return HTTPException(exc.status, str(exc))


@router.post("/otp/request", response_model=OtpRequested, dependencies=[Depends(rate_limit("otp", 10, 600))])
def otp_request(body: OtpRequest, db: Session = Depends(get_db),
                settings: Settings = Depends(get_settings)) -> OtpRequested:
    try:
        phone = normalize_phone(body.phone)
        issued = request_otp(db, phone, settings.otp_pepper, otp_provider)
    except OtpError as exc:
        raise _otp_error(exc) from exc
    existing = db.execute(select(User).where(User.phone_e164 == phone)).scalar_one_or_none()
    test_mode = settings.demo_mode or not settings.is_production
    return OtpRequested(phone=phone, is_new_user=existing is None,
                        needs_consent=existing is None or existing.consented_at is None, test_mode=test_mode,
                        test_code=issued.code if test_mode else None)


@router.post("/otp/verify", response_model=AuthOut, dependencies=[Depends(rate_limit("verify", 20, 600))])
def otp_verify(body: OtpVerify, request: Request, response: Response, db: Session = Depends(get_db),
               settings: Settings = Depends(get_settings)) -> AuthOut:
    try:
        phone = normalize_phone(body.phone)
        verify_otp(db, phone, body.code, settings.otp_pepper)
    except OtpError as exc:
        raise _otp_error(exc) from exc
    user = db.execute(select(User).where(User.phone_e164 == phone)).scalar_one_or_none()
    now = utcnow()
    if user is None or user.consented_at is None:
        if not body.consent:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please accept the privacy notice to continue")
        if user is None:
            if not body.name:
                raise HTTPException(status.HTTP_400_BAD_REQUEST, "Please tell us your name")
            user = User(phone_e164=phone, name=body.name.strip())
            db.add(user)
            db.flush()
            audit.record(db, "user.signup", actor_user_id=user.id, subject_type="user", subject_id=user.id)
        user.consent_version, user.consented_at = CONSENT_VERSION, now
        audit.record(db, "user.consent", actor_user_id=user.id, subject_type="user", subject_id=user.id,
                     payload={"version": CONSENT_VERSION})
    user.phone_verified_at = now
    issued = start_session(db, user.id, settings.jwt_secret, request.headers.get("user-agent"))
    audit.record(db, "auth.login", actor_user_id=user.id, subject_type="user", subject_id=user.id)
    db.commit()
    _set_refresh_cookie(response, issued.refresh_token, settings)
    return AuthOut(access_token=issued.access_token, user=user_out(db, user))


@router.post("/refresh", response_model=AuthOut, dependencies=[Depends(rate_limit("refresh", 60, 600))])
def refresh(request: Request, response: Response, db: Session = Depends(get_db),
            settings: Settings = Depends(get_settings)) -> AuthOut:
    token = request.cookies.get(REFRESH_COOKIE)
    if not token:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required")
    try:
        user_id, issued = rotate(db, token, settings.jwt_secret, request.headers.get("user-agent"))
    except SessionError as exc:
        response.delete_cookie(REFRESH_COOKIE, path="/api/auth")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    db.commit()
    _set_refresh_cookie(response, issued.refresh_token, settings)
    user = db.get(User, user_id)
    assert user is not None
    return AuthOut(access_token=issued.access_token, user=user_out(db, user))


@router.post("/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)) -> None:
    token = request.cookies.get(REFRESH_COOKIE)
    if token:
        revoke(db, token)
        db.commit()
    response.delete_cookie(REFRESH_COOKIE, path="/api/auth")


@me_router.get("/me", response_model=UserOut)
def me(user: User = Depends(current_user), db: Session = Depends(get_db)) -> UserOut:
    return user_out(db, user)
