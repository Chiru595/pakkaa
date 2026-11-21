from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import BigInteger, Float, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, new_id, utcnow


class Role(StrEnum):
    OWNER = "owner"
    FINANCE = "finance"
    MEMBER = "member"
    CLIENT_GUEST = "client_guest"
    VENDOR_GUEST = "vendor_guest"


TEAM_ROLES = frozenset({Role.OWNER, Role.FINANCE, Role.MEMBER})
GUEST_ROLES = frozenset({Role.CLIENT_GUEST, Role.VENDOR_GUEST})


class RoomType(StrEnum):
    DEAL = "deal"
    PAYLANE = "paylane"


class RoomSide(StrEnum):
    PROVIDER = "provider"
    CLIENT = "client"
    TEAM = "team"
    VENDOR = "vendor"


class Org(Base):
    __tablename__ = "orgs"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class User(Base):
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    phone_e164: Mapped[str] = mapped_column(String(20), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    phone_verified_at: Mapped[datetime | None]
    consent_version: Mapped[str | None] = mapped_column(String(20))
    consented_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    @property
    def chat_id(self) -> str:
        return f"u{self.id}"

    @property
    def phone_last4(self) -> str:
        return self.phone_e164[-4:]


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("org_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)

    user: Mapped[User] = relationship(lazy="joined")
    org: Mapped[Org] = relationship(lazy="joined")


class Room(Base):
    __tablename__ = "rooms"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(10))
    title: Mapped[str] = mapped_column(String(120))
    nexconn_channel_id: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class RoomMember(Base):
    __tablename__ = "room_members"
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    side: Mapped[str] = mapped_column(String(10))
    joined_at: Mapped[datetime] = mapped_column(default=utcnow)

    user: Mapped[User] = relationship(lazy="joined")


class OtpChallenge(Base):
    __tablename__ = "otp_challenges"
    __table_args__ = (Index("ix_otp_phone_created", "phone_e164", "created_at"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    phone_e164: Mapped[str] = mapped_column(String(20))
    purpose: Mapped[str] = mapped_column(String(80), default="login", server_default="login")
    code_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[datetime]
    attempts: Mapped[int] = mapped_column(default=0)
    consumed_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AuthSession(Base):
    __tablename__ = "auth_sessions"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    family_id: Mapped[str] = mapped_column(String(32), index=True)
    refresh_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime]
    revoked_at: Mapped[datetime | None]
    replaced_by: Mapped[str | None] = mapped_column(String(32))
    user_agent: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Invite(Base):
    __tablename__ = "invites"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"))
    role: Mapped[str] = mapped_column(String(20))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    expires_at: Mapped[datetime]
    used_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    used_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_chain_seq", "chain_key", "seq"),)
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    chain_key: Mapped[str] = mapped_column(String(32))
    org_id: Mapped[str | None] = mapped_column(String(32), index=True)
    actor_user_id: Mapped[str | None] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(60))
    subject_type: Mapped[str | None] = mapped_column(String(30))
    subject_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    payload_hash: Mapped[str] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64))
    hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class MessageKind(StrEnum):
    TEXT = "text"
    CARD = "card"


class ChatMessage(Base):
    """Our record of every room message; Nexconn delivers it live, this table is the source of truth."""

    __tablename__ = "chat_messages"
    __table_args__ = (Index("ix_chat_messages_room_created", "room_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"))
    nexconn_message_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    sender_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    sender_chat_id: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(10))
    body: Mapped[str] = mapped_column(Text, default="")
    card: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class TermSheetStatus(StrEnum):
    DRAFT = "draft"
    LOCKED = "locked"


