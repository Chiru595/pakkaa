from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Index, String, Text, UniqueConstraint
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
    seq: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
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
