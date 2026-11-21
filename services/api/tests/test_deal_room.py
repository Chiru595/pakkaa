from sqlalchemy import select

from app.models import AuditEvent, TermProposal
from app.security import audit
from tests.test_orgs_rooms import _invite_token, _org, _room

OWNER, CLIENT, OUTSIDER = "9000000001", "9000000003", "9000000004"


def setup_deal(client, login_as, title="Logo and brand kit for Ravi Foods"):
    owner = login_as(OWNER, "Asha")
    org_id = _org(client, owner)
    room = _room(client, owner, org_id, title=title)
    guest = login_as(CLIENT, "Ravi")
    client.post(f"/api/invites/{_invite_token(client, owner, room['id'])}/accept", headers=guest["headers"])
    return owner, guest, org_id, room


def say(client, who, room, text):
    r = client.post(f"/api/rooms/{room['id']}/messages", json={"text": text}, headers=who["headers"])
    assert r.status_code == 201, r.text
    return r.json()


def deal(client, who, room):
    r = client.get(f"/api/rooms/{room['id']}/deal", headers=who["headers"])
    assert r.status_code == 200, r.text
    return r.json()


def test_deal_starts_empty_with_category_from_title(client, login_as):
    owner, guest, _, room = setup_deal(client, login_as)
    d = deal(client, owner, room)
    assert d["my_side"] == "provider" and d["term_sheet"]["version"] == 1
    assert d["term_sheet"]["category"] == "brand_identity"
    assert deal(client, guest, room)["my_side"] == "client"


def test_scribe_proposes_and_human_confirms(client, login_as, llm, nexconn):
    owner, guest, _, room = setup_deal(client, login_as)
    llm.queue("scribe", {"proposals": [
        {"term_key": "price_paise", "value": "50k", "confidence": 0.9, "quote": "Let's do 50k"},
        {"term_key": "advance_pct", "value": "40", "confidence": 0.8, "quote": "40% advance"},
        {"term_key": "timeline_days", "value": "21", "confidence": 0.3, "quote": "maybe 3 weeks"},
        {"term_key": "revisions", "value": "lots", "confidence": 0.9, "quote": "lots"},
    ]})
    say(client, guest, room, "Let's do 50k with 40% advance, maybe 3 weeks?")
    d = deal(client, owner, room)
    assert {p["term_key"]: p["value"] for p in d["proposals"]} == {"price_paise": 5_000_000, "advance_pct": 40}
    assert d["term_sheet"]["version"] == 1, "proposals never change the sheet by themselves"
    assert any(c[0] == "send_card" and c[1][2]["kind"] == "scribe" for c in nexconn.calls)
    assert "untrusted_data" in llm.data_for("scribe")

    price = next(p for p in d["proposals"] if p["term_key"] == "price_paise")
    d = client.post(f"/api/rooms/{room['id']}/proposals/{price['id']}/accept", headers=guest["headers"]).json()
    terms = {t["key"]: t for t in d["term_sheet"]["terms"]}
    assert terms["price_paise"]["value"] == 5_000_000 and terms["price_paise"]["display"] == "₹50,000"
    assert terms["price_paise"]["via"] == "scribe" and terms["price_paise"]["source_message_ids"]
    assert d["term_sheet"]["version"] == 2
    r = client.post(f"/api/rooms/{room['id']}/proposals/{price['id']}/accept", headers=owner["headers"])
    assert r.status_code == 409


def test_scribe_failure_never_blocks_chat(client, login_as, llm):
    owner, guest, _, room = setup_deal(client, login_as)
    llm.available = False
    say(client, owner, room, "Quote is 60k")
    assert deal(client, owner, room)["proposals"] == []


def test_manual_edit_validates_and_bumps_version(client, login_as):
    owner, _, _, room = setup_deal(client, login_as)
    url = f"/api/rooms/{room['id']}/term-sheet"
    for body in [{"key": "advance_pct", "value": 150}, {"key": "price_paise", "value": -5},
                 {"key": "timeline_days", "value": "soon"}, {"key": "scope", "value": "   "}]:
        assert client.patch(url, json=body, headers=owner["headers"]).status_code in (400, 422), body
    assert client.patch(url, json={"key": "nope", "value": 1}, headers=owner["headers"]).status_code == 422
    d = client.patch(url, json={"key": "price_paise", "value": "60,000"}, headers=owner["headers"]).json()
    assert d["term_sheet"]["version"] == 2
    d = client.patch(url, json={"key": "price_paise", "value": 6_000_000}, headers=owner["headers"]).json()
    assert d["term_sheet"]["version"] == 2, "same value is not a change"


def test_private_profile_is_never_visible_to_the_other_side(client, login_as):
    owner, guest, _, room = setup_deal(client, login_as)
    url = f"/api/rooms/{room['id']}/copilot/profile"
    r = client.put(url, json={"walk_away_paise": 4_500_000, "target_paise": 6_000_000, "notes": "Need cash flow"},
                   headers=owner["headers"])
    assert r.status_code == 200
    mine = client.get(url, headers=guest["headers"]).json()
    assert mine["walk_away_paise"] is None and mine["notes"] is None and mine["side"] == "client"
    assert client.put(url, json={"walk_away_paise": 0}, headers=owner["headers"]).status_code == 422


def test_fair_price_uses_rate_band_and_never_leaks_other_orgs(client, login_as):
    owner, _, _, room = setup_deal(client, login_as)
    client.patch(f"/api/rooms/{room['id']}/term-sheet", json={"key": "price_paise", "value": "30k"},
                 headers=owner["headers"])
    r = client.get(f"/api/rooms/{room['id']}/fair-price", headers=owner["headers"]).json()
    assert r["category"] == "brand_identity" and r["position"] == "below_range" and r["source"] and r["as_of"]
    r = client.get(f"/api/rooms/{room['id']}/fair-price?category=logo_design&offer_paise=2500000",
                   headers=owner["headers"]).json()
    assert r["position"] == "upper_half"
    assert client.get(f"/api/rooms/{room['id']}/fair-price?category=nope", headers=owner["headers"]).status_code == 400
    outsider = login_as(OUTSIDER, "Mallory")
    assert client.get(f"/api/rooms/{room['id']}/fair-price", headers=outsider["headers"]).status_code == 404


def test_lock_review_lists_missing_terms(client, login_as):
    owner, _, _, room = setup_deal(client, login_as)
    items = client.get(f"/api/rooms/{room['id']}/term-sheet/review", headers=owner["headers"]).json()
    assert {i["term_key"] for i in items if i["level"] == "missing"} == {"scope", "price_paise", "advance_pct",
                                                                         "timeline_days"}


def test_paylane_has_no_deal_endpoints(client, login_as):
    owner = login_as(OWNER)
    org_id = _org(client, owner)
    lane = _room(client, owner, org_id, "paylane", "Payouts")
    assert client.get(f"/api/rooms/{lane['id']}/deal", headers=owner["headers"]).status_code == 404


def test_term_changes_are_audited(client, login_as, db):
    owner, _, _, room = setup_deal(client, login_as)
    client.patch(f"/api/rooms/{room['id']}/term-sheet", json={"key": "advance_pct", "value": 40},
                 headers=owner["headers"])
    actions = db.execute(select(AuditEvent.action)).scalars().all()
    assert "term.set" in actions and audit.verify_chain(db).ok
    assert db.execute(select(TermProposal)).first() is None
