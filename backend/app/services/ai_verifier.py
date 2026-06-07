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
        return MilestoneVerifyResult(
            confidence=confidence,
            verdict=verdict,
            checks={
                "doc_type_valid": {"pass": True, "note": "mock"},
                "supplier_name_match": {"pass": True, "note": "mock fuzzy 94%"},
                "nominal_proportional": {"pass": True, "note": "mock"},
                "date_valid": {"pass": True, "note": "mock"},
                "doc_authenticity": {"pass": True, "note": "mock"},
            },
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
        milestone_desc = _MILESTONE_DESCRIPTIONS.get(
            milestone_idx, f"Milestone {milestone_idx}"
        )
        prompt = (
            f"Verify this proof document for a milestone in a {product_type} "
            "financing transaction.\n\n"
            f"Milestone to prove: {milestone_desc}\n"
            f"Supplier (issuer) name: {financing_meta.get('issuer_name', 'N/A')}\n"
            f"Buyer name: {financing_meta.get('buyer_name', 'N/A')}\n"
            f"Expected total amount: {financing_meta.get('total_amount', 'N/A')}\n"
            f"Due date: {financing_meta.get('due_date', 'N/A')}\n\n"
            "Run these five checks, each with a pass boolean and a short note:\n"
            "- doc_type_valid: the document type matches what this milestone needs.\n"
            "- supplier_name_match: the supplier name appears and matches.\n"
            "- nominal_proportional: any amount shown is proportional/consistent "
            "with the expected total.\n"
            "- date_valid: dates are present and consistent with the due date.\n"
            "- doc_authenticity: the document looks genuine, not tampered.\n\n"
            "Confidence scoring rule based on how many of the 5 checks pass:\n"
            "- all 5 pass -> confidence 0.85-0.99\n"
            "- 4/5 pass -> 0.65-0.84\n"
            "- 3/5 pass -> 0.40-0.64\n"
            "- fewer than 3 pass -> 0.10-0.39\n\n"
            "Set verdict to APPROVED only when you are confident the milestone "
            "is genuinely proven, otherwise REJECTED.\n\n"
            "Return ONLY a JSON object with this exact schema:\n"
            "{\n"
            '  "verdict": "APPROVED" | "REJECTED",\n'
            '  "confidence": 0.0-1.0,\n'
            '  "checks": {\n'
            '    "doc_type_valid": {"pass": true, "note": "..."},\n'
            '    "supplier_name_match": {"pass": true, "note": "..."},\n'
            '    "nominal_proportional": {"pass": true, "note": "..."},\n'
            '    "date_valid": {"pass": true, "note": "..."},\n'
            '    "doc_authenticity": {"pass": true, "note": "..."}\n'
            "  },\n"
            '  "fail_reasons": ["..."],\n'
            '  "display_message": "One sentence summary for the supplier."\n'
            "}"
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
