"""AI verification service.

Two modes:
- Mock (default): returns deterministic scores. Useful for FE integration
  and CI where we don't want to spend API credits.
- Real: stub for now. Will swap in Claude/Gemini Vision later.

The interface mirrors the 4-stage pipeline described in the PRD:
  A: Document authenticity (OCR + visual anomaly)
  B: Counterparty (AHU/OSS NPWP check)
  C: Relationship plausibility (on-chain history)
  D: Double-financing (on-chain hash lookup)

Score formula: 0.4*doc + 0.3*counterparty + 0.2*relationship + 0.1*uniqueness
PO baseline: -10 points
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass, field
from typing import Dict, List, Literal, Optional

from app.core.config import get_settings


log = logging.getLogger(__name__)

DocumentType = Literal["invoice", "po"]


@dataclass
class VerifyResult:
    risk_score: int
    risk_tier: Literal["low", "medium", "high", "reject"]
    doc_score: int
    counterparty_score: int
    relationship_score: int
    unique: bool
    flags: List[str] = field(default_factory=list)
    stages: Dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "risk_score": self.risk_score,
            "risk_tier": self.risk_tier,
            "doc_score": self.doc_score,
            "counterparty_score": self.counterparty_score,
            "relationship_score": self.relationship_score,
            "unique": self.unique,
            "flags": self.flags,
            "stages": self.stages,
        }


@dataclass
class MilestoneVerifyResult:
    confidence: float
    verdict: Literal["APPROVED", "REJECTED"]
    checks: Dict
    fail_reasons: List[str]
    display_message: str

    def to_dict(self) -> dict:
        return {
            "confidence": self.confidence,
            "verdict": self.verdict,
            "checks": self.checks,
            "fail_reasons": self.fail_reasons,
            "display_message": self.display_message,
        }


def _tier_for(score: int) -> str:
    if score >= 80:
        return "low"
    if score >= 60:
        return "medium"
    if score >= 40:
        return "high"
    return "reject"


def _deterministic_score(seed: str, low: int = 60, high: int = 95) -> int:
    """Hash the seed and project onto [low, high]. Same input = same output."""
    h = int(hashlib.sha256(seed.encode()).hexdigest(), 16)
    return low + (h % (high - low + 1))


class AIVerifier:
    """Pluggable interface. Swap implementations via env."""

    async def verify_document(
        self,
        file_bytes: bytes,
        document_type: DocumentType,
        metadata: dict,
    ) -> VerifyResult:
        raise NotImplementedError

    async def verify_milestone(
        self,
        file_bytes: bytes,
        milestone_idx: int,
        product_type: DocumentType,
        financing_meta: dict,
    ) -> MilestoneVerifyResult:
        raise NotImplementedError


class MockAIVerifier(AIVerifier):
    """Deterministic mock. Scores derived from file hash so repeated uploads
    of the same file return the same verdict."""

    async def verify_document(
        self,
        file_bytes: bytes,
        document_type: DocumentType,
        metadata: dict,
    ) -> VerifyResult:
        seed = hashlib.sha256(file_bytes).hexdigest()
        doc = _deterministic_score(seed + "doc")
        counterparty = _deterministic_score(seed + "ctp")
        relationship = _deterministic_score(seed + "rel", low=50, high=90)

        risk = int(
            0.4 * doc + 0.3 * counterparty + 0.2 * relationship + 0.1 * 100
        )
        if document_type == "po":
            risk = max(0, risk - 10)

        return VerifyResult(
            risk_score=risk,
            risk_tier=_tier_for(risk),  # type: ignore[arg-type]
            doc_score=doc,
            counterparty_score=counterparty,
            relationship_score=relationship,
            unique=True,
            flags=[],
            stages={
                "A": {"score": doc, "notes": "mock: OCR + anomaly"},
                "B": {"score": counterparty, "notes": "mock: NPWP lookup"},
                "C": {"score": relationship, "notes": "mock: on-chain history"},
                "D": {"unique": True, "notes": "mock: registry lookup"},
            },
        )

    async def verify_milestone(
        self,
        file_bytes: bytes,
        milestone_idx: int,
        product_type: DocumentType,
        financing_meta: dict,
    ) -> MilestoneVerifyResult:
        seed = hashlib.sha256(file_bytes + str(milestone_idx).encode()).hexdigest()
        confidence_int = _deterministic_score(seed, low=75, high=98)
        confidence = confidence_int / 100.0
        verdict = "APPROVED" if confidence >= 0.5 else "REJECTED"

        # Return the same check-key names the real Claude verifier uses so tests
        # can validate the response schema without making actual API calls.
        checks: dict
        pt = str(product_type)
        if pt == "invoice" and milestone_idx == 2:
            checks = {
                "doc_type_valid": {"pass": True, "note": "mock"},
                "supplier_name_match": {"pass": True, "note": "mock fuzzy 94%"},
                "nominal_proportional": {"pass": True, "note": "mock within 20-80%"},
                "date_valid": {"pass": True, "note": "mock"},
                "sub_vendor_identifiable": {"pass": True, "note": "mock"},
                "anomaly_count_acceptable": {"pass": True, "note": "mock"},
            }
        elif pt == "invoice" and milestone_idx == 3:
            checks = {
                "doc_type_valid": {"pass": True, "note": "mock surat jalan"},
                "buyer_name_match": {"pass": True, "note": "mock fuzzy 91%"},
                "delivery_consistent_with_invoice": {"pass": True, "note": "mock 80%"},
                "quantity_not_exceeded": {"pass": True, "note": "mock"},
                "timeline_valid": {"pass": True, "note": "mock within due+14d"},
            }
        elif pt == "po" and milestone_idx == 2:
            checks = {
                "doc_type_valid": {"pass": True, "note": "mock"},
                "supplier_name_match": {"pass": True, "note": "mock fuzzy 94%"},
                "nominal_proportional": {"pass": True, "note": "mock within 20-75%"},
                "date_valid": {"pass": True, "note": "mock"},
                "sub_vendor_identifiable": {"pass": True, "note": "mock"},
                "anomaly_count_acceptable": {"pass": True, "note": "mock"},
            }
        elif pt == "po" and milestone_idx == 3:
            checks = {
                "doc_type_valid": {"pass": True, "note": "mock qc report"},
                "production_evidence_visible": {"pass": True, "note": "mock"},
                "exif_consistency": {"pass": True, "note": "mock"},
                "visual_manipulation_check": {"pass": True, "note": "mock"},
                "supplier_context_match": {"pass": True, "note": "mock"},
            }
        elif pt == "po" and milestone_idx == 4:
            checks = {
                "doc_type_valid": {"pass": True, "note": "mock surat jalan"},
                "buyer_signature_present": {"pass": True, "note": "mock stamp found"},
                "buyer_name_match": {"pass": True, "note": "mock fuzzy 88%"},
                "quantity_consistent_with_production": {"pass": True, "note": "mock within 5%"},
                "timeline_valid": {"pass": True, "note": "mock"},
            }
        else:
            # M1 fallback or unknown milestone.
            checks = {
                "doc_type_valid": {"pass": True, "note": "mock"},
                "doc_authenticity": {"pass": True, "note": "mock"},
            }

        return MilestoneVerifyResult(
            confidence=confidence,
            verdict=verdict,
            checks=checks,
            fail_reasons=[],
            display_message=f"Mock verification for milestone {milestone_idx}: {verdict}",
        )


_MILESTONE_DESCRIPTIONS: Dict[int, str] = {
    1: "Purchase Order / Contract signed",
    2: "Goods shipped / delivery in transit (shipping docs, bill of lading)",
    3: "Goods received / delivery confirmed (delivery receipt, proof of receipt)",
    4: "Invoice paid / payment confirmation",
}

_MILESTONE_SYSTEM_PROMPT = (
    "You are a financial document verifier for an invoice financing platform "
    "in Indonesia. Your job is to verify that a supplier's proof document "
    "genuinely demonstrates completion of a specific milestone in a trade "
    "financing transaction. Be strict but fair. Return ONLY valid JSON."
)


# ---------------------------------------------------------------------------
# Per-milestone prompt specialization (PRD v3.0 §Milestone Details)
#
# Each tuple is (checks_description, response_schema) where checks_description
# lists the exact checks Claude must run and response_schema names the JSON keys.
#
# Invoice milestones:
#   M1 — auto on funding, never uploaded; M2 — sub-vendor purchase invoice;
#   M3 — Surat Jalan / BAST delivery proof.
#
# PO milestones:
#   M1 — auto on funding, never uploaded; M2 — sub-vendor purchase invoice;
#   M3 — QC report / production photos; M4 — Surat Jalan/BAST with buyer signature.
# ---------------------------------------------------------------------------


def _build_milestone_prompt(
    milestone_idx: int,
    product_type: str,
    financing_meta: dict,
) -> tuple[str, str]:
    """Return (user_prompt, schema_hint) tailored to the specific milestone.

    M1 is never uploaded (auto-released on funding) — the agent loop short-
    circuits it. This builder is only called for M2+ in practice, but handles
    M1 as a fallback.
    """
    supplier = financing_meta.get("issuer_name", "N/A")
    buyer = financing_meta.get("buyer_name", "N/A")
    total_amount = financing_meta.get("total_amount", "N/A")
    due_date = financing_meta.get("due_date", "N/A")

    # Common JSON output schema shared by all milestones. Specific milestones
    # override individual check names but keep the same envelope.
    _BASE_SCHEMA = (
        "Return ONLY a JSON object with this exact schema:\n"
        "{\n"
        '  "verdict": "APPROVED" | "REJECTED",\n'
        '  "confidence": 0.0-1.0,\n'
        '  "checks": { <check_name>: {"pass": true/false, "note": "..."}, ... },\n'
        '  "fail_reasons": ["..."],\n'
        '  "display_message": "One sentence summary for the supplier."\n'
        "}\n\n"
        "Confidence scoring (based on check passes):\n"
        "- all checks pass -> confidence 0.85-0.99\n"
        "- one check fails -> 0.65-0.84\n"
        "- two checks fail -> 0.40-0.64\n"
        "- three or more fail -> 0.10-0.39\n\n"
        "Set verdict to APPROVED only when you are genuinely confident the "
        "milestone is proven. When in doubt, REJECT and explain why."
    )

    if product_type == "invoice":
        return _invoice_milestone_prompt(
            milestone_idx, supplier, buyer, total_amount, due_date, _BASE_SCHEMA
        )
    return _po_milestone_prompt(
        milestone_idx, supplier, buyer, total_amount, due_date, _BASE_SCHEMA
    )


def _invoice_milestone_prompt(
    milestone_idx: int,
    supplier: str,
    buyer: str,
    total_amount: str,
    due_date: str,
    schema: str,
) -> tuple[str, str]:
    """Invoice-specific prompts for M2 and M3 (M1 is auto)."""

    if milestone_idx == 2:
        # Invoice M2: sub-vendor purchase invoice for raw materials.
        # PRD: doc type = PURCHASE invoice, supplier name fuzzy ≥80%,
        #       nominal 20–80% of parent, date valid, sub-vendor identifiable,
        #       ≤1 minor anomaly flag.
        checks_text = (
            "Run these six checks, each with a pass boolean and a short note:\n"
            "- doc_type_valid: the document is a PURCHASE invoice (sub-vendor billing supplier "
            "for raw materials). A sales invoice from supplier to buyer is the WRONG type.\n"
            f"- supplier_name_match: the supplier/consignee name on the document matches "
            f'"{supplier}" with at least 80% fuzzy similarity (typos, abbreviations OK).\n'
            f"- nominal_proportional: the amount shown is between 20% and 80% of the parent "
            f"financing amount {total_amount}. Amount exactly equal to {total_amount} is suspicious.\n"
            "- date_valid: the invoice date is present and logically before or on the due date "
            f"{due_date}. Future dates or dates after {due_date} are invalid.\n"
            "- sub_vendor_identifiable: a sub-vendor / issuing company name is clearly shown "
            "on the document (separate from the financing supplier).\n"
            "- anomaly_count_acceptable: the document has at most 1 minor visual anomaly "
            "(minor = misalignment, light smudge). Any heavy anomaly (erasure, inconsistent font, "
            "digital alteration) → fail.\n\n"
            "Use check keys exactly: doc_type_valid, supplier_name_match, nominal_proportional, "
            "date_valid, sub_vendor_identifiable, anomaly_count_acceptable.\n\n"
        )
        context = (
            f"Financing context:\n"
            f"  Product type: Invoice financing\n"
            f"  Milestone: M2 — sub-vendor purchase invoice for raw materials\n"
            f"  Supplier name: {supplier}\n"
            f"  Buyer name: {buyer}\n"
            f"  Parent financing amount: {total_amount}\n"
            f"  Financing due date: {due_date}\n\n"
        )
        return context + checks_text + schema, "invoice_m2"

    if milestone_idx == 3:
        # Invoice M3: Surat Jalan / BAST (delivery proof).
        # PRD: doc type = delivery proof, buyer name fuzzy ≥80%,
        #       semantic similarity to invoice ≥60%, quantity ≤ invoice,
        #       timeline within due+14d grace, zero heavy anomaly flags.
        checks_text = (
            "Run these five checks, each with a pass boolean and a short note:\n"
            "- doc_type_valid: the document is a delivery proof — Surat Jalan (delivery order), "
            "BAST (handover certificate), or equivalent. A purchase invoice is the WRONG type.\n"
            f"- buyer_name_match: the buyer/recipient name on the document matches "
            f'"{buyer}" with at least 80% fuzzy similarity.\n'
            "- delivery_consistent_with_invoice: the goods description, quantity, and reference "
            "numbers are semantically consistent with the underlying trade (≥60% alignment). "
            "Completely unrelated goods are a hard fail.\n"
            "- quantity_not_exceeded: the delivered quantity is less than or equal to the "
            "quantity on the parent invoice. Quantity exceeding the invoice is a fail.\n"
            f"- timeline_valid: the delivery date is on or before {due_date} + 14 calendar days "
            "(grace period). A delivery dated more than 14 days after the financing due date is invalid. "
            "Zero heavy anomaly flags (erasure, digital manipulation, forged stamps) — any heavy "
            "anomaly fails this check.\n\n"
            "Use check keys exactly: doc_type_valid, buyer_name_match, "
            "delivery_consistent_with_invoice, quantity_not_exceeded, timeline_valid.\n\n"
        )
        context = (
            f"Financing context:\n"
            f"  Product type: Invoice financing\n"
            f"  Milestone: M3 — Surat Jalan / BAST delivery proof\n"
            f"  Supplier name: {supplier}\n"
            f"  Buyer name: {buyer}\n"
            f"  Parent financing amount: {total_amount}\n"
            f"  Financing due date: {due_date}\n\n"
        )
        return context + checks_text + schema, "invoice_m3"

    # M1 fallback (should never be called in production — M1 is auto-released).
    context = (
        f"Financing context:\n"
        f"  Product type: Invoice financing\n"
        f"  Milestone: M1 — auto-released on funding (should not require manual upload)\n"
        f"  Supplier name: {supplier}\n"
        f"  Buyer name: {buyer}\n\n"
    )
    checks_text = (
        "Run these two checks:\n"
        "- doc_type_valid: the document relates to initial purchase order / contract signing.\n"
        "- doc_authenticity: the document appears genuine and untampered.\n\n"
        "Use check keys: doc_type_valid, doc_authenticity.\n\n"
    )
    return context + checks_text + schema, "invoice_m1_fallback"


def _po_milestone_prompt(
    milestone_idx: int,
    supplier: str,
    buyer: str,
    total_amount: str,
    due_date: str,
    schema: str,
) -> tuple[str, str]:
    """PO-specific prompts for M2, M3, M4 (M1 is auto)."""

    if milestone_idx == 2:
        # PO M2: invoice for raw material purchase.
        # PRD: same checks as Invoice M2 but nominal 20–75% of PO value.
        checks_text = (
            "Run these six checks, each with a pass boolean and a short note:\n"
            "- doc_type_valid: the document is a PURCHASE invoice (sub-vendor billing supplier "
            "for raw materials). A sales invoice or PO confirmation from the buyer is the WRONG type.\n"
            f"- supplier_name_match: the supplier/consignee name matches "
            f'"{supplier}" with at least 80% fuzzy similarity.\n'
            f"- nominal_proportional: the amount shown is between 20% and 75% of the PO financing "
            f"amount {total_amount}. Amount exceeding 75% of {total_amount} is disproportionate.\n"
            "- date_valid: the invoice date is present and logically consistent with the PO timeline "
            f"(on or before {due_date}).\n"
            "- sub_vendor_identifiable: a sub-vendor / issuing company name is clearly shown "
            "(separate from the financing supplier).\n"
            "- anomaly_count_acceptable: at most 1 minor visual anomaly; zero heavy anomalies "
            "(erasure, font inconsistency, digital alteration).\n\n"
            "Use check keys exactly: doc_type_valid, supplier_name_match, nominal_proportional, "
            "date_valid, sub_vendor_identifiable, anomaly_count_acceptable.\n\n"
        )
        context = (
            f"Financing context:\n"
            f"  Product type: PO financing\n"
            f"  Milestone: M2 — purchase invoice from sub-vendor for raw materials\n"
            f"  Supplier name: {supplier}\n"
            f"  Buyer name: {buyer}\n"
            f"  PO financing amount: {total_amount}\n"
            f"  Financing due date: {due_date}\n\n"
        )
        return context + checks_text + schema, "po_m2"

    if milestone_idx == 3:
        # PO M3: QC report / production photos / berita acara.
        # PRD: AI Vision checks photo metadata, EXIF consistency, visual manipulation detection.
        checks_text = (
            "Run these five checks, each with a pass boolean and a short note:\n"
            "- doc_type_valid: the document is a quality control (QC) report, berita acara "
            "(inspection certificate), or production photos. A delivery receipt, invoice, or "
            "unrelated document is the WRONG type.\n"
            "- production_evidence_visible: the document/photos show concrete production "
            "evidence — goods being manufactured, assembly in progress, or finished items "
            "ready for shipment. Generic office photos or irrelevant scenes are a fail.\n"
            "- exif_consistency: for photos, check visual EXIF consistency — timestamps visible "
            "in the image (e.g. camera overlay), metadata watermarks, or image properties "
            "consistent with real production photography. Signs of digital insertion or "
            "screenshot-of-screenshot artifacts are a fail.\n"
            "- visual_manipulation_check: detect visual manipulation — inconsistent lighting, "
            "copy-pasted elements, mismatched shadows, compression artifacts around objects "
            "suggesting digital editing. Any heavy manipulation evidence fails.\n"
            f"- supplier_context_match: the supplier/company name ({supplier}) or their "
            "facility, products, or branding appears somewhere in the document/photos, "
            "establishing traceability to this specific financing.\n\n"
            "Use check keys exactly: doc_type_valid, production_evidence_visible, "
            "exif_consistency, visual_manipulation_check, supplier_context_match.\n\n"
        )
        context = (
            f"Financing context:\n"
            f"  Product type: PO financing\n"
            f"  Milestone: M3 — QC report / production photos / berita acara\n"
            f"  Supplier name: {supplier}\n"
            f"  Buyer name: {buyer}\n"
            f"  PO financing amount: {total_amount}\n"
            f"  Financing due date: {due_date}\n\n"
        )
        return context + checks_text + schema, "po_m3"

    if milestone_idx == 4:
        # PO M4: Surat Jalan / BAST signed by buyer (final delivery).
        # PRD: buyer signature/stamp detected, fuzzy match ≥80%, quantity matches M3 within 5%,
        #       timeline ≥M3 and ≤delivery+14d.
        checks_text = (
            "Run these five checks, each with a pass boolean and a short note:\n"
            "- doc_type_valid: the document is a Surat Jalan (delivery order) or BAST "
            "(handover certificate) representing final goods delivery. An invoice or QC report "
            "is the WRONG type.\n"
            f"- buyer_signature_present: a buyer signature, rubber stamp, or official mark "
            f'from "{buyer}" (or their representative) is visibly present on the document. '
            "An unsigned/unstamped delivery note is not accepted.\n"
            f"- buyer_name_match: the buyer/recipient name on the document matches "
            f'"{buyer}" with at least 80% fuzzy similarity.\n'
            "- quantity_consistent_with_production: the delivered quantity is consistent with "
            "what was produced in M3 (within 5% tolerance). A sudden quantity discrepancy "
            "between production and delivery is suspicious.\n"
            f"- timeline_valid: the delivery date is after the production milestone date "
            f"and on or before {due_date} + 14 calendar days. Delivery before production or "
            "more than 14 days after the financing due date is invalid.\n\n"
            "Use check keys exactly: doc_type_valid, buyer_signature_present, buyer_name_match, "
            "quantity_consistent_with_production, timeline_valid.\n\n"
        )
        context = (
            f"Financing context:\n"
            f"  Product type: PO financing\n"
            f"  Milestone: M4 — Surat Jalan / BAST final delivery with buyer signature\n"
            f"  Supplier name: {supplier}\n"
            f"  Buyer name: {buyer}\n"
            f"  PO financing amount: {total_amount}\n"
            f"  Financing due date: {due_date}\n\n"
        )
        return context + checks_text + schema, "po_m4"

    # M1 fallback.
    context = (
        f"Financing context:\n"
        f"  Product type: PO financing\n"
        f"  Milestone: M1 — auto-released on funding (should not require manual upload)\n"
        f"  Supplier name: {supplier}\n"
        f"  Buyer name: {buyer}\n\n"
    )
    checks_text = (
        "Run these two checks:\n"
        "- doc_type_valid: the document relates to the initial purchase order or contract.\n"
        "- doc_authenticity: the document appears genuine and untampered.\n\n"
        "Use check keys: doc_type_valid, doc_authenticity.\n\n"
    )
    return context + checks_text + schema, "po_m1_fallback"

_DOCUMENT_SYSTEM_PROMPT = (
    "You are a financial document verifier for an invoice financing platform "
    "in Indonesia. Assess whether an uploaded document is a genuine, usable "
    "trade document. Be strict but fair. Return ONLY valid JSON."
)


def _build_source_block(file_bytes: bytes) -> dict:
    """Build an Anthropic content block (document for PDF, image otherwise),
    sniffing the media type from the leading bytes."""
    if file_bytes[:4] == b"%PDF":
        return {
            "type": "document",
            "source": {
                "type": "base64",
                "media_type": "application/pdf",
                "data": base64.b64encode(file_bytes).decode(),
            },
        }

    # Image type sniffing. Anthropic supports image/jpeg, image/png, image/gif, image/webp.
    if file_bytes[:2] == b"\xff\xd8":
        media_type = "image/jpeg"
    elif file_bytes[:4] == b"\x89PNG":
        media_type = "image/png"
    elif file_bytes[:6] in (b"GIF87a", b"GIF89a"):
        media_type = "image/gif"
    elif file_bytes[:4] == b"RIFF" and file_bytes[8:12] == b"WEBP":
        media_type = "image/webp"
    else:
        media_type = "image/jpeg"

    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.b64encode(file_bytes).decode(),
        },
    }


def _extract_json(text: str) -> dict:
    """Pull a JSON object out of Claude's text response, tolerating markdown
    code fences and surrounding prose."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        # Drop the opening fence (``` or ```json) and the trailing fence.
        cleaned = cleaned.split("\n", 1)[1] if "\n" in cleaned else cleaned
        cleaned = cleaned.strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[: -len("```")]
        cleaned = cleaned.strip()

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        # Fall back to the outermost {...} span.
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(cleaned[start : end + 1])
        raise


