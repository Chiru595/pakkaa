"""Shared, validated input types. Whitespace is trimmed before length checks; control characters are refused."""
from __future__ import annotations

from typing import Annotated

from pydantic import StringConstraints

_SINGLE_LINE = r"^[^\x00-\x1f\x7f]+$"
_MULTI_LINE = r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+$"

PersonName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80, pattern=_SINGLE_LINE)]
Title = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120, pattern=_SINGLE_LINE)]
MessageText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4000,
                                               pattern=_MULTI_LINE)]
PhoneInput = Annotated[str, StringConstraints(strip_whitespace=True, min_length=10, max_length=16,
                                              pattern=r"^\+?[0-9 ()-]+$")]
OtpCode = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^\d{6}$")]

_SINGLE_LINE_OR_EMPTY = r"^[^\x00-\x1f\x7f]*$"
_MULTI_LINE_OR_EMPTY = r"^[^\x00-\x08\x0b\x0c\x0e-\x1f\x7f]*$"


def bounded_text(min_length: int, max_length: int, *, multiline: bool = False) -> object:
    """Trimmed text with length bounds; control characters refused (newlines allowed only when multiline)."""
    if min_length > 0:
        pattern = _MULTI_LINE if multiline else _SINGLE_LINE
    else:
        pattern = _MULTI_LINE_OR_EMPTY if multiline else _SINGLE_LINE_OR_EMPTY
    return Annotated[str, StringConstraints(strip_whitespace=True, min_length=min_length, max_length=max_length,
                                            pattern=pattern)]


ObjectId = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[a-f0-9]{32}$")]
ClientRef = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64,
                                             pattern=r"^[A-Za-z0-9_-]+$")]
