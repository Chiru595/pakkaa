from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.dispatch import on_new_message
from app.db import get_db
from app.deps import get_llm_dep, get_nexconn
from app.domain.rooms import NexconnError, send_text
from app.integrations.nexconn import NexconnClient
from app.llm import LLM
from app.models import ChatMessage, User
from app.schemas import MessageText
from app.security.deps import current_user, rate_limit, room_access

router = APIRouter(prefix="/api/rooms", tags=["messages"])


class MessageIn(BaseModel):
    text: MessageText


class MessageOut(BaseModel):
    id: str
    sender_chat_id: str
    kind: str
    text: str
    card: dict[str, Any] | None
    sent_time: int


def message_out(m: ChatMessage) -> MessageOut:
    return MessageOut(id=m.nexconn_message_id or m.id, sender_chat_id=m.sender_chat_id, kind=m.kind, text=m.body,
                      card=m.card, sent_time=int(m.created_at.timestamp() * 1000))


@router.get("/{room_id}/messages", response_model=list[MessageOut])
def list_messages(room_id: str, before: int | None = Query(default=None, description="Unix ms"),
                  limit: int = Query(default=50, ge=1, le=100), user: User = Depends(current_user),
                  db: Session = Depends(get_db)) -> list[MessageOut]:
    room, _, _ = room_access(db, user, room_id)
    q = select(ChatMessage).where(ChatMessage.room_id == room.id)
    if before is not None:
        q = q.where(ChatMessage.created_at < datetime.fromtimestamp(before / 1000).astimezone())
    rows = db.execute(q.order_by(ChatMessage.created_at.desc()).limit(limit)).scalars().all()
    return [message_out(m) for m in reversed(rows)]


@router.post("/{room_id}/messages", response_model=MessageOut, status_code=201,
             dependencies=[Depends(rate_limit("send", 120, 60))])
def post_message(room_id: str, body: MessageIn, background: BackgroundTasks, user: User = Depends(current_user),
                 db: Session = Depends(get_db), nexconn: NexconnClient = Depends(get_nexconn),
                 llm: LLM = Depends(get_llm_dep)) -> MessageOut:
    room, _, member = room_access(db, user, room_id)
    if member is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Join the room before posting")
    try:
        msg = send_text(db, nexconn, room, user, body.text)
    except NexconnError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Message not delivered. Try again") from exc
    background.add_task(on_new_message, room.id, msg.id, nexconn, llm)
    return message_out(msg)
