"""Tests for pure pricing logic — no DB, no network."""

from __future__ import annotations

from decimal import Decimal

import pytest

from app.services.pricing import advance_rate_for, price, tier_for


def test_tier_thresholds():
    assert tier_for(95) == "low"
    assert tier_for(80) == "low"
    assert tier_for(79) == "medium"
    assert tier_for(60) == "medium"
    assert tier_for(59) == "high"
    assert tier_for(40) == "high"
    assert tier_for(39) == "reject"


def test_advance_rate_invoice_always_100():
    for tier in ("low", "medium", "high"):
        assert advance_rate_for("invoice", tier) == 100  # type: ignore[arg-type]


def test_advance_rate_po_varies_by_tier():
    assert advance_rate_for("po", "low") == 80
    assert advance_rate_for("po", "medium") == 75
    assert advance_rate_for("po", "high") == 70


def test_price_invoice_low_risk():
    # $15,000 invoice, 60-day tenor, low risk.
    p = price("invoice", risk_score=85, nominal=Decimal("15000"), tenor_days=60)
    assert p.advance_rate == 100
    assert p.yield_rate == Decimal("0.07")
    assert p.funding_amount < Decimal("15000")
    assert p.expected_yield_amount > 0
    assert p.platform_fee > 0
    assert p.total_repayment == p.funding_amount + p.expected_yield_amount


def test_price_po_medium_risk():
    # $10K PO, 90d tenor, medium risk.
    p = price("po", risk_score=65, nominal=Decimal("10000"), tenor_days=90)
    assert p.advance_rate == 75
    # Face value funded = 7500
    assert p.funding_amount < Decimal("7500")
    assert p.yield_rate == Decimal("0.12")


def test_price_rejected_raises():
    with pytest.raises(ValueError):
        price("invoice", risk_score=30, nominal=Decimal("1000"), tenor_days=60)
