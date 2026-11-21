"""Create or update .env from .env.example and fill every [GENERATED] secret that is still empty.

Existing values in .env are never overwritten, so it is safe to re-run after adding new variables.
Usage: python scripts/gen_secrets.py
"""
from __future__ import annotations

import base64
import os
import re
import secrets
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / ".env.example"
ENV = ROOT / ".env"

GENERATORS = {
    "JWT_SECRET": lambda: secrets.token_urlsafe(64),
    "GUEST_LINK_SECRET": lambda: secrets.token_urlsafe(48),
    "OTP_PEPPER": lambda: secrets.token_urlsafe(48),
    "FIELD_ENCRYPTION_KEYS": lambda: "v1:" + base64.urlsafe_b64encode(os.urandom(32)).decode(),
    "CRON_SECRET": lambda: secrets.token_urlsafe(32),
}
LINE = re.compile(r"^([A-Z0-9_]+)=(.*)$")


def _value(rest: str) -> str:
    return rest.split("#", 1)[0].strip()


def main() -> None:
    existing: dict[str, str] = {}
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8").splitlines():
            m = LINE.match(line)
            if m and _value(m.group(2)):
                existing[m.group(1)] = line

    out, filled = [], []
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = LINE.match(line)
        if not m:
            out.append(line)
            continue
        key, rest = m.groups()
        if key in existing:
            out.append(existing[key])
        elif key in GENERATORS and not _value(rest):
            comment = rest[rest.index("#"):] if "#" in rest else ""
            out.append(f"{key}={GENERATORS[key]()}" + (f"  {comment}" if comment else ""))
            filled.append(key)
        else:
            out.append(line)

    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Wrote {ENV}. Generated: {', '.join(filled) or 'nothing new'}.")


if __name__ == "__main__":
    main()
