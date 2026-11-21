"""Pay Lane risk engine. Rules run in code with explicit evidence; nothing here calls an LLM."""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import timedelta
from statistics import median
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.money import format_inr
from app.rules import load_pack

IST = ZoneInfo("Asia/Kolkata")


@dataclass
class Signal:
    rule_id: str
    weight: int
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)


@dataclass
class Assessment:
    score: int
    level: str
    signals: list[Signal]
    step_up_required: bool


def policy() -> dict[str, Any]:
    return load_pack("paylane_policy")


def approval_band(amount_paise: int) -> tuple[int, bool]:
    for band in policy()["approval_bands"]:
        limit = band["up_to_rupees"]
        if limit is None or amount_paise <= int(limit) * 100:
            return int(band["approvals"]), bool(band["owner_required"])
    raise ValueError("no approval band")


def normalise_name(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().casefold()
    return re.sub(r"[^a-z]", "", folded)


def name_collisions(db: Session, org_id: str, user_id: str, display_name: str) -> list[dict[str, str]]:
    """Other members whose name looks the same once case, spaces, accents and punctuation are ignored."""
    from app.models import Membership

    target = normalise_name(display_name)
    if not target:
        return []
    out = []
    for m in db.execute(select(Membership).where(Membership.org_id == org_id, Membership.user_id != user_id)).scalars():
        if normalise_name(m.user.name) == target:
            out.append({"user_id": m.user_id, "name": m.user.name, "role": m.role})
    return out


def _words(text: str, words: list[str]) -> list[str]:
    low = text.casefold()
    return [w for w in words if re.search(rf"(?<![a-z]){re.escape(w.casefold())}(?![a-z])", low)]


def assess(db: Session, *, org_id: str, card_id: str | None, payee_id: str | None, amount_paise: int, reason: str,
           requester_id: str | None, requester_role: str | None, extra_text: str = "",
           deal_room_id: str | None = None) -> Assessment:
    from app.domain import terms as T
    from app.domain.policy import effective_risk
    from app.models import CardState, Payee, PaymentCard, Room, TermSheet, User

    p = effective_risk(db, org_id)
    now = utcnow()
    signals: list[Signal] = []
    paid_q = select(PaymentCard).where(PaymentCard.org_id == org_id, PaymentCard.state == CardState.PAID)
    payee = db.get(Payee, payee_id) if payee_id else None

    if requester_role not in ("owner", "finance", "member"):
        signals.append(Signal("unverified_requester", 60, "Requested by someone who is not a verified team member",
                              {"role": requester_role}))
    if requester_id:
        requester = db.get(User, requester_id)
        if requester is not None:
            clashes = name_collisions(db, org_id, requester_id, requester.name)
            if requester_role in ("owner", "finance", "member"):
                clashes = [c for c in clashes if c["role"] in ("owner", "finance", "member")]
            if clashes:
                signals.append(Signal("name_collision", 40,
                                      f"Requester's name matches {clashes[0]['name']} ({clashes[0]['role']}), a "
                                      "different account", {"requester": requester.name, "matches": clashes}))
        prior = db.execute(paid_q.where(PaymentCard.requester_id == requester_id).limit(1)).first()
        if prior is None and requester_role in ("owner", "finance", "member"):
            signals.append(Signal("first_time_requester", 10, "First payment requested by this person", {}))

    if payee is None:
        signals.append(Signal("unknown_payee", 35, "Payee is not on the team's verified payee list", {}))
    else:
        history = db.execute(paid_q.where(PaymentCard.payee_id == payee.id)
                             .order_by(PaymentCard.created_at)).scalars().all()
        if not history:
            signals.append(Signal("new_payee", 25, f"{payee.name} has never been paid before", {"payee": payee.name}))
        else:
            base = int(median([c.amount_paise for c in history]))
            if amount_paise > base * p["baseline_multiplier"]:
                signals.append(Signal("above_baseline", 20,
                                      f"{format_inr(amount_paise)} is above the usual {format_inr(base)} for "
                                      f"{payee.name}", {"baseline_paise": base, "payments": len(history),
                                                        "last_paid": history[-1].created_at.date().isoformat()}))
        if len(history) >= 3:
            gaps = [(b.created_at - a.created_at).days for a, b in zip(history, history[1:], strict=False)]
            usual = median(gaps)
            since = (now - history[-1].created_at).days
            if usual >= 7 and since < usual * 0.4:
                signals.append(Signal("unusual_timing", 10,
                                      f"{payee.name} was paid {since} day(s) ago; usually every {round(usual)} days",
                                      {"days_since_last": since, "usual_gap_days": round(usual)}))
        if payee.details_changed_at and now - payee.details_changed_at < timedelta(days=p["bank_change_window_days"]):
            signals.append(Signal("bank_changed", 35,
                                  f"Bank/UPI details changed on {payee.details_changed_at.astimezone(IST):%d %b %Y}",
                                  {"changed_at": payee.details_changed_at.isoformat(),
                                   "changed_by": payee.details_changed_by, "now": payee.masked}))
        window = now - timedelta(days=p["split_window_days"])
        recent = db.execute(select(PaymentCard).where(
            PaymentCard.org_id == org_id, PaymentCard.payee_id == payee.id, PaymentCard.created_at > window,
            PaymentCard.state.in_([CardState.PENDING, CardState.APPROVED, CardState.PAID]),
            PaymentCard.id != (card_id or ""))).scalars().all()
        threshold = int(policy()["approval_bands"][0]["up_to_rupees"]) * 100
        small = [c for c in recent if c.amount_paise <= threshold]
        if amount_paise <= threshold and small and sum(c.amount_paise for c in small) + amount_paise > threshold:
            signals.append(Signal("split_payments", 30,
                                  f"{len(small) + 1} small payments to {payee.name} in {p['split_window_days']} days "
                                  f"add up to {format_inr(sum(c.amount_paise for c in small) + amount_paise)}",
                                  {"card_ids": [c.id for c in small]}))
    if payee is None or not db.execute(paid_q.where(PaymentCard.payee_id == payee.id).limit(1)).first():
        if amount_paise > int(p["no_baseline_large_rupees"]) * 100:
            signals.append(Signal("large_first_payment", 10, f"Large amount ({format_inr(amount_paise)}) with no "
                                                             "payment history", {}))

    text = f"{reason}\n{extra_text}"
    urgent = _words(text, p["urgency_words"])
    if urgent:
        signals.append(Signal("urgency", 15, f"Urgency words: {', '.join(urgent)}", {"words": urgent}))
    secret = _words(text, p["secrecy_words"])
    if secret:
        signals.append(Signal("secrecy", 25, f"Secrecy words: {', '.join(secret)}", {"words": secret}))

    local = now.astimezone(IST)
    start, end = p["working_hours_ist"]
    if local.weekday() == 6 or not start <= local.hour < end:
        signals.append(Signal("off_hours", 10, f"Raised outside working hours ({local:%a %H:%M} IST)", {}))

    if deal_room_id:
        room = db.get(Room, deal_room_id)
        sheet = db.execute(select(TermSheet).where(TermSheet.room_id == deal_room_id)).scalar_one_or_none()
        price = T.values(sheet.terms).get("price_paise") if sheet else None
        if room is None or room.org_id != org_id:
            signals.append(Signal("deal_mismatch", 30, "Linked deal is not in this workspace", {}))
        elif price and amount_paise > int(price):
            signals.append(Signal("deal_mismatch", 20,
                                  f"{format_inr(amount_paise)} is more than the deal's agreed {format_inr(int(price))}",
                                  {"deal": room.title, "price_paise": int(price)}))

    fired = {s.rule_id for s in signals}
    if "bank_changed" in fired and fired & {"urgency", "secrecy"}:
        signals.append(Signal("bank_change_with_pressure", 20,
                              "Bank details changed and the request pushes for speed or secrecy: the classic "
                              "payment-fraud pattern", {}))
    score = min(100, sum(s.weight for s in signals))
    level = "high" if score >= p["high_at"] else "medium" if score >= p["medium_at"] else "low"
    return Assessment(score, level, signals, step_up_required=level == "high" or "bank_changed" in fired)
