"""PRD scenario tests — the PRD encoded as executable assertions.

Every other test file in this directory verifies that some unit of code does
what its author intended. This file verifies that the code does what
**references/prd-summary.md** says it should do.

Source of truth: ``references/prd-summary.md`` (LIEN PRD v3.0 condensed) and
the live ``contracts/src/FundingPool.sol`` where the PRD and contract conflict.

Each test class corresponds to a section of the PRD. If a constant drifts
(someone bumps the retry window from 7 to 5 days, someone changes the
origination fee from 1.5% to 2%, someone changes M3 split from 20% to 25%),
this file fails loudly, *before* it ships to demo.

Naming convention:
    TestPRD<Section>::test_<concrete assertion>

Each test docstring quotes the PRD line it enforces verbatim so a reviewer
can audit "code matches spec" without leaving this file.

PRD-vs-implementation conflicts are explicitly called out in the relevant
test docstrings — see ``TestPRDFeeStructure::test_origination_basis_matches_contract``
for the canonical example.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from app.services.freeze_default import (
    B1_OVERDUE_DAYS,
    F1_FREEZE_HOURS,
    F1_MAX_REJECTIONS,
    F1_WINDOW_DAYS,
)
from app.services.milestones import for_product
from app.services.pricing import (
    ORIGINATION_FEE_BPS,
    PERFORMANCE_FEE_BPS,
    PO_ADVANCE_BY_TIER,
    YIELD_TABLE,
    advance_rate_for,
    pool_cap_for,
    price,
    tier_for,
)

# buyer_anon ships in PR #15 (feat/buyer-anonymization). Skip cleanly if not
# yet merged — this PR (PRD scenario tests) is independently valuable even
# without the anonymization module landed.
try:
    from app.services.buyer_anon import anonymize_buyer_name, is_idx_listed
    _BUYER_ANON_AVAILABLE = True
except ImportError:
    _BUYER_ANON_AVAILABLE = False


# ===========================================================================
# PRD §Fee Structure — Numeric Example
# ===========================================================================
#
# PRD quote (references/prd-summary.md §Fee Structure):
#   "Numeric Example (Invoice $15K, tenor 60d, risk score 73)
#    - Funded amount: $14,753 (discount 10% APR × 60/365)
#    - Origination fee: $225
#    - Supplier receives total: $14,528
#    - Yield to investor gross: $247
#    - Performance fee: $24.70
#    - Yield net to investor: $222.30 (~9.1% APR)
#    - Lien total revenue: $249.70"
#
# This example is internally inconsistent:
#   - Says "Origination fee: $225" → that's 1.5% × $15,000 (face value)
#   - Says "deducted from funded_amount" → which would be 1.5% × $14,753 = $221.30
#   - On-chain contract (FundingPool.sol:135) computes 1.5% × fundedAmount = $221.30
#
# Resolution: the **CONTRACT IS THE SOURCE OF TRUTH**. If BE displays $225 while
# the chain transfers $221.30 to treasury, the supplier's net would be over-
# promised by ~$3.70 per $15K deal and the diff would surface during demo.
# ===========================================================================


class TestPRDFeeStructure:
    """PRD §Fee Structure: 1.5% origination + 10% performance, basis defined."""

    def test_origination_fee_bps_constant(self):
        """PRD: 'Origination fee: 1.5% flat'."""
        assert ORIGINATION_FEE_BPS == 150  # 1.5% = 150bps

    def test_performance_fee_bps_constant(self):
        """PRD: 'Performance fee: 10% of yield'."""
        assert PERFORMANCE_FEE_BPS == 1000  # 10% = 1000bps

    def test_prd_example_funded_amount(self):
        """PRD: $15K invoice, 60d tenor, risk 73 → funded $14,753.

        Discount: 10% APR × 60/365 ≈ 1.644%
        Face $15,000 × (1 - 0.0164) ≈ $14,753.40
        """
        p = price("invoice", risk_score=73, nominal=Decimal("15000"), tenor_days=60)
        # PRD rounds to nearest dollar; we keep cents — accept ±$1.
        assert abs(p.funding_amount - Decimal("14753")) < Decimal("1.00"), (
            f"PRD numeric example: funded_amount should be ~$14,753, got {p.funding_amount}"
        )

    def test_prd_example_expected_yield_gross(self):
        """PRD: 'Yield to investor gross: $247' on $15K/60d/73 example."""
        p = price("invoice", risk_score=73, nominal=Decimal("15000"), tenor_days=60)
        assert abs(p.expected_yield_amount - Decimal("247")) < Decimal("1.00"), (
            f"PRD numeric example: yield gross should be ~$247, got {p.expected_yield_amount}"
        )

    def test_prd_example_performance_fee(self):
        """PRD: 'Performance fee: $24.70' = 10% × $247 yield."""
        p = price("invoice", risk_score=73, nominal=Decimal("15000"), tenor_days=60)
        assert abs(p.performance_fee_projected - Decimal("24.70")) < Decimal("0.20"), (
            f"PRD numeric example: performance fee should be ~$24.70, got {p.performance_fee_projected}"
        )

    def test_origination_basis_matches_contract_not_prd_example(self):
        """**Contract beats PRD when they conflict.**

        FundingPool.fund() at contracts/src/FundingPool.sol:135 computes:
            origination = fundedAmount × ORIGINATION_FEE_BPS / BPS_DENOM
                        = fundedAmount × 1.5%

        PRD example claims $225 (= 1.5% × $15,000 face) — but PRD prose says
        'deducted from funded_amount'. Internal inconsistency in PRD example.
        BE must match contract or treasury transfer will differ from displayed
        fee during demo.
        """
        p = price("invoice", risk_score=73, nominal=Decimal("15000"), tenor_days=60)
        expected_from_contract = (p.funding_amount * Decimal("0.015")).quantize(Decimal("0.01"))
        assert p.origination_fee == expected_from_contract, (
            f"BE origination must match FundingPool.fund() chain computation. "
            f"BE={p.origination_fee} vs contract={expected_from_contract}"
        )
        # Sanity: BE-vs-PRD-example divergence is ~$3-4 on this deal.
        # If/when PRD is fixed to say $221, delete this assertion.
        prd_example_value = Decimal("225")
        assert p.origination_fee != prd_example_value, (
            "PRD example ($225) and contract ($221.30) disagree on this case; "
            "BE follows contract. If this test starts passing, PRD has been "
            "updated to match contract — remove this assertion."
        )

    def test_total_repayment_identity(self):
        """Invariant: total_repayment = funding_amount + expected_yield."""
        p = price("invoice", risk_score=73, nominal=Decimal("15000"), tenor_days=60)
        assert p.total_repayment == p.funding_amount + p.expected_yield_amount


# ===========================================================================
# PRD §Risk Tiers — Score / Tier / Yield / Pool Cap Matrix
# ===========================================================================
#
# PRD quote (references/prd-summary.md §Risk Tiers):
#   | Score | Tier   | Invoice Yield | PO Yield   | Pool Cap                |
#   | 80-100| Low    | 6-8% APR      | 8-10% APR  | None                    |
#   | 60-79 | Med    | 9-12% APR     | 11-14% APR | Invoice $10K, PO $7.5K  |
#   | 40-59 | High   | 13-18% APR    | 15-18% APR | $5K (both)              |
#   | <40   | Reject | -             | -          | -                       |
#
# Yield is a RANGE in the PRD; implementation picks a representative point.
# These tests assert the PICKED VALUES fall inside the PRD range.
# ===========================================================================


class TestPRDRiskTiers:
    def test_tier_score_thresholds(self):
        """PRD score → tier mapping. Boundaries are inclusive on the high end."""
        assert tier_for(100) == "low"
        assert tier_for(80) == "low"
        assert tier_for(79) == "medium"
        assert tier_for(60) == "medium"
        assert tier_for(59) == "high"
        assert tier_for(40) == "high"
        assert tier_for(39) == "reject"
        assert tier_for(0) == "reject"

    def test_invoice_yields_in_prd_range(self):
        """Invoice yields: Low 6-8%, Med 9-12%, High 13-18%."""
        low = YIELD_TABLE[("invoice", "low")]
        med = YIELD_TABLE[("invoice", "medium")]
        high = YIELD_TABLE[("invoice", "high")]
        assert Decimal("0.06") <= low <= Decimal("0.08"), f"Invoice low yield {low} out of PRD 6-8%"
        assert Decimal("0.09") <= med <= Decimal("0.12"), f"Invoice med yield {med} out of PRD 9-12%"
        assert Decimal("0.13") <= high <= Decimal("0.18"), f"Invoice high yield {high} out of PRD 13-18%"

    def test_po_yields_in_prd_range(self):
        """PO yields: Low 8-10%, Med 11-14%, High 15-18%."""
        low = YIELD_TABLE[("po", "low")]
        med = YIELD_TABLE[("po", "medium")]
        high = YIELD_TABLE[("po", "high")]
        assert Decimal("0.08") <= low <= Decimal("0.10"), f"PO low yield {low} out of PRD 8-10%"
        assert Decimal("0.11") <= med <= Decimal("0.14"), f"PO med yield {med} out of PRD 11-14%"
        assert Decimal("0.15") <= high <= Decimal("0.18"), f"PO high yield {high} out of PRD 15-18%"

    def test_po_yield_always_above_invoice_yield_per_tier(self):
        """PRD: PO yields are always >= invoice yields at every tier
        (PO is structurally riskier)."""
        for tier in ("low", "medium", "high"):
            assert YIELD_TABLE[("po", tier)] >= YIELD_TABLE[("invoice", tier)], (
                f"PO yield must be >= invoice yield at tier={tier} per PRD"
            )

    def test_pool_caps_match_prd_matrix(self):
        """PRD pool caps: Low∞ / Invoice-med $10K / PO-med $7.5K / High $5K."""
        assert pool_cap_for("invoice", "low") is None  # ∞
        assert pool_cap_for("po", "low") is None  # ∞
        assert pool_cap_for("invoice", "medium") == Decimal("10000")
        assert pool_cap_for("po", "medium") == Decimal("7500")
        assert pool_cap_for("invoice", "high") == Decimal("5000")
        assert pool_cap_for("po", "high") == Decimal("5000")

    def test_reject_tier_raises_on_price(self):
        """PRD: score <40 → 'Reject' tier, can't price."""
        with pytest.raises(ValueError, match="rejected"):
            price("invoice", risk_score=39, nominal=Decimal("10000"), tenor_days=60)
        with pytest.raises(ValueError, match="rejected"):
            price("po", risk_score=20, nominal=Decimal("10000"), tenor_days=60)


