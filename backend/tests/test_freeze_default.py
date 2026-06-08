"""Tests for F1 retry freeze and B1 auto-default (PRD v3.0 §Milestone Retry Policy,
§Default Conditions).

All Supabase calls are mocked so no network needed.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.freeze_default import (
    B1_OVERDUE_DAYS,
    F1_FREEZE_HOURS,
    F1_MAX_REJECTIONS,
    F1_WINDOW_DAYS,
    check_and_apply_f1_freeze,
    is_frozen,
    run_auto_default_scan,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _now() -> datetime:
    return datetime.now(timezone.utc)


def _make_sb_mock(rows_data: Any = None, count: Any = None) -> MagicMock:
    """Return a mock Supabase client whose chained .select(...).eq(...).execute()
    returns a response with .data and .count set to the given values."""
    response = MagicMock()
    response.data = rows_data
    response.count = count
    query = MagicMock()
    query.select.return_value = query
    query.eq.return_value = query
    query.gte.return_value = query
    query.lt.return_value = query
    query.in_.return_value = query
    query.limit.return_value = query
    query.update.return_value = query
    query.execute.return_value = response
    sb = MagicMock()
    sb.table.return_value = query
    return sb


# ---------------------------------------------------------------------------
# is_frozen
# ---------------------------------------------------------------------------

class TestIsFrozen:
    def test_no_rows_returns_not_frozen(self):
        sb = _make_sb_mock(rows_data=[])
        with patch("app.services.freeze_default.get_supabase", return_value=sb):
            frozen, until = is_frozen("financing-abc")
        assert frozen is False
        assert until is None

    def test_frozen_until_null_returns_not_frozen(self):
        sb = _make_sb_mock(rows_data=[{"frozen_until": None}])
        with patch("app.services.freeze_default.get_supabase", return_value=sb):
            frozen, until = is_frozen("financing-abc")
        assert frozen is False
        assert until is None

    def test_frozen_until_in_future_returns_frozen(self):
        future = (_now() + timedelta(hours=24)).isoformat()
        sb = _make_sb_mock(rows_data=[{"frozen_until": future}])
        with patch("app.services.freeze_default.get_supabase", return_value=sb):
            frozen, until = is_frozen("financing-abc")
        assert frozen is True
        assert until is not None
        assert until > _now()

    def test_frozen_until_in_past_returns_not_frozen(self):
        past = (_now() - timedelta(hours=1)).isoformat()
        sb = _make_sb_mock(rows_data=[{"frozen_until": past}])
        with patch("app.services.freeze_default.get_supabase", return_value=sb):
            frozen, until = is_frozen("financing-abc")
        assert frozen is False
        assert until is None


# ---------------------------------------------------------------------------
# check_and_apply_f1_freeze
# ---------------------------------------------------------------------------

class TestF1Freeze:
    """PRD rule: 3 REJECTs within 7 days on same (financing_id, milestone_idx) → 48h freeze."""

    def test_constants_match_prd(self):
        assert F1_MAX_REJECTIONS == 3
        assert F1_WINDOW_DAYS == 7
        assert F1_FREEZE_HOURS == 48

    def test_below_threshold_no_freeze(self):
        """2 rejections in window → no freeze."""
        sb = _make_sb_mock(rows_data=[], count=2)
        with patch("app.services.freeze_default.get_supabase", return_value=sb):
            applied = check_and_apply_f1_freeze("f1", 2)
        assert applied is False
        # Should NOT have called update (no freeze)
        sb.table.return_value.update.assert_not_called()

    def test_at_threshold_freeze_applied(self):
        """Exactly 3 rejections → freeze is applied."""
        sb = _make_sb_mock(rows_data=[], count=3)
        # We need a second sb for the update call
        update_query = MagicMock()
        update_query.eq.return_value = update_query
        update_query.execute.return_value = MagicMock()
        sb.table.return_value.update.return_value = update_query

        with patch("app.services.freeze_default.get_supabase", return_value=sb):
            applied = check_and_apply_f1_freeze("f1", 2)

        assert applied is True
        # Must have called update with status=frozen
        sb.table.return_value.update.assert_called_once()
        call_kwargs = sb.table.return_value.update.call_args[0][0]
        assert call_kwargs["status"] == "frozen"
        assert "frozen_until" in call_kwargs

    def test_above_threshold_freeze_applied(self):
        """5 rejections (already frozen then unfrozen) → freeze re-applied."""
        sb = _make_sb_mock(rows_data=[], count=5)
        update_query = MagicMock()
        update_query.eq.return_value = update_query
        update_query.execute.return_value = MagicMock()
        sb.table.return_value.update.return_value = update_query

        with patch("app.services.freeze_default.get_supabase", return_value=sb):
            applied = check_and_apply_f1_freeze("f1", 2)

        assert applied is True

    def test_freeze_window_is_48h(self):
        """frozen_until must be approximately now() + 48h."""
        before = _now()
        sb = _make_sb_mock(rows_data=[], count=3)
        captured_update: dict = {}

        def _capture_update(patch_dict):
            captured_update.update(patch_dict)
            q = MagicMock()
            q.eq.return_value = q
            q.execute.return_value = MagicMock()
            return q

        sb.table.return_value.update.side_effect = _capture_update

        with patch("app.services.freeze_default.get_supabase", return_value=sb):
            check_and_apply_f1_freeze("f1", 1)

        after = _now()
        frozen_until = datetime.fromisoformat(captured_update["frozen_until"])
        if frozen_until.tzinfo is None:
            frozen_until = frozen_until.replace(tzinfo=timezone.utc)

        expected_min = before + timedelta(hours=F1_FREEZE_HOURS) - timedelta(seconds=2)
        expected_max = after + timedelta(hours=F1_FREEZE_HOURS) + timedelta(seconds=2)
        assert expected_min <= frozen_until <= expected_max, (
            f"frozen_until={frozen_until} not within expected 48h window"
        )


# ---------------------------------------------------------------------------
# B1: run_auto_default_scan
# ---------------------------------------------------------------------------

class TestB1AutoDefault:
    """PRD rule: due_date + 44 calendar days overdue → mark_defaulted() on-chain + DB."""

    def test_constant_matches_prd(self):
        assert B1_OVERDUE_DAYS == 44

    def test_no_overdue_returns_empty(self):
        sb = _make_sb_mock(rows_data=[])
        with patch("app.services.freeze_default.get_supabase", return_value=sb):
            result = run_auto_default_scan()
        assert result == []

    def test_overdue_financing_gets_defaulted(self):
        """One overdue financing → mark_defaulted called, DB updated, ID returned."""
        import asyncio

        financing_id = "fin-overdue-001"
        due_date = (datetime.now(timezone.utc) - timedelta(days=50)).date().isoformat()

        sb = _make_sb_mock(rows_data=[{
            "id": financing_id,
            "due_date": due_date,
            "status": "in_progress",
            "token_id": 42,
        }])
        # The update call chain
        update_q = MagicMock()
        update_q.eq.return_value = update_q
        update_q.execute.return_value = MagicMock()
        sb.table.return_value.update.return_value = update_q

        mock_chain = MagicMock()
        from app.services.chain import ChainResult
        # Use AsyncMock for async method
        from unittest.mock import AsyncMock
        mock_chain.mark_defaulted = AsyncMock(
            return_value=ChainResult(tx_hash="0xdeadbeef")
        )

        with (
            patch("app.services.freeze_default.get_supabase", return_value=sb),
            patch("app.services.freeze_default.get_chain_client", return_value=mock_chain),
        ):
            result = run_auto_default_scan()

        assert financing_id in result
        mock_chain.mark_defaulted.assert_called_once_with(financing_id)

        # DB update must have set status=defaulted
        sb.table.return_value.update.assert_called_once()
        update_dict = sb.table.return_value.update.call_args[0][0]
        assert update_dict["status"] == "defaulted"
        assert "defaulted_at" in update_dict

    def test_chain_failure_does_not_update_db(self):
        """If mark_defaulted() raises, DB must NOT be updated."""
        from unittest.mock import AsyncMock

        financing_id = "fin-overdue-002"
        due_date = (datetime.now(timezone.utc) - timedelta(days=50)).date().isoformat()

        sb = _make_sb_mock(rows_data=[{
            "id": financing_id,
            "due_date": due_date,
            "status": "in_progress",
            "token_id": 99,
        }])

        mock_chain = MagicMock()
        mock_chain.mark_defaulted = AsyncMock(side_effect=RuntimeError("RPC down"))

        with (
            patch("app.services.freeze_default.get_supabase", return_value=sb),
            patch("app.services.freeze_default.get_chain_client", return_value=mock_chain),
        ):
            result = run_auto_default_scan()

        # Should return empty (no successful defaults)
        assert financing_id not in result
        # DB update must NOT have been called
        sb.table.return_value.update.assert_not_called()

    def test_multiple_overdue_financings(self):
        """Multiple overdue deals → all defaulted."""
        from unittest.mock import AsyncMock
        from app.services.chain import ChainResult

        ids = ["fin-a", "fin-b", "fin-c"]
        due_date = (datetime.now(timezone.utc) - timedelta(days=60)).date().isoformat()
        rows = [
            {"id": fid, "due_date": due_date, "status": "in_progress", "token_id": i}
            for i, fid in enumerate(ids, 1)
        ]

        sb = _make_sb_mock(rows_data=rows)
        update_q = MagicMock()
        update_q.eq.return_value = update_q
        update_q.execute.return_value = MagicMock()
        sb.table.return_value.update.return_value = update_q

        mock_chain = MagicMock()
        mock_chain.mark_defaulted = AsyncMock(
            return_value=ChainResult(tx_hash="0xabc")
        )

        with (
            patch("app.services.freeze_default.get_supabase", return_value=sb),
            patch("app.services.freeze_default.get_chain_client", return_value=mock_chain),
        ):
            result = run_auto_default_scan()

        assert set(result) == set(ids)
        assert mock_chain.mark_defaulted.call_count == 3
