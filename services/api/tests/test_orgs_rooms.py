OWNER, FINANCE, CLIENT, OUTSIDER = "9000000001", "9000000002", "9000000003", "9000000004"


def _org(client, s, name="Acme Studio"):
    r = client.post("/api/orgs", json={"name": name}, headers=s["headers"])
    assert r.status_code == 201, r.text
    return r.json()["id"]


def _room(client, s, org_id, type_="deal", title="Logo for Ravi Foods"):
    r = client.post(f"/api/orgs/{org_id}/rooms", json={"type": type_, "title": title}, headers=s["headers"])
    assert r.status_code == 201, r.text
    return r.json()


def _invite_token(client, s, room_id, role="client_guest"):
    r = client.post(f"/api/rooms/{room_id}/invites", json={"role": role}, headers=s["headers"])
    assert r.status_code == 201, r.text
    return r.json()["url"].rsplit("/", 1)[1]


def test_owner_creates_org_and_deal_room_on_nexconn(client, login_as, nexconn):
    owner = login_as(OWNER, "Asha")
    org_id = _org(client, owner)
    room = _room(client, owner, org_id)
    assert room["type"] == "deal" and room["my_side"] == "provider"
    assert room["channel_id"] == f"r{room['id']}"
    assert nexconn.groups[room["channel_id"]] >= {owner["user"]["chat_id"]}
    assert "send_card" in nexconn.names()


def test_paylane_includes_whole_team(client, login_as, nexconn):
    owner = login_as(OWNER, "Asha")
    org_id = _org(client, owner)
    r = client.post(f"/api/orgs/{org_id}/members", json={"phone": FINANCE, "name": "Farah", "role": "finance"},
                    headers=owner["headers"])
    assert r.status_code == 201 and r.json()["verified"] is False
    room = _room(client, owner, org_id, "paylane", "Acme payouts")
    sides = {m["name"]: m["side"] for m in room["members"]}
    assert sides == {"Asha": "team", "Farah": "team"}


def test_guest_invite_flow_is_single_use_and_scoped(client, login_as, nexconn):
    owner = login_as(OWNER, "Asha")
    org_id = _org(client, owner)
    room = _room(client, owner, org_id)
    other_room = _room(client, owner, org_id, title="Private internal deal")
    token = _invite_token(client, owner, room["id"])

    preview = client.get(f"/api/invites/{token}").json()
    assert preview["org_name"] == "Acme Studio" and preview["role"] == "client_guest"

    guest = login_as(CLIENT, "Ravi")
    joined = client.post(f"/api/invites/{token}/accept", headers=guest["headers"])
    assert joined.status_code == 200 and joined.json()["my_side"] == "client"
    assert guest["user"]["chat_id"] in nexconn.groups[room["channel_id"]]

    assert client.get(f"/api/invites/{token}").status_code == 404
    outsider = login_as(OUTSIDER, "Mallory")
    assert client.post(f"/api/invites/{token}/accept", headers=outsider["headers"]).status_code == 404

    rooms = client.get(f"/api/orgs/{org_id}/rooms", headers=guest["headers"]).json()
    assert [r["id"] for r in rooms] == [room["id"]]
    assert client.get(f"/api/rooms/{other_room['id']}", headers=guest["headers"]).status_code == 404
    assert client.get(f"/api/orgs/{org_id}/members", headers=guest["headers"]).status_code == 403


def test_guest_cannot_create_rooms_or_invites(client, login_as):
    owner = login_as(OWNER)
    org_id = _org(client, owner)
    room = _room(client, owner, org_id)
    guest = login_as(CLIENT)
    client.post(f"/api/invites/{_invite_token(client, owner, room['id'])}/accept", headers=guest["headers"])
    r = client.post(f"/api/orgs/{org_id}/rooms", json={"type": "deal", "title": "x y"}, headers=guest["headers"])
    assert r.status_code == 403
    r = client.post(f"/api/rooms/{room['id']}/invites", json={"role": "client_guest"}, headers=guest["headers"])
    assert r.status_code == 403


def test_invite_role_must_match_room_type(client, login_as):
    owner = login_as(OWNER)
    org_id = _org(client, owner)
    deal = _room(client, owner, org_id)
    r = client.post(f"/api/rooms/{deal['id']}/invites", json={"role": "vendor_guest"}, headers=owner["headers"])
    assert r.status_code == 400


def test_org_isolation(client, login_as):
    a = login_as(OWNER, "Asha")
    org_a = _org(client, a, "Acme Studio")
    room_a = _room(client, a, org_a)
    b = login_as(OUTSIDER, "Bob")
    _org(client, b, "Bob Agency")
    for method, path in [("get", f"/api/orgs/{org_a}/rooms"), ("get", f"/api/orgs/{org_a}/members"),
                         ("get", f"/api/rooms/{room_a['id']}"), ("post", f"/api/rooms/{room_a['id']}/join")]:
        assert getattr(client, method)(path, headers=b["headers"]).status_code == 404, path
    r = client.post(f"/api/orgs/{org_a}/rooms", json={"type": "deal", "title": "hijack"}, headers=b["headers"])
    assert r.status_code == 404
    r = client.post(f"/api/rooms/{room_a['id']}/invites", json={"role": "client_guest"}, headers=b["headers"])
    assert r.status_code == 404


def test_only_owner_adds_teammates(client, login_as):
    owner = login_as(OWNER)
    org_id = _org(client, owner)
    client.post(f"/api/orgs/{org_id}/members", json={"phone": FINANCE, "name": "Farah", "role": "finance"},
                headers=owner["headers"])
    finance = login_as(FINANCE, "Farah")
    r = client.post(f"/api/orgs/{org_id}/members", json={"phone": OUTSIDER, "name": "X", "role": "member"},
                    headers=finance["headers"])
    assert r.status_code == 403
    r = client.post(f"/api/orgs/{org_id}/members", json={"phone": OUTSIDER, "name": "X", "role": "owner"},
                    headers=owner["headers"])
    assert r.status_code == 400


def test_chat_token_is_issued_for_own_chat_id_only(client, login_as, nexconn):
    s = login_as(OWNER, "Asha")
    r = client.post("/api/chat/token", headers=s["headers"])
    assert r.status_code == 200
    body = r.json()
    assert body["chat_user_id"] == s["user"]["chat_id"] and body["token"] == f"token-{s['user']['chat_id']}"
    assert "secret" not in str(body).lower()
