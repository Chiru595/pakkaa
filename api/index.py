"""Vercel Python function entry point: serves the FastAPI app for every /api/* request.

If the app cannot start, a minimal app answers /api/* with a redacted startup error so a failed deploy is
diagnosable without exposing credentials; the full traceback goes to the function logs.
"""
import json
import logging
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "services" / "api"))

log = logging.getLogger("pakkaa.startup")


def _redact(text: str) -> str:
    text = re.sub(r"\b[a-z][a-z0-9+.-]*://\S+", "<url>", text, flags=re.I)
    text = re.sub(r"(password|secret|key|token)=\S+", r"\1=<redacted>", text, flags=re.I)
    return text[:300]


try:
    from app.main import app
except Exception as exc:  # noqa: BLE001
    log.exception("Pakkaa API failed to start")
    _error = json.dumps({"detail": "API failed to start", "error": type(exc).__name__,
                         "message": _redact(str(exc))}).encode()

    async def app(scope, receive, send):  # type: ignore[no-redef]
        if scope["type"] != "http":
            return
        await send({"type": "http.response.start", "status": 503,
                    "headers": [(b"content-type", b"application/json")]})
        await send({"type": "http.response.body", "body": _error})

__all__ = ["app"]
