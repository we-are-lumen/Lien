"""Tests for doc hash determinism."""

from __future__ import annotations

from app.services.doc_hash import compute_doc_hash


def test_hash_is_deterministic():
    a = compute_doc_hash("PT ABC", "1000.00", "2026-06-01", "INV-001")
    b = compute_doc_hash("PT ABC", "1000.00", "2026-06-01", "INV-001")
    assert a == b
    assert a.startswith("0x")
    assert len(a) == 66  # 0x + 64 hex chars


def test_hash_changes_with_any_field():
    base = compute_doc_hash("PT ABC", "1000.00", "2026-06-01", "INV-001")
    assert base != compute_doc_hash("PT DEF", "1000.00", "2026-06-01", "INV-001")
    assert base != compute_doc_hash("PT ABC", "1000.01", "2026-06-01", "INV-001")
    assert base != compute_doc_hash("PT ABC", "1000.00", "2026-06-02", "INV-001")
    assert base != compute_doc_hash("PT ABC", "1000.00", "2026-06-01", "INV-002")


def test_hash_normalizes_whitespace_and_case():
    a = compute_doc_hash("  PT ABC ", "1000.00", "2026-06-01", "INV-001")
    b = compute_doc_hash("pt abc", "1000.00", "2026-06-01", "inv-001")
    assert a == b
