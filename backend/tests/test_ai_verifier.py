"""Tests for AI verifier mock + milestone config."""

from __future__ import annotations

import asyncio

from app.services.ai_verifier import MockAIVerifier, _build_milestone_prompt
from app.services.milestones import for_product, INVOICE_MILESTONES, PO_MILESTONES


def test_invoice_milestone_config():
    assert len(INVOICE_MILESTONES) == 3
    assert sum(m.percentage for m in INVOICE_MILESTONES) == 100
    assert INVOICE_MILESTONES[0].auto is True


def test_po_milestone_config():
    assert len(PO_MILESTONES) == 4
    assert sum(m.percentage for m in PO_MILESTONES) == 100
    assert PO_MILESTONES[0].auto is True


def test_for_product_invoice():
    assert for_product("invoice") == INVOICE_MILESTONES


def test_for_product_po():
    assert for_product("po") == PO_MILESTONES


def test_mock_ai_is_deterministic():
    verifier = MockAIVerifier()
    a = asyncio.run(verifier.verify_document(b"identical", "invoice", {}))
    b = asyncio.run(verifier.verify_document(b"identical", "invoice", {}))
    assert a.risk_score == b.risk_score
    assert a.risk_tier == b.risk_tier


def test_mock_ai_po_penalty():
    verifier = MockAIVerifier()
    inv = asyncio.run(verifier.verify_document(b"same bytes", "invoice", {}))
    po = asyncio.run(verifier.verify_document(b"same bytes", "po", {}))
    # PO baseline is invoice - 10
    assert po.risk_score == max(0, inv.risk_score - 10)


# ---------------------------------------------------------------------------
# Per-milestone check keys — MockAIVerifier
# ---------------------------------------------------------------------------

_META = {
    "issuer_name": "CV Maju Bersama",
    "buyer_name": "PT Astra International",
    "total_amount": "10000000",
    "due_date": "2026-09-30",
}


def test_mock_invoice_m2_check_keys():
    """Invoice M2 must return the 6 checks defined in PRD §Invoice M2."""
    verifier = MockAIVerifier()
    result = asyncio.run(verifier.verify_milestone(b"doc", 2, "invoice", _META))
    expected = {
        "doc_type_valid",
        "supplier_name_match",
        "nominal_proportional",
        "date_valid",
        "sub_vendor_identifiable",
        "anomaly_count_acceptable",
    }
    assert set(result.checks.keys()) == expected


def test_mock_invoice_m3_check_keys():
    """Invoice M3 must return the 5 delivery-proof checks defined in PRD §Invoice M3."""
    verifier = MockAIVerifier()
    result = asyncio.run(verifier.verify_milestone(b"doc", 3, "invoice", _META))
    expected = {
        "doc_type_valid",
        "buyer_name_match",
        "delivery_consistent_with_invoice",
        "quantity_not_exceeded",
        "timeline_valid",
    }
    assert set(result.checks.keys()) == expected


def test_mock_po_m2_check_keys():
    """PO M2 must return the 6 checks (same as Invoice M2 but 20–75% threshold)."""
    verifier = MockAIVerifier()
    result = asyncio.run(verifier.verify_milestone(b"doc", 2, "po", _META))
    expected = {
        "doc_type_valid",
        "supplier_name_match",
        "nominal_proportional",
        "date_valid",
        "sub_vendor_identifiable",
        "anomaly_count_acceptable",
    }
    assert set(result.checks.keys()) == expected


def test_mock_po_m3_check_keys():
    """PO M3 must return the 5 QC / photo manipulation checks."""
    verifier = MockAIVerifier()
    result = asyncio.run(verifier.verify_milestone(b"doc", 3, "po", _META))
    expected = {
        "doc_type_valid",
        "production_evidence_visible",
        "exif_consistency",
        "visual_manipulation_check",
        "supplier_context_match",
    }
    assert set(result.checks.keys()) == expected


def test_mock_po_m4_check_keys():
    """PO M4 must return the 5 final-delivery checks including buyer_signature_present."""
    verifier = MockAIVerifier()
    result = asyncio.run(verifier.verify_milestone(b"doc", 4, "po", _META))
    expected = {
        "doc_type_valid",
        "buyer_signature_present",
        "buyer_name_match",
        "quantity_consistent_with_production",
        "timeline_valid",
    }
    assert set(result.checks.keys()) == expected


# ---------------------------------------------------------------------------
# Prompt builder — smoke tests (no API calls)
# ---------------------------------------------------------------------------

def test_build_invoice_m2_prompt_contains_80pct():
    """Invoice M2 prompt must mention the 80% upper bound for nominal check."""
    prompt, tag = _build_milestone_prompt(2, "invoice", _META)
    assert "80%" in prompt
    assert tag == "invoice_m2"


def test_build_invoice_m3_prompt_mentions_grace():
    """Invoice M3 prompt must mention the 14-day grace period."""
    prompt, tag = _build_milestone_prompt(3, "invoice", _META)
    assert "14" in prompt
    assert tag == "invoice_m3"


def test_build_po_m3_prompt_mentions_exif():
    """PO M3 prompt must include EXIF-consistency check."""
    prompt, tag = _build_milestone_prompt(3, "po", _META)
    assert "exif" in prompt.lower()
    assert tag == "po_m3"


def test_build_po_m4_prompt_mentions_signature():
    """PO M4 prompt must require buyer signature/stamp detection."""
    prompt, tag = _build_milestone_prompt(4, "po", _META)
    assert "signature" in prompt.lower() or "stamp" in prompt.lower()
    assert tag == "po_m4"


def test_build_po_m2_prompt_75pct_cap():
    """PO M2 nominal cap is 75% (tighter than Invoice M2's 80%)."""
    inv_prompt, _ = _build_milestone_prompt(2, "invoice", _META)
    po_prompt, _ = _build_milestone_prompt(2, "po", _META)
    assert "75%" in po_prompt
    # Invoice M2 uses 80%; PO M2 uses 75% — they must differ.
    assert "75%" not in inv_prompt or "80%" in inv_prompt
