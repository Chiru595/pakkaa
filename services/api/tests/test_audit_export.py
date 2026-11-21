import importlib.util
import json
from pathlib import Path

from sqlalchemy import select

from app.config import get_settings
from app.models import AuditEvent
from app.security import audit
from tests.test_copilot import OPTIONS
from tests.test_deal_room import deal, say, setup_deal
from tests.test_orgs_rooms import _invite_token, _org, _room
from tests.test_paylane import act, new_card, team  # noqa: F401
from tests.test_signing import lock

_spec = importlib.util.spec_from_file_location(
    "verify_audit_export", Path(__file__).resolve().parents[3] / "scripts" / "verify_audit_export.py")
verifier = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(verifier)


def _export(client, who, org_id, fmt, tmp_path):
    r = client.get(f"/api/orgs/{org_id}/audit/export?format={fmt}", headers=who["headers"])
    assert r.status_code == 200, r.text
    path = tmp_path / f"export.{fmt}"
    path.write_text(r.text, encoding="utf-8")
    return path


def test_export_verifies_on_its_own_and_detects_tampering(client, login_as, sent_codes, tmp_path):
    owner, guest, org_id, room = setup_deal(client, login_as)
    lock(client, owner, guest, room, sent_codes)
    for fmt in ("jsonl", "csv"):
        path = _export(client, owner, org_id, fmt, tmp_path)
        ok, count, _ = verifier.verify(verifier.read_events(path))
        assert ok and count >= 10, fmt
    lines = (tmp_path / "export.jsonl").read_text(encoding="utf-8").splitlines()
    events = [json.loads(line) for line in lines]
    assert {e["chain_key"] for e in events} == {org_id}, "an org's export contains only its own chain"
    events[3]["payload"]["value"] = 1
    assert not verifier.verify(events)[0]
    events = [json.loads(line) for line in lines]
    del events[2]
    assert not verifier.verify(events)[0]


def test_export_and_verify_are_owner_and_finance_only(client, team, login_as):  # noqa: F811
    org = team["org"]
    assert client.get(f"/api/orgs/{org}/audit/verify", headers=team["finance"]["headers"]).json()["ok"]
    assert client.get(f"/api/orgs/{org}/audit/verify", headers=team["member"]["headers"]).status_code == 403
    outsider = login_as("9000000009", "Eve")
    _org(client, outsider, "Eve Co")
    assert client.get(f"/api/orgs/{org}/audit/export", headers=outsider["headers"]).status_code == 404
    assert client.get(f"/api/orgs/{org}/audit/export?format=xml", headers=team["owner"]["headers"]).status_code == 422


def test_every_main_flow_is_audited_and_every_chain_verifies(client, login_as, sent_codes, llm, db, team):  # noqa: F811
    owner, org_id = team["owner"], team["org"]
    room = _room(client, owner, org_id, title="Logo kit")
    guest = login_as("9000000003", "Ravi")
    client.post(f"/api/invites/{_invite_token(client, owner, room['id'])}/accept", headers=guest["headers"])
    llm.queue("scribe", {"proposals": [{"term_key": "revisions", "value": "3", "confidence": 0.9,
                                        "quote": "3 rounds"}]})
    say(client, guest, room, "3 rounds of revisions please")
    p = deal(client, owner, room)["proposals"][0]
    client.post(f"/api/rooms/{room['id']}/proposals/{p['id']}/reject", headers=owner["headers"])
    llm.queue("copilot", OPTIONS)
    client.post(f"/api/rooms/{room['id']}/copilot/suggest", json={}, headers=owner["headers"])
    lock(client, owner, guest, room, sent_codes)
    link = client.post(f"/api/rooms/{room['id']}/advance-link", headers=owner["headers"]).json()
    client.post(f"/api/pay/{link['url'].rsplit('/', 1)[1]}/simulate", json={"outcome": "paid"})
    client.post(f"/api/rooms/{room['id']}/outcome", json={"outcome": "won"}, headers=owner["headers"])

    card = new_card(client, team, amount=500_000)
    act(client, team, "finance", card, "approve", version=1)
    act(client, team, "finance", card, "payout", idempotency_key="audit-walk-" + "a" * 16)
    act(client, team, "finance", new_card(client, team, amount=300_000), "reject", suspected_fraud=True)
    client.post(f"/api/orgs/{team['org']}/freeze", json={"reason": "drill"}, headers=team["member"]["headers"])

    actions = set(db.execute(select(AuditEvent.action)).scalars())
    expected = {"user.signup", "user.consent", "auth.login", "org.create", "room.create", "invite.create",
                "invite.accept", "scribe.proposed", "scribe.proposal_decided", "term.set", "payment_link.created",
                "payment_link.paid", "deal.outcome", "memory.add", "card.create", "card.approve", "card.paid",
                "card.reject", "policy.tighten", "control.freeze", "payee.create"}
    assert expected <= actions, expected - actions
    assert audit.verify_chain(db).ok
    for chain in (org_id, audit.SYSTEM_CHAIN):
        assert audit.verify_chain(db, chain).ok


def test_openapi_schema_hidden_in_production(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app

    monkeypatch.setenv("APP_ENV", "production")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as c:
            assert c.get("/api/openapi.json").status_code == 404
            assert c.get("/api/docs").status_code == 404
            assert c.get("/api/healthz").status_code == 200
    finally:
        monkeypatch.setenv("APP_ENV", "test")
        get_settings.cache_clear()
