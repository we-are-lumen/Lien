"""Milestone configuration by product type. Defined in PRD v3.0.

Invoice (3 milestones):
- M1: 30% — auto on funding
- M2: 50% — supplier uploads purchase invoice
- M3: 20% — supplier uploads Surat Jalan/BAST

PO (4 milestones):
- M1: 30% — auto on funding
- M2: 30% — invoice for raw material purchase
- M3: 20% — QC report / production photos
- M4: 20% — Surat Jalan/BAST signed by buyer
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal


@dataclass(frozen=True)
class MilestoneSpec:
    idx: int
    name: str
    percentage: int
    release_trigger: str
    auto: bool = False


INVOICE_MILESTONES: List[MilestoneSpec] = [
    MilestoneSpec(1, "1st Payout", 30, "Auto-released on funding", auto=True),
    MilestoneSpec(2, "2nd Payout", 50, "Upload purchase invoice from sub-vendor"),
    MilestoneSpec(3, "3rd Payout", 20, "Upload Surat Jalan or BAST"),
]

PO_MILESTONES: List[MilestoneSpec] = [
    MilestoneSpec(1, "1st Payout", 30, "Auto-released on funding", auto=True),
    MilestoneSpec(2, "2nd Payout", 30, "Upload purchase invoice from sub-vendor"),
    MilestoneSpec(3, "3rd Payout", 20, "Upload QC report or production photos"),
    MilestoneSpec(4, "4th Payout", 20, "Upload Surat Jalan or BAST"),
]


def for_product(product_type: Literal["invoice", "po"]) -> List[MilestoneSpec]:
    if product_type == "invoice":
        return INVOICE_MILESTONES
    if product_type == "po":
        return PO_MILESTONES
    raise ValueError(f"Unknown product type: {product_type}")
