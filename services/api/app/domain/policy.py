"""Per-workspace risk thresholds on top of the policy pack.

Learning may only tighten a threshold. Loosening one is a person's decision: the owner, with a written reason, and
it is audited. Every change keeps the previous value and what caused it.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PaymentCard, PolicyOverride, Role
from app.rules import load_pack
from app.security import audit


@dataclass(frozen=True)
class Tunable:
    label: str
    lower_is_tighter: bool
    minimum: float
    maximum: float
    integer: bool = True


TUNABLES: dict[str, Tunable] = {
    "high_at": Tunable("High-risk score threshold", True, 35, 90),
    "medium_at": Tunable("Medium-risk score threshold", True, 15, 60),
    "baseline_multiplier": Tunable("Above-usual amount multiplier", True, 1.1, 3.0, integer=False),
    "bank_change_window_days": Tunable("Days a bank change stays risky", False, 7, 120),
    "no_baseline_large_rupees": Tunable("Large first payment", True, 5000, 500000),
}
LEARNING_STEP = 5
MIN_REASON = 10


def _pack_risk() -> dict[str, Any]:
    return dict(load_pack("paylane_policy")["risk"])


def _latest(db: Session, org_id: str) -> dict[str, PolicyOverride]:
    rows = db.execute(select(PolicyOverride).where(PolicyOverride.org_id == org_id)
                      .order_by(PolicyOverride.created_at)).scalars()
    latest: dict[str, PolicyOverride] = {}
    for row in rows:
        latest[row.key] = row
    return latest


def effective_risk(db: Session, org_id: str) -> dict[str, Any]:
    risk = _pack_risk()
    for key, row in _latest(db, org_id).items():
        t = TUNABLES.get(key)
        if t:
            risk[key] = int(row.value) if t.integer else float(row.value)
    if risk["medium_at"] >= risk["high_at"]:
        risk["medium_at"] = max(TUNABLES["medium_at"].minimum, risk["high_at"] - 5)
    return risk


def direction(key: str, old: float, new: float) -> str:
    if new == old:
        return "same"
    tighter = new < old if TUNABLES[key].lower_is_tighter else new > old
    return "tighten" if tighter else "loosen"


def set_threshold(db: Session, org_id: str, key: str, value: float, *, source: str, reason: str,
                  actor_id: str | None = None, actor_role: str | None = None,
                  source_id: str | None = None) -> PolicyOverride | None:
    if source != "learning" and actor_role not in (Role.OWNER, Role.FINANCE):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only owners and finance can change risk thresholds")
    t = TUNABLES.get(key)
    if t is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Unknown threshold")
    if not t.minimum <= value <= t.maximum:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"{t.label} must be between {t.minimum:g} and {t.maximum:g}")
    value = float(round(value)) if t.integer else round(value, 2)
    old = float(effective_risk(db, org_id)[key])
    move = direction(key, old, value)
    if move == "same":
        return None
    reason = " ".join(reason.split())
    if source == "learning":
        if move != "tighten":
            raise ValueError("learning may only tighten thresholds")
    elif move == "loosen":
        if actor_role != Role.OWNER:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Only the owner can loosen a risk threshold")
        if len(reason) < MIN_REASON:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, "Explain why you are loosening this threshold")
    row = PolicyOverride(org_id=org_id, key=key, value=value, previous=old, direction=move, source=source,
                         reason=reason[:300], set_by=actor_id, source_id=source_id)
    db.add(row)
    db.flush()
    audit.record(db, f"policy.{move}", actor_user_id=actor_id, org_id=org_id, subject_type="policy",
                 subject_id=key, payload={"key": key, "from": old, "to": value, "source": source,
                                          "reason": reason[:300], "source_id": source_id})
    return row


def learn_from_fraud(db: Session, card: PaymentCard, actor_id: str) -> list[PolicyOverride]:
    """A card marked as suspected fraud that the engine did not rate high: make the thresholds stricter."""
    if card.risk_level == "high":
        return []
    risk = effective_risk(db, card.org_id)
    reason = (f"Card #{card.number} was marked as suspected fraud at risk score {card.risk_score} "
              f"({card.risk_level})")
    changed = []
    for key in ("high_at", "medium_at"):
        target = max(TUNABLES[key].minimum, min(risk[key] - LEARNING_STEP, card.risk_score))
        if key == "medium_at":
            target = max(TUNABLES[key].minimum, risk[key] - LEARNING_STEP)
        if target < risk[key]:
            row = set_threshold(db, card.org_id, key, target, source="learning", reason=reason,
                                actor_id=actor_id, source_id=card.id)
            if row:
                changed.append(row)
    return changed


def history(db: Session, org_id: str, limit: int = 20) -> list[PolicyOverride]:
    return list(db.execute(select(PolicyOverride).where(PolicyOverride.org_id == org_id)
                           .order_by(PolicyOverride.created_at.desc()).limit(limit)).scalars())
