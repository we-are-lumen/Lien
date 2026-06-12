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


class FundedPayload(BaseModel):
    """Goldsky-forwarded Funded event from FundingPool.fund().

    Event signature: ``Funded(uint256 indexed tokenId, address indexed investor, uint256 amount)``.

    On Funded we close the gap between off-chain financing UUID and on-chain
    tokenId by writing financings.token_id. Subsequent ProofSubmitted webhooks
    use token_id -> financing_id resolution.

    Lookup: we match the event to the pre-existing financings row by tx_hash
    (which the FE submits to /financing/{id}/fund right after the wagmi tx
    confirms). If no row matches the tx_hash, return ``{indexed: false}`` so
    Goldsky retries — the bookkeeping POST may arrive after the event in race
    conditions on testnet.
    """
    token_id: Annotated[int, Field(ge=0)]
    investor: Annotated[str, Field(min_length=42, max_length=42)]  # 0x + 40 hex
    amount: Annotated[str, Field(min_length=1, max_length=78)]      # uint256 as string
    tx_hash: Annotated[str, Field(min_length=66, max_length=66)]    # 0x + 64 hex
    block_number: Annotated[int, Field(ge=0)]


class RepaidPayload(BaseModel):
    """Goldsky-forwarded Repaid event from FundingPool.repay().

    Event signature: ``Repaid(uint256 indexed tokenId, uint256 totalPaid, uint256 toInvestor)``.

    Flips financings.status from ``in_progress`` → ``repaid`` and stamps
    ``repaid_at`` + ``repay_tx_hash`` so the FE can render the happy-path
    completion state. This is the BE-side piece of the supplier's direct
    on-chain ``FundingPool.repay()`` call (no /repayment endpoint per design).

    Idempotent: replays just no-op because status is already ``repaid`` and
    repay_tx_hash is unchanged.
    """
    token_id: Annotated[int, Field(ge=0)]
    total_paid: Annotated[str, Field(min_length=1, max_length=78)]
    to_investor: Annotated[str, Field(min_length=1, max_length=78)]
    tx_hash: Annotated[str, Field(min_length=66, max_length=66)]
    block_number: Annotated[int, Field(ge=0)]


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


def _handle_funded(payload: FundedPayload) -> dict:
    """Write financings.token_id by matching on fund_tx_hash.

    The FE's /financing/{id}/fund POST sets fund_tx_hash before this event
    arrives (real-mode flow); on testnet ordering can flip, so a miss here
    must instruct Goldsky to retry rather than 404.

    Idempotent: if token_id is already set to the same value, the update is a
    no-op. Conflicting token_id is a data-integrity error worth surfacing.
    """
    sb = get_supabase()
    rows = (
        sb.table("financings")
        .select("id, token_id")
        .eq("fund_tx_hash", payload.tx_hash)
        .limit(1)
        .execute()
    ).data or []

    if not rows:
        # Event arrived before the FE bookkeeping POST. Goldsky should retry.
        return {
            "indexed": False,
            "error": f"No financing found for fund_tx_hash={payload.tx_hash[:18]}...",
        }

    row = rows[0]
    financing_id = str(row["id"])  # type: ignore[index]
    existing = row.get("token_id")  # type: ignore[union-attr]
    incoming = str(payload.token_id)

    if existing is not None and str(existing) != incoming:
        # Same fund_tx_hash mapping to a different token_id — shouldn't happen.
        # Don't overwrite silently; flag for manual reconciliation.
        return {
            "indexed": False,
            "error": (
                f"token_id conflict for financing {financing_id}: "
                f"existing={existing} incoming={incoming}"
            ),
        }

    if existing is None:
        sb.table("financings").update({"token_id": incoming}).eq("id", financing_id).execute()

    return {"indexed": True, "financing_id": financing_id, "token_id": incoming}


def _handle_repaid(payload: RepaidPayload) -> dict:
    """Flip financings.status to 'repaid' + stamp repay_tx_hash.

    Idempotent: if status is already 'repaid' AND repay_tx_hash matches,
    no-op. If status is in a state where repaid is invalid (defaulted,
    blacklisted), refuse to overwrite — on-chain Repaid on a defaulted
    deal would be a contract bug worth surfacing, not auto-resolving.
    """
    sb = get_supabase()
    rows = (
        sb.table("financings")
        .select("id, status, repay_tx_hash")
        .eq("token_id", str(payload.token_id))
        .limit(1)
        .execute()
    ).data or []

    if not rows:
        return {
            "indexed": False,
            "error": f"No financing found for token_id={payload.token_id}",
        }

    row = rows[0]
    financing_id = str(row["id"])  # type: ignore[index]
    current_status = row.get("status")  # type: ignore[union-attr]
    current_tx = row.get("repay_tx_hash")  # type: ignore[union-attr]

    # Idempotent replay.
    if current_status == "repaid" and current_tx == payload.tx_hash:
        return {"indexed": True, "financing_id": financing_id, "noop": True}

    # Refuse to overwrite terminal non-repaid states.
    if current_status in ("defaulted", "blacklisted"):
        return {
            "indexed": False,
            "error": (
                f"Refusing to mark financing {financing_id} repaid: "
                f"current status is {current_status}"
            ),
        }

    sb.table("financings").update({
        "status": "repaid",
        "repay_tx_hash": payload.tx_hash,
    }).eq("id", financing_id).execute()

    return {"indexed": True, "financing_id": financing_id, "status": "repaid"}


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


def _check_webhook_secret(x_webhook_secret: Optional[str]) -> None:
    """Shared webhook auth: constant-time compare against settings.webhook_secret.

    Raises Unauthorized if the secret is configured and doesn't match. If unset
    (None/empty), accepts all callers — dev/local mode.
    """
    settings = get_settings()
    if settings.webhook_secret:
        if not x_webhook_secret or not hmac.compare_digest(
            x_webhook_secret, settings.webhook_secret
        ):
            raise Unauthorized("Invalid or missing webhook secret")


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
    _check_webhook_secret(x_webhook_secret)
    return await asyncio.to_thread(_enqueue_job, payload)


@router.post("/webhook/funded")
async def agent_webhook_funded(
    payload: FundedPayload,
    x_webhook_secret: Optional[str] = Header(default=None),
) -> dict:
    """Goldsky-forwarded ``Funded`` event.

    Closes the off-chain financing UUID <-> on-chain tokenId gap by writing
    financings.token_id. Without this, the ProofSubmitted webhook's
    token_id -> financing_id lookup returns nothing in real Mantle mode and
    the agent loop never fires.

    Real-mode blocker (per PRD gap map). Lookup by fund_tx_hash, which the
    FE writes to /financing/{id}/fund right after the wagmi tx confirms.
    """
    import asyncio
    _check_webhook_secret(x_webhook_secret)
    return await asyncio.to_thread(_handle_funded, payload)


@router.post("/webhook/repaid")
async def agent_webhook_repaid(
    payload: RepaidPayload,
    x_webhook_secret: Optional[str] = Header(default=None),
) -> dict:
    """Goldsky-forwarded ``Repaid`` event.

    Flips financings.status to ``repaid`` and stamps repay_tx_hash. This is
    the BE-side piece of the supplier's direct on-chain FundingPool.repay()
    call (no /repayment endpoint per design — chain is the source of truth).

    Without this, financings stay at ``in_progress`` forever after all
    milestones release. The ``repaid`` enum value was dead before this PR.
    """
    import asyncio
    _check_webhook_secret(x_webhook_secret)
    return await asyncio.to_thread(_handle_repaid, payload)


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
