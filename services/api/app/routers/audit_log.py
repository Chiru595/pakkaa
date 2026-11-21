from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db import get_db, utcnow
from app.models import Role, User
from app.security import audit
from app.security.deps import current_user, rate_limit, require_org_role

router = APIRouter(prefix="/api/orgs", tags=["audit"])

AUDITORS = {Role.OWNER, Role.FINANCE}
CSV_COLUMNS = ["seq", *audit.HASH_FIELDS, "payload", "prev_hash", "hash"]


class VerifyOut(BaseModel):
    ok: bool
    events: int
    broken_at_seq: int | None
    reason: str | None
    head_hash: str | None


@router.get("/{org_id}/audit/verify", response_model=VerifyOut, dependencies=[Depends(rate_limit("audit", 30, 60))])
def verify(org_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> VerifyOut:
    require_org_role(db, user, org_id, AUDITORS)
    events = audit.chain_events(db, org_id)
    check = audit.verify_events(audit.event_dict(e) for e in events)
    return VerifyOut(ok=check.ok, events=check.events, broken_at_seq=check.broken_at_seq, reason=check.reason,
                     head_hash=events[-1].hash if events else None)


@router.get("/{org_id}/audit/export", dependencies=[Depends(rate_limit("audit-export", 10, 600))])
def export(org_id: str, format: Literal["jsonl", "csv"] = Query(default="jsonl"), user: User = Depends(current_user),
           db: Session = Depends(get_db)) -> StreamingResponse:
    """The org's whole chain in seq order. It verifies on its own: see scripts/verify_audit_export.py."""
    require_org_role(db, user, org_id, AUDITORS)
    rows = [audit.event_dict(e) for e in audit.chain_events(db, org_id)]
    audit.record(db, "audit.export", actor_user_id=user.id, org_id=org_id, subject_type="org", subject_id=org_id,
                 payload={"format": format, "events": len(rows), "head_hash": rows[-1]["hash"] if rows else None})
    db.commit()
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")

    def jsonl() -> Iterator[str]:
        for r in rows:
            yield json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n"

    def as_csv() -> Iterator[str]:
        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for r in rows:
            writer.writerow({**{k: "" if r[k] is None else r[k] for k in CSV_COLUMNS if k != "payload"},
                             "payload": audit.canonical(r["payload"])})
        yield buf.getvalue()

    media = "application/x-ndjson" if format == "jsonl" else "text/csv"
    return StreamingResponse(jsonl() if format == "jsonl" else as_csv(), media_type=media, headers={
        "Content-Disposition": f'attachment; filename="pakkaa-audit-{org_id[:8]}-{stamp}.{format}"',
        "Cache-Control": "no-store"})
