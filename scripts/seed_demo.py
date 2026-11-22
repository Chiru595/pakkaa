"""Seeds the "Acme Studio" demo workspace: people, rooms, a past deal with a client who paid the advance late,
payees with payment history, and the memory those produce.

Idempotent: if the demo workspace exists it does nothing, unless --reset is given (which deletes and rebuilds
only the demo workspace). It refuses to touch a non-local database unless --allow-remote is given.

    services/api/.venv/Scripts/python scripts/seed_demo.py [--reset] [--allow-remote]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path
from urllib.parse import urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api"))

from app.config import get_settings  # noqa: E402
from app.db import session_factory  # noqa: E402
from app.demo import ORG_NAME, PEOPLE  # noqa: E402
from app.demo_seed import find_demo_org, rebuild  # noqa: E402
from app.integrations.nexconn import NexconnClient  # noqa: E402


def _check_target(allow_remote: bool) -> None:
    host = urlparse(get_settings().database_url.replace("+psycopg", "")).hostname or ""
    if host not in ("localhost", "127.0.0.1", "::1") and not allow_remote:
        sys.exit(f"Refusing to seed {host}: pass --allow-remote to seed a non-local database.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reset", action="store_true", help="delete and rebuild the demo workspace")
    parser.add_argument("--allow-remote", action="store_true", help="allow seeding a non-local database")
    args = parser.parse_args()
    _check_target(args.allow_remote)
    s = get_settings()
    nexconn = NexconnClient(s.nexconn_app_key, s.nexconn_app_secret, s.nexconn_hosts)
    with session_factory()() as db:
        existing = find_demo_org(db)
        if existing and not args.reset:
            print(f"Demo workspace already exists ({existing.id}); nothing to do. Use --reset to rebuild it.")
            return
        ids = rebuild(db, nexconn, reason="script")
        db.commit()
    nexconn.close()
    print(f"Seeded {ORG_NAME}: {ids}")
    for p in PEOPLE:
        print(f"  +91 {p.phone[:5]} {p.phone[5:]}  {p.name:<14} {p.role.value:<13} {p.about}")


if __name__ == "__main__":
    main()
