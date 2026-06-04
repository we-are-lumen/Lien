"""Pricing and risk tier logic. Pulled out of the request handler so the
formulas are testable in isolation.

References:
- PRD v3.0 Risk Tiers section
- Fee structure: 1.5% origination + 10% performance
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Literal

ProductType = Literal["invoice", "po"]
RiskTier = Literal["low", "medium", "high", "reject"]


YIELD_TABLE = {
    ("invoice", "low"): Decimal("0.07"),
    ("invoice", "medium"): Decimal("0.10"),
    ("invoice", "high"): Decimal("0.15"),
    ("po", "low"): Decimal("0.09"),
    ("po", "medium"): Decimal("0.12"),
    ("po", "high"): Decimal("0.16"),
}

# Advance rate by tier (PO only; invoice always 100%)
PO_ADVANCE_BY_TIER = {"low": 80, "medium": 75, "high": 70}

ORIGINATION_FEE_BPS = 150       # 1.5%
PERFORMANCE_FEE_BPS = 1000      # 10%


@dataclass
class Pricing:
    advance_rate: int
    yield_rate: Decimal             # APR (e.g. 0.10 = 10%)
    funding_amount: Decimal
    expected_yield_amount: Decimal
    origination_fee: Decimal
    performance_fee_projected: Decimal
    platform_fee: Decimal           # origination + projected performance
    total_repayment: Decimal


def tier_for(risk_score: int) -> RiskTier:
    if risk_score >= 80:
        return "low"
    if risk_score >= 60:
        return "medium"
    if risk_score >= 40:
        return "high"
    return "reject"


def advance_rate_for(product_type: ProductType, tier: RiskTier) -> int:
    if product_type == "invoice":
        return 100
    if tier == "reject":
        return 0
    return PO_ADVANCE_BY_TIER[tier]


def price(
    product_type: ProductType,
    risk_score: int,
    nominal: Decimal,
    tenor_days: int,
) -> Pricing:
    """Compute pricing terms from nominal + risk + tenor.

    The investor's APR is converted to the discount applied to the face value
    over `tenor_days`. We then layer the 1.5% origination fee on top.
    """
    tier = tier_for(risk_score)
    advance_rate = advance_rate_for(product_type, tier)
    if tier == "reject" or advance_rate == 0:
        raise ValueError("Document rejected — cannot price")

    yield_rate = YIELD_TABLE[(product_type, tier)]
    advance_decimal = Decimal(advance_rate) / Decimal(100)

    # Face value the investor is funding (after advance rate haircut).
    face = (nominal * advance_decimal).quantize(Decimal("0.01"))
    discount = (yield_rate * Decimal(tenor_days) / Decimal(365)).quantize(Decimal("0.0001"))
    funding_amount = (face * (Decimal("1") - discount)).quantize(Decimal("0.01"))
    expected_yield = (face - funding_amount).quantize(Decimal("0.01"))

    origination = (funding_amount * Decimal(ORIGINATION_FEE_BPS) / Decimal(10_000)).quantize(Decimal("0.01"))
    performance = (expected_yield * Decimal(PERFORMANCE_FEE_BPS) / Decimal(10_000)).quantize(Decimal("0.01"))

    return Pricing(
        advance_rate=advance_rate,
        yield_rate=yield_rate,
        funding_amount=funding_amount,
        expected_yield_amount=expected_yield,
        origination_fee=origination,
        performance_fee_projected=performance,
        platform_fee=(origination + performance).quantize(Decimal("0.01")),
        total_repayment=(funding_amount + expected_yield).quantize(Decimal("0.01")),
    )