# ===========================================================================
# PRD §Advance Rate — Invoice always 100, PO varies
# ===========================================================================
#
# PRD quote: "advance_rate: 100 or 70-80" (Schema Notes)
# Plus implicit from PO milestone structure: 30% M1 means M1 is 30% of the
# already-advanced face value, so total advance must equal face × (advance_rate/100).
# ===========================================================================


class TestPRDAdvanceRates:
    def test_invoice_always_100_percent_advance(self):
        """PRD: invoice = 100% advance rate (face is fully funded after discount)."""
        for tier in ("low", "medium", "high"):
            assert advance_rate_for("invoice", tier) == 100  # type: ignore[arg-type]

    def test_po_advance_rates_per_tier(self):
        """PRD: PO advance rate varies 70-80% by risk tier.

        Implementation: low=80, medium=75, high=70 (linear with risk).
        """
        assert PO_ADVANCE_BY_TIER == {"low": 80, "medium": 75, "high": 70}
        # PRD constraint: must fall in 70-80 range.
        for tier, rate in PO_ADVANCE_BY_TIER.items():
            assert 70 <= rate <= 80, f"PO advance rate {rate} for tier {tier} outside PRD 70-80 range"

    def test_po_advance_rate_inversely_proportional_to_risk(self):
        """PRD intent: higher-risk PO gets lower advance (smaller exposure)."""
        assert PO_ADVANCE_BY_TIER["low"] > PO_ADVANCE_BY_TIER["medium"]
        assert PO_ADVANCE_BY_TIER["medium"] > PO_ADVANCE_BY_TIER["high"]


