"""Create or update .env from .env.example, generating any empty secret.

Values already set in .env are kept. Keys no longer in .env.example are dropped.
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
}
LINE = re.compile(r"^([A-Z0-9_]+)=(.*)$")


def _value(raw: str) -> str:
    return raw.split(" #", 1)[0].strip()


def _read(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = LINE.match(line)
        if m and _value(m.group(2)):
            values[m.group(1)] = _value(m.group(2))
    return values


def main() -> None:
    existing = _read(ENV)
    out, generated = [], []
    for line in EXAMPLE.read_text(encoding="utf-8").splitlines():
        m = LINE.match(line)
        if not m:
            out.append(line)
            continue
        key, default = m.group(1), _value(m.group(2))
        value = existing.get(key) or default
        if not value and key in GENERATORS:
            value = GENERATORS[key]()
            generated.append(key)
        out.append(f"{key}={value}")
    ENV.write_text("\n".join(out) + "\n", encoding="utf-8")
    print(f"Wrote {ENV}. Generated: {', '.join(generated) or 'none'}.")


if __name__ == "__main__":
    main()
