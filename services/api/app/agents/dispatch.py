"""Work that runs after a room message is stored: the Scribe for deal rooms, the money-talk detector for Pay Lanes."""
from __future__ import annotations

import logging

from app.config import get_settings
from app.db import session_factory
from app.integrations.nexconn import NexconnClient
from app.llm import LLM
from app.models import Room, RoomType

log = logging.getLogger(__name__)


def on_new_message(room_id: str, message_id: str, nexconn: NexconnClient, llm: LLM) -> None:
    settings = get_settings()
    with session_factory()() as db:
        room = db.get(Room, room_id)
        if room is None:
            return
        try:
            if room.type == RoomType.DEAL:
                from app.agents import scribe
                from app.agents.money_talk import ignore_commands

                ignore_commands(db, nexconn, settings, room_id, message_id)
                scribe.run(db, llm, nexconn, settings, room_id, message_id)
            elif room.type == RoomType.PAYLANE:
                from app.agents import money_talk

                money_talk.run(db, llm, nexconn, settings, room_id, message_id)
        except Exception:
            db.rollback()
            log.exception("post-message agent failed", extra={"room_id": room_id})
