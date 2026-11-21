from datetime import timedelta

from sqlalchemy import select, update

from app.models import OtpChallenge
from app.security.otp import OTP_MAX_ATTEMPTS

PHONE = "9876543210"
E164 = "+919876543210"


def _request(client):
    return client.post("/api/auth/otp/request", json={"phone": PHONE})


def test_signup_requires_consent_and_name_then_logs_in(client, sent_codes):
    assert _request(client).json() == {"phone": E164, "is_new_user": True, "test_mode": True,
                                       "test_code": sent_codes[E164]}
    r = client.post("/api/auth/otp/verify", json={"phone": PHONE, "code": sent_codes[E164], "name": "Asha"})
    assert r.status_code == 400 and "privacy" in r.json()["detail"]


def test_full_signup_and_me(client, login_as):
    s = login_as(PHONE, "Asha")
    me = client.get("/api/me", headers=s["headers"]).json()
    assert me["name"] == "Asha" and me["phone_last4"] == "3210" and me["orgs"] == []
    assert "phone_e164" not in me


def test_invalid_phone_rejected(client):
    assert client.post("/api/auth/otp/request", json={"phone": "1234567"}).status_code == 400


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


def test_refresh_rotates_and_reuse_signs_out_family(client, login_as):
    s = login_as(PHONE)
    old_cookie = client.cookies.get("pk_refresh")
    r = client.post("/api/auth/refresh")
    assert r.status_code == 200
    new_access = r.json()["access_token"]
    assert client.cookies.get("pk_refresh") != old_cookie
    client.cookies.set("pk_refresh", old_cookie, path="/api/auth")
    assert client.post("/api/auth/refresh").status_code == 401
    assert client.get("/api/me", headers={"Authorization": f"Bearer {new_access}"}).status_code == 401
    assert client.get("/api/me", headers=s["headers"]).status_code == 401


def test_logout_ends_session(client, login_as):
    s = login_as(PHONE)
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/me", headers=s["headers"]).status_code == 401


def test_me_requires_valid_token(client):
    assert client.get("/api/me").status_code == 401
    assert client.get("/api/me", headers={"Authorization": "Bearer nope"}).status_code == 401
