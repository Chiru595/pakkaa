from datetime import timedelta

import pytest
from sqlalchemy import select, update

from app.db import utcnow
from app.domain import policy as Policy
from app.models import AuditEvent, MemoryItem, MemoryKind, PaymentCard, TermSheet
from app.security import audit
from tests.test_copilot import OPTIONS
from tests.test_deal_room import deal, say, setup_deal
from tests.test_orgs_rooms import _invite_token, _org, _room
from tests.test_paylane import act, new_card, team  # noqa: F401
from tests.test_signing import lock


def _late_advance(client, owner, guest, room, sent_codes, db, days_late_by=18):
    lock(client, owner, guest, room, sent_codes)
    db.execute(update(TermSheet).where(TermSheet.room_id == room["id"])
               .values(locked_at=utcnow() - timedelta(days=days_late_by)))
    db.commit()
    link = client.post(f"/api/rooms/{room['id']}/advance-link", headers=owner["headers"]).json()
    token = link["url"].rsplit("/", 1)[1]
    assert client.post(f"/api/pay/{token}/simulate", json={"outcome": "paid"}).json()["status"] == "paid"


def _second_deal(client, owner, guest, org_id, title="Website for Ravi Foods"):
    room = _room(client, owner, org_id, title=title)
    r = client.post(f"/api/invites/{_invite_token(client, owner, room['id'])}/accept", headers=guest["headers"])
    assert r.status_code == 200
    return room


def test_late_advance_warns_the_team_on_the_next_deal_only(client, login_as, sent_codes, db, nexconn, llm):
    owner, guest, org_id, room = setup_deal(client, login_as)
    _late_advance(client, owner, guest, room, sent_codes, db)
    notes = db.execute(select(MemoryItem).where(MemoryItem.kind == MemoryKind.COUNTERPARTY)).scalars().all()
    assert len(notes) == 1 and "18 days after" in notes[0].text and "15 days late" in notes[0].text
    assert notes[0].data["days_late"] == 15 and notes[0].source_type == "payment_link"

    second = _second_deal(client, owner, guest, org_id)
    warning = [c for c in nexconn.calls if c[0] == "send_card" and c[1][2].get("kind") == "memory_warning"]
    assert len(warning) == 1
    assert warning[0][2]["to_user_ids"] == [owner["user"]["chat_id"]], "only the team's side sees it"
    assert "Heads-up about Ravi" in warning[0][1][2]["title"]

    assert any("15 days late" in n for n in deal(client, owner, second)["counterparty_notes"])
    assert deal(client, guest, second)["counterparty_notes"] == []

    say(client, guest, second, "Shall we start?")
    llm.queue("copilot", OPTIONS, OPTIONS)
    client.post(f"/api/rooms/{second['id']}/copilot/suggest", json={}, headers=owner["headers"])
    r = client.post(f"/api/rooms/{second['id']}/copilot/suggest", json={}, headers=guest["headers"])
    assert r.status_code == 403
    owner_ctx = [c["data"] for c in llm.calls if c["purpose"] == "copilot"][0]
    assert "15 days late" in owner_ctx


