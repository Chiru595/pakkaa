from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.config import Settings, get_settings
from app.demo import ORG_NAME, PEOPLE, SCRIPT

router = APIRouter(prefix="/api/demo", tags=["demo"])


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
    org_name: str
    accounts: list[DemoAccount]
    steps: list[DemoStep]
    note: str


@router.get("", response_model=DemoOut)
def demo_guide(settings: Settings = Depends(get_settings)) -> DemoOut:
    """Only in demo mode: the seeded test accounts and the 3-minute script. Codes are shown on screen in demo mode."""
    if not settings.demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    return DemoOut(org_name=ORG_NAME,
                   accounts=[DemoAccount(phone=p.phone, name=p.name, role=p.role.value, about=p.about) for p in PEOPLE],
                   steps=[DemoStep(title=s.title, who=s.who, actions=list(s.actions)) for s in SCRIPT],
                   note="Demo data is fictional. No SMS is sent and payments are simulated.")
