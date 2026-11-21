"""Writing and reading memory items.

Rules enforced here, not in prompts:
- every query is filtered by org_id;
- private items are returned only to their owner;
- team items are returned only to team members (owner, finance, member), never to guests;
- every item records the interaction it came from (source_type, source_id).
Embeddings are best effort: if the embeddings API is unavailable the item is stored without one and recall falls
back to recency and keyword overlap.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.llm import LLM, LLMUnavailable
from app.models import TEAM_ROLES, CopilotSuggestion, MemoryItem, MemoryKind, MemoryVisibility
from app.security import audit

log = logging.getLogger(__name__)

MAX_TEXT = 1000
_WORD = re.compile(r"[a-z0-9ऀ-ॿ]{3,}", re.I)


def _embed(llm: LLM | None, text: str) -> list[float] | None:
    if llm is None or not llm.available:
        return None
    try:
        return llm.embed([text])[0]
    except (LLMUnavailable, IndexError) as exc:
        log.info("memory stored without embedding: %s", exc)
        return None


def remember(db: Session, llm: LLM | None, *, org_id: str, kind: MemoryKind, text: str, source_type: str,
             source_id: str, visibility: MemoryVisibility = MemoryVisibility.TEAM, owner_user_id: str | None = None,
             subject_type: str | None = None, subject_id: str | None = None, data: dict[str, Any] | None = None,
             actor_user_id: str | None = None) -> MemoryItem:
    """Adds one memory item in the caller's transaction and audits it. The caller commits."""
    if visibility == MemoryVisibility.PRIVATE and not owner_user_id:
        raise ValueError("private memory needs an owner")
    text = " ".join(text.split())[:MAX_TEXT]
    item = MemoryItem(org_id=org_id, visibility=visibility, owner_user_id=owner_user_id, kind=kind,
                      subject_type=subject_type, subject_id=subject_id, text=text, data=data or {},
                      embedding=_embed(llm, text), source_type=source_type, source_id=source_id)
    db.add(item)
    db.flush()
    audit.record(db, "memory.add", actor_user_id=actor_user_id, org_id=org_id, subject_type="memory_item",
                 subject_id=item.id, payload={"kind": kind.value, "visibility": visibility.value,
                                              "source": f"{source_type}:{source_id}", "embedded": item.embedding
                                              is not None})
    return item


def _visible(org_id: str, viewer_id: str, viewer_role: str):
    q = select(MemoryItem).where(MemoryItem.org_id == org_id)
    if viewer_role in TEAM_ROLES:
        return q.where((MemoryItem.visibility == MemoryVisibility.TEAM)
                       | ((MemoryItem.visibility == MemoryVisibility.PRIVATE)
                          & (MemoryItem.owner_user_id == viewer_id)))
    return q.where(MemoryItem.visibility == MemoryVisibility.PRIVATE, MemoryItem.owner_user_id == viewer_id)


def recall(db: Session, llm: LLM | None, *, org_id: str, viewer_id: str, viewer_role: str, query: str,
           kinds: Iterable[MemoryKind], limit: int = 3, exclude_source_ids: Iterable[str] = ()) -> list[MemoryItem]:
    """The most relevant items this viewer may see: vector similarity when available, else keyword overlap."""
    q = _visible(org_id, viewer_id, viewer_role).where(MemoryItem.kind.in_([k.value for k in kinds]))
    excluded = [s for s in exclude_source_ids if s]
    if excluded:
        q = q.where(MemoryItem.source_id.not_in(excluded))
    vector = _embed(llm, query) if query.strip() else None
    if vector is not None:
        ranked = db.execute(q.where(MemoryItem.embedding.is_not(None))
                            .order_by(MemoryItem.embedding.cosine_distance(vector)).limit(limit)).scalars().all()
        if ranked:
            return list(ranked)
    candidates = db.execute(q.order_by(MemoryItem.created_at.desc()).limit(50)).scalars().all()
    words = {w.lower() for w in _WORD.findall(query)}
    scored = sorted(candidates, key=lambda m: (-len(words & {w.lower() for w in _WORD.findall(m.text)}),
                                               -m.created_at.timestamp()))
    return scored[:limit]


def counterparty_items(db: Session, org_id: str, subject_ids: Iterable[str], limit: int = 5) -> list[MemoryItem]:
    """Team-only facts about people the team deals with. Never shown to those people."""
    ids = [s for s in subject_ids if s]
    if not ids:
        return []
    return list(db.execute(select(MemoryItem).where(
        MemoryItem.org_id == org_id, MemoryItem.visibility == MemoryVisibility.TEAM,
        MemoryItem.kind == MemoryKind.COUNTERPARTY, MemoryItem.subject_type == "user",
        MemoryItem.subject_id.in_(ids)).order_by(MemoryItem.created_at.desc()).limit(limit)).scalars())


def scribe_corrections(db: Session, org_id: str, limit: int = 5) -> list[MemoryItem]:
    return list(db.execute(select(MemoryItem).where(
        MemoryItem.org_id == org_id, MemoryItem.kind == MemoryKind.CORRECTION,
        MemoryItem.visibility == MemoryVisibility.TEAM).order_by(MemoryItem.created_at.desc()).limit(limit)).scalars())


def tactic_preferences(db: Session, user_id: str, limit: int = 200) -> list[str]:
    """Tactics this person sends or edits most, most used first. Ignored suggestions count against a tactic."""
    rows = db.execute(select(CopilotSuggestion).where(CopilotSuggestion.user_id == user_id,
                                                      CopilotSuggestion.outcome.is_not(None))
                      .order_by(CopilotSuggestion.created_at.desc()).limit(limit)).scalars().all()
    score: Counter[str] = Counter()
    for s in rows:
        if s.outcome in ("sent", "edited") and s.chosen_index is not None and s.chosen_index < len(s.options):
            score[s.options[s.chosen_index].get("tactic", "")] += 2 if s.outcome == "sent" else 1
        elif s.outcome == "ignored":
            for opt in s.options:
                score[opt.get("tactic", "")] -= 0.5
    return [t for t, v in score.most_common() if t and v > 0]