def test_deal_outcome_becomes_team_playbook_not_client_memory(client, login_as, sent_codes, db, llm):
    owner, guest, org_id, room = setup_deal(client, login_as)
    url = f"/api/rooms/{room['id']}/outcome"
    assert client.post(url, json={"outcome": "won"}, headers=owner["headers"]).status_code == 409
    lock(client, owner, guest, room, sent_codes)
    assert client.post(url, json={"outcome": "won"}, headers=guest["headers"]).status_code == 403
    d = client.post(url, json={"outcome": "won", "note": "  Liked the 3-concept approach "},
                    headers=owner["headers"]).json()
    assert d["outcome"]["outcome"] == "won" and d["outcome"]["final_display"] == "₹50,000"
    assert d["outcome"]["note"] == "Liked the 3-concept approach"
    assert client.post(url, json={"outcome": "lost"}, headers=owner["headers"]).status_code == 409
    assert client.post(url, json={"outcome": "maybe"}, headers=owner["headers"]).status_code == 422

    playbook = db.execute(select(MemoryItem).where(MemoryItem.kind == MemoryKind.PLAYBOOK)).scalar_one()
    assert "was won" in playbook.text and playbook.source_id == room["id"] and playbook.embedding is not None

    second = _second_deal(client, owner, guest, org_id)
    say(client, guest, second, "What would a website cost?")
    llm.queue("copilot", OPTIONS, OPTIONS)
    client.post(f"/api/rooms/{second['id']}/copilot/suggest", json={}, headers=owner["headers"])
    r = client.post(f"/api/rooms/{second['id']}/copilot/suggest", json={}, headers=guest["headers"])
    assert r.status_code == 403
    owner_ctx = [c["data"] for c in llm.calls if c["purpose"] == "copilot"][0]
    assert "was won" in owner_ctx
    assert deal(client, guest, second)["counterparty_notes"] == []


def test_memory_works_without_embeddings(client, login_as, sent_codes, db, llm):
    owner, guest, org_id, room = setup_deal(client, login_as)
    lock(client, owner, guest, room, sent_codes)
    llm.available = False
    client.post(f"/api/rooms/{room['id']}/outcome", json={"outcome": "won"}, headers=owner["headers"])
    item = db.execute(select(MemoryItem).where(MemoryItem.kind == MemoryKind.PLAYBOOK)).scalar_one()
    assert item.embedding is None
    from app.memory import store

    found = store.recall(db, llm, org_id=org_id, viewer_id=owner["user"]["id"], viewer_role="owner",
                         query="brand kit Ravi", kinds=[MemoryKind.PLAYBOOK])
    assert [m.id for m in found] == [item.id]


def test_memory_is_isolated_per_org_and_hidden_from_guests(client, login_as, sent_codes, db):
    owner, guest, org_id, room = setup_deal(client, login_as)
    _late_advance(client, owner, guest, room, sent_codes, db)
    assert len(client.get(f"/api/orgs/{org_id}/memory", headers=owner["headers"]).json()) >= 1
    assert client.get(f"/api/orgs/{org_id}/memory", headers=guest["headers"]).status_code == 403
    other = login_as("9000000004", "Bob")
    other_org = _org(client, other, "Bob Agency")
    assert client.get(f"/api/orgs/{org_id}/memory", headers=other["headers"]).status_code == 404
    assert client.get(f"/api/orgs/{other_org}/memory", headers=other["headers"]).json() == []
    from app.memory import store

    assert store.recall(db, None, org_id=other_org, viewer_id=other["user"]["id"], viewer_role="owner", query="Ravi",
                        kinds=list(MemoryKind)) == []


def test_tactic_preferences_rank_copilot_options(client, login_as, llm):
    owner, guest, _, room = setup_deal(client, login_as)
    say(client, guest, room, "Can we do 45k?")
    for _ in range(2):
        llm.queue("copilot", OPTIONS)
        r = client.post(f"/api/rooms/{room['id']}/copilot/suggest", json={}, headers=owner["headers"])
        sid = r.text.split('"suggestion_id": "')[1].split('"')[0]
        client.post(f"/api/rooms/{room['id']}/copilot/suggestions/{sid}/feedback",
                    json={"chosen_index": 1, "outcome": "sent"}, headers=owner["headers"])
    llm.queue("copilot", OPTIONS)
    client.post(f"/api/rooms/{room['id']}/copilot/suggest", json={}, headers=owner["headers"])
    last = [c["data"] for c in llm.calls if c["purpose"] == "copilot"][-1]
    assert "Tactics I usually pick, most used first: trade_scope" in last


