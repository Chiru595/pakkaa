"""Rule packs: versioned YAML files where every figure carries its source and as_of date."""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PACKS = Path(__file__).resolve().parent / "packs"


@lru_cache
def load_pack(name: str) -> dict[str, Any]:
    with open(PACKS / f"{name}.yaml", encoding="utf-8") as f:
        pack = yaml.safe_load(f)
    if not pack.get("source") or not pack.get("as_of"):
        raise ValueError(f"rule pack {name} must declare source and as_of")
    return pack
