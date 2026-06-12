"""FastAPI app entrypoint.

Run locally with:
    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.routers import agent as agent_router
from app.routers import auth as auth_router
from app.routers import documents as documents_router
from app.routers import financing as financing_router
from app.routers import health as health_router
from app.routers import stats as stats_router
from app.services.agent import agent_loop, auto_default_loop


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    agent_task = asyncio.create_task(agent_loop())
    default_task = asyncio.create_task(auto_default_loop())
    try:
        yield
    finally:
        for task in (agent_task, default_task):
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="LIEN API", version="0.1.0", lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(health_router.router, tags=["health"])
    app.include_router(auth_router.router, tags=["auth"])
    app.include_router(stats_router.router, tags=["stats"])
    app.include_router(financing_router.router, tags=["financing"])
    app.include_router(documents_router.router, tags=["documents"])
    app.include_router(agent_router.router, prefix="/agent", tags=["agent"])

    return app


app = create_app()
