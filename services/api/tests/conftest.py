from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def _test_database_url() -> str:
    if os.environ.get("TEST_DATABASE_URL"):
        return os.environ["TEST_DATABASE_URL"]
    env = (ROOT / ".env").read_text(encoding="utf-8") if (ROOT / ".env").exists() else ""
    m = re.search(r"^DATABASE_URL=(.+)$", env, flags=re.M)
    base = m.group(1).strip() if m else "postgresql+psycopg://pakkaa:pakkaa@localhost:5432/pakkaa"
    return base.rsplit("/", 1)[0] + "/pakkaa_test"


TEST_DB = _test_database_url()
os.environ.update({
    "DATABASE_URL": TEST_DB, "DATABASE_URL_UNPOOLED": TEST_DB, "APP_ENV": "test", "DEMO_MODE": "false",
    "JWT_SECRET": "test-jwt-secret-" + "x" * 48, "OTP_PEPPER": "test-pepper", "GUEST_LINK_SECRET": "test-guest",
    "NEXCONN_APP_KEY": "testkey", "NEXCONN_APP_SECRET": "testsecret", "NEXCONN_AREA": "SG",
    "WEB_BASE_URL": "http://web.test",
})

import pytest  # noqa: E402
from alembic import command  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from app.db import get_engine, session_factory  # noqa: E402
from app.main import create_app  # noqa: E402
from app.security import otp as otp_module  # noqa: E402
from app.security.deps import reset_rate_limits  # noqa: E402


class FakeNexconn:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []
        self.groups: dict[str, set[str]] = {}

    def _log(self, name, *args, **kwargs):
        self.calls.append((name, args, kwargs))

    def issue_token(self, user_id, name, avatar_url=None):
        self._log("issue_token", user_id, name)
        return f"token-{user_id}"

    def create_group(self, channel_id, name, owner, user_ids=()):
        self._log("create_group", channel_id, name, owner, list(user_ids))
        self.groups[channel_id] = {owner, *user_ids}

    def join_group(self, channel_id, user_ids):
        self._log("join_group", channel_id, list(user_ids))
        self.groups.setdefault(channel_id, set()).update(user_ids)

    def leave_group(self, channel_id, user_ids):
        self._log("leave_group", channel_id, list(user_ids))

    def _next_id(self) -> str:
        self.counter = getattr(self, "counter", 0) + 1
        return f"msg{self.counter}"

    def send_card(self, from_user_id, channel_id, card, to_user_ids=None):
        self._log("send_card", from_user_id, channel_id, card, to_user_ids=to_user_ids)
        return self._next_id()

    def send_group_message(self, *args, **kwargs):
        self._log("send_group_message", *args, **kwargs)
        return self._next_id()

    def close(self):
        pass

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


class FakeLLM:
    """Records every prompt it receives and answers from canned outputs keyed by purpose."""

    def __init__(self) -> None:
        self.outputs: dict[str, list] = {}
        self.calls: list[dict] = []
        self.available = True

    def queue(self, purpose: str, *outputs) -> None:
        self.outputs.setdefault(purpose, []).extend(outputs)

    def structured(self, lane, task, data, schema, *, max_output_tokens=0, purpose=""):
        from app.llm import LLMUnavailable

        self.calls.append({"purpose": purpose, "lane": lane, "task": task, "data": data})
        if not self.available or not self.outputs.get(purpose):
            raise LLMUnavailable("no canned output")
        out = self.outputs[purpose].pop(0)
        if isinstance(out, Exception):
            raise out
        return schema.model_validate(out)

    def stream_items(self, lane, task, data, item_schema, *, max_items=3, max_output_tokens=0, purpose=""):
        from app.llm import LLMUnavailable

        self.calls.append({"purpose": purpose, "lane": lane, "task": task, "data": data})
        if not self.available or not self.outputs.get(purpose):
            raise LLMUnavailable("no canned output")
        for item in self.outputs[purpose].pop(0)[:max_items]:
            yield item_schema.model_validate(item)

    def embed(self, texts):
        return [[float(len(t) % 7) / 7] * 1536 for t in texts]

    def data_for(self, purpose: str) -> str:
        return chr(10).join(c["data"] + c["task"] for c in self.calls if c["purpose"] == purpose)


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    cfg = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    cfg.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    cfg.set_main_option("sqlalchemy.url", TEST_DB)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(autouse=True)
def clean_tables():
    reset_rate_limits()
    with get_engine().begin() as conn:
        tables = conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' "
                                   "AND tablename <> 'alembic_version'")).scalars().all()
        conn.execute(text(f"TRUNCATE {', '.join(tables)} RESTART IDENTITY CASCADE"))
    yield


@pytest.fixture
def db():
    with session_factory()() as s:
        yield s


@pytest.fixture
def nexconn() -> FakeNexconn:
    return FakeNexconn()


@pytest.fixture
def llm() -> FakeLLM:
    return FakeLLM()


@pytest.fixture
def app(nexconn, llm):
    from app.deps import get_llm_dep

    application = create_app()
    application.dependency_overrides[get_llm_dep] = lambda: llm
    return application


@pytest.fixture
def client(app, nexconn):
    with TestClient(app) as c:
        app.state.nexconn = nexconn
        yield c


@pytest.fixture
def sent_codes(monkeypatch):
    codes: dict[str, str] = {}
    monkeypatch.setattr(otp_module.LogOtpProvider, "send", lambda self, phone, code: codes.__setitem__(phone, code))
    return codes


def login(client: TestClient, sent_codes: dict[str, str], phone: str, name: str = "Test User") -> dict:
    """Signs a user in through the real OTP flow and returns auth headers plus the user payload."""
    r = client.post("/api/auth/otp/request", json={"phone": phone})
    assert r.status_code == 200, r.text
    e164 = r.json()["phone"]
    r = client.post("/api/auth/otp/verify", json={"phone": phone, "code": sent_codes[e164], "name": name,
                                                   "consent": True})
    assert r.status_code == 200, r.text
    body = r.json()
    return {"headers": {"Authorization": f"Bearer {body['access_token']}"}, "user": body["user"]}


@pytest.fixture
def login_as(client, sent_codes):
    return lambda phone, name="Test User": login(client, sent_codes, phone, name)
