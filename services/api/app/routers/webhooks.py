from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Request

from app.config import Settings, get_settings
from app.integrations.nexconn import verify_webhook_signature

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


@router.post("/nexconn")
async def nexconn_webhook(request: Request, settings: Settings = Depends(get_settings)) -> dict[str, bool]:
    if not verify_webhook_signature(settings.nexconn_app_secret, request.headers, settings.webhook_max_skew_seconds):
        raise HTTPException(status_code=401, detail="bad signature")
    try:
        event = json.loads(await request.body())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid JSON") from exc
    log.info("nexconn webhook", extra={"event_type": event.get("type"), "event_id": event.get("id"),
                                       "count": len(event.get("data") or [])})
    return {"ok": True}
