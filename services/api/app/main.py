from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import auth, copilot, deals, messages, orgs, webhooks


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=logging.INFO)
    if settings.migrate_on_start or settings.serverless:
        from app.migrate import upgrade_to_head

        upgrade_to_head(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        client = getattr(app.state, "nexconn", None)
        if client is not None:
            client.close()

    app = FastAPI(title="Pakkaa API", version="0.1.0", lifespan=lifespan,
                  docs_url=None if settings.is_production else "/api/docs", openapi_url="/api/openapi.json")
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True,
                       allow_methods=["GET", "POST", "PATCH", "DELETE"], allow_headers=["*"])

    @app.get("/api/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(auth.me_router)
    app.include_router(orgs.router)
    app.include_router(messages.router)
    app.include_router(deals.router)
    app.include_router(copilot.router)
    app.include_router(webhooks.router)
    return app


app = create_app()
