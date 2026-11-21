from tests.test_deal_room import deal, say, setup_deal

OPTIONS = [
    {"tactic": "hold_price", "text": "My rate for the full kit is ₹60,000.", "why": "Anchor on value"},
    {"tactic": "trade_scope", "text": "For ₹50,000 I can do the logo and 2 social templates.", "why": "Less scope"},
    {"tactic": "ask_question", "text": "Which items matter most to you?", "why": "Find priorities"},
]


def _profiles(client, owner, guest, room):
    url = f"/api/rooms/{room['id']}/copilot/profile"
    client.put(url, json={"walk_away_paise": 4_500_000, "notes": "FREELANCER-SECRET rent due"},
               headers=owner["headers"])
    client.put(url, json={"walk_away_paise": 5_500_000, "notes": "CLIENT-SECRET board approved 55k"},
               headers=guest["headers"])


def test_copilot_streams_options_and_records_suggestion(client, login_as, llm):
    owner, guest, _, room = setup_deal(client, login_as)
    say(client, guest, room, "Can we do 50k?")
    llm.queue("copilot", OPTIONS)
    r = client.post(f"/api/rooms/{room['id']}/copilot/suggest", json={}, headers=owner["headers"])
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    events = [b for b in r.text.split("\n\n") if b]
    assert sum(e.startswith("event: option") for e in events) == 3
    assert "Trade scope" in r.text and events[-1].startswith("event: done")
    sid = events[-1].split('"suggestion_id": "')[1].split('"')[0]
    url = f"/api/rooms/{room['id']}/copilot/suggestions/{sid}/feedback"
    assert client.post(url, json={"chosen_index": 1, "outcome": "edited"}, headers=owner["headers"]).status_code == 204
    assert client.post(url, json={"chosen_index": 1, "outcome": "sent"}, headers=guest["headers"]).status_code == 404


def test_copilot_offline_degrades_gracefully(client, login_as, llm):
    owner, _, _, room = setup_deal(client, login_as)
    llm.available = False
    r = client.post(f"/api/rooms/{room['id']}/copilot/suggest", json={}, headers=owner["headers"])
    assert r.status_code == 200 and "event: error" in r.text and "offline" in r.text


def test_client_copilot_context_has_no_freelancer_side_data(client, login_as, llm):
    owner, guest, _, room = setup_deal(client, login_as)
    _profiles(client, owner, guest, room)
    say(client, owner, room, "Full kit is 60k.")
    llm.queue("copilot", OPTIONS, OPTIONS)
    client.post(f"/api/rooms/{room['id']}/copilot/suggest", json={}, headers=guest["headers"])
    client.post(f"/api/rooms/{room['id']}/copilot/suggest", json={}, headers=owner["headers"])
    guest_ctx, owner_ctx = [c["data"] for c in llm.calls if c["purpose"] == "copilot"]
    assert "CLIENT-SECRET" in guest_ctx and "₹55,000" in guest_ctx
    assert "FREELANCER-SECRET" not in guest_ctx and "₹45,000" not in guest_ctx
    assert "FREELANCER-SECRET" in owner_ctx and "CLIENT-SECRET" not in owner_ctx and "₹55,000" not in owner_ctx
    assert "untrusted_data" in guest_ctx


def test_presend_warns_but_never_blocks(client, login_as):
    owner, guest, _, room = setup_deal(client, login_as)
    _profiles(client, owner, guest, room)
    url = f"/api/rooms/{room['id']}/copilot/check"
    codes = lambda who, text: {w["code"] for w in client.post(url, json={"draft": text}, headers=who["headers"]).json()}  # noqa: E731
    assert "below_walk_away" in codes(owner, "Okay, 40k final")
    assert codes(owner, "Okay, 50k final") == set()
    assert "above_budget" in codes(guest, "We can stretch to 60k")
    assert {"no_advance", "unlimited_revisions"} <= codes(owner, "No advance needed, unlimited revisions")
    assert "secret" in codes(guest, "my OTP is 482913")
    say(client, owner, room, "Okay, 40k final")


def test_presend_flags_change_to_locked_price(client, login_as, db):
    from app.domain.deal import get_sheet
    from app.models import Room, TermSheetStatus

    owner, _, _, room = setup_deal(client, login_as)
    for k, v in [("price_paise", "50k"), ("advance_pct", 40)]:
        client.patch(f"/api/rooms/{room['id']}/term-sheet", json={"key": k, "value": v}, headers=owner["headers"])
    sheet = get_sheet(db, db.get(Room, room["id"]))
    sheet.status = TermSheetStatus.LOCKED
    db.commit()
    url = f"/api/rooms/{room['id']}/copilot/check"
    warn = client.post(url, json={"draft": "Actually make it 55k"}, headers=owner["headers"]).json()
    assert [w["code"] for w in warn] == ["contradicts_locked"]
    ok = client.post(url, json={"draft": "Advance of ₹20,000 on the ₹50,000 total"}, headers=owner["headers"]).json()
    assert ok == []


def test_explainer_validates_language_and_degrades(client, login_as, llm):
    owner, _, _, room = setup_deal(client, login_as)
    url = f"/api/rooms/{room['id']}/explain"
    llm.queue("explain", {"meaning": "Client owns the work only after paying in full.", "standard": "standard",
                          "why": "Protects the freelancer", "suggestion": ""})
    r = client.post(url, json={"text": "IP transfers on full payment", "language": "hinglish"},
                    headers=owner["headers"])
    assert r.status_code == 200 and r.json()["standard"] == "standard" and "not legal advice" in r.json()["disclaimer"]
    assert client.post(url, json={"text": "x", "language": "en"}, headers=owner["headers"]).status_code == 422
    assert client.post(url, json={"text": "Some clause", "language": "fr"}, headers=owner["headers"]).status_code == 422
    assert client.post(url, json={"text": "Another clause"}, headers=owner["headers"]).status_code == 503
    assert deal(client, owner, room)["term_sheet"]["version"] == 1
