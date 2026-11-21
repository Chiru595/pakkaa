from tests.test_orgs_rooms import _invite_token, _org, _room


def _setup(client, login_as):
    owner = login_as("9000000001", "Asha")
    org_id = _org(client, owner)
    room = _room(client, owner, org_id)
    guest = login_as("9000000003", "Ravi")
    client.post(f"/api/invites/{_invite_token(client, owner, room['id'])}/accept", headers=guest["headers"])
    return owner, guest, org_id, room


def test_messages_go_through_api_and_history_comes_from_db(client, login_as, nexconn):
    owner, guest, _, room = _setup(client, login_as)
    r = client.post(f"/api/rooms/{room['id']}/messages", json={"text": "Quote is 60k"}, headers=owner["headers"])
    assert r.status_code == 201
    sent = [c for c in nexconn.calls if c[0] == "send_group_message"][-1]
    assert sent[1][0] == owner["user"]["chat_id"] and sent[1][2] == "RC:TxtMsg"
    assert sent[1][3] == {"content": "Quote is 60k"}
    assert sent[2]["echo_to_sender"] is True
    client.post(f"/api/rooms/{room['id']}/messages", json={"text": "Can we do 50k?"}, headers=guest["headers"])
    history = client.get(f"/api/rooms/{room['id']}/messages", headers=guest["headers"]).json()
    assert [m["text"] for m in history if m["kind"] == "text"] == ["Quote is 60k", "Can we do 50k?"]
    assert history[0]["kind"] == "card"
    assert history[-1]["sender_chat_id"] == guest["user"]["chat_id"]


def test_outsider_cannot_read_or_post(client, login_as):
    _, _, _, room = _setup(client, login_as)
    mallory = login_as("9000000004", "Mallory")
    assert client.get(f"/api/rooms/{room['id']}/messages", headers=mallory["headers"]).status_code == 404
    r = client.post(f"/api/rooms/{room['id']}/messages", json={"text": "hi"}, headers=mallory["headers"])
    assert r.status_code == 404


def test_team_member_must_join_before_posting(client, login_as):
    owner, _, org_id, room = _setup(client, login_as)
    client.post(f"/api/orgs/{org_id}/members", json={"phone": "9000000002", "name": "Farah", "role": "finance"},
                headers=owner["headers"])
    farah = login_as("9000000002", "Farah")
    r = client.post(f"/api/rooms/{room['id']}/messages", json={"text": "hi"}, headers=farah["headers"])
    assert r.status_code == 403
    client.post(f"/api/rooms/{room['id']}/join", headers=farah["headers"])
    r = client.post(f"/api/rooms/{room['id']}/messages", json={"text": "hi"}, headers=farah["headers"])
    assert r.status_code == 201


def test_empty_and_oversized_messages_rejected(client, login_as):
    owner, _, _, room = _setup(client, login_as)
    for text in ["", "x" * 4001]:
        r = client.post(f"/api/rooms/{room['id']}/messages", json={"text": text}, headers=owner["headers"])
        assert r.status_code == 422
