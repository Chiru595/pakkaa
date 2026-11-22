from fastapi.testclient import TestClient

from app.config import get_settings
from app.main import create_app


def test_demo_guide_only_in_demo_mode(client, monkeypatch):
    off = client.get("/api/demo")
    assert off.status_code == 200 and off.json() == {"enabled": False, "org_name": "", "accounts": [], "steps": [],
                                                     "note": ""}
    monkeypatch.setenv("DEMO_MODE", "true")
    get_settings.cache_clear()
    try:
        with TestClient(create_app()) as c:
            body = c.get("/api/demo").json()
        assert body["enabled"] is True and body["org_name"] == "Acme Studio" and len(body["steps"]) == 4
        roles = {a["role"] for a in body["accounts"]}
        assert {"owner", "finance", "client_guest", "vendor_guest"} <= roles
        assert all(len(a["phone"]) == 10 for a in body["accounts"])
    finally:
        monkeypatch.setenv("DEMO_MODE", "false")
        get_settings.cache_clear()


def _demo_client(monkeypatch, nexconn, **env):
    monkeypatch.setenv("DEMO_MODE", "true")
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    get_settings.cache_clear()
    app = create_app()
    app.state.nexconn = nexconn
    return TestClient(app)


def test_demo_reset_rebuilds_only_the_demo_workspace(client, login_as, nexconn, monkeypatch, db):
    from sqlalchemy import select

    from app.models import Org
    from app.routers import demo as demo_router

    assert client.post("/api/demo/reset").status_code == 404
    other = login_as("9123400001", "Real User")
    assert client.post("/api/orgs", json={"name": "Real Studio"}, headers=other["headers"]).status_code in (200, 201)
    try:
        with _demo_client(monkeypatch, nexconn, CRON_SECRET="cron-test-secret") as c:  # noqa: S106
            first = c.post("/api/demo/reset")
            assert first.status_code == 200 and first.json() == {"reset": True, "org_name": "Acme Studio"}
            assert c.post("/api/demo/reset").status_code == 429
            monkeypatch.setattr(demo_router, "RESET_COOLDOWN", demo_router.timedelta(0))
            assert c.get("/api/demo/cron-reset").status_code == 404
            assert c.get("/api/demo/cron-reset", headers={"Authorization": "Bearer wrong"}).status_code == 404
            ok = c.get("/api/demo/cron-reset", headers={"Authorization": "Bearer cron-test-secret"})
            assert ok.status_code == 200
        names = sorted(o.name for o in db.execute(select(Org)).scalars())
        assert names == ["Acme Studio", "Real Studio"]
    finally:
        monkeypatch.setenv("DEMO_MODE", "false")
        get_settings.cache_clear()
