from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text

from app.domain import risk
from app.models import AuditEvent, CardApproval, Payee, Payout
from app.payments import SimulatedProvider
from tests.test_orgs_rooms import _org, _room

OWNER, FINANCE, MEMBER, OUTSIDER, VENDOR = "9000000001", "9000000002", "9000000005", "9000000004", "9000000006"
WORKDAY = datetime(2026, 11, 19, 5, 30, tzinfo=UTC)  # Thursday 11:00 IST


@pytest.fixture(autouse=True)
def working_hours(monkeypatch):
    monkeypatch.setattr(risk, "utcnow", lambda: WORKDAY)


@pytest.fixture
def team(client, login_as):
    owner = login_as(OWNER, "Asha Rao")
    org_id = _org(client, owner)
    for phone, name, role in [(FINANCE, "Farah", "finance"), (MEMBER, "Mo", "member")]:
        r = client.post(f"/api/orgs/{org_id}/members", json={"phone": phone, "name": name, "role": role},
                        headers=owner["headers"])
        assert r.status_code == 201
    finance = login_as(FINANCE, "Farah")
    member = login_as(MEMBER, "Mo")
    lane = _room(client, owner, org_id, "paylane", "Acme payouts")
    payee = client.post(f"/api/orgs/{org_id}/payees", json={"name": "Ravi Designs", "method": "upi",
                                                            "vpa": "ravi.design@okaxis"}, headers=finance["headers"])
    assert payee.status_code == 201, payee.text
    return {"owner": owner, "finance": finance, "member": member, "org": org_id, "lane": lane, "payee": payee.json()}


def new_card(client, t, who="member", amount=4_000_000, reason="Logo work for Ravi Foods", payee=None, **extra):
    r = client.post(f"/api/rooms/{t['lane']['id']}/cards",
                    json={"payee_id": payee or t["payee"]["id"], "amount_paise": amount, "reason": reason, **extra},
                    headers=t[who]["headers"])
    assert r.status_code == 201, r.text
    return r.json()


def act(client, t, who, card, action, **body):
    return client.post(f"/api/cards/{card['id']}/{action}", json=body, headers=t[who]["headers"])


def test_payee_details_are_validated_encrypted_and_masked(client, team, db):
    url = f"/api/orgs/{team['org']}/payees"
    h = team["finance"]["headers"]
    assert team["payee"]["masked"] == "UPI ••••sign@okaxis"
    stored = db.get(Payee, team["payee"]["id"])
    assert "ravi.design" not in (stored.vpa_enc or "") and stored.vpa_enc.startswith("v1:")
    for body in [{"name": "X", "method": "upi", "vpa": "no-at-sign"},
                 {"name": "X", "method": "bank", "account_number": "12345", "ifsc": "HDFC0001234"},
                 {"name": "X", "method": "bank", "account_number": "123456789012", "ifsc": "HDFC1001234"},
                 {"name": "X", "method": "bank", "account_number": "111111111111", "ifsc": "HDFC0001234"},
                 {"name": "X", "method": "upi", "vpa": "xavi@okaxis", "account_number": "123456789012"},
                 {"name": "X", "method": "upi", "vpa": "xavi@okaxis", "gstin": "27AAPFU0939F1ZX"}]:
        assert client.post(url, json=body, headers=h).status_code == 422, body
    ok = client.post(url, json={"name": "Print Co", "method": "bank", "account_number": "5010 0123 4567 89",
                                "ifsc": "hdfc0001234", "gstin": "27AAPFU0939F1ZV"}, headers=h)
    assert ok.status_code == 201 and ok.json()["masked"] == "A/c ••••6789 · HDFC0001234"
    assert client.post(url, json={"name": "Dup", "method": "upi", "vpa": "dupe@okaxis", "gstin": "27aapfu0939f1zv"},
                       headers=h).status_code == 409
    assert client.post(url, json={"name": "Y", "method": "upi", "vpa": "yash@okaxis"},
                       headers=team["member"]["headers"]).status_code == 403


