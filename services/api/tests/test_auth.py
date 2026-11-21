from datetime import timedelta

from sqlalchemy import select, update

from app.models import AuthSession, OtpChallenge
from app.security.otp import OTP_MAX_ATTEMPTS

PHONE = "9876543210"
E164 = "+919876543210"


def _request(client):
    return client.post("/api/auth/otp/request", json={"phone": PHONE})


def test_signup_requires_consent_and_name_then_logs_in(client, sent_codes):
    assert _request(client).json() == {"phone": E164, "is_new_user": True, "needs_consent": True, "test_mode": True,
                                       "test_code": sent_codes[E164]}
    r = client.post("/api/auth/otp/verify", json={"phone": PHONE, "code": sent_codes[E164], "name": "Asha"})
    assert r.status_code == 400 and "privacy" in r.json()["detail"]


def test_full_signup_and_me(client, login_as):
    s = login_as(PHONE, "Asha")
    me = client.get("/api/me", headers=s["headers"]).json()
    assert me["name"] == "Asha" and me["phone_last4"] == "3210" and me["orgs"] == []
    assert "phone_e164" not in me


def test_phone_formats_and_rejections():
    import pytest

    from app.security.otp import OtpError, normalize_phone

    for raw in ["9876543210", "98765 43210", "098765 43210", "+91 98765 43210", "+91-98765-43210", "919876543210"]:
        assert normalize_phone(raw) == E164, raw
    for raw in ["98765432101", "1234567890", "5876543210", "+1 415 555 0100", "+92 9876543210", "987654321"]:
        with pytest.raises(OtpError):
            normalize_phone(raw)


def test_invalid_phone_rejected_by_api(client):
    assert client.post("/api/auth/otp/request", json={"phone": "98765432101"}).status_code == 400
    assert client.post("/api/auth/otp/request", json={"phone": "98765abc10"}).status_code == 422
    assert client.post("/api/auth/otp/request", json={"phone": "98765"}).status_code == 422


def test_whitespace_only_name_rejected(client, sent_codes):
    _request(client)
    r = client.post("/api/auth/otp/verify", json={"phone": PHONE, "code": sent_codes[E164], "name": "   ",
                                                   "consent": True})
    assert r.status_code == 422


def test_wrong_code_locks_after_max_attempts(client, sent_codes):
    _request(client)
    good = sent_codes[E164]
    bad = "000000" if good != "000000" else "111111"
    for _ in range(OTP_MAX_ATTEMPTS):
        r = client.post("/api/auth/otp/verify", json={"phone": PHONE, "code": bad, "name": "A", "consent": True})
        assert r.status_code == 400
    r = client.post("/api/auth/otp/verify", json={"phone": PHONE, "code": good, "name": "A", "consent": True})
    assert r.status_code == 429


def test_code_is_single_use_and_stored_hashed(client, sent_codes, db):
    _request(client)
    code = sent_codes[E164]
    stored = db.execute(select(OtpChallenge.code_hash)).scalar_one()
    assert code not in stored
    body = {"phone": PHONE, "code": code, "name": "A", "consent": True}
    assert client.post("/api/auth/otp/verify", json=body).status_code == 200
    assert client.post("/api/auth/otp/verify", json=body).status_code == 400


def test_resend_cooldown(client, sent_codes):
    assert _request(client).status_code == 200
    assert _request(client).status_code == 429


def test_expired_code_rejected(client, sent_codes, db):
    _request(client)
    db.execute(update(OtpChallenge).values(expires_at=OtpChallenge.created_at - timedelta(seconds=1)))
    db.commit()
    r = client.post("/api/auth/otp/verify", json={"phone": PHONE, "code": sent_codes[E164], "name": "A",
                                                   "consent": True})
    assert r.status_code == 400 and "expired" in r.json()["detail"]


def test_concurrent_refresh_within_grace_is_allowed(client, login_as):
    login_as(PHONE)
    old_cookie = client.cookies.get("pk_refresh")
    assert client.post("/api/auth/refresh").status_code == 200
    client.cookies.set("pk_refresh", old_cookie, path="/api/auth")
    r = client.post("/api/auth/refresh")
    assert r.status_code == 200
    assert client.get("/api/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).status_code == 200


def test_refresh_rotates_and_reuse_signs_out_family(client, login_as, db):
    s = login_as(PHONE)
    old_cookie = client.cookies.get("pk_refresh")
    r = client.post("/api/auth/refresh")
    assert r.status_code == 200
    new_access = r.json()["access_token"]
    assert client.cookies.get("pk_refresh") != old_cookie
    db.execute(update(AuthSession).where(AuthSession.replaced_by.is_not(None))
               .values(revoked_at=AuthSession.revoked_at - timedelta(seconds=30)))
    db.commit()
    client.cookies.set("pk_refresh", old_cookie, path="/api/auth")
    assert client.post("/api/auth/refresh").status_code == 401
    assert client.get("/api/me", headers={"Authorization": f"Bearer {new_access}"}).status_code == 401
    assert client.get("/api/me", headers=s["headers"]).status_code == 401


def test_session_hint_cookie_set_and_cleared(client, login_as):
    login_as(PHONE)
    assert client.cookies.get("pk_session") == "1"
    client.cookies.set("pk_refresh", "forged", path="/api/auth")
    r = client.post("/api/auth/refresh")
    assert r.status_code == 401
    assert 'pk_session=""' in r.headers.get("set-cookie", "") or "pk_session=;" in r.headers.get("set-cookie", "")


def test_logout_ends_session(client, login_as):
    s = login_as(PHONE)
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/me", headers=s["headers"]).status_code == 401


def test_me_requires_valid_token(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer nope"}).status_code == 401
