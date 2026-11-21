"""Indian payment identifiers. Each validator returns the normalised value or raises ValueError with a clear message."""
from __future__ import annotations

import re

_GSTIN = re.compile(r"^\d{2}[A-Z]{5}\d{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_IFSC = re.compile(r"^[A-Z]{4}0[A-Z0-9]{6}$")
_ACCOUNT = re.compile(r"^\d{9,18}$")
_VPA = re.compile(r"^[a-z0-9][a-z0-9._-]{1,255}@[a-z][a-z0-9]{1,63}$")


def gstin(value: str) -> str:
    v = re.sub(r"\s", "", value).upper()
    if not _GSTIN.match(v):
        raise ValueError("GSTIN must be 15 characters, like 29ABCDE1234F1Z5")
    total = 0
    for i, ch in enumerate(v[:14]):
        product = _GSTIN_CHARS.index(ch) * (1 if i % 2 == 0 else 2)
        total += product // 36 + product % 36
    check = _GSTIN_CHARS[(36 - total % 36) % 36]
    if v[14] != check:
        raise ValueError("GSTIN check digit does not match; please re-check the number")
    return v


def ifsc(value: str) -> str:
    v = re.sub(r"\s", "", value).upper()
    if not _IFSC.match(v):
        raise ValueError("IFSC must be 11 characters: 4 letters, a zero, then 6 letters or digits (e.g. HDFC0001234)")
    return v


def account_number(value: str) -> str:
    v = re.sub(r"[\s-]", "", value)
    if not _ACCOUNT.match(v):
        raise ValueError("Bank account number must be 9 to 18 digits")
    if len(set(v)) == 1:
        raise ValueError("Bank account number looks invalid")
    return v


def vpa(value: str) -> str:
    v = value.strip().lower()
    if not _VPA.match(v):
        raise ValueError("UPI ID must look like name@bank (e.g. ravi.design@okaxis)")
    return v