# ===========================================================================
# PRD §Milestone Details — Splits + Auto-Release
# ===========================================================================
#
# PRD quote:
#   Invoice — 3 Milestones (30/50/20)
#     M1 (30%): Auto on funding
#     M2 (50%): Supplier uploads invoice from sub-vendor
#     M3 (20%): Supplier uploads Surat Jalan/BAST
#
#   PO — 4 Milestones (30/30/20/20)
#     M1 (30%): Auto on funding
#     M2 (30%): Invoice for raw material purchase
#     M3 (20%): QC report / production photos
#     M4 (20%): Surat Jalan/BAST signed by buyer
# ===========================================================================


class TestPRDMilestoneSplits:
    def test_invoice_3_milestones(self):
        """PRD: Invoice product has exactly 3 milestones."""
        ms = for_product("invoice")
        assert len(ms) == 3

    def test_invoice_split_30_50_20(self):
        """PRD: Invoice splits 30/50/20."""
        ms = for_product("invoice")
        assert [m.percentage for m in ms] == [30, 50, 20]

    def test_invoice_m1_auto_released_on_funding(self):
        """PRD: 'M1 (30%): Auto on funding'."""
        ms = for_product("invoice")
        assert ms[0].auto is True
        assert ms[0].percentage == 30
        # M2/M3 require supplier upload — NOT auto.
        assert ms[1].auto is False
        assert ms[2].auto is False

    def test_po_4_milestones(self):
        """PRD: PO product has exactly 4 milestones."""
        ms = for_product("po")
        assert len(ms) == 4

    def test_po_split_30_30_20_20(self):
        """PRD: PO splits 30/30/20/20."""
        ms = for_product("po")
        assert [m.percentage for m in ms] == [30, 30, 20, 20]

    def test_po_m1_auto_released_on_funding(self):
        """PRD: 'M1 (30%): Auto on funding' (same as invoice)."""
        ms = for_product("po")
        assert ms[0].auto is True
        assert ms[0].percentage == 30

    def test_milestone_percentages_sum_to_100_invoice(self):
        """Invariant: every product's milestone splits sum to 100%."""
        ms = for_product("invoice")
        assert sum(m.percentage for m in ms) == 100

    def test_milestone_percentages_sum_to_100_po(self):
        """Invariant: every product's milestone splits sum to 100%."""
        ms = for_product("po")
        assert sum(m.percentage for m in ms) == 100

    def test_milestone_payout_amounts_sum_to_funding_amount(self):
        """Demo scenario: investor funds $X → milestones release $X total.

        documents.py:200 computes payout_amount = funding_amount × pct / 100.
        Sum of payouts must equal funding_amount (no leakage, no dust).
        """
        p = price("invoice", risk_score=73, nominal=Decimal("15000"), tenor_days=60)
        funding = p.funding_amount
        ms = for_product("invoice")
        payouts = [funding * Decimal(m.percentage) / Decimal(100) for m in ms]
        # Allow penny-level rounding tolerance from float arithmetic in documents.py.
        diff = abs(sum(payouts) - funding)
        assert diff < Decimal("0.05"), (
            f"Milestone payouts sum {sum(payouts)} drifts from funding {funding} by {diff}"
        )


