"""Document hash for the on-chain double-financing registry.

The hash is the keccak256 of canonicalized fields. Keep this function as the
single source of truth — used by both the upload endpoint and any future
re-check job.
"""

from __future__ import annotations

from eth_utils import keccak


def compute_doc_hash(
    buyer_name: str,
    nominal: str,
    due_date: str,
    document_number: str,
) -> str:
    """keccak256(buyer || nominal || due_date || document_number).

    Inputs are normalised to UTF-8 strings and concatenated with `||`. The
    same algorithm runs in the smart contract; do not change without
    coordinating with InvoiceRegistry.sol.
    """
    canonical = "||".join(
        [
            buyer_name.strip().lower(),
            nominal.strip(),
            due_date.strip(),
            document_number.strip().lower(),
        ]
    )
    return "0x" + keccak(text=canonical).hex()
