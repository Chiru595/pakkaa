"""Short-lived access JWTs plus rotating refresh tokens with reuse detection."""
from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta

import jwt
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import new_id, utcnow
from app.models import AuthSession

ACCESS_TTL = timedelta(minutes=15)
REFRESH_TTL = timedelta(days=14)
ISSUER = "pakkaa"
REFRESH_COOKIE = "pk_refresh"


class SessionError(Exception):
    pass


@dataclass
class IssuedSession:
    access_token: str
    refresh_token: str
    session_id: str


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def create_access_token(user_id: str, secret: str, session_id: str) -> str:
    now = utcnow()
    return jwt.encode({"sub": user_id, "sid": session_id, "iss": ISSUER, "iat": now, "exp": now + ACCESS_TTL},
                      secret, algorithm="HS256")


def decode_access_token(token: str, secret: str) -> dict:
    try:
        return jwt.decode(token, secret, algorithms=["HS256"], issuer=ISSUER, options={"require": ["exp", "sub"]})
    except jwt.PyJWTError as exc:
        raise SessionError("invalid token") from exc


def start_session(db: Session, user_id: str, secret: str, user_agent: str | None,
                  family_id: str | None = None) -> IssuedSession:
    refresh = secrets.token_urlsafe(48)
    s = AuthSession(user_id=user_id, family_id=family_id or new_id(), refresh_hash=_hash(refresh),
                    expires_at=utcnow() + REFRESH_TTL, user_agent=(user_agent or "")[:200])
    db.add(s)
    db.flush()
    return IssuedSession(create_access_token(user_id, secret, s.id), refresh, s.id)


def rotate(db: Session, refresh_token: str, secret: str, user_agent: str | None) -> tuple[str, IssuedSession]:
    """Exchanges a refresh token for a new pair. Reusing an old token revokes the whole session family."""
    s = db.execute(select(AuthSession).where(AuthSession.refresh_hash == _hash(refresh_token)).with_for_update()
                   ).scalar_one_or_none()
    now = utcnow()
    if s is None:
        raise SessionError("unknown session")
    if s.revoked_at is not None or s.replaced_by is not None:
        db.execute(update(AuthSession).where(AuthSession.family_id == s.family_id, AuthSession.revoked_at.is_(None))
                   .values(revoked_at=now))
        db.commit()
        raise SessionError("refresh token reuse detected; all sessions in this family were signed out")
    if s.expires_at < now:
        raise SessionError("session expired")
    issued = start_session(db, s.user_id, secret, user_agent, family_id=s.family_id)
    s.replaced_by = issued.session_id
    s.revoked_at = now
    return s.user_id, issued


def revoke(db: Session, refresh_token: str) -> None:
    db.execute(update(AuthSession).where(AuthSession.refresh_hash == _hash(refresh_token),
                                         AuthSession.revoked_at.is_(None)).values(revoked_at=utcnow()))


def is_session_active(db: Session, session_id: str) -> bool:
    """False once the session's family was signed out (logout or refresh-token reuse)."""
    s = db.get(AuthSession, session_id)
    if s is None:
        return False
    killed = db.execute(select(AuthSession.id).where(
        AuthSession.family_id == s.family_id, AuthSession.revoked_at.is_not(None), AuthSession.replaced_by.is_(None)
    ).limit(1)).first()
    return killed is None
