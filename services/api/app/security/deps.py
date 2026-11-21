from __future__ import annotations

import time
from collections import defaultdict, deque
from collections.abc import Callable, Iterable
from threading import Lock

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db
from app.models import TEAM_ROLES, Membership, Role, Room, RoomMember, User
from app.security.sessions import SessionError, decode_access_token, is_session_active


def current_user(request: Request, db: Session = Depends(get_db),
                 settings: Settings = Depends(get_settings)) -> User:
    auth = request.headers.get("authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required")
    try:
        claims = decode_access_token(auth[7:].strip(), settings.jwt_secret)
    except SessionError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session expired") from exc
    if not is_session_active(db, claims.get("sid", "")):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Session ended")
    user = db.get(User, claims["sub"])
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in required")
    return user


def membership_for(db: Session, user_id: str, org_id: str) -> Membership | None:
    return db.execute(select(Membership).where(Membership.org_id == org_id, Membership.user_id == user_id)
                      ).scalar_one_or_none()


def require_org_role(db: Session, user: User, org_id: str, roles: Iterable[Role]) -> Membership:
    """404 for non-members (never reveal another org exists), 403 for members without the role."""
    m = membership_for(db, user.id, org_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if m.role not in set(roles):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Your role cannot do this")
    return m


def room_access(db: Session, user: User, room_id: str) -> tuple[Room, Membership, RoomMember | None]:
    """Team members can open any room in their org; guests only rooms they were invited to."""
    room = db.get(Room, room_id)
    if room is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    m = membership_for(db, user.id, room.org_id)
    rm = db.get(RoomMember, (room.id, user.id))
    if m is None or (m.role not in TEAM_ROLES and rm is None):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return room, m, rm


class RateLimiter:
    """In-process sliding window. Shared limits across instances come from the DB-backed OTP limits."""

    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit, self.window = limit, window_seconds
        self.hits: dict[str, deque[float]] = defaultdict(deque)
        self.lock = Lock()

    def check(self, key: str) -> None:
        now = time.monotonic()
        with self.lock:
            q = self.hits[key]
            while q and now - q[0] > self.window:
                q.popleft()
            if len(q) >= self.limit:
                raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests. Slow down")
            q.append(now)


def client_ip(request: Request) -> str:
    """Vercel sets x-real-ip at its edge; locally there is no proxy."""
    return request.headers.get("x-real-ip") or (request.client.host if request.client else "unknown")


_LIMITERS: list[RateLimiter] = []


def reset_rate_limits() -> None:
    for limiter in _LIMITERS:
        with limiter.lock:
            limiter.hits.clear()


def rate_limit(name: str, limit: int, window_seconds: float) -> Callable[[Request], None]:
    limiter = RateLimiter(limit, window_seconds)
    _LIMITERS.append(limiter)

    def dependency(request: Request) -> None:
        limiter.check(f"{name}:{client_ip(request)}")

    return dependency
