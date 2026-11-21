from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import TEAM_ROLES, MemoryItem, MemoryKind, MemoryVisibility, User
from app.security.deps import current_user, rate_limit, require_org_role

router = APIRouter(prefix="/api/orgs", tags=["memory"])


class MemoryOut(BaseModel):
    id: str
    kind: str
    visibility: str
    text: str
    source: str
    created_at: datetime
    data: dict[str, Any]


@router.get("/{org_id}/memory", response_model=list[MemoryOut], dependencies=[Depends(rate_limit("memory", 60, 60))])
def list_memory(org_id: str, kind: MemoryKind | None = Query(default=None),
                limit: int = Query(default=30, ge=1, le=100), user: User = Depends(current_user),
                db: Session = Depends(get_db)) -> list[MemoryOut]:
    """What the workspace has learned that this person may see: team items plus their own private items."""
    require_org_role(db, user, org_id, TEAM_ROLES)
    q = select(MemoryItem).where(MemoryItem.org_id == org_id).where(
        (MemoryItem.visibility == MemoryVisibility.TEAM)
        | ((MemoryItem.visibility == MemoryVisibility.PRIVATE) & (MemoryItem.owner_user_id == user.id)))
    if kind is not None:
        q = q.where(MemoryItem.kind == kind)
    rows = db.execute(q.order_by(MemoryItem.created_at.desc()).limit(limit)).scalars()
    return [MemoryOut(id=m.id, kind=m.kind, visibility=m.visibility, text=m.text,
                      source=f"{m.source_type}:{m.source_id}", created_at=m.created_at, data=m.data) for m in rows]
