"""Start a local Postgres 16 with pgvector (embedded via pgserver) and point .env at it.

Used when Docker is unavailable. Data lives in .pgdata/. Run with the API virtualenv:
    services/api/.venv/Scripts/python scripts/dev_db.py
"""
from __future__ import annotations

import re
from pathlib import Path

import pgserver
import psycopg

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / ".pgdata"
ENV = ROOT / ".env"
DATABASES = ("pakkaa", "pakkaa_test")


def main() -> None:
    DATA_DIR.mkdir(exist_ok=True)
    server = pgserver.get_server(DATA_DIR, cleanup_mode=None)
    admin_uri = server.get_uri()
    with psycopg.connect(admin_uri, autocommit=True) as conn:
        for name in DATABASES:
            if not conn.execute("select 1 from pg_database where datname = %s", (name,)).fetchone():
                conn.execute(f"create database {name}")
    base = admin_uri.rsplit("/", 1)[0].replace("postgresql://", "postgresql+psycopg://", 1)
    url = f"{base}/pakkaa"
    text = ENV.read_text(encoding="utf-8")
    for key in ("DATABASE_URL", "DATABASE_URL_UNPOOLED"):
        text = re.sub(rf"^{key}=.*$", f"{key}={url}", text, flags=re.M)
    ENV.write_text(text, encoding="utf-8")
    print(f"Postgres running: {url}")


if __name__ == "__main__":
    main()
