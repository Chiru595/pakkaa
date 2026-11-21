from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.integrations.nexconn import NexconnClient
from app.routers import auth, orgs, webhooks


def create_app() -> FastAPI:
    settings = get_settings()
    logging.basicConfig(level=logging.INFO)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.nexconn = NexconnClient(settings.nexconn_app_key, settings.nexconn_app_secret,
                                          settings.nexconn_hosts)
        yield
        app.state.nexconn.close()

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
    app.include_router(webhooks.router)
    return app


app = create_app()