# ===========================================================================
# PRD §Retry Rule + F1 Freeze
# ===========================================================================
#
# PRD quote:
#   "Max 3 retries per milestone within 7 days. After exhausted: status
#    'Milestone Escalated', freeze disbursement, notify investor."
#
#   F1: 3 failed milestone upload attempts in 7 days → freeze 48h
# ===========================================================================


class TestPRDRetryAndFreeze:
    def test_f1_max_rejections_is_3(self):
        """PRD: 'Max 3 retries per milestone'."""
        assert F1_MAX_REJECTIONS == 3

    def test_f1_window_is_7_days(self):
        """PRD: 'within 7 days'."""
        assert F1_WINDOW_DAYS == 7

    def test_f1_freeze_duration_is_48_hours(self):
        """PRD §Freeze Triggers: 'F1: ... → freeze 48h pending manual review'."""
        assert F1_FREEZE_HOURS == 48


# ===========================================================================
# PRD §Default + B1 Auto-Default
# ===========================================================================
#
# PRD quote:
#   "B1: Default — no payment after due_date + 14d grace + 30d overdue
#    (44 days total), LP triggers markDefaulted()"
# ===========================================================================


class TestPRDDefault:
    def test_b1_overdue_threshold_is_44_days(self):
        """PRD: '14d grace + 30d overdue (44 days total)'."""
        assert B1_OVERDUE_DAYS == 44, (
            "PRD §Default: B1 fires at due_date + 14d grace + 30d overdue = 44 days. "
            f"Got {B1_OVERDUE_DAYS}"
        )


# ===========================================================================
# PRD §Buyer Anonymization
# ===========================================================================
#
# PRD quote (references/prd-summary.md §Buyer anonymization in marketplace):
#   "- IDX-listed buyers: show name (already public)
#    - Non-IDX buyers: hash name"
# ===========================================================================


