from __future__ import annotations

from fastapi import Request

from app.integrations.nexconn import NexconnClient


def get_nexconn(request: Request) -> NexconnClient:
    return request.app.state.nexconn
