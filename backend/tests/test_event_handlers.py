"""Tests for Funded and Repaid event handlers in app.routers.agent.

These webhooks close two real-mode blockers identified in the PRD gap map:

  1. Funded → write financings.token_id by matching on fund_tx_hash. Without
     this, ProofSubmitted webhooks lose token_id -> financing_id lookups and
     the agent loop never fires on Mantle.

  2. Repaid → flip status to 'repaid' + stamp repay_tx_hash. Without this,
     financings stay at 'in_progress' forever after milestones release; the
     'repaid' enum value is dead code.

All Supabase calls mocked — no DB needed.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from app.routers.agent import (
    FundedPayload,
    RepaidPayload,
    _handle_funded,
    _handle_repaid,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sb_mock(select_data: list[dict] | None = None) -> MagicMock:
    """Build a Supabase client mock where .table(...).select(...).eq(...).limit(...).execute()
    returns the given rows, and .update(...).eq(...).execute() is recorded.

    The returned mock has .table.return_value pre-wired so callers can assert
    on update args via update_calls below.
    """
    sb = MagicMock()
    table_q = MagicMock()
    sb.table.return_value = table_q

    # SELECT chain.
    select_q = MagicMock()
    select_q.eq.return_value = select_q
    select_q.limit.return_value = select_q
    select_q.execute.return_value = MagicMock(data=select_data or [])
    table_q.select.return_value = select_q

    # UPDATE chain.
    update_q = MagicMock()
    update_q.eq.return_value = update_q
    update_q.execute.return_value = MagicMock()
    table_q.update.return_value = update_q

    return sb


def _funded(token_id: int = 42, tx_hash: str | None = None) -> FundedPayload:
    return FundedPayload(
        token_id=token_id,
        investor="0x" + "a" * 40,
        amount="14753000000",
        tx_hash=tx_hash or ("0x" + "b" * 64),
        block_number=123,
    )


def _repaid(token_id: int = 42, tx_hash: str | None = None) -> RepaidPayload:
    return RepaidPayload(
        token_id=token_id,
        total_paid="15000000000",
        to_investor="247000000",
        tx_hash=tx_hash or ("0x" + "c" * 64),
        block_number=456,
    )


# ---------------------------------------------------------------------------
# Funded handler
# ---------------------------------------------------------------------------

class TestHandleFunded:
    def test_writes_token_id_when_row_found(self):
        sb = _make_sb_mock(select_data=[{"id": "fin-1", "token_id": None}])
        payload = _funded(token_id=42)

        with patch("app.routers.agent.get_supabase", return_value=sb):
            result = _handle_funded(payload)

        assert result == {"indexed": True, "financing_id": "fin-1", "token_id": "42"}
        # Verify update was called with the right payload.
        update_call = sb.table.return_value.update.call_args
        assert update_call is not None
        assert update_call[0][0] == {"token_id": "42"}

    def test_returns_indexed_false_when_no_row_matches_tx_hash(self):
        """Event arrived before FE bookkeeping POST — Goldsky must retry."""
        sb = _make_sb_mock(select_data=[])

        with patch("app.routers.agent.get_supabase", return_value=sb):
            result = _handle_funded(_funded())

        assert result["indexed"] is False
        assert "No financing found" in result["error"]
        # Critical: must NOT have called update.
        sb.table.return_value.update.assert_not_called()

    def test_idempotent_when_token_id_already_set_to_same_value(self):
        """Replayed event: token_id matches → no update, still indexed=True."""
        sb = _make_sb_mock(select_data=[{"id": "fin-1", "token_id": "42"}])

        with patch("app.routers.agent.get_supabase", return_value=sb):
            result = _handle_funded(_funded(token_id=42))

        assert result == {"indexed": True, "financing_id": "fin-1", "token_id": "42"}
        # No write — already set.
        sb.table.return_value.update.assert_not_called()

    def test_refuses_to_overwrite_conflicting_token_id(self):
        """Same fund_tx_hash mapping to a different token_id is data corruption.
        Surface it instead of silently overwriting."""
        sb = _make_sb_mock(select_data=[{"id": "fin-1", "token_id": "999"}])

        with patch("app.routers.agent.get_supabase", return_value=sb):
            result = _handle_funded(_funded(token_id=42))

        assert result["indexed"] is False
        assert "conflict" in result["error"].lower()
        sb.table.return_value.update.assert_not_called()


# ---------------------------------------------------------------------------
# Repaid handler
# ---------------------------------------------------------------------------

class TestHandleRepaid:
    def test_flips_status_to_repaid(self):
        sb = _make_sb_mock(select_data=[
            {"id": "fin-1", "status": "in_progress", "repay_tx_hash": None},
        ])
        payload = _repaid()

        with patch("app.routers.agent.get_supabase", return_value=sb):
            result = _handle_repaid(payload)

        assert result == {"indexed": True, "financing_id": "fin-1", "status": "repaid"}
        update_call = sb.table.return_value.update.call_args
        assert update_call[0][0] == {
            "status": "repaid",
            "repay_tx_hash": payload.tx_hash,
        }

    def test_returns_indexed_false_when_token_id_not_found(self):
        sb = _make_sb_mock(select_data=[])

        with patch("app.routers.agent.get_supabase", return_value=sb):
            result = _handle_repaid(_repaid())

        assert result["indexed"] is False
        assert "No financing found" in result["error"]
        sb.table.return_value.update.assert_not_called()

    def test_idempotent_replay_returns_noop(self):
        """Already-repaid with same tx_hash → noop, no update."""
        tx = "0x" + "c" * 64
        sb = _make_sb_mock(select_data=[
            {"id": "fin-1", "status": "repaid", "repay_tx_hash": tx},
        ])

        with patch("app.routers.agent.get_supabase", return_value=sb):
            result = _handle_repaid(_repaid(tx_hash=tx))

        assert result == {"indexed": True, "financing_id": "fin-1", "noop": True}
        sb.table.return_value.update.assert_not_called()

    def test_refuses_to_overwrite_defaulted_status(self):
        """A Repaid event arriving for a defaulted financing is a contract bug —
        don't auto-resolve, surface it."""
        sb = _make_sb_mock(select_data=[
            {"id": "fin-1", "status": "defaulted", "repay_tx_hash": None},
        ])

        with patch("app.routers.agent.get_supabase", return_value=sb):
            result = _handle_repaid(_repaid())

        assert result["indexed"] is False
        assert "defaulted" in result["error"]
        sb.table.return_value.update.assert_not_called()

    def test_refuses_to_overwrite_blacklisted_status(self):
        sb = _make_sb_mock(select_data=[
            {"id": "fin-1", "status": "blacklisted", "repay_tx_hash": None},
        ])

        with patch("app.routers.agent.get_supabase", return_value=sb):
            result = _handle_repaid(_repaid())

        assert result["indexed"] is False
        assert "blacklisted" in result["error"]
        sb.table.return_value.update.assert_not_called()
