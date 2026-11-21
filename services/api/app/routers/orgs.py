from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db import get_db, utcnow
from app.deps import get_nexconn
from app.domain.rooms import NexconnError, add_member, create_room, post_system_card
from app.integrations.nexconn import NexconnClient
from app.models import (
    GUEST_ROLES,
    TEAM_ROLES,
    Invite,
    Membership,
    Org,
    Role,
    Room,
    RoomMember,
    RoomSide,
    RoomType,
    User,
)
from app.security import audit
from app.security.deps import current_user, membership_for, rate_limit, require_org_role, room_access
from app.security.otp import OtpError, normalize_phone

router = APIRouter(prefix="/api", tags=["orgs"])

INVITE_TTL = timedelta(hours=72)


class OrgCreate(BaseModel):
    name: str = Field(min_length=2, max_length=120)


class OrgOut(BaseModel):
    id: str
    name: str
    role: str


class MemberOut(BaseModel):
    user_id: str
    name: str
    role: str
    phone_last4: str
    verified: bool


class TeammateAdd(BaseModel):
    phone: str = Field(min_length=6, max_length=20)
    name: str = Field(min_length=1, max_length=80)
    role: Role


class RoomCreate(BaseModel):
    type: RoomType
    title: str = Field(min_length=2, max_length=120)


class RoomMemberOut(BaseModel):
    user_id: str
    name: str
    side: str
    role: str | None
    phone_last4: str
    verified: bool


class RoomOut(BaseModel):
    id: str
    org_id: str
    type: str
    title: str
    channel_id: str
    created_at: datetime
    my_side: str | None
    members: list[RoomMemberOut] = []


class InviteCreate(BaseModel):
    role: Role


class InviteOut(BaseModel):
    url: str
    expires_at: datetime


class InvitePreview(BaseModel):
    org_name: str
    room_title: str
    room_type: str
    role: str
    expires_at: datetime


class ChatTokenOut(BaseModel):
    token: str
    app_key: str
    area: str
    chat_user_id: str


def _member_out(m: Membership) -> MemberOut:
    return MemberOut(user_id=m.user_id, name=m.user.name, role=m.role, phone_last4=m.user.phone_last4,
                     verified=m.user.phone_verified_at is not None)


def _room_out(db: Session, room: Room, viewer_id: str, with_members: bool = False) -> RoomOut:
    mine = db.get(RoomMember, (room.id, viewer_id))
    out = RoomOut(id=room.id, org_id=room.org_id, type=room.type, title=room.title,
                  channel_id=room.nexconn_channel_id, created_at=room.created_at, my_side=mine.side if mine else None)
    if with_members:
        roles = {m.user_id: m.role for m in db.execute(
            select(Membership).where(Membership.org_id == room.org_id)).scalars()}
        rms = db.execute(select(RoomMember).where(RoomMember.room_id == room.id).order_by(RoomMember.joined_at))
        out.members = [RoomMemberOut(user_id=rm.user_id, name=rm.user.name, side=rm.side, role=roles.get(rm.user_id),
                                     phone_last4=rm.user.phone_last4,
                                     verified=rm.user.phone_verified_at is not None) for rm in rms.scalars()]
    return out


def _invite_hash(settings: Settings, token: str) -> str:
    return hmac.new(settings.guest_link_secret.encode(), token.encode(), hashlib.sha256).hexdigest()


def _chat_unavailable(exc: NexconnError) -> HTTPException:
    return HTTPException(status.HTTP_502_BAD_GATEWAY, "Chat service is unavailable. Try again")


# ---------- orgs ----------

@router.post("/orgs", response_model=OrgOut, status_code=201)
def create_org(body: OrgCreate, user: User = Depends(current_user), db: Session = Depends(get_db)) -> OrgOut:
    org = Org(name=body.name.strip())
    db.add(org)
    db.flush()
    db.add(Membership(org_id=org.id, user_id=user.id, role=Role.OWNER))
    audit.record(db, "org.create", actor_user_id=user.id, org_id=org.id, subject_type="org", subject_id=org.id,
                 payload={"name": org.name})
    db.commit()
    return OrgOut(id=org.id, name=org.name, role=Role.OWNER)


