"""Applies Alembic migrations at startup on serverless deploys, serialised with a Postgres advisory lock."""
from __future__ import annotations

import logging
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.pool import NullPool

from app.config import Settings

log = logging.getLogger(__name__)
API_DIR = Path(__file__).resolve().parent.parent
_MIGRATION_LOCK_KEY = 7_420_002
_done = False


def upgrade_to_head(settings: Settings) -> None:
    global _done
    if _done:
        return
    url = settings.database_url_unpooled or settings.database_url
    cfg = Config(str(API_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(API_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    engine = create_engine(url, poolclass=NullPool)
    with engine.connect() as conn:
        conn.execute(text("select pg_advisory_lock(:k)"), {"k": _MIGRATION_LOCK_KEY})
        try:
            command.upgrade(cfg, "head")
            log.info("database migrations are at head")
        finally:
            conn.execute(text("select pg_advisory_unlock(:k)"), {"k": _MIGRATION_LOCK_KEY})
            conn.commit()
    engine.dispose()
    _done = True
