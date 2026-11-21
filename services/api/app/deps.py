from __future__ import annotations

from fastapi import Request

from app.config import get_settings
from app.integrations.nexconn import NexconnClient


def get_nexconn(request: Request) -> NexconnClient:
    """Created on first use: serverless runtimes may not run ASGI lifespan events."""
    client = getattr(request.app.state, "nexconn", None)
    if client is None:
        s = get_settings()
        client = NexconnClient(s.nexconn_app_key, s.nexconn_app_secret, s.nexconn_hosts)
        request.app.state.nexconn = client
    return client
