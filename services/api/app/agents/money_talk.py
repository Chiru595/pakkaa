"""Money-talk detector for Pay Lanes (filled in with the Pay Lane milestone)."""
from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.nexconn import NexconnClient
from app.llm import LLM


def run(db: Session, llm: LLM, nexconn: NexconnClient, settings: Settings, room_id: str, message_id: str) -> int:
    return 0