class TestPRDBuyerAnonymization:
    pytestmark = pytest.mark.skipif(
        not _BUYER_ANON_AVAILABLE,
        reason="buyer_anon module not yet merged (PR #15 — feat/buyer-anonymization)",
    )

    def test_idx_buyer_name_shown_raw(self):
        """PRD: IDX-listed buyers shown as-is."""
        # Sample from PRD §AHU/OSS Fallback static cache (tier-1 IDX BUMN).
        out = anonymize_buyer_name("PT Pertamina (Persero)")
        assert out == "PT Pertamina (Persero)"
        assert "Buyer #" not in out

    def test_non_idx_buyer_hashed(self):
        """PRD: Non-IDX buyers hashed (privacy)."""
        out = anonymize_buyer_name("CV Toko Tekstil Bandung")
        assert out.startswith("Buyer #"), f"Non-IDX buyer should be hashed, got {out}"
        assert "Tekstil" not in out
        assert "Bandung" not in out

    def test_anonymization_stable(self):
        """Same input → same hash. FE can group listings."""
        a = anonymize_buyer_name("CV Random Supplier XYZ")
        b = anonymize_buyer_name("CV Random Supplier XYZ")
        assert a == b

    def test_idx_check_handles_entity_suffix_variations(self):
        """Real supplier docs vary: 'Tbk', 'Tbk.', 'PT Foo' vs 'PT. Foo'."""
        # All these reference the same legal entity per PRD intent.
        assert is_idx_listed("PT Astra International Tbk") is True
        assert is_idx_listed("PT. Astra International Tbk.") is True
        assert is_idx_listed("PT ASTRA INTERNATIONAL TBK") is True


# ===========================================================================
# PRD §Demo Day 4 Mandatory Scenarios
# ===========================================================================
#
# PRD quote:
#   "1. Fraud detection live — fake doc → AI flags → score <40 → reject
#       with per-field explanation
#    2. Double-financing block — same doc twice → registry hard reject
#       with hash display
#    3. Milestone-gated flow — fund → 30% → upload proof → AI approve
#       → 50% → upload → 20%
#    4. Happy path E2E — Andi submit → Sarah fund → milestones → buyer
#       repay → yield distributed"
#
# Scenarios 2 and 4 require integration paths (chain + agent loop) that
# we test elsewhere with mocks. These tests lock in the deterministic
# PRD-spec numbers that *frame* each demo scenario.
# ===========================================================================


class TestPRDDemoScenarios:
    def test_demo_1_fraud_detection_score_under_40_rejects(self):
        """Demo scenario 1: score <40 → reject. Pricing refuses to quote."""
        for score in (0, 20, 39):
            with pytest.raises(ValueError, match="rejected"):
                price("invoice", risk_score=score, nominal=Decimal("10000"), tenor_days=60)

    def test_demo_3_invoice_milestone_gated_split_30_50_20(self):
        """Demo scenario 3: 'fund → 30% → upload → 50% → upload → 20%'.

        PRD's exact spoken demo flow encodes the Invoice 30/50/20 split.
        Any drift here breaks the live demo narrative.
        """
        ms = for_product("invoice")
        assert ms[0].percentage == 30, "Demo says '→ 30%' on funding"
        assert ms[1].percentage == 50, "Demo says '→ 50%' after first proof"
        assert ms[2].percentage == 20, "Demo says '→ 20%' after second proof"

    def test_demo_4_happy_path_15k_invoice_numbers(self):
        """Demo scenario 4 (Andi/Sarah happy path) uses the PRD numeric example.

        Locks in: Andi gets $14,753 funded, fee $221.30 (NOT PRD's stated $225 —
        contract wins), expected yield $247, performance fee $24.70.
        """
        p = price("invoice", risk_score=73, nominal=Decimal("15000"), tenor_days=60)

        # Andi's net (after origination, before milestone fan-out):
        andi_net = p.funding_amount - p.origination_fee
        # PRD says ~$14,528. Contract math: $14,753.40 - $221.30 = $14,532.10.
        assert abs(andi_net - Decimal("14528")) < Decimal("10"), (
            f"Andi's net should be ~$14,528 per PRD demo narrative, got {andi_net}"
        )

        # Sarah's gross yield:
        assert abs(p.expected_yield_amount - Decimal("247")) < Decimal("2.00")

        # Sarah's net yield (after 10% performance fee):
        sarah_net = p.expected_yield_amount - p.performance_fee_projected
        assert abs(sarah_net - Decimal("222.30")) < Decimal("2.00"), (
            f"Sarah's net yield should be ~$222.30 per PRD demo narrative, got {sarah_net}"
        )


