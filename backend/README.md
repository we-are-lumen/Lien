# LIEN Backend

FastAPI service backing the LIEN invoice/PO financing platform.

## Quick start

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env  # then fill in values
uvicorn app.main:app --reload --port 8000
```

OpenAPI docs at http://localhost:8000/docs

## Layout

```
app/
  main.py              FastAPI app factory + router registration
  core/
    config.py          env-driven settings
    auth.py            wallet-signature auth + JWT
    db.py              Supabase client factory
    errors.py          shared exceptions
  models/
    schemas.py         Pydantic request/response models
  services/
    repos.py           Supabase queries (one place for all SQL)
    ai_verifier.py     AI verification (mock + real interface)
    ipfs.py            IPFS uploads (mock + Pinata interface)
    chain.py           On-chain calls (mock + real interface)
    pricing.py         Risk tier + pricing formulas
    doc_hash.py        keccak256 doc registry hash
    milestones.py      Milestone spec by product type
  routers/
    health.py          GET /health
    auth.py            /auth/* and /me
    stats.py           /<role>/stats/{user_id}
    financing.py       /financing/*, /marketplace, /milestones/options
    documents.py       POST /documents/upload
tests/                 pytest suite (pure unit tests, no DB)
supabase/migrations/   SQL migrations (applied separately)
```

## Mock mode

By default the service runs in mock mode for AI, IPFS, and on-chain calls.
This lets the FE integrate against the full API surface before external
services are wired up.

| Flag | Default | Effect when true |
|------|---------|------------------|
| `AI_MOCK_MODE` | true | Deterministic risk scores derived from file hash |
| `IPFS_MOCK_MODE` | true | Returns a fake CID; no network call |
| `CHAIN_MOCK_MODE` | true | In-memory registry + fake tx hashes |

Flip a flag to `false` to use the real implementation. Real implementations
are stubbed for now and will raise `NotImplementedError` until wired up.

## Tests

```bash
pytest -v
```

15 unit tests cover pricing, doc hash, AI verifier, and milestone config.
None require a database or network connection.

## Endpoints

See the [Notion API contract](https://app.notion.com/p/API-Contract-37555b001e6a8092909de9c23a7fd1d9) for the FE-facing schema.

Implemented endpoints:

- `GET /health`
- `GET /auth/nonce/{address}`, `POST /auth/verify`, `GET /me`
- `GET /<role>/stats/{user_id}` (suppliers, investors, buyers)
- `GET /<role>/financing` (suppliers, investors, buyers)
- `GET /financing/{id}`, `GET /financing/{id}/report`
- `POST /financing/{id}/fund`
- `POST /financing/{id}/milestone-proof` — IPFS upload only, see [Milestone proof flow](#milestone-proof-flow-fe-integration)
- `GET /marketplace`
- `POST /documents/upload`
- `GET /milestones/options`
- `POST /agent/webhook` — Goldsky-only, X-Webhook-Secret required (ProofSubmitted)
- `POST /agent/funded-webhook` — Goldsky-only (FundedWithRef) — writes `financings.token_id`
- `POST /agent/repaid-webhook` — Goldsky-only (Repaid) — flips status to `repaid`
- `GET /agent/status` — last 20 queue jobs + decisions
- `GET /agent/decisions/{financing_id}` — full verdict history for a financing

## Milestone proof flow (FE integration)

**The autonomous AI agent owns milestone verification.** The HTTP endpoint
`POST /financing/{id}/milestone-proof` is now IPFS-only — it does not call
Claude and does not release the milestone on-chain. That work runs inside the
agent loop, triggered by an on-chain `ProofSubmitted` event.

### Sequence

```
1. Supplier uploads file
     POST /financing/{financing_id}/milestone-proof
       form: file=<binary>, milestone_idx=<2|3|4>
     -> 200 { milestone_id, cid, url, status: "proof_uploaded", next_step }

2. Supplier signs an on-chain tx (wagmi/viem):
     FundingPool.submitProof(tokenId, milestoneIdx, cid)
     -> emits ProofSubmitted(tokenId, milestoneIdx, ipfsCid, supplier)

3. Goldsky subgraph indexes the event and POSTs:
     POST /agent/webhook  (X-Webhook-Secret header)
       { token_id, milestone_idx, ipfs_hash, submitted_by }
     -> 200 { queued: true, queue_id }

4. Agent loop picks up the job (within ~5s):
     - fetches the file from IPFS
     - runs Claude Vision verification
     - if APPROVED + confidence >= 0.75 -> releaseMilestone() on-chain
     - writes agent_decisions audit row
     - sets milestone.status to released/rejected/escalated

5. FE polls for the result:
     GET /agent/decisions/{financing_id}
     -> 200 { decisions: [{ milestone_idx, verdict, confidence, tx_hash, ... }] }
```

### Why two calls instead of one

Step 1 puts the file on IPFS so it has a content-addressable identifier
before any on-chain reference exists. Step 2 is the on-chain commitment that
the proof was submitted — Goldsky listens to that event, not to HTTP traffic.
The agent then acts on the event, not on the HTTP upload. This is what makes
the verification autonomous rather than a synchronous tool call.

### Polling

`GET /agent/decisions/{financing_id}` is cheap (single indexed query). Poll
every 2-5 seconds while a milestone is in `proof_uploaded` state. Stop polling
when:
- `milestone.status == "released"` (success: `tx_hash` is in the latest decision)
- `milestone.status == "rejected"` (resubmittable: upload a new file, repeat from step 1)
- `milestone.status == "escalated"` (low confidence: manual review required)

The milestone status is the source of truth; the decision rows are the audit
trail.

### Constraints

- `milestone_idx` must be in `2..4`. **M1 auto-releases at funding time** —
  do not call `/milestone-proof` for M1 and do not call `submitProof(_, 1, _)`
  on-chain (the contract reverts with `CannotSubmitM1`).
- The CID returned in step 1 is the exact string to pass to `submitProof`.
  No transformation needed.
- Goldsky must include header `X-Webhook-Secret: <WEBHOOK_SECRET>` matching
  the backend setting. The webhook handler verifies via `hmac.compare_digest`.
  In **real-chain mode** (`CHAIN_MOCK_MODE=false`), an unset `WEBHOOK_SECRET`
  is treated as a misconfiguration and the handler refuses ALL webhook
  traffic (HTTP 401). Dev/mock mode (`CHAIN_MOCK_MODE=true`) still accepts
  unsigned calls so local development isn't blocked.
- If `token_id` is not yet in the DB when the webhook fires, the response is
  **HTTP 503** with `detail: "No financing found...; retry later"`. Goldsky's
  at-least-once delivery retries with backoff. The agent is idempotent on
  `(financing_id, milestone_idx, ipfs_hash)`.

### Dev shortcut

For local testing without Goldsky, POST directly to `/agent/webhook` after
step 1. Same payload shape:

```bash
curl -X POST http://localhost:8000/agent/webhook \
  -H "Content-Type: application/json" \
  -H "X-Webhook-Secret: $WEBHOOK_SECRET" \
  -d '{
    "token_id": 42,
    "milestone_idx": 2,
    "ipfs_hash": "<cid from step 1>",
    "submitted_by": "0xabc..."
  }'
```

Watch agent activity in the FastAPI server logs (look for `agent:` prefix).

## Funding flow (FE integration)

The on-chain `fund()` tx is the source of truth. In real mode, the BE does
not call `releaseMilestone()` for M1 — the contract auto-releases it inside
`fund()`. The BE only learns about funding when Goldsky delivers the event.

### Use `fundWithRef`, not `fund`

`FundingPool.fund(...)` works but emits only `Funded(tokenId, investor, amount)`,
which gives us no way to map `tokenId` back to a `financings.id` UUID.

`FundingPool.fundWithRef(..., bytes32 financingRef)` additionally emits
`FundedWithRef(tokenId, investor, financingRef, amount)`. The BE indexes
incoming `financingRef` values against `keccak256(<financing_id UUID string>)`
to resolve the financing row deterministically.

### Sequence

```
1. Investor calls fundWithRef on-chain (wagmi/viem):
     const ref = keccak256(toUtf8Bytes(financingId))  // financingId is the UUID string
     FundingPool.fundWithRef(
       tokenId, fundedAmount, totalRepayment, supplier,
       milestoneCount, milestoneSplitBps, nominal,
       ref
     )
     -> emits Funded + FundedWithRef
     -> M1 auto-released to supplier on the same tx

2. Goldsky indexes FundedWithRef and POSTs:
     POST /agent/funded-webhook  (X-Webhook-Secret header)
       { token_id, investor, amount, financing_ref, tx_hash }
     -> 200 { applied: true, financing_id, token_id }

3. BE writes financings.token_id + status=funded + fund_tx_hash.
   FE polls GET /financing/{id} to see the new status.
```

### Idempotency

Replays of the same `(financing_ref, token_id)` return `{ applied: false,
duplicate: true }`. A `token_id` mismatch against an already-mapped
financing is logged at WARNING and refused (HTTP 200 with
`error: "token_id_mismatch"`).

### Race: webhook before DB row

If the chain event arrives before the financing row is visible (e.g. the
publish transaction and the fund transaction land in the same block), the
handler returns **HTTP 503** with `detail: "No financing matches...; retry
later"`. Goldsky's at-least-once delivery retries with backoff. A 2xx
response would ACK and permanently drop the event.

## Repayment flow

`FundingPool.repay(tokenId)` emits `Repaid(tokenId, totalPaid, toInvestor)`.
Goldsky POSTs to `/agent/repaid-webhook`; the BE resolves `tokenId`
to a financing row via `financings.token_id` and sets `status=repaid`,
`payment_status=paid`, `repay_tx_hash`. Idempotent.

If the financing is in any status other than `funded` or `in_progress`
(the only valid source states for Repaid per the state machine), the
handler records `repay_tx_hash` only and leaves status untouched —
operational anomaly worth surfacing in logs, but the off-chain state
machine isn't overridden by a stray on-chain payment.

Missing `token_id` (Repaid arrived before FundedWithRef indexed) returns
HTTP 503 so Goldsky retries.

## Buyer name visibility

`GET /financing/{id}` returns a different `buyer_name` per viewer:

- **Supplier / buyer of this financing** -> raw name.
- **Everyone else (investors, public, unauthenticated)** -> raw name iff
  the buyer is on the IDX-listed whitelist (`app/assets/idx_buyers.json`),
  otherwise `Buyer_<8-char hash>`.

The hash is a deterministic prefix of `sha256(normalized buyer name)`, so
the same opaque label appears across listings for a recurring buyer.

Marketplace listings (`GET /marketplace`) already omit `buyer_name`
entirely, no change there.

