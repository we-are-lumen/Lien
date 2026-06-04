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
- `POST /financing/{id}/milestone-proof`
- `GET /marketplace`
- `POST /documents/upload`
- `GET /milestones/options`
