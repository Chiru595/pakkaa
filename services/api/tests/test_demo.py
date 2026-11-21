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
