from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import logging

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("LIEN backend starting up")
    yield
    logger.info("LIEN backend shutting down")


app = FastAPI(
    title="LIEN API",
    description="Invoice/PO financing platform on Mantle blockchain",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers (imported when implemented)
# from app.api import invoices, financing, marketplace, agent


@app.get("/api/health")
async def health():
    return {"status": "ok", "service": "lien-backend", "version": "0.1.0"}
