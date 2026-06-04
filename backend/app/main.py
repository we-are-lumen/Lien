"""FastAPI app entrypoint.

Run locally with:
    uvicorn app.main:app --reload --port 8000
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core.config import get_settings
from app.routers import auth as auth_router
from app.routers import documents as documents_router
from app.routers import financing as financing_router
from app.routers import health as health_router
from app.routers import stats as stats_router


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="LIEN API", version="0.1.0")

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

    return app


app = create_app()
