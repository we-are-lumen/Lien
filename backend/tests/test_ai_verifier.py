"""Tests for AI verifier mock + milestone config."""

from __future__ import annotations

import asyncio

from app.services.ai_verifier import MockAIVerifier
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
