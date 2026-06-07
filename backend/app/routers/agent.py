"""Agent routes — /agent/webhook, /agent/funded-webhook, /agent/repaid-webhook,
/agent/status, /agent/decisions/{financing_id}.

The webhook receives on-chain ProofSubmitted events (via Goldsky) and enqueues
them into ``agent_queue``. The background agent loop (app.services.agent) picks
them up. The status/decisions endpoints expose the queue and audit trail to the
authenticated dashboard.

The funded-webhook receives ``FundedWithRef(tokenId, investor, financingRef, amount)``
events and writes ``financings.token_id`` so the agent loop can later resolve
proof events back to financing rows. The repaid-webhook receives ``Repaid(tokenId,
totalPaid, toInvestor)`` events and flips the financing to ``status=repaid /
payment_status=paid``.

Soft-failure protocol: when an event resolves to no DB row (race: the chain
event landed before the DB insert is visible), the handler raises HTTP 503 so
Goldsky's at-least-once delivery retries. 2xx would ACK and permanently drop
the event.
"""

from __future__ import annotations

import hmac
import logging
from typing import Annotated, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, status
from pydantic import BaseModel, Field

from app.core.auth import require_auth
from app.core.config import get_settings
from app.core.db import get_supabase
from app.core.errors import Unauthorized


logger = logging.getLogger(__name__)


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


class FundedWebhookPayload(BaseModel):
    """Goldsky-forwarded FundedWithRef event.

    The on-chain ``financingRef`` is ``keccak256(financing UUID string)``.
    Resolution: hash every published financing UUID and match against the
    incoming ``financing_ref``. Cheaper alternatives (storing the keccak in a
    DB column at publish time) can replace this if the published row count
    grows past the few-hundred mark.
    """
    token_id: Annotated[int, Field(ge=0)]
    investor: Annotated[str, Field(min_length=42, max_length=42)]
    amount: Annotated[str, Field(min_length=1, max_length=80)]
    # 0x-prefixed 32-byte hash. keccak256(financing_id UUID string).
    financing_ref: Annotated[str, Field(min_length=66, max_length=66)]
    tx_hash: Annotated[str, Field(min_length=66, max_length=66)]


class RepaidWebhookPayload(BaseModel):
    """Goldsky-forwarded Repaid event."""
    token_id: Annotated[int, Field(ge=0)]
    total_paid: Annotated[str, Field(min_length=1, max_length=80)]
    to_investor: Annotated[str, Field(min_length=1, max_length=80)]
    tx_hash: Annotated[str, Field(min_length=66, max_length=66)]


def _keccak256_hex(data: bytes) -> str:
    """Solidity-compatible keccak256 over raw bytes, returned as 0x-prefixed hex.

    Mirrors ``keccak256(abi.encodePacked(string))`` on-chain: the ABI-packed
    encoding of a string is just its UTF-8 bytes.
    """
    from eth_utils import keccak  # type: ignore[import-not-found]
    return "0x" + keccak(data).hex()


def _resolve_financing_by_ref(financing_ref: str) -> Optional[dict]:
    """Resolve a FundedWithRef.financingRef to a financings row.

    Hashes every financing UUID and matches. O(N) over all rows; acceptable
    for hackathon scale (<1k rows). For production scale, precompute and
    store the ref on the financings row at publish time.

    Matches against ALL statuses (not just active ones): a replay against
    a later-progressed or terminal financing must still resolve so the
    handler can return ``duplicate`` / ``status_locked`` instead of a
    soft-fail that triggers Goldsky's infinite retry.
    """
    sb = get_supabase()
    ref_lower = financing_ref.lower()
    rows = (
        sb.table("financings")
        .select("id, token_id, status")
        .execute()
    ).data or []
    for row in rows:
        candidate_ref = _keccak256_hex(str(row["id"]).encode("utf-8"))
        if candidate_ref.lower() == ref_lower:
            return row
    return None


# Statuses where a late FundedWithRef replay must NOT regress state.
# - 'published' is excluded — it's the legitimate flip target.
# - 'funded' is excluded — first apply writes it; replays hit the
#   token_id idempotency branch instead.
# - 'draft' is locked — funding a never-published financing skips the
#   publish gate. Write token_id (so the agent loop can resolve future
#   proof events) but don't auto-promote a draft to funded.
_FUNDED_WEBHOOK_LOCKED_STATUSES = {
    "draft", "in_progress", "repaid", "defaulted", "blacklisted", "frozen",
}


