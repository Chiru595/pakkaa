from __future__ import annotations

import hmac
from datetime import timedelta

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db, utcnow
from app.demo import ORG_NAME, PEOPLE, SCRIPT
from app.demo_seed import find_demo_org, rebuild
from app.deps import get_nexconn
from app.integrations.nexconn import NexconnClient
from app.security.deps import rate_limit

router = APIRouter(prefix="/api/demo", tags=["demo"])

RESET_COOLDOWN = timedelta(seconds=60)


class DemoAccount(BaseModel):
    phone: str
    name: str
    role: str
    about: str


class DemoStep(BaseModel):
    title: str
    who: str
    actions: list[str]


class DemoOut(BaseModel):
    enabled: bool
    org_name: str = ""
    accounts: list[DemoAccount] = []
    steps: list[DemoStep] = []
    note: str = ""


class ResetOut(BaseModel):
    reset: bool
    org_name: str


@router.get("", response_model=DemoOut)
def demo_guide(settings: Settings = Depends(get_settings)) -> DemoOut:
    """Only in demo mode: the seeded test accounts and the 3-minute script. Codes are shown on screen in demo mode."""
    if not settings.demo_mode:
        return DemoOut(enabled=False)
    return DemoOut(enabled=True, org_name=ORG_NAME,
                   accounts=[DemoAccount(phone=p.phone, name=p.name, role=p.role.value, about=p.about) for p in PEOPLE],
                   steps=[DemoStep(title=s.title, who=s.who, actions=list(s.actions)) for s in SCRIPT],
                   note="Demo data is fictional. No SMS is sent and payments are simulated.")


def _reset(db: Session, nexconn: NexconnClient, reason: str) -> ResetOut:
    existing = find_demo_org(db)
    if existing and utcnow() - existing.created_at < RESET_COOLDOWN:
        raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "The demo was rebuilt less than a minute ago")
    rebuild(db, nexconn, reason=reason)
    return ResetOut(reset=True, org_name=ORG_NAME)


@router.post("/reset", response_model=ResetOut, dependencies=[Depends(rate_limit("demo_reset", 3, 600))])
def reset_demo(db: Session = Depends(get_db), nexconn: NexconnClient = Depends(get_nexconn),
               settings: Settings = Depends(get_settings)) -> ResetOut:
    """Demo mode only: rebuild the Acme Studio workspace. Other workspaces are never touched."""
    if not settings.demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return _reset(db, nexconn, "button")


@router.get("/cron-reset", response_model=ResetOut)
def cron_reset(authorization: str = Header(default=""), db: Session = Depends(get_db),
               nexconn: NexconnClient = Depends(get_nexconn), settings: Settings = Depends(get_settings)) -> ResetOut:
    """Nightly rebuild by Vercel Cron. It does no more than the public reset button, so a secret is optional: when
    CRON_SECRET is set, Vercel sends `Authorization: Bearer $CRON_SECRET` and anything else is refused."""
    if not settings.demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if settings.cron_secret and not hmac.compare_digest(authorization, f"Bearer {settings.cron_secret}"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return _reset(db, nexconn, "cron")
