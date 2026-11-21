"""Field encryption for bank and UPI details: AES-256-GCM with versioned keys.

FIELD_ENCRYPTION_KEYS holds comma-separated "version:base64key" entries, newest first. New values are always
encrypted with the newest key; older keys stay readable so keys can be rotated without downtime.
Stored form: "<version>:<base64(nonce + ciphertext)>".
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
from functools import lru_cache

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.config import get_settings


class CryptoError(ValueError):
    pass


@lru_cache
def _keys(spec: str) -> tuple[str, dict[str, bytes]]:
    keys: dict[str, bytes] = {}
    order: list[str] = []
    for part in [p.strip() for p in spec.split(",") if p.strip()]:
        version, _, b64 = part.partition(":")
        key = base64.urlsafe_b64decode(b64 + "=" * (-len(b64) % 4))
        if len(key) != 32 or not version:
            raise CryptoError("FIELD_ENCRYPTION_KEYS entries must be version:base64(32 bytes)")
        keys[version] = key
        order.append(version)
    if not order:
        raise CryptoError("FIELD_ENCRYPTION_KEYS is not set")
    return order[0], keys


def encrypt(plaintext: str, context: str) -> str:
    """`context` (for example the payee id) is bound as associated data, so a value cannot be moved between rows."""
    current, keys = _keys(get_settings().field_encryption_keys)
    nonce = os.urandom(12)
    sealed = AESGCM(keys[current]).encrypt(nonce, plaintext.encode(), context.encode())
    return f"{current}:{base64.urlsafe_b64encode(nonce + sealed).decode()}"


def decrypt(stored: str, context: str) -> str:
    _, keys = _keys(get_settings().field_encryption_keys)
    version, _, b64 = stored.partition(":")
    if version not in keys:
        raise CryptoError("unknown key version")
    raw = base64.urlsafe_b64decode(b64)
    try:
        return AESGCM(keys[version]).decrypt(raw[:12], raw[12:], context.encode()).decode()
    except Exception as exc:
        raise CryptoError("cannot decrypt") from exc


def fingerprint(value: str) -> str:
    """Keyed hash for comparing details (has the bank account changed?) without decrypting."""
    _, keys = _keys(get_settings().field_encryption_keys)
    key = next(iter(keys.values()))
    return hmac.new(key, value.encode(), hashlib.sha256).hexdigest()


def mask(value: str, keep: int = 4) -> str:
    """Shows only the last `keep` characters: 1234567890 -> ••••7890; ravi@okaxis -> ••••axis."""
    tail = value[-keep:] if len(value) > keep else value
    return f"••••{tail}"
