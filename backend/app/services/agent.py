"""Agent loop — autonomous AI verification worker.

Architecture:
    On-chain ProofSubmitted event
        → Goldsky webhook → POST /agent/webhook
        → row inserted into agent_queue
        → background asyncio task picks it up
        → Claude Vision AI pipeline runs
        → AI Verifier wallet calls releaseMilestone() on-chain
        → agent_decisions row written
        → milestone row updated

The loop runs as an asyncio background task started at app startup.
No Celery, no Redis — single-process for MVP simplicity.

For multi-instance safety: optimistic lock via `locked_at` + `locked_by`.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Optional

from app.core.db import get_supabase
from app.services.chain import (
    MilestoneAlreadyReleasedError,
    PriorMilestoneNotReleasedError,
)

log = logging.getLogger(__name__)

# Interval between queue polls (seconds)
POLL_INTERVAL = 5

# Max age of a lock before it is considered stale (seconds)
LOCK_TIMEOUT = 120

# Worker instance id — unique per process
WORKER_ID = f"worker-{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# Repo helpers (agent-specific, not in repos.py to keep separation clean)
# ---------------------------------------------------------------------------


MAX_ATTEMPTS = 3


def _claim_next_pending() -> Optional[dict]:
    """Atomically claim the oldest pending job. Returns the row or None."""
    sb = get_supabase()
    now_iso = _now_iso()

    # Release stale locks first (previous worker crashed).
    from datetime import datetime, timedelta, timezone
    stale_cutoff = (
        datetime.now(timezone.utc) - timedelta(seconds=LOCK_TIMEOUT)
    ).isoformat()

    # Jobs that exhausted attempts → failed. Others → back to pending.
    sb.table("agent_queue").update({
        "status": "failed",
        "locked_at": None,
        "locked_by": None,
        "error_message": "Stale lock: exceeded max attempts",
    }).eq("status", "processing").lt("locked_at", stale_cutoff).gte("attempt_count", MAX_ATTEMPTS).execute()

    sb.table("agent_queue").update(
        {"status": "pending", "locked_at": None, "locked_by": None}
    ).eq("status", "processing").lt("locked_at", stale_cutoff).lt("attempt_count", MAX_ATTEMPTS).execute()

    # Fetch the oldest pending item that hasn't exceeded max attempts.
    rows = (
        sb.table("agent_queue")
        .select("*")
        .eq("status", "pending")
        .lt("attempt_count", MAX_ATTEMPTS)
        .order("created_at", desc=False)
        .limit(1)
        .execute()
    )
    if not rows.data:
        return None

    row: dict = rows.data[0]  # type: ignore[assignment]

    # Optimistic lock: update to processing only if still pending.
    result = (
        sb.table("agent_queue")
        .update({
            "status": "processing",
            "locked_at": now_iso,
            "locked_by": WORKER_ID,
            "attempt_count": int(row["attempt_count"]) + 1,
        })
        .eq("id", row["id"])
        .eq("status", "pending")  # guard
        .execute()
    )
    if not result.data:
        return None  # lost the race

    return result.data[0]  # type: ignore[return-value]


def _mark_done(queue_id: str) -> None:
    get_supabase().table("agent_queue").update(
        {"status": "done", "locked_at": None, "locked_by": None}
    ).eq("id", queue_id).execute()


def _mark_failed(queue_id: str, error: str) -> None:
    get_supabase().table("agent_queue").update({
        "status": "failed",
        "locked_at": None,
        "locked_by": None,
        "error_message": error[:2000],
    }).eq("id", queue_id).execute()


def _requeue_job(queue_id: str, reason: str) -> None:
    """Reset a job back to pending without burning an attempt_count slot.

    Used for retriable out-of-order milestone errors — the job will be picked
    up again on the next poll cycle after the prior milestone is released.
    """
    get_supabase().table("agent_queue").update({
        "status": "pending",
        "locked_at": None,
        "locked_by": None,
        "attempt_count": 0,  # reset so it doesn't exhaust MAX_ATTEMPTS
        "error_message": f"Requeued: {reason[:1900]}",
    }).eq("id", queue_id).execute()


def _get_existing_decision(queue_id: str) -> Optional[dict]:
    """Return the latest decision row for this queue job, or None.

    Used by the idempotency guard in _process_job to detect jobs that were
    processed in a prior run but crashed before _mark_done completed.
    """
    rows = (
        get_supabase()
        .table("agent_decisions")
        .select("id, verdict, tx_hash")
        .eq("queue_id", queue_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    return rows.data[0] if rows.data else None  # type: ignore[return-value]


def _write_decision(
    queue_id: str,
    financing_id: str,
    milestone_idx: int,
    verdict: str,
    confidence: float,
    checks: dict,
    fail_reasons: list,
    display_message: str,
    tx_hash: Optional[str],
    block_number: Optional[int],
    ai_latency_ms: int,
) -> None:
    get_supabase().table("agent_decisions").insert({
        "queue_id": queue_id,
        "financing_id": financing_id,
        "milestone_idx": milestone_idx,
        "verdict": verdict,
        "confidence": confidence,
        "checks": checks,
        "fail_reasons": fail_reasons,
        "display_message": display_message,
        "tx_hash": tx_hash,
        "block_number": block_number,
        "ai_latency_ms": ai_latency_ms,
    }).execute()


def _get_milestone(financing_id: str, milestone_idx: int) -> Optional[dict]:
    sb = get_supabase()
    rows = (
        sb.table("milestones")
        .select("id, status, release_tx_hash")
        .eq("financing_id", financing_id)
        .eq("idx", milestone_idx)
        .limit(1)
        .execute()
    )
    return rows.data[0] if rows.data else None  # type: ignore[return-value]


def _update_milestone_status(financing_id: str, milestone_idx: int, patch: dict) -> None:
    sb = get_supabase()
    rows = (
        sb.table("milestones")
        .select("id")
        .eq("financing_id", financing_id)
        .eq("idx", milestone_idx)
        .limit(1)
        .execute()
    )
    if not rows.data:
        raise ValueError(
            f"Milestone row not found: financing_id={financing_id} idx={milestone_idx}"
        )
    sb.table("milestones").update(patch).eq("id", rows.data[0]["id"]).execute()


def _get_financing(financing_id: str) -> Optional[dict]:
    sb = get_supabase()
    rows = (
        sb.table("financings")
        .select("*, documents(*)")
        .eq("id", financing_id)
        .limit(1)
        .execute()
    )
    return rows.data[0] if rows.data else None


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Core processing logic
# ---------------------------------------------------------------------------


async def _release_on_chain_with_recovery(
    queue_id: str, financing_id: str, milestone_idx: int
) -> tuple[Optional[str], Optional[int]]:
    """Call releaseMilestone() on-chain, with full crash-and-revert recovery.

    Three success paths, all return (tx_hash, block_number):
      1. release_tx_hash already in DB (prior run mined + persisted) → skip chain call
      2. Chain call succeeds → persist tx_hash, return result
      3. Chain reverts MilestoneAlreadyReleased (prior run mined but crashed before
         persisting tx_hash) → look up the historical event, persist + return it

    PriorMilestoneNotReleasedError propagates out — the agent layer requeues for
    later retry once the prior milestone clears.
    """
    from app.services.chain import get_chain_client
    chain = get_chain_client()

    # 1. Already persisted by a prior run — reuse it.
    existing_milestone = await asyncio.to_thread(
        _get_milestone, financing_id, milestone_idx
    )
    if existing_milestone and existing_milestone.get("release_tx_hash"):
        tx_hash = existing_milestone["release_tx_hash"]
        log.warning(
            "agent: job %s milestone already has release_tx_hash=%s — skipping chain call",
            queue_id, tx_hash,
        )
        return tx_hash, None

    # 2. Try the chain call.
    try:
        chain_result = await chain.release_milestone(financing_id, milestone_idx)
    except MilestoneAlreadyReleasedError as exc:
        # 3. Already released on-chain but tx_hash never persisted (prior crash
        #    between mine and DB write). Recover the tx_hash from event logs.
        log.warning(
            "agent: job %s hit MilestoneAlreadyReleased — recovering tx_hash from chain: %s",
            queue_id, exc,
        )
        recovered = await chain.find_milestone_released_tx(financing_id, milestone_idx)
        if not recovered:
            # Couldn't find the event — shouldn't happen but escalate as hard failure.
            raise RuntimeError(
                f"MilestoneAlreadyReleased on-chain but no MilestoneReleased event found "
                f"for financing={financing_id} milestone={milestone_idx} — manual review needed"
            )
        chain_result = recovered

    await asyncio.to_thread(_update_milestone_status, financing_id, milestone_idx, {
        "release_tx_hash": chain_result.tx_hash,
    })
    return chain_result.tx_hash, chain_result.block_number


async def _process_job(job: dict) -> None:
    """Process a single agent_queue row end-to-end."""
    queue_id = job["id"]
    financing_id = job["financing_id"]
    milestone_idx = job["milestone_idx"]
    ipfs_hash = job["ipfs_hash"]

    log.info(
        "agent: processing job %s financing=%s milestone=%d",
        queue_id, financing_id, milestone_idx,
    )

    # M1 is auto-released by FundingPool.fund() at investor funding time.
    # The agent must never call releaseMilestone(tokenId, 1) — it would revert
    # with MilestoneAlreadyReleased. If a webhook arrives for M1 (e.g. replayed
    # Goldsky event), sync the DB milestone status and mark done.
    if milestone_idx == 1:
        log.info(
            "agent: job %s is M1 (auto-released at fund time) — syncing DB and marking done",
            queue_id,
        )
        # M1 was released on-chain by fund() but the DB row may still be 'pending'.
        # Sync it so _advance_financing_status can correctly detect when all
        # milestones are released and transition financing → 'repaid'.
        try:
            await asyncio.to_thread(_update_milestone_status, financing_id, milestone_idx, {
                "status": "released",
                "released_at": _now_iso(),
            })
        except ValueError:
            log.warning("agent: job %s M1 milestone row not found — skipping DB sync", queue_id)
        await asyncio.to_thread(_mark_done, queue_id)
        return

    try:
        # 0. Idempotency guards (run in this order):
        #
        #    (a) If a prior run already wrote an agent_decisions row, just mark done.
        #        Covers: crash between _write_decision and _mark_done.
        existing = await asyncio.to_thread(_get_existing_decision, queue_id)
        if existing:
            log.warning(
                "agent: job %s already has decision (prior run crashed) — marking done",
                queue_id,
            )
            await asyncio.to_thread(_mark_done, queue_id)
            return

        #    (b) If the milestone already has release_tx_hash set, a prior run
        #        successfully released funds on-chain but crashed before writing
        #        the audit row. Re-running the AI would risk a different verdict
        #        (REJECTED) being persisted while funds are already out.
        #        Write a recovery decision matching the on-chain truth and exit.
        pre_milestone = await asyncio.to_thread(_get_milestone, financing_id, milestone_idx)
        if pre_milestone and pre_milestone.get("release_tx_hash"):
            recovered_tx = pre_milestone["release_tx_hash"]
            log.warning(
                "agent: job %s milestone has release_tx_hash=%s but no audit row — "
                "writing recovery decision and marking done",
                queue_id, recovered_tx,
            )
            await asyncio.to_thread(
                _write_decision,
                queue_id, financing_id, milestone_idx,
                "APPROVED",  # on-chain release implies prior approval
                1.0,         # placeholder — actual confidence lost with the crash
                {"recovered": True, "reason": "release_tx_hash set without audit row"},
                [],
                "Recovered from crash — prior run released funds before writing audit",
                recovered_tx,
                None,
                0,
            )
            await asyncio.to_thread(
                _update_milestone_status, financing_id, milestone_idx,
                {"status": "released"},
            )
            await asyncio.to_thread(_advance_financing_status, financing_id)
            await asyncio.to_thread(_mark_done, queue_id)
            return

        # 1. Fetch proof file from IPFS.
        from app.services.ipfs import get_ipfs_client
        ipfs = get_ipfs_client()
        t0 = time.monotonic()
        file_bytes = await ipfs.fetch_bytes(ipfs_hash)

        # 2. Fetch financing metadata for context.
        financing = await asyncio.to_thread(_get_financing, financing_id)
        if not financing:
            raise ValueError(f"Financing {financing_id} not found")

        # documents is a list from Supabase join (select "*, documents(*)")
        doc_list = financing.get("documents") or []
        doc = doc_list[0] if isinstance(doc_list, list) and doc_list else (doc_list if isinstance(doc_list, dict) else {})
        financing_meta = {
            "product_type": financing["product_type"],
            "issuer_name": doc.get("issuer_name", ""),
            "buyer_name": doc.get("buyer_name", ""),
            "total_amount": str(financing["amount"]),
            "due_date": str(financing["due_date"]),
        }

        # 3. Mark milestone as processing.
        await asyncio.to_thread(
            _update_milestone_status,
            financing_id, milestone_idx,
            {"status": "proof_uploaded"},
        )

        # 4. Run AI milestone verifier.
        from app.services.ai_verifier import get_ai_verifier
        verifier = get_ai_verifier()
        ai_result = await verifier.verify_milestone(
            file_bytes=file_bytes,
            milestone_idx=milestone_idx,
            product_type=financing["product_type"],
            financing_meta=financing_meta,
        )
        ai_latency_ms = int((time.monotonic() - t0) * 1000)

        verdict = ai_result.verdict
        confidence = ai_result.confidence

        log.info(
            "agent: AI verdict=%s confidence=%.3f financing=%s milestone=%d",
            verdict, confidence, financing_id, milestone_idx,
        )

        # 5. Act on verdict.
        tx_hash: Optional[str] = None
        block_number: Optional[int] = None

        if verdict == "APPROVED" and confidence >= 0.75:
            # Write DB state first so a crash after the chain call but before
            # _write_decision doesn't cause a double-release on retry.
            await asyncio.to_thread(_update_milestone_status, financing_id, milestone_idx, {
                "status": "released",
                "released_at": _now_iso(),
                "ai_verification": {
                    "verdict": verdict,
                    "confidence": confidence,
                    "checks": ai_result.checks,
                },
            })
            tx_hash, block_number = await _release_on_chain_with_recovery(
                queue_id, financing_id, milestone_idx,
            )
            await asyncio.to_thread(_advance_financing_status, financing_id)

        elif verdict == "APPROVED" and confidence >= 0.50:
            # Release with low-confidence flag.
            await asyncio.to_thread(_update_milestone_status, financing_id, milestone_idx, {
                "status": "released",
                "released_at": _now_iso(),
                "ai_verification": {
                    "verdict": verdict,
                    "confidence": confidence,
                    "checks": ai_result.checks,
                    "flagged": True,
                },
            })
            tx_hash, block_number = await _release_on_chain_with_recovery(
                queue_id, financing_id, milestone_idx,
            )
            await asyncio.to_thread(_advance_financing_status, financing_id)

        elif verdict == "REJECTED" and confidence >= 0.30:
            # Clear rejection — supplier can retry.
            await asyncio.to_thread(_update_milestone_status, financing_id, milestone_idx, {
                "status": "rejected",
                "ai_verification": {
                    "verdict": verdict,
                    "confidence": confidence,
                    "fail_reasons": ai_result.fail_reasons,
                },
            })

        else:
            # APPROVED with confidence < 0.50 (model hedging), or any verdict
            # with confidence < 0.30 — escalate for human review.
            await asyncio.to_thread(_update_milestone_status, financing_id, milestone_idx, {
                "status": "escalated",
                "ai_verification": {
                    "verdict": verdict,
                    "confidence": confidence,
                    "fail_reasons": ai_result.fail_reasons,
                },
            })
            verdict = "ESCALATED"

        # 6. Write audit decision.
        await asyncio.to_thread(
            _write_decision,
            queue_id,
            financing_id,
            milestone_idx,
            verdict,
            confidence,
            ai_result.checks,
            ai_result.fail_reasons,
            ai_result.display_message,
            tx_hash,
            block_number,
            ai_latency_ms,
        )

        await asyncio.to_thread(_mark_done, queue_id)
        log.info("agent: job %s done", queue_id)

    except PriorMilestoneNotReleasedError as exc:
        # Out-of-order webhook — prior milestone not yet released on-chain.
        # Reset to pending (decrement attempt_count so we don't burn retries) and
        # let the loop pick it up again after the prior milestone clears.
        log.warning(
            "agent: job %s PriorMilestoneNotReleased — re-queuing (attempt_count decremented)",
            queue_id,
        )
        await asyncio.to_thread(_requeue_job, queue_id, str(exc))

    except Exception as exc:
        log.exception("agent: job %s failed: %s", queue_id, exc)
        await asyncio.to_thread(_mark_failed, queue_id, str(exc))


_TERMINAL_FINANCING_STATUSES = {"defaulted", "blacklisted", "frozen"}


def _advance_financing_status(financing_id: str) -> None:
    """After a milestone release, check if all milestones are released and update status.

    Never overrides terminal or frozen statuses (defaulted, blacklisted, frozen).
    A replayed Goldsky event on a defaulted deal must not resurrect it.
    """
    sb = get_supabase()

    # Guard: don't touch terminal statuses.
    financing_rows = (
        sb.table("financings")
        .select("status")
        .eq("id", financing_id)
        .limit(1)
        .execute()
    ).data or []
    if not financing_rows:
        return
    current_status: str = str((financing_rows[0] or {}).get("status", ""))  # type: ignore[union-attr]
    if current_status in _TERMINAL_FINANCING_STATUSES:
        log.warning(
            "agent: _advance_financing_status skipped — financing %s is in terminal state %s",
            financing_id, current_status,
        )
        return

    milestones = (
        sb.table("milestones")
        .select("status, idx")
        .eq("financing_id", financing_id)
        .execute()
    ).data or []

    if not milestones:
        return

    all_released = all(m["status"] == "released" for m in milestones)
    any_released = any(m["status"] == "released" for m in milestones)

    if all_released:
        sb.table("financings").update({"status": "repaid"}).eq("id", financing_id).execute()
    elif any_released:
        sb.table("financings").update({"status": "in_progress"}).eq("id", financing_id).execute()


# ---------------------------------------------------------------------------
# Background loop
# ---------------------------------------------------------------------------


async def agent_loop() -> None:
    """Long-running asyncio task. Polls agent_queue and processes jobs."""
    log.info("agent: worker %s started", WORKER_ID)
    while True:
        try:
            job = await asyncio.to_thread(_claim_next_pending)
            if job:
                await _process_job(job)
            else:
                await asyncio.sleep(POLL_INTERVAL)
        except asyncio.CancelledError:
            log.info("agent: worker %s shutting down", WORKER_ID)
            break
        except Exception as exc:
            log.exception("agent: unexpected error in loop: %s", exc)
            await asyncio.sleep(POLL_INTERVAL)
