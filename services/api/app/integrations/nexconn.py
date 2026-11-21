"""Nexconn Chat server API client.

Requests are signed with sha1(AppSecret + Nonce + Timestamp) and sent to the data center's primary host,
falling back to the backup host on a timeout, a transport error or a 5xx response.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import secrets
import time
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

log = logging.getLogger(__name__)

CARD_MESSAGE_TYPE = "PK:Card"
TEXT_MESSAGE_TYPE = "RC:TxtMsg"
REQUEST_TIMEOUT_SECONDS = 5.0


class NexconnError(RuntimeError):
    def __init__(self, path: str, code: Any, detail: str = "") -> None:
        super().__init__(f"Nexconn {path} failed: code={code} {detail}".strip())
        self.path = path
        self.code = code


def sign(app_secret: str, nonce: str, timestamp: str) -> str:
    return hashlib.sha1(f"{app_secret}{nonce}{timestamp}".encode()).hexdigest()


def verify_webhook_signature(
    app_secret: str, headers: Mapping[str, str], max_skew_seconds: int = 300, now_ms: int | None = None
) -> bool:
    """Checks the Nonce/Timestamp/Signature headers Nexconn sends with each webhook."""
    lowered = {k.lower(): v for k, v in headers.items()}
    nonce, timestamp, signature = lowered.get("nonce"), lowered.get("timestamp"), lowered.get("signature")
    if not (nonce and timestamp and signature) or not timestamp.isdigit():
        return False
    now_ms = now_ms if now_ms is not None else int(time.time() * 1000)
    if abs(now_ms - int(timestamp)) > max_skew_seconds * 1000:
        return False
    return hmac.compare_digest(sign(app_secret, nonce, timestamp), signature.lower())


class NexconnClient:
    def __init__(self, app_key: str, app_secret: str, hosts: Sequence[str],
                 http: httpx.Client | None = None) -> None:
        if not app_key or not app_secret:
            raise ValueError("Nexconn App Key and App Secret are required")
        self._app_key = app_key
        self._app_secret = app_secret
        self._hosts = list(hosts)
        self._http = http or httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS)

    def close(self) -> None:
        self._http.close()

    def _headers(self) -> dict[str, str]:
        nonce = secrets.token_hex(8)
        timestamp = str(int(time.time() * 1000))
        return {
            "App-Key": self._app_key,
            "Nonce": nonce,
            "Timestamp": timestamp,
            "Signature": sign(self._app_secret, nonce, timestamp),
            "Content-Type": "application/json",
        }

    def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        last_error: Exception | None = None
        for host in self._hosts:
            try:
                resp = self._http.post(f"{host}{path}", json=body, headers=self._headers())
            except httpx.TransportError as exc:
                last_error = exc
                log.warning("nexconn transport error", extra={"path": path, "host": host})
                continue
            if resp.status_code >= 500:
                last_error = NexconnError(path, resp.status_code, "server error")
                continue
            try:
                data = resp.json()
            except ValueError as exc:
                raise NexconnError(path, resp.status_code, "non-JSON response") from exc
            if resp.status_code != 200 or data.get("code") not in (0, 200):
                detail = str(data.get("msg") or data.get("message") or "")
                raise NexconnError(path, data.get("code", resp.status_code), detail)
            return data
        raise NexconnError(path, "unreachable", str(last_error))

    def issue_token(self, user_id: str, name: str, avatar_url: str | None = None) -> str:
        body: dict[str, Any] = {"userId": user_id, "name": name[:64]}
        if avatar_url:
            body["avatarUrl"] = avatar_url
        data = self._post("/v4/auth/access-token/issue", body)
        token = (data.get("result") or {}).get("accessToken")
        if not token:
            raise NexconnError("/v4/auth/access-token/issue", data.get("code"), "no accessToken in response")
        return token

    def expire_tokens(self, user_ids: Sequence[str], expires_at_ms: int) -> None:
        self._post("/v4/auth/access-token/expire", {"userIds": list(user_ids)[:20], "expiresAt": expires_at_ms})

    def create_group(self, channel_id: str, name: str, owner: str, user_ids: Sequence[str] = ()) -> None:
        self._post("/v4/group-channel/create",
                         {"channelId": channel_id, "name": name[:64], "owner": owner, "userIds": list(user_ids)[:30]})

    def join_group(self, channel_id: str, user_ids: Sequence[str]) -> None:
        self._post("/v4/group-channel/join", {"channelId": channel_id, "userIds": list(user_ids)[:100]})

    def leave_group(self, channel_id: str, user_ids: Sequence[str]) -> None:
        self._post("/v4/group-channel/leave", {"channelId": channel_id, "userIds": list(user_ids)[:100]})

    def send_group_message(
        self,
        from_user_id: str,
        channel_id: str,
        message_type: str,
        content: dict[str, Any] | str,
        *,
        to_user_ids: Sequence[str] | None = None,
        push_content: str | None = None,
        persist: bool = True,
    ) -> str | None:
        """Posts to a group as `from_user_id`. `to_user_ids` makes it visible only to those members."""
        body: dict[str, Any] = {
            "fromUserId": from_user_id,
            "toChannelIds": [channel_id],
            "messageType": message_type,
            "content": content if isinstance(content, str) else json.dumps(content, ensure_ascii=False),
            "shouldPersist": 1 if persist else 0,
        }
        if to_user_ids:
            body["toUserIds"] = list(to_user_ids)
        if push_content:
            body["pushContent"] = push_content
        data = self._post("/v4/group-channel/message/send", body)
        ids = (data.get("result") or {}).get("messageIds") or []
        return ids[0].get("messageId") if ids else None

    def send_card(self, from_user_id: str, channel_id: str, card: dict[str, Any],
                        to_user_ids: Sequence[str] | None = None) -> str | None:
        return self.send_group_message(
            from_user_id, channel_id, CARD_MESSAGE_TYPE, card, to_user_ids=to_user_ids,
            push_content=str(card.get("title") or "New card"))
