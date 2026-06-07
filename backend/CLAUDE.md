# LIEN Backend — Real Claude Vision AI Verifier

## Context
Invoice-financing DeFi platform on Mantle. Suppliers submit milestone proof documents
(PDF/image scans). An AI agent verifies them and decides whether to release on-chain payment.

## Git
- branch: feat/be-agent-loop (already checked out)
- no em dashes in commit messages

## Stack
- FastAPI, Python 3.11, anthropic SDK already in requirements.txt
- `AI_MOCK_MODE=true` → MockAIVerifier (untouched), `AI_MOCK_MODE=false` → real Claude Vision
- File types: PDF and image (JPEG/PNG). Supplier uploads proof of milestone completion.

---

## TASK: Implement `ClaudeAIVerifier` in `app/services/ai_verifier.py`

### What it does
Replace the `raise NotImplementedError` in `get_ai_verifier()` with a real
`ClaudeAIVerifier` class that:
1. Calls Claude Vision (`claude-opus-4-5` model, or configurable via `ANTHROPIC_MODEL` env var)
   via the `anthropic` Python SDK
2. Returns the same `MilestoneVerifyResult` and `VerifyResult` dataclasses the mock returns
3. Uses `ANTHROPIC_API_KEY` from environment (the SDK picks it up automatically)

### `verify_milestone` — the main method (priority)

Called by the agent loop for every milestone proof. Receives:
- `file_bytes: bytes` — raw bytes of the uploaded proof file
- `milestone_idx: int` — which milestone (1-4)
- `product_type: Literal["invoice", "po"]`
- `financing_meta: dict` — keys: `issuer_name`, `buyer_name`, `total_amount`, `due_date`

**What each milestone means (hardcode this mapping):**
```
1 → "Purchase Order / Contract signed"
2 → "Goods shipped / delivery in transit (shipping docs, bill of lading)"
3 → "Goods received / delivery confirmed (delivery receipt, proof of receipt)"
4 → "Invoice paid / payment confirmation"
```

**Single-call approach** — one Claude API call per verification, structured JSON output:

System prompt (roughly):
```
You are a financial document verifier for an invoice financing platform in Indonesia.
Your job is to verify that a supplier's proof document genuinely demonstrates
completion of a specific milestone in a trade financing transaction.
Be strict but fair. Return ONLY valid JSON.
```

User message content:
- The document as a vision content block (base64 image or PDF)
- A text block with: milestone description, supplier name, buyer name, expected amount, due date

Ask Claude to return JSON matching this schema:
```json
{
  "verdict": "APPROVED" | "REJECTED",
  "confidence": 0.0-1.0,
  "checks": {
    "doc_type_valid": {"pass": true/false, "note": "..."},
    "supplier_name_match": {"pass": true/false, "note": "..."},
    "nominal_proportional": {"pass": true/false, "note": "..."},
    "date_valid": {"pass": true/false, "note": "..."},
    "doc_authenticity": {"pass": true/false, "note": "..."}
  },
  "fail_reasons": ["..."],
  "display_message": "One sentence summary for the supplier."
}
```

**Confidence scoring rule** (tell Claude in the prompt):
- All 5 checks pass → confidence 0.85-0.99
- 4/5 pass → 0.65-0.84
- 3/5 pass → 0.40-0.64
- <3 pass → 0.10-0.39

**File handling:**
- If bytes look like a PDF (`file_bytes[:4] == b"%PDF"`):
  - Use `base64.b64encode(file_bytes).decode()` with `media_type="application/pdf"`
  - Pass as document content block: `{"type": "document", "source": {"type": "base64", "media_type": "application/pdf", "data": <b64>}}`
- Otherwise treat as image:
  - Sniff media type from first bytes: `\xff\xd8` → `image/jpeg`, `\x89PNG` → `image/png`, else `image/jpeg`
  - Pass as image content block: `{"type": "image", "source": {"type": "base64", "media_type": <type>, "data": <b64>}}`

**Parse response:**
- Extract JSON from Claude's text response (may have markdown fences — strip them)
- If parse fails or Claude returns unexpected shape: return `MilestoneVerifyResult` with
  `verdict="REJECTED"`, `confidence=0.1`, `fail_reasons=["AI parse error"]`

### `verify_document` — secondary method (document upload check)

Simpler version used at document upload time (not milestone time). Receives:
- `file_bytes`, `document_type` ("invoice" or "po"), `metadata: dict`

Single Claude call. Ask: "Is this a valid {document_type} document? Score these dimensions 0-100:
doc authenticity, counterparty legibility, relationship clarity."

Return `VerifyResult` with real scores from Claude's JSON response.
Same PDF/image handling as above.

If parse fails: return a safe mid-range `VerifyResult` with `risk_score=50`, tier="medium".

### Config additions needed in `app/core/config.py`
- `anthropic_api_key: str = ""` — read from env `ANTHROPIC_API_KEY`
- `anthropic_model: str = "claude-opus-4-5"` — which Claude model to use

### Wire it up in `get_ai_verifier()`
```python
def get_ai_verifier() -> AIVerifier:
    settings = get_settings()
    if settings.ai_mock_mode:
        return MockAIVerifier()
    return ClaudeAIVerifier()  # uses ANTHROPIC_API_KEY from env
```

---

## Files to READ first
- `app/services/ai_verifier.py` — full file, understand existing dataclasses + mock
- `app/core/config.py` — to add new settings fields correctly

## Quality bar
- Async throughout (the SDK has `AsyncAnthropic`)
- Type hints on all new functions/methods
- Don't modify MockAIVerifier or existing dataclasses
- Handle API errors gracefully (catch `anthropic.APIError`, log, return REJECTED with low confidence)
- Single commit: "feat: implement real Claude Vision AI verifier"
