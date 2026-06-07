"""Buyer name anonymization for marketplace + financing detail endpoints.

PRD requirement: only IDX-listed buyer names may appear publicly. Everyone
else is anonymized as ``Buyer_<hash8>``. The supplier of a financing and
the buyer themselves always see the raw name on their own detail view.

Known tradeoff: normalization is intentionally lossy (strips ``PT``/``Tbk``/
parentheticals, lowercases, collapses whitespace) so legitimate spelling
variants of an IDX-listed company match the whitelist entry. The same
fuzziness means a non-listed name that happens to normalize to the same
key as a whitelist entry would be treated as listed. For the hackathon
scope this is acceptable — whitelist entries are short, distinctive
Indonesian conglomerate names; the realistic collision space is small.
A production hardening would replace fuzzy normalization with a canonical
IDX-ticker -> display-name table (e.g. ``BBCA`` -> ``PT Bank Central Asia``)
and require an exact match on the canonical form.
"""

from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path


_ASSET_PATH = Path(__file__).resolve().parent.parent / "assets" / "idx_buyers.json"
_LOCK = threading.Lock()
_NORMALIZED_WHITELIST: set[str] | None = None


def _normalize(name: str) -> str:
    """Lowercase, collapse whitespace, strip PT prefix + Tbk suffix.

    Indonesian company names are inconsistently written:
      "PT Telekomunikasi Indonesia (Persero) Tbk" vs "Telekomunikasi Indonesia"
    A strict equality check would miss most real-world variants.
    """
    s = name.strip().lower()
    # Strip parenthetical qualifiers like "(persero)".
    s = re.sub(r"\([^)]*\)", " ", s)
    # Strip PT prefix.
    s = re.sub(r"^pt\.?\s+", "", s)
    # Strip Tbk suffix.
    s = re.sub(r"\s+tbk\.?$", "", s)
    # Collapse whitespace.
    s = re.sub(r"\s+", " ", s).strip()
    return s


def _load_whitelist() -> set[str]:
    global _NORMALIZED_WHITELIST
    if _NORMALIZED_WHITELIST is not None:
        return _NORMALIZED_WHITELIST
    with _LOCK:
        if _NORMALIZED_WHITELIST is not None:
            return _NORMALIZED_WHITELIST
        try:
            raw = json.loads(_ASSET_PATH.read_text(encoding="utf-8"))
            buyers = raw.get("buyers", [])
        except (FileNotFoundError, json.JSONDecodeError):
            buyers = []
        _NORMALIZED_WHITELIST = {_normalize(b) for b in buyers if b}
        return _NORMALIZED_WHITELIST


def is_idx_listed(buyer_name: str) -> bool:
    """Return True if the buyer's normalized name is on the IDX whitelist."""
    if not buyer_name:
        return False
    return _normalize(buyer_name) in _load_whitelist()


def anonymize_buyer_name(buyer_name: str) -> str:
    """Return ``buyer_name`` if IDX-listed, else ``Buyer_<8-char hash>``.

    The hash is deterministic so the same buyer shows the same opaque label
    across listings, letting investors recognize repeat buyers without
    learning their identity.
    """
    if not buyer_name:
        return ""
    if is_idx_listed(buyer_name):
        return buyer_name
    digest = hashlib.sha256(_normalize(buyer_name).encode("utf-8")).hexdigest()
    return f"Buyer_{digest[:8]}"


def maybe_anonymize(buyer_name: str, *, viewer_is_party: bool) -> str:
    """Return raw name when the viewer is the supplier or buyer; else anonymize.

    Marketplace / public investor browsing -> viewer_is_party=False.
    Supplier or buyer dashboard -> viewer_is_party=True.
    """
    if viewer_is_party:
        return buyer_name or ""
    return anonymize_buyer_name(buyer_name)
