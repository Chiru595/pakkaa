"""Verifies a Pakkaa audit export (JSONL or CSV) without the database or the Pakkaa code.

Each event's hash must equal sha256(prev_hash + canonical JSON of its fields), its payload_hash must match its
payload, and its prev_hash must equal the previous event's hash (the first one uses 64 zeros).
Usage: python scripts/verify_audit_export.py pakkaa-audit-....jsonl
Exit code 0 when the chain is intact, 1 when it is not.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path

GENESIS = "0" * 64
FIELDS = ("chain_key", "org_id", "actor_user_id", "action", "subject_type", "subject_id", "payload_hash",
          "created_at")


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)


def read_events(path: Path) -> Iterator[dict]:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".csv":
        for row in csv.DictReader(text.splitlines()):
            event = {k: (v if v != "" else None) for k, v in row.items()}
            event["seq"] = int(row["seq"])
            event["payload"] = json.loads(row["payload"])
            yield event
    else:
        for line in text.splitlines():
            if line.strip():
                yield json.loads(line)


def verify(events: Iterable[dict]) -> tuple[bool, int, str]:
    prev, count = GENESIS, 0
    for e in events:
        count += 1
        if e["prev_hash"] != prev:
            return False, count, f"event seq {e.get('seq')}: prev_hash does not match the previous event"
        if hashlib.sha256(canonical(e["payload"]).encode()).hexdigest() != e["payload_hash"]:
            return False, count, f"event seq {e.get('seq')}: payload changed"
        body = canonical({k: e[k] for k in FIELDS})
        if hashlib.sha256(f"{prev}{body}".encode()).hexdigest() != e["hash"]:
            return False, count, f"event seq {e.get('seq')}: fields changed"
        prev = e["hash"]
    return True, count, f"intact; head {prev}"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    ok, count, message = verify(read_events(Path(sys.argv[1])))
    print(f"{'OK' if ok else 'BROKEN'}: {count} event(s) checked, {message}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