# ===========================================================================
# PRD §Invariants — Cross-Cutting Properties
# ===========================================================================


class TestPRDInvariants:
    @pytest.mark.parametrize(
        "product_type,score,nominal,tenor",
        [
            ("invoice", 85, Decimal("5000"),  30),
            ("invoice", 73, Decimal("15000"), 60),
            ("invoice", 50, Decimal("3000"),  45),
            ("po",      82, Decimal("10000"), 90),
            ("po",      65, Decimal("7000"),  60),
            ("po",      45, Decimal("4500"),  120),
        ],
    )
    def test_funding_amount_never_exceeds_face(self, product_type, score, nominal, tenor):
        """Invariant: discounted funding < face. Investor would never overpay."""
        p = price(product_type, risk_score=score, nominal=nominal, tenor_days=tenor)
        face = nominal * Decimal(p.advance_rate) / Decimal(100)
        assert p.funding_amount < face

    @pytest.mark.parametrize(
        "product_type,score,nominal,tenor",
        [
            ("invoice", 85, Decimal("5000"),  30),
            ("invoice", 73, Decimal("15000"), 60),
            ("po",      82, Decimal("10000"), 90),
            ("po",      65, Decimal("7000"),  60),
        ],
    )
    def test_expected_yield_positive(self, product_type, score, nominal, tenor):
        """Invariant: yield > 0. Otherwise investor takes a loss vs face value."""
        p = price(product_type, risk_score=score, nominal=nominal, tenor_days=tenor)
        assert p.expected_yield_amount > 0

    @pytest.mark.parametrize(
        "product_type,score,nominal,tenor",
        [
            ("invoice", 85, Decimal("5000"),  30),
            ("invoice", 73, Decimal("15000"), 60),
            ("po",      82, Decimal("10000"), 90),
            ("po",      45, Decimal("4500"),  120),
        ],
    )
    def test_origination_always_on_funding_amount_basis(self, product_type, score, nominal, tenor):
        """Cross-product invariant: origination basis is *always* funding_amount.

        Locks the PR #12 decision across every priceable input. If this drifts,
        BE and on-chain treasury transfers diverge."""
        p = price(product_type, risk_score=score, nominal=nominal, tenor_days=tenor)
        expected = (p.funding_amount * Decimal("0.015")).quantize(Decimal("0.01"))
        assert p.origination_fee == expected

    @pytest.mark.parametrize(
        "product_type,score,nominal,tenor",
        [
            ("invoice", 85, Decimal("5000"),  30),
            ("invoice", 73, Decimal("15000"), 60),
            ("po",      82, Decimal("10000"), 90),
            ("po",      65, Decimal("7000"),  60),
        ],
    )
    def test_performance_always_on_yield_basis(self, product_type, score, nominal, tenor):
        """Cross-product invariant: performance fee is 10% × yield (PRD + contract)."""
        p = price(product_type, risk_score=score, nominal=nominal, tenor_days=tenor)
        expected = (p.expected_yield_amount * Decimal("0.10")).quantize(Decimal("0.01"))
        assert p.performance_fee_projected == expected

    def test_higher_risk_means_higher_yield_invoice(self):
        """PRD intent: investor compensated more for higher risk."""
        same_amt = Decimal("10000")
        low = price("invoice", risk_score=85, nominal=same_amt, tenor_days=60)
        med = price("invoice", risk_score=65, nominal=same_amt, tenor_days=60)
        high = price("invoice", risk_score=45, nominal=same_amt, tenor_days=60)
        assert low.yield_rate < med.yield_rate < high.yield_rate

    def test_higher_risk_means_higher_yield_po(self):
        """PRD intent: investor compensated more for higher risk (PO version)."""
        same_amt = Decimal("10000")
        low = price("po", risk_score=85, nominal=same_amt, tenor_days=60)
        med = price("po", risk_score=65, nominal=same_amt, tenor_days=60)
        high = price("po", risk_score=45, nominal=same_amt, tenor_days=60)
        assert low.yield_rate < med.yield_rate < high.yield_rate