class TermSheet(Base):
    __tablename__ = "term_sheets"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), unique=True)
    version: Mapped[int] = mapped_column(default=1)
    terms: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    category: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(10), default=TermSheetStatus.DRAFT)
    locked_hash: Mapped[str | None] = mapped_column(String(64))
    locked_at: Mapped[datetime | None]
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class TermSheetSignature(Base):
    __tablename__ = "term_sheet_signatures"
    __table_args__ = (UniqueConstraint("term_sheet_id", "version", "user_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    term_sheet_id: Mapped[str] = mapped_column(ForeignKey("term_sheets.id", ondelete="CASCADE"), index=True)
    version: Mapped[int]
    sheet_hash: Mapped[str] = mapped_column(String(64))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    side: Mapped[str] = mapped_column(String(10))
    method: Mapped[str] = mapped_column(String(10), default="otp")
    signed_at: Mapped[datetime] = mapped_column(default=utcnow)


class ProposalStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"
    SUPERSEDED = "superseded"


class TermProposal(Base):
    """A term the Scribe read from chat. It changes nothing until a person in the room accepts it."""

    __tablename__ = "term_proposals"
    __table_args__ = (Index("ix_term_proposals_room_status", "room_id", "status"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"))
    term_key: Mapped[str] = mapped_column(String(30))
    value: Mapped[Any] = mapped_column(JSONB)
    quote: Mapped[str] = mapped_column(String(300), default="")
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("chat_messages.id", ondelete="SET NULL"))
    confidence_pct: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(12), default=ProposalStatus.PENDING)
    decided_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    decided_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class CopilotProfile(Base):
    """Private to one person in one room. Never shown to, or used for, anyone else."""

    __tablename__ = "copilot_profiles"
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    walk_away_paise: Mapped[int | None] = mapped_column(BigInteger)
    target_paise: Mapped[int | None] = mapped_column(BigInteger)
    notes: Mapped[str | None] = mapped_column(String(1000))
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class CopilotSuggestion(Base):
    __tablename__ = "copilot_suggestions"
    __table_args__ = (Index("ix_copilot_suggestions_user", "user_id", "created_at"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    trigger_message_id: Mapped[str | None] = mapped_column(String(64))
    options: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    chosen_index: Mapped[int | None]
    outcome: Mapped[str | None] = mapped_column(String(12))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PaymentStatus(StrEnum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"


class PaymentLink(Base):
    __tablename__ = "payment_links"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), index=True)
    term_sheet_id: Mapped[str] = mapped_column(ForeignKey("term_sheets.id"))
    term_version: Mapped[int]
    purpose: Mapped[str] = mapped_column(String(20), default="advance")
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(10), default=PaymentStatus.PENDING)
    provider: Mapped[str] = mapped_column(String(20), default="simulated")
    provider_ref: Mapped[str] = mapped_column(String(64), unique=True)
    public_token: Mapped[str] = mapped_column(String(64), unique=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    paid_at: Mapped[datetime | None]


class Payee(Base):
    """Someone the team pays. Bank and UPI details are encrypted; only masked forms leave the server."""

    __tablename__ = "payees"
    __table_args__ = (UniqueConstraint("org_id", "gstin"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    gstin: Mapped[str | None] = mapped_column(String(15))
    method: Mapped[str] = mapped_column(String(10))
    vpa_enc: Mapped[str | None] = mapped_column(String(400))
    account_enc: Mapped[str | None] = mapped_column(String(200))
    ifsc: Mapped[str | None] = mapped_column(String(11))
    masked: Mapped[str] = mapped_column(String(40))
    details_fingerprint: Mapped[str] = mapped_column(String(64))
    details_changed_at: Mapped[datetime | None]
    details_changed_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    bank_history: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    contact_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class CardState(StrEnum):
    UNVERIFIED = "unverified"
    PENDING = "pending_approval"
    APPROVED = "approved"
    PAID = "paid"
    REJECTED = "rejected"
    DISMISSED = "dismissed"


class PaymentCard(Base):
    __tablename__ = "payment_cards"
    __table_args__ = (Index("ix_payment_cards_org_state", "org_id", "state"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    number: Mapped[int] = mapped_column(BigInteger, default=0)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), index=True)
    payee_id: Mapped[str | None] = mapped_column(ForeignKey("payees.id"))
    payee_name_claimed: Mapped[str | None] = mapped_column(String(80))
    deal_room_id: Mapped[str | None] = mapped_column(ForeignKey("rooms.id"))
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(300))
    request_text: Mapped[str | None] = mapped_column(String(1000))
    state: Mapped[str] = mapped_column(String(20))
    source: Mapped[str] = mapped_column(String(20))
    source_message_id: Mapped[str | None] = mapped_column(ForeignKey("chat_messages.id", ondelete="SET NULL"))
    requester_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    version: Mapped[int] = mapped_column(default=1)
    risk_score: Mapped[int] = mapped_column(default=0)
    risk_level: Mapped[str] = mapped_column(String(10), default="low")
    required_approvals: Mapped[int] = mapped_column(default=1)
    owner_approval_required: Mapped[bool] = mapped_column(default=False)
    step_up_required: Mapped[bool] = mapped_column(default=False)
    step_up_hash: Mapped[str | None] = mapped_column(String(64))
    step_up_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    decided_at: Mapped[datetime | None]


class CardApproval(Base):
    __tablename__ = "card_approvals"
    __table_args__ = (UniqueConstraint("card_id", "card_version", "approver_id"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    card_id: Mapped[str] = mapped_column(ForeignKey("payment_cards.id", ondelete="CASCADE"), index=True)
    card_version: Mapped[int]
    approver_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(20))
    override_reason: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class RiskSignal(Base):
    __tablename__ = "risk_signals"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    card_id: Mapped[str] = mapped_column(ForeignKey("payment_cards.id", ondelete="CASCADE"), index=True)
    card_version: Mapped[int]
    rule_id: Mapped[str] = mapped_column(String(40))
    weight: Mapped[int]
    message: Mapped[str] = mapped_column(String(300))
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Payout(Base):
    __tablename__ = "payouts"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    card_id: Mapped[str] = mapped_column(ForeignKey("payment_cards.id"), unique=True)
    idempotency_key: Mapped[str] = mapped_column(String(64), unique=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    amount_paise: Mapped[int] = mapped_column(BigInteger)
    provider: Mapped[str] = mapped_column(String(20))
    provider_ref: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(10))
    initiated_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class OrgControl(Base):
    """Kill switch. Anyone on the team can freeze; unfreezing takes two different approvers."""

    __tablename__ = "org_controls"
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), primary_key=True)
    frozen: Mapped[bool] = mapped_column(default=False)
    frozen_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    frozen_at: Mapped[datetime | None]
    reason: Mapped[str | None] = mapped_column(String(300))
    unfreeze_votes: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, default=list)
    card_seq: Mapped[int] = mapped_column(BigInteger, default=0)


EMBEDDING_DIMENSIONS = 1536


class MemoryKind(StrEnum):
    PLAYBOOK = "playbook"
    CORRECTION = "correction"
    COUNTERPARTY = "counterparty"
    PREFERENCE = "preference"
    PAYEE = "payee"
    RISK_OVERRIDE = "risk_override"


class MemoryVisibility(StrEnum):
    TEAM = "team"
    PRIVATE = "private"


class MemoryItem(Base):
    """Something the workspace learned. Scoped to one org; private items are visible only to their owner.
    Every item points back to the interaction it came from."""

    __tablename__ = "memory_items"
    __table_args__ = (
        Index("ix_memory_org_kind", "org_id", "kind", "created_at"),
        Index("ix_memory_org_subject", "org_id", "subject_type", "subject_id"),
    )
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))
    visibility: Mapped[str] = mapped_column(String(10))
    owner_user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(String(20))
    subject_type: Mapped[str | None] = mapped_column(String(20))
    subject_id: Mapped[str | None] = mapped_column(String(64))
    text: Mapped[str] = mapped_column(String(1000))
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIMENSIONS))
    source_type: Mapped[str] = mapped_column(String(30))
    source_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class DealOutcomeKind(StrEnum):
    WON = "won"
    LOST = "lost"


class DealOutcome(Base):
    __tablename__ = "deal_outcomes"
    room_id: Mapped[str] = mapped_column(ForeignKey("rooms.id", ondelete="CASCADE"), primary_key=True)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"), index=True)
    outcome: Mapped[str] = mapped_column(String(10))
    first_ask_paise: Mapped[int | None] = mapped_column(BigInteger)
    final_paise: Mapped[int | None] = mapped_column(BigInteger)
    note: Mapped[str | None] = mapped_column(String(300))
    recorded_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PolicyOverride(Base):
    """A change to a risk threshold. Learning may only tighten; loosening needs an owner and a reason."""

    __tablename__ = "policy_overrides"
    __table_args__ = (Index("ix_policy_overrides_org_key", "org_id", "key", "created_at"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    org_id: Mapped[str] = mapped_column(ForeignKey("orgs.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(40))
    value: Mapped[float] = mapped_column(Float)
    previous: Mapped[float] = mapped_column(Float)
    direction: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(10))
    reason: Mapped[str] = mapped_column(String(300))
    set_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    source_id: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