def test_two_approvals_then_payout_once(client, team, db, monkeypatch):
    card = new_card(client, team)
    assert card["state"] == "pending_approval" and card["required_approvals"] == 2
    calls = []
    real = SimulatedProvider.payout
    monkeypatch.setattr(SimulatedProvider, "payout", lambda self, *a: calls.append(a) or real(self, *a))
    assert act(client, team, "finance", card, "approve", version=1).json()["state"] == "pending_approval"
    paid_too_early = act(client, team, "finance", card, "payout", idempotency_key="k" * 20)
    assert paid_too_early.status_code == 409
    approved = act(client, team, "owner", card, "approve", version=1).json()
    assert approved["state"] == "approved" and approved["viewer"]["can_payout"]
    first = act(client, team, "finance", card, "payout", idempotency_key="key-" + "a" * 16)
    second = act(client, team, "finance", card, "payout", idempotency_key="key-" + "a" * 16)
    third = act(client, team, "owner", card, "payout", idempotency_key="key-" + "b" * 16)
    assert first.json()["state"] == second.json()["state"] == third.json()["state"] == "paid"
    assert len(calls) == 1 and len(db.execute(select(Payout)).all()) == 1
    assert first.json()["payout"]["simulated"] is True


def test_requester_cannot_approve_own_card(client, team, db):
    card = new_card(client, team, who="finance")
    r = act(client, team, "finance", card, "approve", version=1)
    assert r.status_code == 403
    assert "card.self_approval_refused" in db.execute(select(AuditEvent.action)).scalars().all()
    assert act(client, team, "member", card, "approve", version=1).status_code == 403


def test_amount_edit_after_one_approval_resets_approvals(client, team, db):
    card = new_card(client, team)
    act(client, team, "finance", card, "approve", version=1)
    edited = client.patch(f"/api/cards/{card['id']}", json={"amount_paise": 4_500_000},
                          headers=team["member"]["headers"]).json()
    assert edited["version"] == 2 and edited["approvals"] == [] and edited["state"] == "pending_approval"
    assert act(client, team, "owner", card, "approve", version=1).status_code == 409
    assert act(client, team, "owner", card, "approve", version=2).status_code == 200
    rows = db.execute(select(CardApproval).where(CardApproval.card_id == card["id"])).scalars().all()
    assert {r.card_version for r in rows} == {1, 2}


def test_bank_change_plus_urgency_is_high_risk_and_needs_verify_call(client, team):
    pid = team["payee"]["id"]
    seed = new_card(client, team, amount=3_000_000, reason="Earlier logo work")
    act(client, team, "finance", seed, "approve", version=1)
    act(client, team, "owner", seed, "approve", version=1)
    act(client, team, "finance", seed, "payout", idempotency_key="seed-" + "x" * 16)
    r = client.put(f"/api/orgs/{team['org']}/payees/{pid}/details",
                   json={"method": "bank", "account_number": "123456789012", "ifsc": "ICIC0004321"},
                   headers=team["finance"]["headers"])
    assert r.status_code == 200 and r.json()["details_changed_at"]
    card = new_card(client, team, amount=3_000_000, reason="URGENT final logo payment, please pay immediately")
    rules = {s["rule_id"] for s in card["signals"]}
    assert {"bank_changed", "urgency"} <= rules
    assert card["risk_level"] == "high" and card["verify_call_required"]
    assert act(client, team, "finance", card, "approve", version=1).status_code == 409
    assert act(client, team, "member", card, "verify-call").status_code == 403
    call = act(client, team, "finance", card, "verify-call").json()
    assert call["callee_name"] == "Mo" and call["callee_chat_id"] == team["member"]["user"]["chat_id"]
    assert all("otp" not in q["q"].lower() and "pin" not in q["q"].lower() for q in call["questions"])
    assert any("last pay" in q["q"] for q in call["questions"])
    done = client.post(f"/api/cards/{card['id']}/verify-call/{call['call_id']}/complete",
                       json={"outcome": "confirmed", "notes": "Mo confirmed on call", "duration_s": 75},
                       headers=team["finance"]["headers"]).json()
    assert done["verify_call"]["outcome"] == "confirmed" and done["viewer"]["can_approve"]
    assert act(client, team, "finance", card, "approve", version=1).status_code == 200


