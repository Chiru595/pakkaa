"""Append-only, hash-chained audit log.

Each event stores sha256(prev_hash + canonical event fields). Changing, deleting or reordering any stored
event breaks every hash after it, which `verify_chain` detects.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import AuditEvent

GENESIS_HASH = "0" * 64
_CHAIN_LOCK_KEY = 7_420_001


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _event_hash(prev_hash: str, e: AuditEvent) -> str:
    body = _canonical({
        "org_id": e.org_id, "actor_user_id": e.actor_user_id, "action": e.action, "subject_type": e.subject_type,
        "subject_id": e.subject_id, "payload_hash": e.payload_hash,
        "created_at": e.created_at.astimezone(UTC).isoformat(),
    })
    return hashlib.sha256(f"{prev_hash}{body}".encode()).hexdigest()


def record(db: Session, action: str, *, actor_user_id: str | None = None, org_id: str | None = None,
           subject_type: str | None = None, subject_id: str | None = None,
           payload: dict[str, Any] | None = None) -> AuditEvent:
    """Adds an audit event to the current transaction. The caller commits."""
    db.execute(text("select pg_advisory_xact_lock(:k)"), {"k": _CHAIN_LOCK_KEY})
    last = db.execute(select(AuditEvent.hash).order_by(AuditEvent.seq.desc()).limit(1)).scalar_one_or_none()
    payload = payload or {}
    event = AuditEvent(org_id=org_id, actor_user_id=actor_user_id, action=action, subject_type=subject_type,
                       subject_id=subject_id, payload=payload,
                       payload_hash=hashlib.sha256(_canonical(payload).encode()).hexdigest(),
                       prev_hash=last or GENESIS_HASH)
    event.created_at = utcnow()
    event.hash = _event_hash(event.prev_hash, event)
    db.add(event)
    db.flush()
    return event


@dataclass
class ChainCheck:
    ok: bool
    events: int
    broken_at_seq: int | None = None
    reason: str | None = None


def verify_chain(db: Session) -> ChainCheck:
    prev = GENESIS_HASH
    count = 0
    for e in db.execute(select(AuditEvent).order_by(AuditEvent.seq)).scalars():
        count += 1
        if e.prev_hash != prev:
            return ChainCheck(False, count, e.seq, "prev_hash does not match the previous event")
        if hashlib.sha256(_canonical(e.payload).encode()).hexdigest() != e.payload_hash:
            return ChainCheck(False, count, e.seq, "payload changed")
        if _event_hash(prev, e) != e.hash:
            return ChainCheck(False, count, e.seq, "event fields changed")
        prev = e.hash
    return ChainCheck(True, count)
