"""Retry freeze (F1) and auto-default (B1) enforcement.

F1 — Retry Freeze (PRD v3.0 §Milestone Retry Policy):
    After 3 REJECTED AI decisions within a 7-day rolling window for the same
    (financing_id, milestone_idx), the financing is frozen for 48 hours.
    The agent loop checks frozen_until before processing any job.

B1 — Auto-Default (PRD v3.0 §Default Conditions):
    Any financing with due_date + 44 calendar days in the past that is still
    in 'published' or 'in_progress' status is automatically marked defaulted.
    Called by a nightly background task (or cron).

Both functions are sync helpers called from asyncio.to_thread() in the
agent loop to avoid blocking the event loop on Supabase I/O.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.core.db import get_supabase
from app.services.chain import get_chain_client

log = logging.getLogger(__name__)

# F1 constants
F1_WINDOW_DAYS = 7
F1_MAX_REJECTIONS = 3
F1_FREEZE_HOURS = 48

# B1 constant
B1_OVERDUE_DAYS = 44

# Financing statuses that can be auto-defaulted
_DEFAULTABLE_STATUSES = {"published", "in_progress"}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# F1: freeze check + freeze setter
# ---------------------------------------------------------------------------


def is_frozen(financing_id: str) -> tuple[bool, Optional[datetime]]:
    """Return (is_frozen, frozen_until) for a financing.

    Returns (True, frozen_until) if frozen_until > now(), else (False, None).
    """
    sb = get_supabase()
    rows = (
        sb.table("financings")
        .select("frozen_until")
        .eq("id", financing_id)
        .limit(1)
        .execute()
    )
    if not rows.data:
        return False, None

    frozen_until_str: Optional[str] = rows.data[0].get("frozen_until")  # type: ignore[union-attr, assignment]
    if not frozen_until_str:
        return False, None

    frozen_until = datetime.fromisoformat(str(frozen_until_str))
    if frozen_until.tzinfo is None:
        frozen_until = frozen_until.replace(tzinfo=timezone.utc)

    if _now_utc() < frozen_until:
        return True, frozen_until
    return False, None


def check_and_apply_f1_freeze(financing_id: str, milestone_idx: int) -> bool:
    """Count REJECTED decisions in the last 7 days for this (financing_id, milestone_idx).

    If the count reaches F1_MAX_REJECTIONS (3), set financing.frozen_until = now() + 48h.

    Returns True if a freeze was just applied (third rejection crossed the threshold).
    Returns False otherwise (freeze not triggered or already frozen).

    Call this AFTER writing the REJECTED agent_decisions row for the current job.
    """
    sb = get_supabase()
    window_start = (_now_utc() - timedelta(days=F1_WINDOW_DAYS)).isoformat()

    rows = (
        sb.table("agent_decisions")
        .select("id", count="exact")  # type: ignore[call-arg]
        .eq("financing_id", financing_id)
        .eq("milestone_idx", milestone_idx)
        .eq("verdict", "REJECTED")
        .gte("created_at", window_start)
        .execute()
    )

    rejection_count = rows.count or 0
    log.info(
        "F1: financing=%s milestone=%d has %d rejections in last %dd window",
        financing_id, milestone_idx, rejection_count, F1_WINDOW_DAYS,
    )

    if rejection_count >= F1_MAX_REJECTIONS:
        frozen_until = (_now_utc() + timedelta(hours=F1_FREEZE_HOURS)).isoformat()
        sb.table("financings").update({
            "frozen_until": frozen_until,
            "status": "frozen",
        }).eq("id", financing_id).execute()

        log.warning(
            "F1: FREEZE applied — financing=%s frozen until %s "
            "(3 rejections on milestone=%d within 7 days)",
            financing_id, frozen_until, milestone_idx,
        )
        return True

    return False


# ---------------------------------------------------------------------------
# B1: nightly auto-default scan
# ---------------------------------------------------------------------------


def run_auto_default_scan() -> list[str]:
    """B1: scan for overdue financings and mark them defaulted on-chain.

    Finds all financings where:
        status IN ('published', 'in_progress')
        AND due_date + 44 days < now()

    For each match: calls chain.mark_defaulted(financing_id), then flips
    status to 'defaulted' and sets defaulted_at.

    Returns the list of financing_ids that were defaulted in this run.
    """
    import asyncio

    sb = get_supabase()
    cutoff_date = (_now_utc() - timedelta(days=B1_OVERDUE_DAYS)).date().isoformat()

    rows = (
        sb.table("financings")
        .select("id, due_date, status, token_id")
        .in_("status", list(_DEFAULTABLE_STATUSES))
        .lt("due_date", cutoff_date)
        .execute()
    )

    if not rows.data:
        log.info("B1: no overdue financings found (cutoff=%s)", cutoff_date)
        return []

    log.warning("B1: found %d overdue financing(s) to auto-default", len(rows.data))
    defaulted_ids: list[str] = []
    chain = get_chain_client()

    for row in rows.data or []:  # type: ignore[union-attr]
        financing_id: str = str(row["id"])  # type: ignore[index]
        due_date: str = str(row["due_date"])  # type: ignore[index]

        try:
            # Call on-chain first (idempotent: contract ignores double-default).
            chain_result = asyncio.run(chain.mark_defaulted(financing_id))
            log.info(
                "B1: mark_defaulted on-chain OK — financing=%s tx=%s",
                financing_id, chain_result.tx_hash,
            )
        except Exception as exc:
            log.error(
                "B1: mark_defaulted FAILED for financing=%s (due_date=%s): %s",
                financing_id, due_date, exc,
            )
            # Don't flip DB status if chain call failed — retry on next cron run.
            continue

        try:
            sb.table("financings").update({
                "status": "defaulted",
                "defaulted_at": _now_utc().isoformat(),
            }).eq("id", financing_id).execute()
            defaulted_ids.append(financing_id)
            log.warning(
                "B1: financing=%s marked defaulted (was due %s, overdue >%dd)",
                financing_id, due_date, B1_OVERDUE_DAYS,
            )
        except Exception as exc:
            log.error(
                "B1: DB update FAILED for financing=%s after chain default: %s",
                financing_id, exc,
            )

    return defaulted_ids
