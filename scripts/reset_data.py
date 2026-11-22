"""Wipe every workspace, user, room, message and log, then rebuild the clean demo workspace.

The schema is kept; only data is removed. Settings come from the env files given (later files override earlier
ones), so the same script resets a local or a hosted database.

    services/api/.venv/Scripts/python scripts/reset_data.py --confirm wipe
    services/api/.venv/Scripts/python scripts/reset_data.py --env-file .env.prodpull --env-file .env.production \\
        --allow-remote --confirm wipe
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
LINE = re.compile(r"^([A-Z0-9_]+)=(.*)$")


def load_env_files(paths: list[str]) -> dict[str, str]:
    values: dict[str, str] = {}
    for name in paths:
        path = (ROOT / name) if not Path(name).is_absolute() else Path(name)
        if not path.exists():
            sys.exit(f"Env file not found: {path}")
        for line in path.read_text(encoding="utf-8").splitlines():
            m = LINE.match(line.strip())
            value = m.group(2).strip().strip('"').strip("'") if m else ""
            if m and value:
                values[m.group(1)] = value
    return values


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--env-file", action="append", default=[], help="env file(s) to load; default .env")
    parser.add_argument("--allow-remote", action="store_true", help="allow a non-local database")
    parser.add_argument("--confirm", required=True, help='type "wipe" to confirm')
    args = parser.parse_args()
    if args.confirm != "wipe":
        sys.exit('Refusing: pass --confirm wipe')

    env = {**os.environ, **load_env_files(args.env_file or [".env"])}
    for key in ("DATABASE_URL", "DATABASE_URL_UNPOOLED"):
        if env.get(key, "").startswith(("postgres://", "postgresql://")):
            env[key] = "postgresql+psycopg://" + env[key].split("://", 1)[1]
    url = env.get("DATABASE_URL_UNPOOLED") or env.get("DATABASE_URL", "")
    if not url:
        sys.exit("No DATABASE_URL in the env files given")
    host = urlparse(url.replace("+psycopg", "")).hostname or ""
    if host not in ("localhost", "127.0.0.1", "::1") and not args.allow_remote:
        sys.exit(f"Refusing to wipe {host}: pass --allow-remote for a hosted database")

    os.environ.update(env)
    os.environ["DATABASE_URL"] = url
    sys.path.insert(0, str(ROOT / "services" / "api"))
    from sqlalchemy import create_engine, text

    from app import models  # noqa: F401
    from app.db import Base

    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    engine = create_engine(url)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    engine.dispose()
    print(f"Wiped all data on {host} ({len(Base.metadata.sorted_tables)} tables); schema kept.", flush=True)

    seed_cmd = [sys.executable, str(ROOT / "scripts" / "seed_demo.py"), "--allow-remote"]
    seed = subprocess.run(seed_cmd, env={**os.environ}, cwd=str(ROOT))  # noqa: S603
    sys.exit(seed.returncode)


if __name__ == "__main__":
    main()