@router.get("/orgs/{org_id}/members", response_model=list[MemberOut])
def list_members(org_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[MemberOut]:
    require_org_role(db, user, org_id, TEAM_ROLES)
    ms = db.execute(select(Membership).where(Membership.org_id == org_id).order_by(Membership.created_at)).scalars()
    return [_member_out(m) for m in ms]


@router.post("/orgs/{org_id}/members", response_model=MemberOut, status_code=201)
def add_teammate(org_id: str, body: TeammateAdd, user: User = Depends(current_user),
                 db: Session = Depends(get_db)) -> MemberOut:
    require_org_role(db, user, org_id, {Role.OWNER})
    if body.role not in TEAM_ROLES or body.role is Role.OWNER:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Teammates can be finance or member")
    try:
        phone = normalize_phone(body.phone)
    except OtpError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    mate = db.execute(select(User).where(User.phone_e164 == phone)).scalar_one_or_none()
    if mate is None:
        mate = User(phone_e164=phone, name=body.name.strip())
        db.add(mate)
        db.flush()
    if membership_for(db, mate.id, org_id):
        raise HTTPException(status.HTTP_409_CONFLICT, "Already a member")
    m = Membership(org_id=org_id, user_id=mate.id, role=body.role)
    db.add(m)
    audit.record(db, "member.add", actor_user_id=user.id, org_id=org_id, subject_type="user", subject_id=mate.id,
                 payload={"role": body.role.value})
    db.commit()
    db.refresh(m)
    return _member_out(m)


# ---------- rooms ----------

@router.get("/orgs/{org_id}/rooms", response_model=list[RoomOut])
def list_rooms(org_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> list[RoomOut]:
    m = membership_for(db, user.id, org_id)
    if m is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    q = select(Room).where(Room.org_id == org_id).order_by(Room.created_at.desc())
    if m.role not in TEAM_ROLES:
        q = q.join(RoomMember, (RoomMember.room_id == Room.id) & (RoomMember.user_id == user.id))
    return [_room_out(db, r, user.id) for r in db.execute(q).scalars()]


@router.post("/orgs/{org_id}/rooms", response_model=RoomOut, status_code=201)
def new_room(org_id: str, body: RoomCreate, user: User = Depends(current_user), db: Session = Depends(get_db),
             settings: Settings = Depends(get_settings), nexconn: NexconnClient = Depends(get_nexconn)) -> RoomOut:
    require_org_role(db, user, org_id, TEAM_ROLES)
    try:
        room = create_room(db, nexconn, settings, org_id=org_id, room_type=body.type, title=body.title.strip(),
                           creator=user)
    except NexconnError as exc:
        db.rollback()
        raise _chat_unavailable(exc) from exc
    db.commit()
    kind = "Deal Room" if body.type is RoomType.DEAL else "Pay Lane"
    post_system_card(nexconn, settings, room, {"kind": "system", "v": 1, "title": f"{kind} created by {user.name}"})
    return _room_out(db, room, user.id, with_members=True)


@router.get("/rooms/{room_id}", response_model=RoomOut)
def get_room(room_id: str, user: User = Depends(current_user), db: Session = Depends(get_db)) -> RoomOut:
    room, _, _ = room_access(db, user, room_id)
    return _room_out(db, room, user.id, with_members=True)


@router.post("/rooms/{room_id}/join", response_model=RoomOut)
def join_room(room_id: str, user: User = Depends(current_user), db: Session = Depends(get_db),
              nexconn: NexconnClient = Depends(get_nexconn)) -> RoomOut:
    """Team members open any org room; joining adds them to its Nexconn group."""
    room, m, rm = room_access(db, user, room_id)
    if rm is None:
        side = RoomSide.TEAM if room.type == RoomType.PAYLANE else RoomSide.PROVIDER
        try:
            add_member(db, nexconn, room, user, side)
        except NexconnError as exc:
            db.rollback()
            raise _chat_unavailable(exc) from exc
        audit.record(db, "room.join", actor_user_id=user.id, org_id=room.org_id, subject_type="room",
                     subject_id=room.id, payload={"side": side.value, "role": m.role})
        db.commit()
    return _room_out(db, room, user.id, with_members=True)


# ---------- invites ----------

@router.post("/rooms/{room_id}/invites", response_model=InviteOut, status_code=201)
def create_invite(room_id: str, body: InviteCreate, user: User = Depends(current_user),
                  db: Session = Depends(get_db), settings: Settings = Depends(get_settings)) -> InviteOut:
    room, m, _ = room_access(db, user, room_id)
    if m.role not in TEAM_ROLES:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only your team can invite people")
    allowed = Role.CLIENT_GUEST if room.type == RoomType.DEAL else Role.VENDOR_GUEST
    if body.role is not allowed:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"This room only accepts {allowed.value} invites")
    token = secrets.token_urlsafe(24)
    inv = Invite(org_id=room.org_id, room_id=room.id, role=body.role, token_hash=_invite_hash(settings, token),
                 created_by=user.id, expires_at=utcnow() + INVITE_TTL)
    db.add(inv)
    audit.record(db, "invite.create", actor_user_id=user.id, org_id=room.org_id, subject_type="room",
                 subject_id=room.id, payload={"role": body.role.value, "invite_id": inv.id})
    db.commit()
    return InviteOut(url=f"{settings.web_base_url.rstrip('/')}/join/{token}", expires_at=inv.expires_at)


def _open_invite(db: Session, settings: Settings, token: str, lock: bool = False) -> Invite:
    q = select(Invite).where(Invite.token_hash == _invite_hash(settings, token))
    inv = db.execute(q.with_for_update() if lock else q).scalar_one_or_none()
    if inv is None or inv.used_at is not None or inv.expires_at < utcnow():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "This invite link is invalid, used or expired")
    return inv


