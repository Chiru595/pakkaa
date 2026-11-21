from tests.test_deal_room import deal, setup_deal

TERMS = [("scope", "Logo and brand kit"), ("price_paise", "50k"), ("advance_pct", 40), ("timeline_days", 21)]


def agree(client, who, room, terms=TERMS):
    for k, v in terms:
        r = client.patch(f"/api/rooms/{room['id']}/term-sheet", json={"key": k, "value": v}, headers=who["headers"])
        assert r.status_code == 200, r.text
    return deal(client, who, room)["term_sheet"]


def sign(client, who, room, sheet, sent_codes, phone):
    target = {"version": sheet["version"], "hash": sheet["hash"]}
    r = client.post(f"/api/rooms/{room['id']}/term-sheet/sign/request", json=target, headers=who["headers"])
    assert r.status_code == 200, r.text
    assert r.json()["test_code"] == sent_codes[phone]
    return client.post(f"/api/rooms/{room['id']}/term-sheet/sign", json={**target, "code": sent_codes[phone]},
                       headers=who["headers"])


def lock(client, owner, guest, room, sent_codes):
    sheet = agree(client, owner, room)
    assert sign(client, owner, room, sheet, sent_codes, "+919000000001").status_code == 200
    r = sign(client, guest, room, sheet, sent_codes, "+919000000003")
    assert r.status_code == 200
    return r.json()["term_sheet"]


def test_both_sides_sign_with_otp_and_sheet_locks(client, login_as, sent_codes, nexconn):
    owner, guest, _, room = setup_deal(client, login_as)
    sheet = agree(client, owner, room)
    r = sign(client, owner, room, sheet, sent_codes, "+919000000001")
    assert r.json()["term_sheet"]["status"] == "draft" and len(r.json()["term_sheet"]["signatures"]) == 1
    locked = sign(client, guest, room, sheet, sent_codes, "+919000000003").json()["term_sheet"]
    assert locked["status"] == "locked" and locked["locked_hash"] == sheet["hash"]
    assert {s["side"] for s in locked["signatures"]} == {"provider", "client"}
    assert any(c[0] == "send_card" and c[1][2].get("locked") for c in nexconn.calls)


def test_cannot_sign_incomplete_or_stale_terms(client, login_as, sent_codes):
    owner, guest, _, room = setup_deal(client, login_as)
    sheet = agree(client, owner, room, TERMS[:2])
    r = client.post(f"/api/rooms/{room['id']}/term-sheet/sign/request",
                    json={"version": sheet["version"], "hash": sheet["hash"]}, headers=owner["headers"])
    assert r.status_code == 400 and "Advance" in r.json()["detail"]
    sheet = agree(client, owner, room, TERMS[2:])
    target = {"version": sheet["version"], "hash": sheet["hash"]}
    client.post(f"/api/rooms/{room['id']}/term-sheet/sign/request", json=target, headers=owner["headers"])
    code = sent_codes["+919000000001"]
    client.patch(f"/api/rooms/{room['id']}/term-sheet", json={"key": "price_paise", "value": "55k"},
                 headers=guest["headers"])
    r = client.post(f"/api/rooms/{room['id']}/term-sheet/sign", json={**target, "code": code},
                    headers=owner["headers"])
    assert r.status_code == 409
    bad = {**target, "hash": "0" * 64}
    assert client.post(f"/api/rooms/{room['id']}/term-sheet/sign/request", json=bad,
                       headers=owner["headers"]).status_code == 409


def test_login_code_cannot_sign_and_wrong_code_fails(client, login_as, sent_codes):
    owner, _, _, room = setup_deal(client, login_as)
    sheet = agree(client, owner, room)
    target = {"version": sheet["version"], "hash": sheet["hash"]}
    login_code = sent_codes["+919000000001"]
    r = client.post(f"/api/rooms/{room['id']}/term-sheet/sign", json={**target, "code": login_code},
                    headers=owner["headers"])
    assert r.status_code == 400


def test_change_after_lock_needs_both_again(client, login_as, sent_codes):
    owner, guest, _, room = setup_deal(client, login_as)
    locked = lock(client, owner, guest, room, sent_codes)
    d = client.patch(f"/api/rooms/{room['id']}/term-sheet", json={"key": "timeline_days", "value": 28},
                     headers=guest["headers"]).json()
    ts = d["term_sheet"]
    assert ts["status"] == "draft" and ts["version"] == locked["version"] + 1 and ts["signatures"] == []


def test_advance_link_after_lock_is_simulated_and_idempotent(client, login_as, sent_codes, nexconn):
    owner, guest, _, room = setup_deal(client, login_as)
    url = f"/api/rooms/{room['id']}/advance-link"
    agree(client, owner, room)
    assert client.post(url, headers=owner["headers"]).status_code == 409
    lock(client, owner, guest, room, sent_codes)
    assert client.post(url, headers=guest["headers"]).status_code == 403
    link = client.post(url, headers=owner["headers"]).json()
    assert link["amount_paise"] == 2_000_000 and link["amount_display"] == "₹20,000" and link["provider"] == "simulated"
    again = client.post(url, headers=owner["headers"]).json()
    assert again["id"] == link["id"]
    token = link["url"].rsplit("/", 1)[1]
    page = client.get(f"/api/pay/{token}").json()
    assert page["simulated"] is True and page["status"] == "pending" and page["org_name"] == "Acme Studio"
    assert client.post(f"/api/pay/{token}/simulate", json={"outcome": "paid"}).json()["status"] == "paid"
    assert client.post(f"/api/pay/{token}/simulate", json={"outcome": "failed"}).json()["status"] == "paid"
    assert deal(client, guest, room)["advance_link"]["status"] == "paid"
    assert client.get("/api/pay/short").status_code == 404
    assert client.post(f"/api/pay/{token}/simulate", json={"outcome": "refund"}).status_code == 422
    assert sum(1 for c in nexconn.calls if c[0] == "send_card" and c[1][2]["kind"] == "payment_status") == 1


def test_stale_scribe_suggestions_close_on_matching_edit_and_on_lock(client, login_as, sent_codes, llm):
    from tests.test_deal_room import say

    owner, guest, _, room = setup_deal(client, login_as)
    llm.queue("scribe", {"proposals": [
        {"term_key": "price_paise", "value": "50k", "confidence": 0.9, "quote": "50k"},
        {"term_key": "revisions", "value": "3", "confidence": 0.9, "quote": "3 revisions"},
    ]})
    say(client, guest, room, "50k and 3 revisions?")
    assert {p["term_key"] for p in deal(client, owner, room)["proposals"]} == {"price_paise", "revisions"}
    sheet = agree(client, owner, room)
    assert [p["term_key"] for p in deal(client, owner, room)["proposals"]] == ["revisions"]
    assert sign(client, owner, room, sheet, sent_codes, "+919000000001").status_code == 200
    locked = sign(client, guest, room, sheet, sent_codes, "+919000000003").json()
    assert locked["term_sheet"]["status"] == "locked" and locked["proposals"] == []
