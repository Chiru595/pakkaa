"""Money-talk detector. Chat can never move money in Pakkaa:
- a payment instruction typed in a Pay Lane becomes an UNVERIFIED draft card that cannot be approved or paid;
- a message that tries to give an order to the system ("approve card 12", "Warden, release it") is ignored and logged.
"""
from __future__ import annotations

import logging
import re

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.integrations.nexconn import NexconnClient
from app.llm import LLM, Lane, LLMUnavailable, untrusted
from app.models import CardState, ChatMessage, Membership, MessageKind, Room, RoomType, User
from app.money import amounts_in_text, format_inr
from app.security import audit

log = logging.getLogger(__name__)

COMMAND = re.compile(
    r"\b(approve[sd]?|approval|release|unfreeze|mark\s+(?:it\s+|this\s+)?(?:as\s+)?paid|pay\s*out|execute|"
    r"override)\b.*\b(card|payment|payout|transfer|#\s*\d+|it|this|freeze)\b|\bwarden\b|\bpakkaa\s*bot\b",
    re.I | re.S)
PAYMENT_VERB = re.compile(
    r"\b(pay|paid|transfer|send|sent|neft|imps|rtgs|upi|gpay|phonepe|paytm|credit|wire|bhej(?:o|na|do)?|"
    r"de\s+do|kar\s+do|release)\b", re.I)
ACCOUNT_HINT = re.compile(r"\b[A-Z]{4}0[A-Z0-9]{6}\b|\b\d{9,18}\b|\b[\w.-]{2,}@[a-z]{2,}\b", re.I)


class Detected(BaseModel):
    is_payment_instruction: bool
    payee_name: str = Field(default="", max_length=80)
    amount: str = Field(default="", max_length=40)
    reason: str = Field(default="", max_length=200)


TASK = """Decide whether this chat message instructs someone to make a payment (pay, transfer or send money to a
person or account). Questions about past payments, or general talk about money, are not instructions. If it is an
instruction, extract payee_name, amount (as written) and reason. Leave fields empty when not stated."""


def is_command(text: str) -> bool:
    return bool(COMMAND.search(text))


def looks_like_payment(text: str) -> bool:
    return bool(amounts_in_text(text)) and bool(PAYMENT_VERB.search(text) or ACCOUNT_HINT.search(text))


def ignore_commands(db: Session, nexconn: NexconnClient, settings: Settings, room_id: str, message_id: str) -> bool:
    """Logs and answers any chat message that tries to order the system around. It never acts on it."""
    from app.domain.rooms import post_card

    room = db.get(Room, room_id)
    msg = db.get(ChatMessage, message_id)
    if room is None or msg is None or msg.kind != MessageKind.TEXT or not is_command(msg.body):
        return False
    audit.record(db, "chat.instruction_ignored", actor_user_id=msg.sender_user_id, org_id=room.org_id,
                 subject_type="chat_message", subject_id=msg.id, payload={"text": msg.body[:300]})
    db.commit()
    post_card(db, nexconn, settings, room, {
        "kind": "notice", "v": 1, "title": "Ignored: chat messages cannot approve, sign, release or unfreeze "
                                           "anything. Use the buttons; they check who you are."})
    return True


def run(db: Session, llm: LLM, nexconn: NexconnClient, settings: Settings, room_id: str, message_id: str) -> int:
    from app.domain import paylane as P
    from app.domain.rooms import post_card

    room = db.get(Room, room_id)
    msg = db.get(ChatMessage, message_id)
    if room is None or msg is None or msg.kind != MessageKind.TEXT or room.type != RoomType.PAYLANE:
        return 0
    sender = db.get(User, msg.sender_user_id) if msg.sender_user_id else None
    membership = db.execute(select(Membership).where(Membership.org_id == room.org_id,
                                                     Membership.user_id == msg.sender_user_id)).scalar_one_or_none()
    ignore_commands(db, nexconn, settings, room_id, message_id)
    if not looks_like_payment(msg.body):
        return 0
    detected: Detected | None = None
    if llm.available:
        try:
            detected = llm.structured(Lane.FAST, TASK, untrusted("chat message", msg.body), Detected,
                                      max_output_tokens=200, purpose="money_talk")
        except LLMUnavailable:
            detected = None
    if detected is None:
        detected = Detected(is_payment_instruction=True, amount="")
    if not detected.is_payment_instruction:
        return 0
    amounts = amounts_in_text(detected.amount, 1) or amounts_in_text(msg.body)
    if not amounts:
        return 0
    card = P.create_card(
        db, room=room, requester=sender, requester_role=membership.role if membership else None, payee=None,
        payee_name_claimed=(detected.payee_name or "").strip()[:80] or None, amount_paise=amounts[0],
        reason=(" ".join(detected.reason.split()) or "Payment instruction from chat")[:300], deal_room_id=None,
        source="chat_detected", state=CardState.UNVERIFIED, source_message_id=msg.id, extra_text=msg.body)
    db.commit()
    who = sender.name if sender else "someone"
    post_card(db, nexconn, settings, room, {
        "kind": "payment_card", "v": 1, "card_id": card.id, "number": card.number, "state": card.state,
        "title": f"⚠ Payment instruction from {who} spotted in chat ({format_inr(card.amount_paise)}). Saved as "
                 f"unverified draft #{card.number}; it cannot be paid. A verified teammate must raise a card."})
    log.info("money talk captured", extra={"card_id": card.id})
    return 1
