import json

import httpx
import pytest
import respx

from app.integrations.nexconn import NexconnClient, NexconnError, sign, verify_webhook_signature

PRIMARY, BACKUP = "https://primary.test", "https://backup.test"


def test_sign_matches_documented_formula():
    import hashlib
    assert sign("secret", "abc", "123") == hashlib.sha1(b"secretabc123").hexdigest()


def test_webhook_signature_accepts_valid_and_rejects_tampered_or_stale():
    ts = "1700000000000"
    headers = {"AppKey": "k", "Nonce": "n1", "Timestamp": ts, "Signature": sign("s", "n1", ts)}
    assert verify_webhook_signature("s", headers, now_ms=int(ts))
    assert not verify_webhook_signature("other", headers, now_ms=int(ts))
    assert not verify_webhook_signature("s", {**headers, "Nonce": "n2"}, now_ms=int(ts))
    assert not verify_webhook_signature("s", headers, now_ms=int(ts) + 301_000)
    assert not verify_webhook_signature("s", {}, now_ms=int(ts))


@pytest.mark.asyncio
@respx.mock
async def test_issue_token_falls_back_to_backup_host_on_5xx():
    respx.post(f"{PRIMARY}/v4/auth/access-token/issue").mock(return_value=httpx.Response(503))
    route = respx.post(f"{BACKUP}/v4/auth/access-token/issue").mock(
        return_value=httpx.Response(200, json={"code": 0, "result": {"userId": "u1", "accessToken": "tok"}}))
    client = NexconnClient("key", "secret", [PRIMARY, BACKUP])
    assert await client.issue_token("u1", "Ravi") == "tok"
    sent = route.calls.last.request
    assert sent.headers["App-Key"] == "key"
    assert sent.headers["Signature"] == sign("secret", sent.headers["Nonce"], sent.headers["Timestamp"])
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_business_error_is_raised_not_retried():
    primary = respx.post(f"{PRIMARY}/v4/group-channel/join").mock(
        return_value=httpx.Response(200, json={"code": 20005, "msg": "bad"}))
    backup = respx.post(f"{BACKUP}/v4/group-channel/join")
    client = NexconnClient("key", "secret", [PRIMARY, BACKUP])
    with pytest.raises(NexconnError):
        await client.join_group("g1", ["u1"])
    assert primary.called and not backup.called
    await client.aclose()


@pytest.mark.asyncio
@respx.mock
async def test_card_is_sent_as_custom_type_with_json_string_content_and_targets():
    route = respx.post(f"{PRIMARY}/v4/group-channel/message/send").mock(
        return_value=httpx.Response(
            200, json={"code": 0, "result": {"messageIds": [{"channelId": "g1", "messageId": "m1"}]}}))
    client = NexconnClient("key", "secret", [PRIMARY])
    mid = await client.send_card("bot", "g1", {"kind": "term_sheet", "title": "Terms"}, to_user_ids=["u2"])
    body = json.loads(route.calls.last.request.content)
    assert mid == "m1"
    assert body["messageType"] == "PK:Card" and body["toUserIds"] == ["u2"] and body["toChannelIds"] == ["g1"]
    assert json.loads(body["content"]) == {"kind": "term_sheet", "title": "Terms"}
    await client.aclose()
