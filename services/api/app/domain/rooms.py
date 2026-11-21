"""Room lifecycle on Nexconn: our DB is the source of truth, Nexconn mirrors membership for chat delivery."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import new_id
from app.integrations.nexconn import TEXT_MESSAGE_TYPE, NexconnClient, NexconnError
from app.models import (
    TEAM_ROLES,
    ChatMessage,
    Membership,
    MessageKind,
    Room,
    RoomMember,
    RoomSide,
    RoomType,
    User,
)
from app.security import audit

log = logging.getLogger(__name__)


def ensure_bot(nexconn: NexconnClient, settings: Settings) -> None:
    nexconn.issue_token(settings.nexconn_bot_user_id, settings.nexconn_bot_name)


def create_room(db: Session, nexconn: NexconnClient, settings: Settings, *, org_id: str, room_type: RoomType,
                title: str, creator: User) -> Room:
    room_id = new_id()
    room = Room(id=room_id, org_id=org_id, type=room_type, title=title, created_by=creator.id,
                nexconn_channel_id=f"r{room_id}")
    db.add(room)
    db.flush()
    members: list[tuple[User, RoomSide]] = []
    if room_type is RoomType.PAYLANE:
        team = db.execute(select(Membership).where(Membership.org_id == org_id,
                                                   Membership.role.in_([r.value for r in TEAM_ROLES]))).scalars()
        members = [(m.user, RoomSide.TEAM) for m in team]
    else:
        members = [(creator, RoomSide.PROVIDER)]
    for user, side in members:
        db.add(RoomMember(room_id=room.id, user_id=user.id, side=side))
    ensure_bot(nexconn, settings)
    nexconn.create_group(room.nexconn_channel_id, title, settings.nexconn_bot_user_id,
                         [u.chat_id for u, _ in members])
    audit.record(db, "room.create", actor_user_id=creator.id, org_id=org_id, subject_type="room",
                 subject_id=room.id, payload={"type": room_type.value, "title": title,
                                              "members": [u.id for u, _ in members]})
    return room


def add_member(db: Session, nexconn: NexconnClient, room: Room, user: User, side: RoomSide) -> RoomMember:
    rm = db.get(RoomMember, (room.id, user.id))
    if rm is None:
        rm = RoomMember(room_id=room.id, user_id=user.id, side=side)
        db.add(rm)
    nexconn.join_group(room.nexconn_channel_id, [user.chat_id])
    return rm


def post_card(db: Session, nexconn: NexconnClient, settings: Settings, room: Room, card: dict[str, Any],
              to_user_ids: list[str] | None = None) -> ChatMessage | None:
    """Posts a bot card and records it. Best effort: a failed notice never rolls back the change it describes."""
    try:
        message_id = nexconn.send_card(settings.nexconn_bot_user_id, room.nexconn_channel_id, card,
                                       to_user_ids=to_user_ids)
    except NexconnError:
        log.exception("could not post card", extra={"room_id": room.id})
        return None
    if to_user_ids:
        return None
    msg = ChatMessage(room_id=room.id, nexconn_message_id=message_id, sender_user_id=None,
                      sender_chat_id=settings.nexconn_bot_user_id, kind=MessageKind.CARD,
                      body=str(card.get("title") or ""), card=card)
    db.add(msg)
    db.commit()
    return msg


def send_text(db: Session, nexconn: NexconnClient, room: Room, sender: User, text: str) -> ChatMessage:
    message_id = nexconn.send_group_message(sender.chat_id, room.nexconn_channel_id, TEXT_MESSAGE_TYPE,
                                            {"content": text}, push_content=f"{sender.name}: {text[:60]}",
                                            echo_to_sender=True)
    msg = ChatMessage(room_id=room.id, nexconn_message_id=message_id, sender_user_id=sender.id,
                      sender_chat_id=sender.chat_id, kind=MessageKind.TEXT, body=text)
    db.add(msg)
    db.commit()
    return msg


__all__ = ["NexconnError", "add_member", "create_room", "post_card", "send_text"]
