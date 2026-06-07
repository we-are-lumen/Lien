"""Agent routes — /agent/webhook, /agent/status, /agent/decisions/{financing_id}.

The webhook receives on-chain ProofSubmitted events (via Goldsky) and enqueues
them into ``agent_queue``. The background agent loop (app.services.agent) picks
them up. The status/decisions endpoints expose the queue and audit trail to the
authenticated dashboard.
"""

from __future__ import annotations

import hmac
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from app.core.auth import require_auth
from app.core.config import get_settings
from app.core.db import get_supabase
from app.core.errors import Unauthorized


router = APIRouter()


class WebhookPayload(BaseModel):
    """Goldsky-forwarded ProofSubmitted event.

    Goldsky sends raw contract event fields. We resolve token_id -> financing_id
    in _enqueue_job via a DB lookup against financings.token_id.
    """
    token_id: Annotated[int, Field(ge=0)]
    milestone_idx: Annotated[int, Field(ge=2, le=4)]  # M1 cannot be submitted (auto-released at fund)
    ipfs_hash: Annotated[str, Field(min_length=1, max_length=200)]
    submitted_by: Annotated[str, Field(min_length=1, max_length=100)]


def _enqueue_job(payload: WebhookPayload) -> dict:
    sb = get_supabase()

    # Resolve token_id -> financing_id. Goldsky forwards raw event fields;
    # financing_id is a Supabase UUID that lives only in the DB.
    fin_rows = (
        sb.table("financings")
        .select("id")
        .eq("token_id", str(payload.token_id))
        .limit(1)
        .execute()
    ).data or []
    if not fin_rows:
        # Not raising 404 here: webhook senders should retry. Return a clear
        # status so Goldsky can backoff and replay once the financing is indexed.
        return {
            "queued": False,
            "error": f"No financing found for token_id={payload.token_id}",
        }
    financing_id = str(fin_rows[0]["id"])  # type: ignore[index]

    existing = (
        sb.table("agent_queue")
        .select("id")
        .eq("financing_id", financing_id)
        .eq("milestone_idx", payload.milestone_idx)
        .eq("ipfs_hash", payload.ipfs_hash)
        .in_("status", ["pending", "processing"])  # exclude done+failed: supplier must be able to resubmit after rejection
        .limit(1)
        .execute()
    )
    if existing.data:
        return {"queued": False, "queue_id": existing.data[0]["id"], "duplicate": True}

    result = (
        sb.table("agent_queue")
        .insert({
            "financing_id": financing_id,
            "milestone_idx": payload.milestone_idx,
            "ipfs_hash": payload.ipfs_hash,
            "submitted_by": payload.submitted_by,
        })
        .execute()
    )
    return {"queued": True, "queue_id": result.data[0]["id"]}


def _fetch_status_jobs() -> tuple[list, list]:
    sb = get_supabase()
    jobs_rows = (
        sb.table("agent_queue")
        .select("*")
        .order("created_at", desc=True)
        .limit(20)
        .execute()
    ).data or []

    if not jobs_rows:
        return [], []

    job_ids = [j["id"] for j in jobs_rows]
    all_decisions = (
        sb.table("agent_decisions")
        .select("*")
        .in_("queue_id", job_ids)
        .order("created_at", desc=True)
        .execute()
    ).data or []

    return jobs_rows, all_decisions


def _fetch_decisions(financing_id: str) -> list:
    return (
        get_supabase()
        .table("agent_decisions")
        .select("*")
        .eq("financing_id", financing_id)
        .order("milestone_idx", desc=False)
        .execute()
    ).data or []


@router.post("/webhook")
async def agent_webhook(
    payload: WebhookPayload,
    x_webhook_secret: Optional[str] = Header(default=None),
) -> dict:
    """Enqueue an incoming ProofSubmitted event into ``agent_queue``.

    Auth: ``X-Webhook-Secret`` header must equal ``settings.webhook_secret``.
    If ``webhook_secret`` is unset (None/empty), all callers are accepted (dev mode).
    """
    import asyncio
    settings = get_settings()
    if settings.webhook_secret:
        if not x_webhook_secret or not hmac.compare_digest(
            x_webhook_secret, settings.webhook_secret
        ):
            raise Unauthorized("Invalid or missing webhook secret")

    return await asyncio.to_thread(_enqueue_job, payload)


@router.get("/status")
async def agent_status(address: str = Depends(require_auth)) -> dict:
    """Return the last 20 queue jobs (newest first) with their latest decision."""
    import asyncio
    jobs_rows, all_decisions = await asyncio.to_thread(_fetch_status_jobs)

    if not jobs_rows:
        return {"jobs": []}

    # Keep only the latest decision per queue_id (already ordered desc by created_at).
    latest_decision: dict = {}
    for d in all_decisions:
        qid = d["queue_id"]
        if qid not in latest_decision:
            latest_decision[qid] = d

    jobs = [
        {
            "id": job["id"],
            "financing_id": job["financing_id"],
            "milestone_idx": job["milestone_idx"],
            "ipfs_hash": job["ipfs_hash"],
            "status": job["status"],
            "attempt_count": job["attempt_count"],
            "error_message": job.get("error_message"),
            "created_at": job["created_at"],
            "decision": latest_decision.get(job["id"]),
        }
        for job in jobs_rows
    ]

    return {"jobs": jobs}


@router.get("/decisions/{financing_id}")
async def agent_decisions(
    financing_id: str,
    address: str = Depends(require_auth),
) -> dict:
    """Return all decisions for a financing, ordered by milestone_idx ascending."""
    import asyncio
    decisions = await asyncio.to_thread(_fetch_decisions, financing_id)
    return {"decisions": decisions}