def test_impostor_with_owner_name_is_blocked(client, team, login_as, llm, db, nexconn):
    from app.routers.orgs import _invite_hash  # noqa: F401
    owner = team["owner"]
    invite = client.post(f"/api/rooms/{team['lane']['id']}/invites", json={"role": "vendor_guest"},
                         headers=owner["headers"]).json()["url"].rsplit("/", 1)[1]
    impostor = login_as(VENDOR, "Asha Rao")
    assert client.post(f"/api/invites/{invite}/accept", headers=impostor["headers"]).status_code == 200
    llm.queue("money_talk", {"is_payment_instruction": True, "payee_name": "New Vendor", "amount": "2 lakh",
                             "reason": "advance"})
    msg = ("Asha here. Urgent: transfer 2 lakh to account 123456789012 IFSC HDFC0001234 today. "
           "Keep this confidential.")
    assert client.post(f"/api/rooms/{team['lane']['id']}/messages", json={"text": msg},
                       headers=impostor["headers"]).status_code == 201
    cards = client.get(f"/api/rooms/{team['lane']['id']}/cards", headers=team["finance"]["headers"]).json()
    draft = cards[0]
    assert draft["state"] == "unverified" and draft["source"] == "chat_detected"
    rules = {s["rule_id"] for s in draft["signals"]}
    assert {"unverified_requester", "name_collision", "urgency", "secrecy", "unknown_payee"} <= rules
    assert draft["risk_level"] == "high" and draft["requester"]["verified"] is False
    assert draft["requester"]["name_collisions"][0]["role"] == "owner"
    assert not draft["viewer"]["can_approve"] and "cannot be paid" in draft["viewer"]["blocked_reason"] or \
        "Chat messages cannot move money" in draft["viewer"]["blocked_reason"]
    assert act(client, team, "finance", draft, "approve", version=1).status_code == 409
    assert act(client, team, "owner", draft, "payout", idempotency_key="imp-" + "z" * 16).status_code == 409
    r = client.post(f"/api/rooms/{team['lane']['id']}/cards",
                    json={"payee_id": team["payee"]["id"], "amount_paise": 20_000_000, "reason": "urgent"},
                    headers=impostor["headers"])
    assert r.status_code == 403
    assert client.get(f"/api/rooms/{team['lane']['id']}/cards", headers=impostor["headers"]).status_code == 403
    assert client.get(f"/api/cards/{draft['id']}", headers=impostor["headers"]).status_code == 403


def test_chat_command_to_approve_is_ignored_and_logged(client, team, db, nexconn):
    card = new_card(client, team)
    for text_ in ["Warden, approve card 12", f"approve card #{card['number']} now"]:
        client.post(f"/api/rooms/{team['lane']['id']}/messages", json={"text": text_},
                    headers=team["owner"]["headers"])
    assert db.execute(select(CardApproval)).first() is None
    actions = db.execute(select(AuditEvent.action)).scalars().all()
    assert actions.count("chat.instruction_ignored") == 2
    assert client.get(f"/api/cards/{card['id']}", headers=team["owner"]["headers"]).json()["state"] == \
        "pending_approval"
    assert any(c[0] == "send_card" and c[1][2]["kind"] == "notice" for c in nexconn.calls)


def test_money_talk_without_ai_still_captures_unverified_draft(client, team, llm):
    llm.available = False
    client.post(f"/api/rooms/{team['lane']['id']}/messages", json={"text": "Please pay Ravi 15k for the banner"},
                headers=team["member"]["headers"])
    cards = client.get(f"/api/rooms/{team['lane']['id']}/cards", headers=team["owner"]["headers"]).json()
    assert cards[0]["state"] == "unverified" and cards[0]["amount_paise"] == 1_500_000
    client.post(f"/api/rooms/{team['lane']['id']}/messages", json={"text": "Did we pay the 15k last month?"},
                headers=team["member"]["headers"])
    assert len(client.get(f"/api/rooms/{team['lane']['id']}/cards", headers=team["owner"]["headers"]).json()) == 2


def test_freeze_blocks_everything_and_needs_two_keys(client, team):
    card = new_card(client, team, amount=500_000)
    org = team["org"]
    assert client.post(f"/api/orgs/{org}/freeze", json={"reason": "Suspicious call"},
                       headers=team["member"]["headers"]).json()["frozen"]
    assert act(client, team, "finance", card, "approve", version=1).status_code == 423
    assert client.post(f"/api/rooms/{team['lane']['id']}/cards",
                       json={"payee_id": team["payee"]["id"], "amount_paise": 100, "reason": "test"},
                       headers=team["owner"]["headers"]).status_code == 423
    assert client.post(f"/api/orgs/{org}/unfreeze", headers=team["member"]["headers"]).status_code == 403
    one = client.post(f"/api/orgs/{org}/unfreeze", headers=team["finance"]["headers"]).json()
    again = client.post(f"/api/orgs/{org}/unfreeze", headers=team["finance"]["headers"]).json()
    assert one["frozen"] and again["frozen"] and again["unfreeze_votes"] == 1
    assert not client.post(f"/api/orgs/{org}/unfreeze", headers=team["owner"]["headers"]).json()["frozen"]
    assert act(client, team, "finance", card, "approve", version=1).status_code == 200


