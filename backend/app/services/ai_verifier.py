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

import hashlib
from dataclasses import dataclass, field
from typing import Dict, List, Literal

from app.core.config import get_settings


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


def get_ai_verifier() -> AIVerifier:
    settings = get_settings()
    if settings.ai_mock_mode:
        return MockAIVerifier()
    raise NotImplementedError("Real AI verifier not wired yet — set AI_MOCK_MODE=true")