def _sync_m1_release(sb, financing_id: str, fund_tx_hash: str) -> None:
    """Backfill M1 milestone state from the on-chain fund tx.

    The on-chain fund() / fundWithRef() / mintAndFundWithRef() auto-releases
    milestone 1 atomically with the fund tx. M1's ProofSubmitted webhook
    never fires (contract reverts CannotSubmitM1 by design — see agent loop's
    M1 short-circuit), so without this sync M1 is stuck at status='pending' /
    release_tx_hash=null / released_at=null forever. The fund_tx_hash IS the
    M1 release tx on-chain, so we reuse it.

    Convergent / idempotent: skip cleanly when all three fields are already
    set. Backfill any subset that's missing. The agent loop's M1 path writes
    status + released_at without release_tx_hash; we backfill release_tx_hash
    so _advance_financing_status._is_released() counts M1. Conversely if
    funded-webhook runs alone (the real-mode default), we also write
    released_at so the FE display has a timestamp.

    Called from EVERY branch in _apply_funded_webhook that has confirmed the
    token_id maps to this financing — including the duplicate and
    status_locked branches — so a crash between the financings UPDATE and
    this sync recovers on Goldsky retry instead of permanently stranding M1.
    """
    from datetime import datetime, timezone
    m1_rows = (
        sb.table("milestones")
        .select("id, status, release_tx_hash, released_at")
        .eq("financing_id", financing_id)
        .eq("idx", 1)
        .limit(1)
        .execute()
    ).data or []
    if not m1_rows:
        return
    m1 = m1_rows[0]
    m1_patch: dict = {}
    if m1.get("status") != "released":
        m1_patch["status"] = "released"
    if not m1.get("release_tx_hash"):
        m1_patch["release_tx_hash"] = fund_tx_hash
    if not m1.get("released_at"):
        m1_patch["released_at"] = datetime.now(timezone.utc).isoformat()
    if m1_patch:
        sb.table("milestones").update(m1_patch).eq("id", m1["id"]).execute()


def _apply_funded_webhook(payload: FundedWebhookPayload) -> dict:
    sb = get_supabase()
    row = _resolve_financing_by_ref(payload.financing_ref)
    if not row:
        # Race: the chain event landed before the financing row is visible
        # (publish + fund in the same block, or replication lag). Raise 503
        # so Goldsky's at-least-once delivery retries with backoff. Returning
        # 2xx here would ACK the event and permanently drop it.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"No financing matches financing_ref={payload.financing_ref}; retry later",
        )

    financing_id = str(row["id"])
    current_status = str(row.get("status") or "")

    # Idempotent: if token_id already set to this value, skip the financings
    # write but still run M1 sync (recovery from a crash between the
    # financings UPDATE and the M1 sync on a prior delivery).
    if row.get("token_id") is not None:
        try:
            existing = int(row["token_id"])
        except (TypeError, ValueError):
            existing = None
        if existing == payload.token_id:
            _sync_m1_release(sb, financing_id, payload.tx_hash)
            return {
                "applied": False,
                "financing_id": financing_id,
                "duplicate": True,
            }
        # token_id mismatch on the same UUID — refuse silently and alert.
        # Do NOT touch M1: this is a permanent semantic conflict, not a
        # recovery case.
        logger.warning(
            "funded-webhook: financing %s already has token_id=%s, ignoring incoming %s",
            financing_id, existing, payload.token_id,
        )
        return {
            "applied": False,
            "financing_id": financing_id,
            "error": "token_id_mismatch",
        }

    # No token_id yet, but the financing has already progressed past `funded`
    # (or is terminal). Don't regress status, but DO write token_id so the
    # agent loop can resolve future proof events. Run M1 sync too: the agent
    # loop may have flipped M1 status without release_tx_hash, and we need
    # the backfill for _is_released() to count it.
    if current_status in _FUNDED_WEBHOOK_LOCKED_STATUSES:
        sb.table("financings").update(
            {"token_id": str(payload.token_id), "fund_tx_hash": payload.tx_hash}
        ).eq("id", financing_id).execute()
        _sync_m1_release(sb, financing_id, payload.tx_hash)
        logger.warning(
            "funded-webhook: financing %s already in status=%s, wrote token_id without status change",
            financing_id, current_status,
        )
        return {
            "applied": True,
            "financing_id": financing_id,
            "token_id": payload.token_id,
            "status_locked": current_status,
        }

    sb.table("financings").update(
        {
            "token_id": str(payload.token_id),
            "status": "funded",
            "fund_tx_hash": payload.tx_hash,
        }
    ).eq("id", financing_id).execute()

    _sync_m1_release(sb, financing_id, payload.tx_hash)

    return {
        "applied": True,
        "financing_id": financing_id,
        "token_id": payload.token_id,
    }


# States from which Repaid is a valid forward transition. Per the documented
# state machine (draft -> published -> funded -> in_progress -> repaid),
# only an active deal in escrow can be repaid. Anything else is a state
# anomaly: record the tx_hash for ops, leave status untouched.
_REPAID_WEBHOOK_VALID_FROM = {"funded", "in_progress"}