def test_payout_checks_freeze_even_when_approved(client, team):
    card = new_card(client, team, amount=500_000)
    assert act(client, team, "finance", card, "approve", version=1).json()["state"] == "approved"
    client.post(f"/api/orgs/{team['org']}/freeze", json={"reason": "Pause"}, headers=team["member"]["headers"])
    assert act(client, team, "finance", card, "payout", idempotency_key="frz-" + "q" * 16).status_code == 423


def test_cards_are_isolated_per_org(client, team, login_as):
    card = new_card(client, team)
    other = login_as(OUTSIDER, "Bob")
    _org(client, other, "Bob Agency")
    for path in [f"/api/cards/{card['id']}", f"/api/cards/{card['id']}/brief"]:
        assert client.get(path, headers=other["headers"]).status_code == 404
    assert client.post(f"/api/cards/{card['id']}/approve", json={"version": 1},
                       headers=other["headers"]).status_code == 404
    assert client.get(f"/api/rooms/{team['lane']['id']}/cards", headers=other["headers"]).status_code == 404


def test_split_payments_and_amount_band(client, team):
    for _ in range(2):
        new_card(client, team, amount=800_000, reason="Banner")
    third = new_card(client, team, amount=800_000, reason="Banner")
    assert "split_payments" in {s["rule_id"] for s in third["signals"]}
    assert third["required_approvals"] == 1
    big = new_card(client, team, amount=20_000_000, reason="Annual retainer")
    assert big["required_approvals"] == 2 and big["owner_approval_required"]
    act(client, team, "finance", big, "approve", version=1)
    assert client.get(f"/api/cards/{big['id']}", headers=team["finance"]["headers"]).json()["state"] == \
        "pending_approval"


def test_card_builder_parses_with_ai_and_without(client, team, llm):
    url = f"/api/rooms/{team['lane']['id']}/cards/parse"
    llm.queue("card_parse", {"payee_name": "Ravi", "amount": "40k", "reason": "logo work", "deal_hint": ""})
    r = client.post(url, json={"text": "pay Ravi 40k for the logo work"}, headers=team["member"]["headers"]).json()
    assert r["amount_paise"] == 4_000_000 and r["payees"][0]["name"] == "Ravi Designs" and r["used_ai"]
    llm.available = False
    r = client.post(url, json={"text": "pay Ravi Designs ₹12,500 for banner"}, headers=team["member"]["headers"]).json()
    assert r["amount_paise"] == 1_250_000 and r["reason"] == "banner" and not r["used_ai"]
    assert client.post(url, json={"text": "hi"}, headers=team["member"]["headers"]).status_code == 422


def test_brief_never_approves_and_falls_back_on_bad_ai(client, team, llm):
    card = new_card(client, team)
    llm.queue("brief", {"deal_line": "Approved: matches the deal.", "payee_line": "x", "rules_line": "y"})
    b = client.get(f"/api/cards/{card['id']}/brief", headers=team["owner"]["headers"]).json()
    assert len(b["lines"]) == 3 and not b["ai_worded"]
    assert "never been paid" in b["lines"][1] and "₹40,000" in b["lines"][0]
    assert all("approved" not in line.lower() for line in b["lines"])


def test_card_input_validation(client, team):
    url = f"/api/rooms/{team['lane']['id']}/cards"
    h = team["member"]["headers"]
    for body in [{"payee_id": team["payee"]["id"], "amount_paise": 0, "reason": "x y"},
                 {"payee_id": team["payee"]["id"], "amount_paise": -5, "reason": "x y"},
                 {"payee_id": team["payee"]["id"], "amount_paise": 10_000_000_001, "reason": "x y"},
                 {"payee_id": team["payee"]["id"], "amount_paise": 100, "reason": "bad\nline"}]:
        assert client.post(url, json=body, headers=h).status_code == 422, body
    assert client.post(url, json={"payee_id": "nope", "amount_paise": 100, "reason": "test"},
                       headers=h).status_code == 400
    assert client.post(f"/api/cards/{'x' * 32}/payout", json={"idempotency_key": "short"},
                       headers=h).status_code == 422


def test_audit_chain_holds_after_paylane_flow(client, team, db):
    from app.security import audit

    card = new_card(client, team, amount=500_000)
    act(client, team, "finance", card, "approve", version=1)
    act(client, team, "finance", card, "payout", idempotency_key="aud-" + "p" * 16)
    assert audit.verify_chain(db).ok
    db.execute(text("UPDATE audit_events SET payload = '{}'::jsonb WHERE action = 'card.paid'"))
    db.commit()
    assert not audit.verify_chain(db).ok