class ClaudeAIVerifier(AIVerifier):
    """Real verifier backed by Claude Vision via the Anthropic SDK.

    One API call per verification, structured JSON output. The SDK picks up
    ANTHROPIC_API_KEY from the environment automatically."""

    def __init__(self) -> None:
        # Imported lazily so the mock path never requires the SDK to be present
        # or an API key to be set.
        from anthropic import AsyncAnthropic

        settings = get_settings()
        self._model = settings.anthropic_model
        # api_key=None lets the SDK fall back to ANTHROPIC_API_KEY in env; pass
        # the configured key when one is present so .env is respected too.
        self._client = AsyncAnthropic(
            api_key=settings.anthropic_api_key or None
        )

    async def verify_milestone(
        self,
        file_bytes: bytes,
        milestone_idx: int,
        product_type: DocumentType,
        financing_meta: dict,
    ) -> MilestoneVerifyResult:
        # Build a specialized prompt for this exact (product_type, milestone_idx) combo.
        # Each milestone has different document expectations, check names, and thresholds
        # per PRD v3.0 §Milestone Details.
        prompt, _prompt_tag = _build_milestone_prompt(
            milestone_idx=milestone_idx,
            product_type=str(product_type),
            financing_meta=financing_meta,
        )

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=_MILESTONE_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            _build_source_block(file_bytes),
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
        except Exception as exc:  # anthropic.APIError and transport errors
            log.exception("Claude milestone verification failed: %s", exc)
            return MilestoneVerifyResult(
                confidence=0.1,
                verdict="REJECTED",
                checks={},
                fail_reasons=["AI service error"],
                display_message="Verification could not be completed automatically.",
            )

        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        try:
            data = _extract_json(text)
            verdict = data["verdict"]
            if verdict not in ("APPROVED", "REJECTED"):
                raise ValueError(f"unexpected verdict: {verdict!r}")
            return MilestoneVerifyResult(
                confidence=min(1.0, max(0.0, float(data["confidence"]))),
                verdict=verdict,
                checks=data.get("checks", {}),
                fail_reasons=list(data.get("fail_reasons", [])),
                display_message=str(data.get("display_message", "")),
            )
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            log.warning("Could not parse Claude milestone response: %s", exc)
            return MilestoneVerifyResult(
                confidence=0.1,
                verdict="REJECTED",
                checks={},
                fail_reasons=["AI parse error"],
                display_message="Verification could not be completed automatically.",
            )

    async def verify_document(
        self,
        file_bytes: bytes,
        document_type: DocumentType,
        metadata: dict,
    ) -> VerifyResult:
        prompt = (
            f"Is this a valid {document_type} document? "
            "Score these dimensions from 0 to 100:\n"
            "- doc_authenticity: how genuine and untampered the document looks.\n"
            "- counterparty_legibility: how clearly the counterparties "
            "(issuer and buyer) are identified and legible.\n"
            "- relationship_clarity: how clearly the trade relationship and "
            "obligation are expressed.\n\n"
            f"Metadata for reference: {json.dumps(metadata, default=str)}\n\n"
            "Return ONLY a JSON object with this exact schema:\n"
            "{\n"
            '  "doc_authenticity": 0-100,\n'
            '  "counterparty_legibility": 0-100,\n'
            '  "relationship_clarity": 0-100,\n'
            '  "flags": ["..."]\n'
            "}"
        )

        try:
            response = await self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=_DOCUMENT_SYSTEM_PROMPT,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            _build_source_block(file_bytes),
                            {"type": "text", "text": prompt},
                        ],
                    }
                ],
            )
        except Exception as exc:  # anthropic.APIError and transport errors
            log.exception("Claude document verification failed: %s", exc)
            return self._safe_document_result(["AI service error"])

        text = "".join(
            block.text for block in response.content if block.type == "text"
        )
        try:
            data = _extract_json(text)
            doc = int(data["doc_authenticity"])
            counterparty = int(data["counterparty_legibility"])
            relationship = int(data["relationship_clarity"])
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as exc:
            log.warning("Could not parse Claude document response: %s", exc)
            return self._safe_document_result(["AI parse error"])

        risk = int(
            0.4 * doc + 0.3 * counterparty + 0.2 * relationship + 0.1 * 100
        )
        if document_type == "po":
            risk = max(0, risk - 10)

        return VerifyResult(
            risk_score=risk,
            risk_tier=_tier_for(risk),  # type: ignore[arg-type]
            doc_score=doc,
            counterparty_score=counterparty,
            relationship_score=relationship,
            unique=True,
            flags=list(data.get("flags", [])),
            stages={
                "A": {"score": doc, "notes": "claude: authenticity"},
                "B": {"score": counterparty, "notes": "claude: counterparty legibility"},
                "C": {"score": relationship, "notes": "claude: relationship clarity"},
                "D": {"unique": True, "notes": "not checked at upload time"},
            },
        )

    @staticmethod
    def _safe_document_result(flags: List[str]) -> VerifyResult:
        """Mid-range fallback when the document check cannot be completed."""
        return VerifyResult(
            risk_score=50,
            risk_tier="medium",
            doc_score=50,
            counterparty_score=50,
            relationship_score=50,
            unique=True,
            flags=flags,
            stages={},
        )


_real_verifier_singleton: Optional[AIVerifier] = None


def get_ai_verifier() -> AIVerifier:
    global _real_verifier_singleton
    settings = get_settings()
    if settings.ai_mock_mode:
        return MockAIVerifier()
    if _real_verifier_singleton is None:
        _real_verifier_singleton = ClaudeAIVerifier()
    return _real_verifier_singleton