def _apply_repaid_webhook(payload: RepaidWebhookPayload) -> dict:
    sb = get_supabase()

    rows = (
        sb.table("financings")
        .select("id, status, payment_status")
        .eq("token_id", str(payload.token_id))
        .limit(1)
        .execute()
    ).data or []
    if not rows:
        # Same race as funded-webhook: Goldsky may deliver Repaid before
        # /agent/funded-webhook has run (extreme reordering) or before the
        # token_id is visible. 503 -> Goldsky retries.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"No financing found for token_id={payload.token_id}; retry later",
        )

    row = rows[0]
    financing_id = str(row["id"])
    current_status = str(row.get("status") or "")

    if current_status == "repaid":
        # Already flipped to repaid. Reconcile payment_status if it wasn't
        # set in lockstep (defensive: this handler is the only path that
        # writes status=repaid, but a hypothetical out-of-band write could
        # leave payment_status stale).
        if row.get("payment_status") != "paid":
            sb.table("financings").update(
                {"payment_status": "paid", "repay_tx_hash": payload.tx_hash}
            ).eq("id", financing_id).execute()
            logger.warning(
                "repaid-webhook: financing %s was status=repaid but payment_status=%s, reconciled to paid",
                financing_id, row.get("payment_status"),
            )
            return {
                "applied": True,
                "financing_id": financing_id,
                "reconciled_payment_status": True,
            }
        return {"applied": False, "financing_id": financing_id, "duplicate": True}

    if current_status not in _REPAID_WEBHOOK_VALID_FROM:
        # State anomaly: on-chain Repaid against an off-chain status that
        # shouldn't have an active escrow (draft never funded, published
        # never funded, or already-terminal defaulted/frozen/blacklisted).
        # Record repay_tx_hash so ops can reconcile, don't flip status.
        sb.table("financings").update(
            {"repay_tx_hash": payload.tx_hash}
        ).eq("id", financing_id).execute()
        logger.warning(
            "repaid-webhook: financing %s is in status=%s (not in %s), recorded repay_tx_hash without status change",
            financing_id, current_status, sorted(_REPAID_WEBHOOK_VALID_FROM),
        )
        return {
            "applied": True,
            "financing_id": financing_id,
            "status_locked": current_status,
        }

    sb.table("financings").update(
        {
            "status": "repaid",
            "payment_status": "paid",
            "repay_tx_hash": payload.tx_hash,
        }
    ).eq("id", financing_id).execute()

    return {"applied": True, "financing_id": financing_id}


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
        # Race: ProofSubmitted arrived before /agent/funded-webhook ran (or
        # before the token_id is visible). 503 -> Goldsky retries. 2xx here
        # would ACK and permanently drop the proof event — same failure mode
        # the funded/repaid webhooks guard against.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"No financing found for token_id={payload.token_id}; retry later",
        )
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


def _check_webhook_secret(x_webhook_secret: Optional[str]) -> None:
    """Verify the X-Webhook-Secret header.

    Fail-open ONLY when (a) the secret isn't configured AND (b) we are clearly
    in dev mode (mock chain). In real-chain production, an unset secret is a
    misconfiguration that would leave the webhooks anonymous, letting anyone
    forge FundedWithRef/Repaid events and corrupt off-chain state. Fail closed
    instead so a forgotten env var surfaces as 503 on every Goldsky delivery
    instead of silently accepting unsigned traffic.
    """
    settings = get_settings()
    if not settings.webhook_secret:
        if not settings.chain_mock_mode:
            # Production deploy without WEBHOOK_SECRET set. Refuse all traffic.
            logger.error(
                "webhook auth misconfigured: WEBHOOK_SECRET unset in real-chain mode; "
                "rejecting incoming webhook"
            )
            raise Unauthorized("Webhook authentication not configured")
        return  # dev mock mode: accept all
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


@router.post("/funded-webhook")
async def agent_funded_webhook(
    payload: FundedWebhookPayload,
    x_webhook_secret: Optional[str] = Header(default=None),
) -> dict:
    """Apply an incoming ``FundedWithRef`` event: write financings.token_id.

    Goldsky forwards on-chain ``FundedWithRef(tokenId, investor, financingRef, amount)``
    events here. ``financingRef`` is ``keccak256(financing UUID)`` set by the FE at
    fund time, which lets us deterministically map tokenId back to a financing row.

    Idempotent: replays with the same token_id are silently skipped.
    """
    import asyncio
    _check_webhook_secret(x_webhook_secret)
    return await asyncio.to_thread(_apply_funded_webhook, payload)


@router.post("/repaid-webhook")
async def agent_repaid_webhook(
    payload: RepaidWebhookPayload,
    x_webhook_secret: Optional[str] = Header(default=None),
) -> dict:
    """Apply an incoming ``Repaid`` event: flip financing to status=repaid.

    Goldsky forwards on-chain ``Repaid(tokenId, totalPaid, toInvestor)`` events.
    No AI verification is involved — this is supplier-driven settlement. We just
    record the on-chain fact in the off-chain DB.

    Idempotent: replays on an already-repaid financing return ``duplicate=True``.
    """
    import asyncio
    _check_webhook_secret(x_webhook_secret)
    return await asyncio.to_thread(_apply_repaid_webhook, payload)


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