def test_scribe_learns_from_rejected_and_corrected_readings(client, login_as, llm):
    owner, guest, _, room = setup_deal(client, login_as)
    llm.queue("scribe", {"proposals": [{"term_key": "price_paise", "value": "45k", "confidence": 0.9,
                                        "quote": "45 is too low"}]})
    say(client, guest, room, "45 is too low for you? ok 50k then")
    p = deal(client, owner, room)["proposals"][0]
    client.post(f"/api/rooms/{room['id']}/proposals/{p['id']}/reject", headers=owner["headers"])
    client.patch(f"/api/rooms/{room['id']}/term-sheet", json={"key": "price_paise", "value": "50k"},
                 headers=owner["headers"])
    llm.queue("scribe", {"proposals": []})
    say(client, guest, room, "Great, and 40% advance")
    last = [c["data"] for c in llm.calls if c["purpose"] == "scribe"][-1]
    assert "Past corrections" in last and "45 is too low" in last and "set it to ₹50,000" in last


def test_fraud_report_tightens_thresholds_and_only_owner_loosens(client, team, db):
    card = new_card(client, team, amount=500_000)
    assert card["risk_level"] != "high"
    r = act(client, team, "finance", card, "reject", note="Vendor never asked for this", suspected_fraud=True)
    assert r.status_code == 200
    pol = client.get(f"/api/orgs/{team['org']}/policy", headers=team["owner"]["headers"]).json()
    high = next(t for t in pol["thresholds"] if t["key"] == "high_at")
    assert high["value"] < high["default"]
    assert pol["changes"][0]["source"] == "learning" and pol["changes"][0]["direction"] == "tighten"

    url = f"/api/orgs/{team['org']}/policy/thresholds"
    loosen = {"key": "high_at", "value": high["default"], "reason": "Too many false alarms on small vendor bills"}
    assert client.put(url, json=loosen, headers=team["finance"]["headers"]).status_code == 403
    assert client.put(url, json={**loosen, "reason": "meh"}, headers=team["owner"]["headers"]).status_code == 400
    assert client.put(url, json={**loosen, "value": 99}, headers=team["owner"]["headers"]).status_code == 400
    r = client.put(url, json=loosen, headers=team["owner"]["headers"])
    assert r.status_code == 200 and r.json()["changes"][0]["direction"] == "loosen"
    tighten = {"key": "bank_change_window_days", "value": 60}
    assert client.put(url, json=tighten, headers=team["finance"]["headers"]).status_code == 200
    assert client.put(url, json=tighten, headers=team["member"]["headers"]).status_code == 403
    actions = db.execute(select(AuditEvent.action).where(AuditEvent.action.like("policy.%"))).scalars().all()
    assert actions.count("policy.tighten") >= 2 and actions.count("policy.loosen") == 1
    assert audit.verify_chain(db).ok


def test_member_cannot_mark_fraud(client, team):
    card = new_card(client, team, who="finance", amount=500_000)
    assert act(client, team, "member", card, "reject", suspected_fraud=True).status_code == 403


def test_learning_can_never_loosen(db, team):
    with pytest.raises(ValueError):
        Policy.set_threshold(db, team["org"], "high_at", 80, source="learning", reason="x" * 20)


def test_high_risk_override_reason_is_remembered(client, team, db):
    act(client, team, "finance", new_card(client, team, amount=200_000), "approve", version=1)
    db.execute(update(PaymentCard).values(risk_level="high"))
    db.commit()
    card = new_card(client, team, amount=300_000)
    db.execute(update(PaymentCard).where(PaymentCard.id == card["id"]).values(risk_level="high"))
    db.commit()
    r = act(client, team, "finance", card, "approve", version=card["version"],
            override_reason="Known vendor, confirmed in person today")
    assert r.status_code == 200, r.text
    item = db.execute(select(MemoryItem).where(MemoryItem.kind == MemoryKind.RISK_OVERRIDE)).scalar_one()
    assert "confirmed in person" in item.text and item.source_id == card["id"]
