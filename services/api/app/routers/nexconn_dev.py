"""Step 0 hello-world: two browsers join one Nexconn group with tokens issued by this API.

Development only. Replaced by OTP login and real rooms in M0; disabled when APP_ENV=production.
"""
from __future__ import annotations

import hashlib
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import Settings, get_settings
from app.deps import get_nexconn
from app.integrations.nexconn import NexconnClient, NexconnError

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dev", tags=["dev"])

HELLO_CHANNEL_ID = "pakkaahello"


class HelloRequest(BaseModel):
    name: str = Field(min_length=1, max_length=40)


class HelloSession(BaseModel):
    user_id: str
    name: str
    token: str
    app_key: str
    area: str
    channel_id: str


def _dev_only(settings: Settings = Depends(get_settings)) -> Settings:
    if settings.is_production:
        raise HTTPException(status_code=404)
    return settings


def _user_id(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]", "", name.lower())[:16] or "user"
    return f"dev{slug}{hashlib.sha256(name.encode()).hexdigest()[:8]}"


@router.post("/hello", response_model=HelloSession)
async def hello(req: HelloRequest, settings: Settings = Depends(_dev_only),
                nexconn: NexconnClient = Depends(get_nexconn)) -> HelloSession:
    user_id = _user_id(req.name.strip())
    try:
        await nexconn.issue_token(settings.nexconn_bot_user_id, settings.nexconn_bot_name)
        try:
            await nexconn.create_group(HELLO_CHANNEL_ID, "Pakkaa hello room", settings.nexconn_bot_user_id)
        except NexconnError as exc:
            log.info("hello group create skipped: %s", exc)
        token = await nexconn.issue_token(user_id, req.name.strip())
        await nexconn.join_group(HELLO_CHANNEL_ID, [user_id])
        await nexconn.send_card(settings.nexconn_bot_user_id, HELLO_CHANNEL_ID,
                                {"kind": "system", "title": f"{req.name.strip()} joined", "v": 1})
    except NexconnError as exc:
        log.exception("nexconn hello failed")
        raise HTTPException(status_code=502, detail="Chat service unavailable") from exc
    return HelloSession(user_id=user_id, name=req.name.strip(), token=token, app_key=settings.nexconn_app_key,
                        area=settings.nexconn_area.upper(), channel_id=HELLO_CHANNEL_ID)
