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
- `POST /agent/webhook` — Goldsky-only, X-Webhook-Secret required
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
- If `token_id` is not yet in the DB when the webhook fires, the response is
  `{ queued: false, error: "..." }` (HTTP 200). Goldsky should retry; the
  agent is idempotent on `(financing_id, milestone_idx, ipfs_hash)`.

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
