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


def test_origination_fee_matches_contract_on_funding_amount():
    """Origination fee MUST equal FundingPool.fund()'s on-chain computation:
    `origination = fundedAmount × 1.5%`.

    PRD v3.0 numeric example claims origination=$225 on a $15K invoice,
    but that's internally inconsistent — 1.5% × $14,753 funding_amount =
    $221.30, not $225. The PRD prose says "deducted from funded_amount",
    which matches the contract at contracts/src/FundingPool.sol:135.

    The contract is the source of truth — if the BE displays $225 while
    the chain transfers $221.30 to treasury, the supplier's net would
    be over-promised by ~$4 per $15K deal. Demo would surface the diff.
    """
    p = price("invoice", risk_score=73, nominal=Decimal("15000"), tenor_days=60)
    # face = $15,000; discount = 10% × 60/365 = 1.644%; funding ~ $14,753.40
    expected_origination = (
        p.funding_amount * Decimal("0.015")
    ).quantize(Decimal("0.01"))
    assert p.origination_fee == expected_origination, (
        f"Origination must be 1.5% × funding_amount to match FundingPool.fund() "
        f"on-chain. Got origination={p.origination_fee}, "
        f"funding_amount={p.funding_amount}, expected={expected_origination}"
    )


def test_origination_fee_po_matches_contract_basis():
    """PO with 80% advance rate: face=$8K, funding < face after discount.
    Origination must still be 1.5% × funding_amount, not 1.5% × face."""
    p = price("po", risk_score=85, nominal=Decimal("10000"), tenor_days=60)
    expected_origination = (
        p.funding_amount * Decimal("0.015")
    ).quantize(Decimal("0.01"))
    assert p.origination_fee == expected_origination, (
        f"PO origination must be 1.5% × funding_amount, "
        f"got {p.origination_fee} on funding {p.funding_amount}"
    )


def test_performance_fee_on_yield():
    """PRD: performance fee = 10% × yield to investor.

    Matches FundingPool.repay() at contracts/src/FundingPool.sol:
    `performance = yieldAmount × PERFORMANCE_FEE_BPS / BPS_DENOM`.
    """
    p = price("invoice", risk_score=73, nominal=Decimal("15000"), tenor_days=60)
    expected_perf = (p.expected_yield_amount * Decimal("0.10")).quantize(Decimal("0.01"))
    assert p.performance_fee_projected == expected_perf