@router.get("/invites/{token}", response_model=InvitePreview, dependencies=[Depends(rate_limit("invite", 30, 60))])
def preview_invite(token: str, db: Session = Depends(get_db),
                   settings: Settings = Depends(get_settings)) -> InvitePreview:
    inv = _open_invite(db, settings, token)
    room = db.get(Room, inv.room_id)
    org = db.get(Org, inv.org_id)
    assert room is not None and org is not None
    return InvitePreview(org_name=org.name, room_title=room.title, room_type=room.type, role=inv.role,
                         expires_at=inv.expires_at)


@router.post("/invites/{token}/accept", response_model=RoomOut,
             dependencies=[Depends(rate_limit("invite", 30, 60))])
def accept_invite(token: str, user: User = Depends(current_user), db: Session = Depends(get_db),
                  settings: Settings = Depends(get_settings), nexconn: NexconnClient = Depends(get_nexconn)) -> RoomOut:
    inv = _open_invite(db, settings, token, lock=True)
    room = db.get(Room, inv.room_id)
    assert room is not None
    m = membership_for(db, user.id, inv.org_id)
    if m is None:
        m = Membership(org_id=inv.org_id, user_id=user.id, role=inv.role)
        db.add(m)
    elif m.role not in GUEST_ROLES:
        raise HTTPException(status.HTTP_409_CONFLICT, "You are already on this team; open the room from your list")
    side = RoomSide.CLIENT if inv.role == Role.CLIENT_GUEST else RoomSide.VENDOR
    try:
        add_member(db, nexconn, room, user, side)
    except NexconnError as exc:
        db.rollback()
        raise _chat_unavailable(exc) from exc
    inv.used_by, inv.used_at = user.id, utcnow()
    audit.record(db, "invite.accept", actor_user_id=user.id, org_id=inv.org_id, subject_type="room",
                 subject_id=room.id, payload={"invite_id": inv.id, "role": inv.role, "side": side.value})
    db.commit()
    label = "client" if side is RoomSide.CLIENT else "vendor"
    title = f"{user.name} joined as {label} (verified phone ••{user.phone_last4})"
    post_system_card(nexconn, settings, room, {"kind": "system", "v": 1, "title": title})
    return _room_out(db, room, user.id, with_members=True)


# ---------- chat ----------

@router.post("/chat/token", response_model=ChatTokenOut, dependencies=[Depends(rate_limit("chat-token", 30, 60))])
def chat_token(user: User = Depends(current_user), settings: Settings = Depends(get_settings),
               nexconn: NexconnClient = Depends(get_nexconn)) -> ChatTokenOut:
    try:
        token = nexconn.issue_token(user.chat_id, user.name)
    except NexconnError as exc:
        raise _chat_unavailable(exc) from exc
    return ChatTokenOut(token=token, app_key=settings.nexconn_app_key, area=settings.nexconn_area.upper(),
                        chat_user_id=user.chat_id)
