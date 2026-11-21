"""Ask Pakkaa: answers questions about the organisation's own records.

Facts are retrieved in code from what the asker may see (team members only). The model may only rephrase those
facts and must cite them; an answer with an amount that is not in the facts is discarded for the plain fact list.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.domain import terms as T
from app.domain.risk import IST, normalise_name
from app.domain.validators import gstin as validate_gstin
from app.llm import LLM, Lane, LLMUnavailable, untrusted
from app.models import CardState, Payee, PaymentCard, PaymentLink, Room, RoomType, TermSheet
from app.money import amounts_in_text, format_inr

GSTIN_IN_TEXT = re.compile(r"\b\d{2}[A-Za-z]{5}\d{4}[A-Za-z][1-9A-Za-z][Zz][0-9A-Za-z]\b")


@dataclass
class Fact:
    id: str
    text: str
    link: str | None = None


@dataclass
class Answer:
    text: str
    facts: list[Fact] = field(default_factory=list)
    ai_worded: bool = False


class Worded(BaseModel):
    answer: str = Field(min_length=1, max_length=600)
    cited: list[str] = Field(default_factory=list, max_length=8)


TASK = """Answer the question using only the numbered facts. Cite every fact you use like [F2]. If the facts do not
answer the question, say so plainly. Keep it under 4 sentences. Copy amounts and dates exactly as written."""


def _payee_facts(db: Session, org_id: str, payee: Payee, n: int) -> list[Fact]:
    paid = db.execute(select(PaymentCard).where(PaymentCard.org_id == org_id, PaymentCard.payee_id == payee.id,
                                                PaymentCard.state == CardState.PAID)
                      .order_by(PaymentCard.created_at.desc())).scalars().all()
    gst = f", GSTIN {payee.gstin}" if payee.gstin else ""
    if not paid:
        return [Fact(f"F{n}", f"{payee.name}{gst} is on the payee list ({payee.masked}) and has never been paid.")]
    total = sum(c.amount_paise for c in paid)
    last = paid[0]
    return [Fact(f"F{n}", f"{payee.name}{gst} was paid {len(paid)} time(s), {format_inr(total)} in total; last "
                          f"{format_inr(last.amount_paise)} on {last.created_at.astimezone(IST):%d %b %Y} (card "
                          f"#{last.number}).", f"/cards/{last.id}")]


def gather(db: Session, org_id: str, question: str) -> list[Fact]:
    facts: list[Fact] = []
    payees = db.execute(select(Payee).where(Payee.org_id == org_id)).scalars().all()
    for raw in GSTIN_IN_TEXT.findall(question):
        try:
            g = validate_gstin(raw)
        except ValueError:
            facts.append(Fact(f"F{len(facts) + 1}", f"{raw.upper()} is not a valid GSTIN (check digit fails)."))
            continue
        match = next((p for p in payees if p.gstin == g), None)
        if match is None:
            facts.append(Fact(f"F{len(facts) + 1}", f"No payee with GSTIN {g}; we have never paid that GSTIN."))
        else:
            facts += _payee_facts(db, org_id, match, len(facts) + 1)
    q = normalise_name(question)
    for p in payees:
        key = normalise_name(p.name)
        if key and key in q and not any(p.name in f.text for f in facts):
            facts += _payee_facts(db, org_id, p, len(facts) + 1)
    deals = db.execute(select(Room, TermSheet).join(TermSheet, TermSheet.room_id == Room.id, isouter=True)
                       .where(Room.org_id == org_id, Room.type == RoomType.DEAL)
                       .order_by(Room.created_at.desc()).limit(8)).all()
    for room, sheet in deals:
        v = T.values(sheet.terms) if sheet else {}
        price = T.display("price_paise", v["price_paise"]) if v.get("price_paise") else "no price yet"
        link = db.execute(select(PaymentLink).where(PaymentLink.room_id == room.id)
                          .order_by(PaymentLink.created_at.desc()).limit(1)).scalar_one_or_none()
        adv = f"; advance {format_inr(link.amount_paise)} {link.status}" if link else ""
        status = sheet.status if sheet else "draft"
        facts.append(Fact(f"F{len(facts) + 1}", f"Deal “{room.title}”: {price}, term sheet {status}{adv}.",
                          f"/rooms/{room.id}"))
    pending = db.execute(select(func.count(), func.coalesce(func.sum(PaymentCard.amount_paise), 0)).where(
        PaymentCard.org_id == org_id, PaymentCard.state.in_([CardState.PENDING, CardState.APPROVED]))).one()
    facts.append(Fact(f"F{len(facts) + 1}", f"{pending[0]} payment card(s) waiting to be approved or paid, "
                                            f"{format_inr(int(pending[1]))} in total."))
    return facts[:20]


def answer(db: Session, llm: LLM, org_id: str, question: str) -> Answer:
    facts = gather(db, org_id, question)
    plain = Answer(" ".join(f"[{f.id}] {f.text}" for f in facts[:4]), facts)
    if not llm.available:
        return plain
    data = "\n".join(f"[{f.id}] {f.text}" for f in facts) + "\n\nQuestion:\n" + untrusted("question", question)
    try:
        out = llm.structured(Lane.SMART, TASK, data, Worded, max_output_tokens=400, purpose="ask")
    except LLMUnavailable:
        return plain
    known = {a for f in facts for a in amounts_in_text(f.text, 1)}
    ids = {f.id for f in facts}
    cited = set(re.findall(r"\[(F\d+)\]", out.answer))
    if set(amounts_in_text(out.answer, 1)) - known or cited - ids:
        return plain
    return Answer(out.answer, facts, ai_worded=True)
