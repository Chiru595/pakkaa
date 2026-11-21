"""Append-only, hash-chained audit log, one chain per workspace.

Each event stores hash = sha256(prev_hash + canonical event fields), where prev_hash is the hash of the previous
event in the same chain (the org id, or "system" for events that belong to no org). Changing, deleting or
reordering any stored event breaks every hash after it in its chain. Because chains are per org, an org's export
can be verified on its own with `verify_events`, without access to the database or to other orgs' events.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import AuditEvent

GENESIS_HASH = "0" * 64
SYSTEM_CHAIN = "system"
HASH_FIELDS = ("chain_key", "org_id", "actor_user_id", "action", "subject_type", "subject_id", "payload_hash",
               "created_at")


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(canonical(payload).encode()).hexdigest()


def _iso(value: datetime | str) -> str:
    return value.astimezone(UTC).isoformat() if isinstance(value, datetime) else value


def event_hash(prev_hash: str, fields: Mapping[str, Any]) -> str:
    body = canonical({k: _iso(fields[k]) if k == "created_at" else fields[k] for k in HASH_FIELDS})
    return hashlib.sha256(f"{prev_hash}{body}".encode()).hexdigest()


def _fields(e: AuditEvent) -> dict[str, Any]:
    return {k: getattr(e, k) for k in HASH_FIELDS}


def record(db: Session, action: str, *, actor_user_id: str | None = None, org_id: str | None = None,
           subject_type: str | None = None, subject_id: str | None = None,
           payload: dict[str, Any] | None = None) -> AuditEvent:
    """Adds an audit event to the current transaction. The caller commits."""
    chain = org_id or SYSTEM_CHAIN
    db.execute(text("select pg_advisory_xact_lock(hashtext(:k))"), {"k": f"audit:{chain}"})
    last = db.execute(select(AuditEvent.hash).where(AuditEvent.chain_key == chain)
                      .order_by(AuditEvent.seq.desc()).limit(1)).scalar_one_or_none()
    payload = payload or {}
    event = AuditEvent(chain_key=chain, org_id=org_id, actor_user_id=actor_user_id, action=action,
                       subject_type=subject_type, subject_id=subject_id, payload=payload,
                       payload_hash=payload_hash(payload), prev_hash=last or GENESIS_HASH, created_at=utcnow())
    event.hash = event_hash(event.prev_hash, _fields(event))
    db.add(event)
    db.flush()
    return event


@dataclass
class ChainCheck:
    ok: bool
    events: int
    broken_at_seq: int | None = None
    reason: str | None = None


def verify_events(events: Iterable[Mapping[str, Any]]) -> ChainCheck:
    """Verifies one chain given as dicts in seq order (for example, parsed from an export)."""
    prev = GENESIS_HASH
    count = 0
    for e in events:
        count += 1
        seq = e.get("seq")
        if e["prev_hash"] != prev:
            return ChainCheck(False, count, seq, "prev_hash does not match the previous event")
        if payload_hash(e["payload"]) != e["payload_hash"]:
            return ChainCheck(False, count, seq, "payload changed")
        if event_hash(prev, e) != e["hash"]:
            return ChainCheck(False, count, seq, "event fields changed")
        prev = e["hash"]
    return ChainCheck(True, count)


def event_dict(e: AuditEvent) -> dict[str, Any]:
    return {"seq": e.seq, **{k: _iso(v) if k == "created_at" else v for k, v in _fields(e).items()},
            "payload": e.payload, "prev_hash": e.prev_hash, "hash": e.hash}


def chain_events(db: Session, chain: str) -> list[AuditEvent]:
    return list(db.execute(select(AuditEvent).where(AuditEvent.chain_key == chain).order_by(AuditEvent.seq))
                .scalars())


def verify_chain(db: Session, chain: str | None = None) -> ChainCheck:
    """Verifies one chain, or every chain when none is given."""
    chains = [chain] if chain else list(db.execute(select(AuditEvent.chain_key).distinct()).scalars())
    total = 0
    for key in chains:
        check = verify_events(event_dict(e) for e in chain_events(db, key))
        total += check.events
        if not check.ok:
            return ChainCheck(False, total, check.broken_at_seq, check.reason)
    return ChainCheck(True, total)
