"""per-org audit chains

Splits the single global audit chain into one chain per org ("system" for events without an org), so an org's
export verifies on its own. Existing events keep their content, order and timestamps; their prev_hash and hash
are recomputed once, in seq order, under the new per-chain scheme. The hashing here is a frozen copy of the
scheme in app.security.audit at this revision.

Revision ID: 2196b8d62ff7
Revises: 8daf6f8b9d4f
Create Date: 2026-11-21 14:10:40.886868

"""
import hashlib
import json
from collections.abc import Sequence
from datetime import UTC

import sqlalchemy as sa
from alembic import op

revision: str = "2196b8d62ff7"
down_revision: str | Sequence[str] | None = "8daf6f8b9d4f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

GENESIS = "0" * 64
FIELDS = ("chain_key", "org_id", "actor_user_id", "action", "subject_type", "subject_id", "payload_hash",
          "created_at")


def _canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def _rechain(conn, with_chain: bool) -> None:
    rows = conn.execute(sa.text(
        "select seq, org_id, actor_user_id, action, subject_type, subject_id, payload_hash, created_at "
        "from audit_events order by seq")).mappings().all()
    last: dict[str, str] = {}
    for r in rows:
        chain = (r["org_id"] or "system") if with_chain else "global"
        prev = last.get(chain, GENESIS)
        fields = {**r, "chain_key": chain, "created_at": r["created_at"].astimezone(UTC).isoformat()}
        if with_chain:
            body = _canonical({k: fields[k] for k in FIELDS})
        else:
            body = _canonical({k: fields[k] for k in FIELDS if k != "chain_key"})
        h = hashlib.sha256(f"{prev}{body}".encode()).hexdigest()
        conn.execute(sa.text("update audit_events set prev_hash = :p, hash = :h where seq = :s"),
                     {"p": prev, "h": h, "s": r["seq"]})
        last[chain] = h


def upgrade() -> None:
    op.add_column("audit_events", sa.Column("chain_key", sa.String(length=32), nullable=True))
    op.execute("update audit_events set chain_key = coalesce(org_id, 'system')")
    op.alter_column("audit_events", "chain_key", nullable=False)
    op.create_index("ix_audit_chain_seq", "audit_events", ["chain_key", "seq"], unique=False)
    _rechain(op.get_bind(), with_chain=True)


def downgrade() -> None:
    op.drop_index("ix_audit_chain_seq", table_name="audit_events")
    op.drop_column("audit_events", "chain_key")
    _rechain(op.get_bind(), with_chain=False)
