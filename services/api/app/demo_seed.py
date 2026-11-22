"""Builds the "Acme Studio" demo workspace: people, rooms, a past deal with a client who paid the advance late,
payees with payment history, and the memory those produce. Used by scripts/seed_demo.py and, in demo mode only,
by the demo reset endpoint and its daily cron.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy import text as sql
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import new_id, utcnow
from app.demo import ORG_NAME, PEOPLE, Person
from app.domain import terms as T
from app.domain.paylane import control
from app.domain.rooms import add_member, create_room, ensure_bot
from app.integrations.nexconn import NexconnClient
from app.llm import get_llm
from app.memory.store import remember
from app.models import (
    CardApproval,
    CardState,
    ChatMessage,
    DealOutcome,
    Membership,
    MemoryKind,
    MessageKind,
    Org,
    Payee,
    PaymentCard,
    PaymentLink,
    PaymentStatus,
    Payout,
    Role,
    RoomSide,
    RoomType,
    TermSheet,
    TermSheetSignature,
    TermSheetStatus,
    User,
)
from app.money import format_inr
from app.security import audit, crypto
from app.security.otp import normalize_phone

CONSENT_VERSION = "2026-11-21"


def find_demo_org(db: Session) -> Org | None:
    owner = db.execute(select(User).where(User.phone_e164 == normalize_phone(PEOPLE[0].phone))).scalar_one_or_none()
    if owner is None:
        return None
    return db.execute(select(Org).join(Membership, Membership.org_id == Org.id).where(
        Membership.user_id == owner.id, Membership.role == Role.OWNER, Org.name == ORG_NAME)).scalar_one_or_none()


def _user(db: Session, p: Person) -> User:
    phone = normalize_phone(p.phone)
    u = db.execute(select(User).where(User.phone_e164 == phone)).scalar_one_or_none()
    now = utcnow()
    if u is None:
        u = User(phone_e164=phone, name=p.name)
        db.add(u)
    u.name, u.phone_verified_at, u.consent_version, u.consented_at = p.name, now, CONSENT_VERSION, now
    db.flush()
    return u


def _msg(db: Session, room_id: str, sender: User | None, text: str, at, *, card: dict | None = None) -> None:
    db.add(ChatMessage(room_id=room_id, sender_user_id=sender.id if sender else None,
                       sender_chat_id=sender.chat_id if sender else get_settings().nexconn_bot_user_id,
                       kind=MessageKind.CARD if card else MessageKind.TEXT, body=text, card=card, created_at=at))


def _payee(db: Session, org: Org, creator: User, name: str, *, vpa: str | None = None,
           account: str | None = None, ifsc: str | None = None, contact: User | None = None) -> Payee:
    p = Payee(id=new_id(), org_id=org.id, name=name, created_by=creator.id,
              contact_user_id=contact.id if contact else None, method="upi" if vpa else "bank", masked="",
              details_fingerprint="")
    if vpa:
        p.vpa_enc = crypto.encrypt(vpa, p.id)
        p.masked = f"UPI {crypto.mask(vpa.split('@')[0])}@{vpa.split('@')[1]}"
        p.details_fingerprint = crypto.fingerprint(f"upi:{vpa}")
    else:
        assert account and ifsc
        p.account_enc, p.ifsc = crypto.encrypt(account, p.id), ifsc
        p.masked = f"A/c {crypto.mask(account)} · {ifsc}"
        p.details_fingerprint = crypto.fingerprint(f"bank:{account}:{ifsc}")
    db.add(p)
    db.flush()
    return p


def _paid_card(db: Session, org: Org, lane_id: str, payee: Payee, requester: User, approvers: list[User],
               amount: int, reason: str, days_ago: int) -> PaymentCard:
    at = utcnow() - timedelta(days=days_ago)
    ctl = control(db, org.id, lock=True)
    ctl.card_seq += 1
    card = PaymentCard(org_id=org.id, room_id=lane_id, number=ctl.card_seq, payee_id=payee.id, amount_paise=amount,
                       reason=reason, state=CardState.PAID, source="builder", requester_id=requester.id, version=1,
                       risk_score=10, risk_level="low", required_approvals=len(approvers), created_at=at,
                       updated_at=at, decided_at=at)
    db.add(card)
    db.flush()
    for a in approvers:
        db.add(CardApproval(card_id=card.id, card_version=1, approver_id=a.id, role=Role.FINANCE, created_at=at))
    db.add(Payout(card_id=card.id, idempotency_key=f"seed_{card.id}", org_id=org.id, amount_paise=amount,
                  provider="simulated", provider_ref=f"sim_po_{card.id[:12]}", status="paid",
                  initiated_by=approvers[0].id, created_at=at))
    return card


def seed(db: Session, nexconn: NexconnClient) -> dict[str, str]:
    settings = get_settings()
    llm = get_llm()
    ensure_bot(nexconn, settings)
    u = {p.key: _user(db, p) for p in PEOPLE}
    owner, finance, finance2, kabir, ravi = u["owner"], u["finance"], u["finance2"], u["freelancer"], u["client"]

    org = Org(name=ORG_NAME)
    db.add(org)
    db.flush()
    for p in PEOPLE:
        db.add(Membership(org_id=org.id, user_id=u[p.key].id, role=p.role))
    db.flush()
    audit.record(db, "org.create", actor_user_id=owner.id, org_id=org.id, subject_type="org", subject_id=org.id,
                 payload={"name": org.name, "seeded": True})

    lane = create_room(db, nexconn, settings, org_id=org.id, room_type=RoomType.PAYLANE, title="Acme payouts",
                       creator=owner)
    for key in ("vendor1", "vendor2", "impostor"):
        add_member(db, nexconn, lane, u[key], RoomSide.VENDOR)

    past = create_room(db, nexconn, settings, org_id=org.id, room_type=RoomType.DEAL,
                       title="Packaging design for Ravi Foods", creator=owner)
    add_member(db, nexconn, past, ravi, RoomSide.CLIENT)
    locked_at = utcnow() - timedelta(days=60)
    for i, (who, text) in enumerate([
        (owner, "Hi Ravi! For the full packaging set (3 SKUs) my quote is ₹55,000, 3 weeks."),
        (ravi, "Budget is tight this quarter. Can you do ₹45,000?"),
        (owner, "₹45,000 works if we keep it to 3 SKUs and 3 revision rounds, with a 30% advance."),
        (ravi, "Deal. I'll pay the advance this week."),
    ]):
        _msg(db, past.id, who, text, locked_at - timedelta(hours=4 - i))
    terms = {k: {"value": v, "source_message_ids": [], "updated_by": owner.id, "via": "manual",
                 "updated_at": locked_at.isoformat()}
             for k, v in {"scope": "Packaging design for 3 SKUs", "price_paise": 4_500_000, "advance_pct": 30,
                          "timeline_days": 21, "revisions": 3}.items()}
    sheet = TermSheet(room_id=past.id, version=6, terms=terms, category="packaging", status=TermSheetStatus.LOCKED,
                      locked_at=locked_at, updated_at=locked_at)
    db.add(sheet)
    db.flush()
    sheet.locked_hash = T.sheet_hash(past.id, sheet.version, terms)
    for who, side in ((owner, RoomSide.PROVIDER), (ravi, RoomSide.CLIENT)):
        db.add(TermSheetSignature(term_sheet_id=sheet.id, version=sheet.version, sheet_hash=sheet.locked_hash,
                                  user_id=who.id, side=side, signed_at=locked_at))
    advance = T.advance_paise(terms) or 0
    paid_at = locked_at + timedelta(days=18)
    link = PaymentLink(org_id=org.id, room_id=past.id, term_sheet_id=sheet.id, term_version=sheet.version,
                       amount_paise=advance, status=PaymentStatus.PAID, provider="simulated",
                       provider_ref=f"sim_link_{new_id()[:12]}", public_token=new_id() + new_id()[:8],
                       created_by=owner.id, created_at=locked_at, paid_at=paid_at)
    db.add(link)
    _msg(db, past.id, None, f"Advance of {format_inr(advance)} received (simulated)", paid_at,
         card={"kind": "payment_status", "v": 1, "status": "paid", "simulated": True,
               "title": f"Advance of {format_inr(advance)} received (simulated)"})
    db.add(DealOutcome(room_id=past.id, org_id=org.id, outcome="won", first_ask_paise=5_500_000,
                       final_paise=4_500_000, note="Client is price-sensitive; held scope to protect margin",
                       recorded_by=owner.id, created_at=paid_at + timedelta(days=25)))
    db.flush()
    remember(db, llm, org_id=org.id, kind=MemoryKind.COUNTERPARTY,
             text=f'{ravi.name} paid the {format_inr(advance)} advance for "{past.title}" 18 days after the terms '
                  "were locked (15 days late).",
             source_type="payment_link", source_id=link.id, subject_type="user", subject_id=ravi.id,
             data={"days_to_pay": 18, "days_late": 15, "amount_paise": advance, "room_id": past.id})
    remember(db, llm, org_id=org.id, kind=MemoryKind.PLAYBOOK,
             text=f'Deal "{past.title}" with {ravi.name} was won. Final {format_inr(4_500_000)} vs first ask '
                  f"{format_inr(5_500_000)} (-18%). Note: Client is price-sensitive; held scope to protect margin.",
             source_type="deal_outcome", source_id=past.id, subject_type="room", subject_id=past.id,
             data={"outcome": "won", "first_ask_paise": 5_500_000, "final_paise": 4_500_000, "category": "packaging"})
    remember(db, llm, org_id=org.id, kind=MemoryKind.COUNTERPARTY,
             text=f'{ravi.name}: deal "{past.title}" won. Final {format_inr(4_500_000)} vs first ask '
                  f"{format_inr(5_500_000)} (-18%).",
             source_type="deal_outcome", source_id=past.id, subject_type="user", subject_id=ravi.id,
             data={"outcome": "won", "final_paise": 4_500_000})

    meera = _payee(db, org, finance, "Meera Prints", vpa="meeraprints@okhdfcbank", contact=u["vendor1"])
    kabir_payee = _payee(db, org, finance, "Kabir Sharma", account="50100234567890", ifsc="HDFC0001234",
                         contact=kabir)
    for days, amount in ((75, 1_800_000), (45, 1_750_000), (15, 1_850_000)):
        card = _paid_card(db, org, lane.id, meera, kabir, [finance], amount, "Brochure and label printing", days)
        remember(db, llm, org_id=org.id, kind=MemoryKind.PAYEE,
                 text=f"Paid Meera Prints {format_inr(amount)} for brochure and label printing (card #{card.number}).",
                 source_type="payment_card", source_id=card.id, subject_type="payee", subject_id=meera.id,
                 data={"amount_paise": amount})
    for days, amount in ((50, 4_000_000), (20, 3_800_000)):
        _paid_card(db, org, lane.id, kabir_payee, owner, [finance, finance2], amount, "Logo and identity work", days)
    changed = utcnow() - timedelta(days=2)
    kabir_payee.bank_history = [{"masked": "A/c ••••4410 · ICIC0000123", "until": changed.isoformat()}]
    kabir_payee.details_changed_at, kabir_payee.details_changed_by = changed, finance.id
    for i, (who, text) in enumerate([
        (finance, "Reminder: every payout goes through a card here. No transfers from WhatsApp requests."),
        (kabir, "Updated my bank details with Farah this week, new HDFC account."),
    ]):
        _msg(db, lane.id, who, text, changed + timedelta(minutes=10 * (i + 1)))
    audit.record(db, "demo.seeded", actor_user_id=owner.id, org_id=org.id, subject_type="org", subject_id=org.id,
                 payload={"people": len(PEOPLE), "rooms": 2, "paid_cards": 5, "memory_items": 6})
    db.commit()
    return {"org_id": org.id, "lane_id": lane.id, "past_deal_id": past.id}


DEMO_LOCK_KEY = 72_517_001


def rebuild(db: Session, nexconn: NexconnClient, *, reason: str) -> dict[str, str]:
    """Delete the demo workspace (only that org) and seed it again. Serialised with an advisory lock."""
    db.execute(sql("select pg_advisory_xact_lock(:k)"), {"k": DEMO_LOCK_KEY})
    existing = find_demo_org(db)
    if existing:
        audit.record(db, "demo.reset", org_id=existing.id, subject_type="org", subject_id=existing.id,
                     payload={"reason": reason})
        db.delete(existing)
        db.flush()
    return seed(db, nexconn)
