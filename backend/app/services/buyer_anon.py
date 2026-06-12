"""Buyer name anonymization for marketplace and other public surfaces.

Per PRD v3.0:
- IDX-listed buyers: show name as-is (already public via stock exchange)
- Non-IDX buyers: hash the name for privacy in marketplace listings

The IDX list is intentionally small and curated — the top tier-1 buyer
counterparties Lien expects to see in MVP. Adding to this list is a
deliberate product decision, not an automated check.

Investor-facing dashboards (after the investor funds a deal) may still show
the raw buyer_name — anonymization applies only to PRE-FUNDING public surfaces
where the buyer hasn't consented to disclosure.
"""

from __future__ import annotations

import hashlib
from typing import Iterable


# ---------------------------------------------------------------------------
# IDX-listed tier-1 buyer cache
#
# Names are normalized via _normalize() (uppercased, whitespace collapsed,
# common entity suffixes stripped) so trailing "Tbk", "Tbk.", "PT.", and
# case differences all collide on the canonical form.
#
# Source: subset of IDX BUMN/Tier-1 issuers commonly involved in supply-chain
# financing. Expand as Lien onboards new counterparties.
# ---------------------------------------------------------------------------

_IDX_LISTED_BUYERS_RAW: tuple[str, ...] = (
    # State-owned (BUMN)
    "PT Pertamina (Persero)",
    "PT Perusahaan Listrik Negara (Persero)",
    "PT Telekomunikasi Indonesia Tbk",
    "PT Bank Mandiri (Persero) Tbk",
    "PT Bank Rakyat Indonesia (Persero) Tbk",
    "PT Bank Negara Indonesia (Persero) Tbk",
    "PT Pupuk Indonesia (Persero)",
    "PT Semen Indonesia (Persero) Tbk",
    "PT Krakatau Steel (Persero) Tbk",
    "PT Aneka Tambang Tbk",
    # Large private IDX listings
    "PT Astra International Tbk",
    "PT Indofood Sukses Makmur Tbk",
    "PT Unilever Indonesia Tbk",
    "PT Gudang Garam Tbk",
    "PT HM Sampoerna Tbk",
    "PT Bank Central Asia Tbk",
    "PT Sumber Alfaria Trijaya Tbk",
    "PT Charoen Pokphand Indonesia Tbk",
    "PT Japfa Comfeed Indonesia Tbk",
    "PT Mayora Indah Tbk",
)


def _normalize(name: str) -> str:
    """Canonicalize a buyer name for membership comparison.

    - Uppercase
    - Collapse internal whitespace
    - Strip common entity suffixes (Tbk, Tbk., (Persero), .) that vary in
      submitted documents but refer to the same legal entity.
    """
    if not name:
        return ""
    n = " ".join(name.upper().split())
    # Strip parenthesized qualifier (PERSERO) anywhere.
    n = n.replace("(PERSERO)", "").replace("PERSERO", "")
    # Strip trailing "TBK" with or without dot.
    if n.endswith(" TBK."):
        n = n[: -len(" TBK.")]
    elif n.endswith(" TBK"):
        n = n[: -len(" TBK")]
    # Strip leading "PT " / "PT. " entity marker so "PT FOO" == "FOO".
    if n.startswith("PT. "):
        n = n[len("PT. "):]
    elif n.startswith("PT "):
        n = n[len("PT "):]
    # Final whitespace collapse after substitutions.
    return " ".join(n.split())


_IDX_LISTED_NORMALIZED: frozenset[str] = frozenset(_normalize(n) for n in _IDX_LISTED_BUYERS_RAW)


def is_idx_listed(buyer_name: str) -> bool:
    """Return True if ``buyer_name`` matches an IDX-listed tier-1 buyer.

    Match is on the normalized form — case-insensitive, ignoring entity
    suffix variations (Tbk / Tbk. / (Persero) / PT prefix).
    """
    return _normalize(buyer_name) in _IDX_LISTED_NORMALIZED


def anonymize_buyer_name(buyer_name: str) -> str:
    """Return a display-safe form of ``buyer_name`` for public marketplace.

    - IDX-listed: returns the raw name (public via stock exchange anyway).
    - Non-IDX:    returns "Buyer #<6-hex>" derived from a stable SHA-256 hash
                  of the normalized name. Same input → same hash, so the FE
                  can group/identify listings without exposing the real name.

    Empty/whitespace input returns "Buyer Unknown" (defensive — shouldn't
    happen in practice because buyer_name is a required field upstream).
    """
    if not buyer_name or not buyer_name.strip():
        return "Buyer Unknown"
    if is_idx_listed(buyer_name):
        return buyer_name
    digest = hashlib.sha256(_normalize(buyer_name).encode("utf-8")).hexdigest()
    return f"Buyer #{digest[:6].upper()}"


def idx_listed_names() -> Iterable[str]:
    """Return the raw IDX-listed names. Exposed for diagnostics / admin UI."""
    return _IDX_LISTED_BUYERS_RAW
