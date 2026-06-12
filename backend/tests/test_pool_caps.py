"""Tests for pool cap enforcement (PRD v3.0 §Risk Tiers — Pool Caps)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.pricing import POOL_CAP, pool_cap_for, price, tier_for


# ---------------------------------------------------------------------------
# POOL_CAP table correctness
# ---------------------------------------------------------------------------

def test_low_risk_invoice_no_cap():
    assert pool_cap_for("invoice", "low") is None


def test_low_risk_po_no_cap():
    assert pool_cap_for("po", "low") is None


def test_medium_invoice_cap_10k():
    cap = pool_cap_for("invoice", "medium")
    assert cap == Decimal("10000")


def test_medium_po_cap_7500():
    """PO medium cap is tighter than invoice medium per PRD."""
    cap = pool_cap_for("po", "medium")
    assert cap == Decimal("7500")


def test_high_invoice_cap_5k():
    cap = pool_cap_for("invoice", "high")
    assert cap == Decimal("5000")


def test_high_po_cap_5k():
    cap = pool_cap_for("po", "high")
    assert cap == Decimal("5000")


def test_medium_po_tighter_than_medium_invoice():
    """Medium PO cap must be strictly less than medium invoice cap."""
    po_cap = pool_cap_for("po", "medium")
    inv_cap = pool_cap_for("invoice", "medium")
    assert po_cap is not None and inv_cap is not None
    assert po_cap < inv_cap


def test_pool_cap_covers_all_non_reject_combinations():
    """Every (product_type, tier) pair except reject must be in POOL_CAP."""
    for pt in ("invoice", "po"):
        for tier in ("low", "medium", "high"):
            # KeyError here means the table is incomplete.
            _ = POOL_CAP[(pt, tier)]


def test_unknown_tier_raises():
    with pytest.raises(KeyError):
        pool_cap_for("invoice", "reject")


# ---------------------------------------------------------------------------
# Integration: price() + pool_cap_for() roundtrip
# ---------------------------------------------------------------------------

def test_medium_invoice_below_cap_accepted():
    """A medium-risk invoice with nominal=8000 (face=8000, 100% advance)
    must be below the 10000 cap."""
    nominal = Decimal("8000")
    p = price("invoice", 65, nominal, 90)  # score=65 -> medium
    cap = pool_cap_for("invoice", tier_for(65))
    assert cap is not None
    assert p.funding_amount <= cap


def test_medium_invoice_above_cap_detectable():
    """nominal=12000 -> face=12000 (invoice 100% advance) > 10000 cap.
    The caller (documents router) should detect this and reject."""
    nominal = Decimal("12000")
    tier = tier_for(65)  # medium
    advance_rate = 100   # invoice always 100%
    face = nominal * Decimal(advance_rate) / Decimal(100)
    cap = pool_cap_for("invoice", tier)
    assert cap is not None
    assert face > cap, f"Expected face {face} > cap {cap}"


def test_medium_po_above_cap_detectable():
    """nominal=10000, PO medium advance=75% -> face=7500.
    7500 is NOT > 7500, so it should just barely pass.
    nominal=10001 -> face=7500.75 which exceeds 7500."""
    tier = tier_for(65)  # medium
    advance_rate = 75    # PO medium
    cap = pool_cap_for("po", tier)
    assert cap is not None

    nominal_at_limit = Decimal("10000")
    face_at_limit = nominal_at_limit * Decimal(advance_rate) / Decimal(100)
    assert face_at_limit <= cap  # exactly at cap — should pass

    nominal_over = Decimal("10001")
    face_over = nominal_over * Decimal(advance_rate) / Decimal(100)
    assert face_over > cap   # just over — should be rejected


def test_high_risk_cap_applies_to_both_products():
    """Both invoice and PO at high risk share the 5000 cap."""
    for pt in ("invoice", "po"):
        cap = pool_cap_for(pt, "high")
        assert cap == Decimal("5000"), f"{pt} high cap should be 5000"
