from __future__ import annotations

import hashlib
import json
import logging
from collections.abc import Iterator
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents import copilot as C
from app.db import get_db, session_factory
from app.deps import get_llm_dep
from app.domain.deal import require_deal_room, require_participant
from app.llm import LLM, Lane, LLMUnavailable, untrusted
from app.models import CopilotSuggestion, Room, RoomMember, User
from app.schemas import ClientRef, MessageText, bounded_text
from app.security import audit
from app.security.deps import current_user, membership_for, rate_limit, room_access

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/rooms", tags=["copilot"])


class SuggestIn(BaseModel):
    trigger_message_id: ClientRef | None = None


class FeedbackIn(BaseModel):
    chosen_index: int | None = Field(default=None, ge=0, le=2)
    outcome: Literal["sent", "edited", "ignored"]


class CheckIn(BaseModel):
    draft: MessageText


class WarningOut(BaseModel):
    code: str
    level: str
    message: str


class ExplainIn(BaseModel):
    text: bounded_text(3, 1500, multiline=True)  # type: ignore[valid-type]
    language: Literal["en", "hi", "hinglish"] = "en"


class ExplainOut(C.ClauseExplanation):
    language: str
    disclaimer: str = "General information, not legal advice."


COPILOT_SIDE_ONLY = "The negotiation copilot is for the freelancer's side of the deal"


def _participant(db: Session, user: User, room_id: str) -> tuple[Room, RoomMember]:
    room, _, member = room_access(db, user, room_id)
    require_deal_room(room)
    return room, require_participant(member)


def _provider(db: Session, user: User, room_id: str) -> tuple[Room, RoomMember]:
    """Reply suggestions and private limits are for the freelancer's side; clients get the contract and payment."""
    room, member = _participant(db, user, room_id)
    if member.side != "provider":
        raise HTTPException(status.HTTP_403_FORBIDDEN, COPILOT_SIDE_ONLY)
    return room, member


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


@router.post("/{room_id}/copilot/suggest", dependencies=[Depends(rate_limit("copilot", 40, 60))])
def suggest(room_id: str, body: SuggestIn, user: User = Depends(current_user), db: Session = Depends(get_db),
            llm: LLM = Depends(get_llm_dep)) -> StreamingResponse:
    """Streams up to three reply options as server-sent events, private to the caller."""
    room, member = _provider(db, user, room_id)
    role = getattr(membership_for(db, user.id, room.org_id), "role", None)
    data = C.build_context(db, room, user, member, llm, role).render()
    db.commit()
    user_id, room_pk = user.id, room.id

    def events() -> Iterator[str]:
        options: list[dict] = []
        try:
            for opt in llm.stream_items(Lane.LIVE, C.TASK, data, C.ReplyOption, max_items=3, purpose="copilot"):
                item = {**opt.model_dump(), "label": C.TACTIC_LABELS[opt.tactic]}
                options.append(item)
                yield _sse("option", item)
        except LLMUnavailable as exc:
            yield _sse("error", {"message": "AI copilot is offline right now. You can still chat and agree terms."})
            log.info("copilot unavailable: %s", exc)
        if not options:
            return
        with session_factory()() as s:
            row = CopilotSuggestion(room_id=room_pk, user_id=user_id, trigger_message_id=body.trigger_message_id,
                                    options=options)
            s.add(row)
            s.commit()
            yield _sse("done", {"suggestion_id": row.id})

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


@router.post("/{room_id}/copilot/suggestions/{suggestion_id}/feedback", status_code=204,
             dependencies=[Depends(rate_limit("feedback", 120, 60))])
def feedback(room_id: str, suggestion_id: str, body: FeedbackIn, user: User = Depends(current_user),
             db: Session = Depends(get_db)) -> None:
    room, _ = _provider(db, user, room_id)
    s = db.get(CopilotSuggestion, suggestion_id)
    if s is None or s.room_id != room.id or s.user_id != user.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found")
    if body.chosen_index is not None and body.chosen_index >= len(s.options):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "No such option")
    s.chosen_index, s.outcome = body.chosen_index, body.outcome
    db.commit()


@router.post("/{room_id}/copilot/check", response_model=list[WarningOut],
             dependencies=[Depends(rate_limit("presend", 120, 60))])
def presend(room_id: str, body: CheckIn, user: User = Depends(current_user),
            db: Session = Depends(get_db)) -> list[WarningOut]:
    room, member = _participant(db, user, room_id)
    warnings = C.presend_check(db, room, user, member, body.draft)
    db.commit()
    return [WarningOut(code=w.code, level=w.level, message=w.message) for w in warnings]


_explain_cache: dict[str, C.ClauseExplanation] = {}


@router.post("/{room_id}/explain", response_model=ExplainOut, dependencies=[Depends(rate_limit("explain", 20, 60))])
def explain(room_id: str, body: ExplainIn, user: User = Depends(current_user), db: Session = Depends(get_db),
            llm: LLM = Depends(get_llm_dep)) -> ExplainOut:
    room, member = _participant(db, user, room_id)
    text = " ".join(body.text.split())
    key = hashlib.sha256(f"{body.language}:{member.side}:{text}".encode()).hexdigest()
    result = _explain_cache.get(key)
    if result is None:
        try:
            result = llm.structured(Lane.SMART, C.explain_task(body.language, member.side),
                                    untrusted("clause", text), C.ClauseExplanation, max_output_tokens=600,
                                    purpose="explain")
        except LLMUnavailable as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                "The clause explainer is offline right now. Try again in a minute") from exc
        if len(_explain_cache) > 500:
            _explain_cache.clear()
        _explain_cache[key] = result
    audit.record(db, "copilot.explain", actor_user_id=user.id, org_id=room.org_id, subject_type="room",
                 subject_id=room.id, payload={"language": body.language, "clause_sha256": key})
    db.commit()
    return ExplainOut(**result.model_dump(), language=body.language)
