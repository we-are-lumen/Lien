"""Tests for buyer name anonymization (PRD v3.0 §Buyer anonymization).

Rules:
- IDX-listed buyers (top tier-1 issuers): name shown as-is.
- Non-IDX buyers: hashed to "Buyer #ABCDEF" for marketplace privacy.

Match is case-insensitive and tolerant of entity-suffix variation
(PT/PT., Tbk/Tbk., (Persero), whitespace).
"""

from __future__ import annotations

import pytest

from app.services.buyer_anon import (
    _normalize,
    anonymize_buyer_name,
    idx_listed_names,
    is_idx_listed,
)


# ---------------------------------------------------------------------------
# Normalization
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("a,b", [
    ("PT Astra International Tbk", "pt astra international tbk."),
    ("PT Bank Mandiri (Persero) Tbk", "Bank Mandiri Tbk"),
    ("PT  Indofood   Sukses Makmur  Tbk", "PT Indofood Sukses Makmur Tbk"),
    ("PT. Unilever Indonesia Tbk", "PT Unilever Indonesia Tbk"),
])
def test_normalize_collapses_equivalent_names(a, b):
    assert _normalize(a) == _normalize(b), (
        f"Expected normalized equality:\n  {a!r} -> {_normalize(a)!r}\n  {b!r} -> {_normalize(b)!r}"
    )


def test_normalize_empty_input():
    assert _normalize("") == ""
    assert _normalize("   ") == ""


# ---------------------------------------------------------------------------
# IDX membership
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name", [
    "PT Astra International Tbk",
    "pt astra international tbk",
    "PT. ASTRA INTERNATIONAL TBK.",
    "Astra International",
    "PT Bank Mandiri (Persero) Tbk",
    "Bank Mandiri Tbk",
])
def test_is_idx_listed_matches_canonical_variations(name):
    assert is_idx_listed(name), f"{name!r} should match IDX list"


@pytest.mark.parametrize("name", [
    "PT Konveksi Bandung Jaya",          # Andi persona — should be hidden
    "CV Lima Bersaudara",
    "UD Sumber Rezeki",
    "Random Garment Co",
    "",
    "PT Not Listed",
])
def test_is_idx_listed_rejects_non_idx_names(name):
    assert not is_idx_listed(name)


# ---------------------------------------------------------------------------
# Anonymization
# ---------------------------------------------------------------------------

def test_idx_listed_passes_through_unchanged():
    raw = "PT Astra International Tbk"
    assert anonymize_buyer_name(raw) == raw


def test_idx_listed_variation_still_returns_original_input():
    """The raw input is preserved (not canonicalized) when IDX-listed."""
    raw = "pt astra international tbk"  # case differs from canonical
    out = anonymize_buyer_name(raw)
    assert out == raw, "IDX-listed names must pass through unchanged"


def test_non_idx_buyer_returns_hashed_handle():
    name = "PT Konveksi Bandung Jaya"
    out = anonymize_buyer_name(name)
    assert out.startswith("Buyer #")
    # 6 hex chars after the prefix
    suffix = out.removeprefix("Buyer #")
    assert len(suffix) == 6
    assert all(c in "0123456789ABCDEF" for c in suffix), f"Non-hex chars in {out!r}"


def test_non_idx_anonymization_is_deterministic():
    name = "PT Konveksi Bandung Jaya"
    assert anonymize_buyer_name(name) == anonymize_buyer_name(name)


def test_non_idx_anonymization_collides_on_normalized_form():
    """Same buyer with different formatting → same anonymized handle.
    Required so the FE can group listings by buyer without revealing identity.
    """
    a = anonymize_buyer_name("PT Konveksi Bandung Jaya")
    b = anonymize_buyer_name("pt   konveksi  bandung jaya")
    c = anonymize_buyer_name("Konveksi Bandung Jaya")
    assert a == b == c, (
        f"Anonymization must be normalization-invariant: {a=}, {b=}, {c=}"
    )


def test_non_idx_anonymization_distinguishes_different_buyers():
    a = anonymize_buyer_name("PT Konveksi Bandung Jaya")
    b = anonymize_buyer_name("PT Garment Solo Sentosa")
    assert a != b


def test_empty_buyer_name_returns_unknown():
    assert anonymize_buyer_name("") == "Buyer Unknown"
    assert anonymize_buyer_name("   ") == "Buyer Unknown"


# ---------------------------------------------------------------------------
# Public list shape
# ---------------------------------------------------------------------------

def test_idx_listed_names_is_iterable_and_nonempty():
    names = list(idx_listed_names())
    assert len(names) >= 10, "IDX list should cover the top tier-1 buyers"
    # All should be strings.
    assert all(isinstance(n, str) and n for n in names)


def test_every_idx_listed_name_round_trips_through_anonymizer():
    """Internal consistency: every name in the curated IDX list must pass
    through anonymize_buyer_name unchanged (proves the list and the
    normalization stay in sync)."""
    for name in idx_listed_names():
        assert anonymize_buyer_name(name) == name, (
            f"Curated IDX name failed round-trip: {name!r}"
        )
