# LIEN API Specification

Base URL: `/api`  
Auth: Bearer JWT (obtained via `/auth/verify`)  
All amounts in USDT0 (6 decimal places)

---

## AUTH

### GET /auth/nonce/{address}
Get challenge message to sign.

**Response 200**
```json
{
  "nonce": "Sign this to login to LIEN: a3f9c2d1",
  "expires_at": "2026-06-02T12:00:00Z"
}
```

### POST /auth/verify
Verify wallet signature, return JWT.

**Body**
```json
{
  "address": "0x...",
  "signature": "0x..."
}
```

**Response 200**
```json
{
  "access_token": "eyJ...",
  "token_type": "bearer",
  "expires_in": 86400,
  "address": "0x..."
}
```

### GET /me
Get current user context. Auth required.

**Response 200**
```json
{
  "address": "0x...",
  "reputation_score": 85,
  "reputation_tier": "low",
  "total_financed": "45000.000000",
  "total_repaid": "30000.000000",
  "active_financing_count": 2,
  "is_blacklisted": false
}
```

---

## INVOICES

### POST /invoice/upload
Upload invoice document, trigger AI verification pipeline. Auth required.

**Body** `multipart/form-data`
```
file: <pdf/image>
invoice_number: string
issuer_name: string
counterparty_name: string
amount: string (numeric)
due_date: string (YYYY-MM-DD)
```

**Response 202** *(pipeline async)*
```json
{
  "invoice_id": "uuid",
  "ipfs_hash": "Qm...",
  "status": "verifying",
  "estimated_completion_seconds": 15
}
```

### GET /invoices
List invoices owned by current user. Auth required.

**Query params:** `status`, `page`, `limit`

**Response 200**
```json
{
  "items": [
    {
      "id": "uuid",
      "invoice_number": "INV-001",
      "counterparty_name": "PT Buyer ABC",
      "amount": "15000.000000",
      "due_date": "2026-07-01",
      "status": "verified",
      "ai_score": 0.82,
      "risk_tier": "low",
      "created_at": "2026-06-01T10:00:00Z"
    }
  ],
  "total": 5,
  "page": 1,
  "limit": 20
}
```

### GET /invoice/{id}
Get invoice detail + full AI report. Auth required (owner only).

**Response 200**
```json
{
  "id": "uuid",
  "ipfs_hash": "Qm...",
  "invoice_number": "INV-001",
  "issuer_name": "CV Supplier",
  "counterparty_name": "PT Buyer ABC",
  "amount": "15000.000000",
  "due_date": "2026-07-01",
  "status": "verified",
  "ai_score": 0.82,
  "risk_tier": "low",
  "ai_stage_a": { "passed": true, "anomalies": [], "confidence": 0.91 },
  "ai_stage_b": { "passed": true, "npwp_verified": true, "confidence": 0.85 },
  "ai_stage_c": { "passed": true, "relationship_score": 0.78, "confidence": 0.80 },
  "ai_stage_d": { "passed": true, "duplicate_found": false, "confidence": 1.0 },
  "created_at": "2026-06-01T10:00:00Z"
}
```

---

## PURCHASE ORDERS

### POST /po/upload
Upload PO document, trigger AI verification pipeline. Auth required.

**Body** `multipart/form-data`
```
file: <pdf/image>
po_number: string
issuer_name: string
counterparty_name: string
total_amount: string (numeric)
delivery_date: string (YYYY-MM-DD)
buyer_confirmed: boolean (optional, default false)
```

**Response 202**
```json
{
  "po_id": "uuid",
  "ipfs_hash": "Qm...",
  "status": "verifying",
  "estimated_completion_seconds": 15
}
```

### GET /purchase-orders
List POs owned by current user. Auth required.

**Query params:** `status`, `page`, `limit`

**Response 200** *(same shape as GET /invoices)*

### GET /po/{id}
Get PO detail + full AI report. Auth required (owner only).

**Response 200** *(same shape as GET /invoice/{id} + `delivery_date`, `buyer_confirmed`)*

---

## FINANCING

### POST /financing
Create financing request from a verified document. Auth required (owner only).

**Body**
```json
{
  "document_type": "invoice",
  "document_id": "uuid",
  "requested_amount": "14753.000000",
  "interest_rate_bps": 1000,
  "tenure_days": 60
}
```

**Response 201**
```json
{
  "financing_id": "uuid",
  "document_type": "invoice",
  "requested_amount": "14753.000000",
  "advance_rate": 1.0,
  "origination_fee": "225.000000",
  "net_disbursement": "14528.000000",
  "interest_rate_bps": 1000,
  "tenure_days": 60,
  "status": "open",
  "milestones": [
    { "index": 1, "percentage": 30, "amount": "4350.000000", "status": "pending" },
    { "index": 2, "percentage": 50, "amount": "7250.000000", "status": "pending" },
    { "index": 3, "percentage": 20, "amount": "2900.000000", "status": "pending" }
  ]
}
```

### GET /financing
List financing requests by current user. Auth required.

**Query params:** `role` (borrower|investor), `status`, `page`, `limit`

**Response 200**
```json
{
  "items": [
    {
      "id": "uuid",
      "document_type": "invoice",
      "requested_amount": "14753.000000",
      "funded_amount": "14753.000000",
      "status": "active",
      "interest_rate_bps": 1000,
      "tenure_days": 60,
      "funded_at": "2026-06-01T12:00:00Z"
    }
  ],
  "total": 3,
  "page": 1,
  "limit": 20
}
```

### GET /financing/{id}/status
Get financing status. Public.

**Response 200**
```json
{
  "id": "uuid",
  "status": "active",
  "funded_amount": "14753.000000",
  "milestones": [
    { "index": 1, "status": "released", "tx_hash": "0x..." },
    { "index": 2, "status": "verified", "tx_hash": null },
    { "index": 3, "status": "pending", "tx_hash": null }
  ]
}
```

### GET /financing/{id}/report
Full AI verification report. Public.

**Response 200**
```json
{
  "financing_id": "uuid",
  "document_type": "invoice",
  "ai_score": 0.82,
  "risk_tier": "low",
  "stages": {
    "a": { "passed": true, "anomalies": [], "confidence": 0.91 },
    "b": { "passed": true, "npwp_verified": true, "confidence": 0.85 },
    "c": { "passed": true, "relationship_score": 0.78, "confidence": 0.80 },
    "d": { "passed": true, "duplicate_found": false, "confidence": 1.0 }
  },
  "agent_decisions": [
    {
      "milestone_index": 1,
      "verdict": "approved",
      "confidence_score": 0.91,
      "tx_hash": "0x...",
      "created_at": "2026-06-01T12:05:00Z"
    }
  ]
}
```

### POST /financing/{id}/fund
Investor funds a financing request. Auth required.

**Body**
```json
{
  "amount": "14753.000000",
  "tx_hash": "0x..."
}
```

**Response 200**
```json
{
  "financing_id": "uuid",
  "status": "funded",
  "funded_amount": "14753.000000",
  "token_id": 42,
  "m1_released": true,
  "m1_tx_hash": "0x..."
}
```

### POST /financing/{id}/milestone
Submit milestone proof. Auth required (borrower only).

**Body** `multipart/form-data`
```
milestone_index: integer (1-4)
file: <pdf/image>
```

**Response 202**
```json
{
  "milestone_id": "uuid",
  "milestone_index": 2,
  "proof_ipfs_hash": "Qm...",
  "status": "submitted",
  "message": "AI verification in progress"
}
```

---

## MARKETPLACE

### GET /marketplace
List open financing requests. Public.

**Query params:** `document_type` (invoice|po), `risk_tier` (low|medium|high), `min_amount`, `max_amount`, `min_yield_bps`, `max_tenor_days`, `page`, `limit`

**Response 200**
```json
{
  "items": [
    {
      "id": "uuid",
      "document_type": "invoice",
      "counterparty_name": "PT Buyer ABC",
      "amount": "14753.000000",
      "interest_rate_bps": 1000,
      "tenure_days": 60,
      "risk_tier": "low",
      "ai_score": 0.82,
      "funded_amount": "0.000000",
      "created_at": "2026-06-01T10:00:00Z"
    }
  ],
  "total": 12,
  "page": 1,
  "limit": 20
}
```

*Note: non-IDX buyer names are hashed per PRD.*

---

## PORTFOLIO

### GET /portfolio/{address}
Portfolio summary. Auth required (own address only).

**Response 200**
```json
{
  "address": "0x...",
  "as_borrower": {
    "total_financed": "45000.000000",
    "total_repaid": "30000.000000",
    "active_count": 2,
    "completed_count": 3,
    "defaulted_count": 0
  },
  "as_investor": {
    "total_invested": "20000.000000",
    "total_yield_earned": "1800.000000",
    "active_positions": [
      {
        "financing_id": "uuid",
        "amount": "10000.000000",
        "yield_bps": 1000,
        "tenure_days": 60,
        "status": "active"
      }
    ]
  },
  "reputation_score": 85,
  "reputation_tier": "low"
}
```

---

## REPAYMENT

### POST /repayment/{id}
Borrower reports repayment, triggers yield distribution. Auth required (borrower only).

**Body**
```json
{
  "tx_hash": "0x..."
}
```

**Response 200**
```json
{
  "financing_id": "uuid",
  "status": "completed",
  "repaid_amount": "14753.000000",
  "yield_distributed": "222.300000",
  "performance_fee": "24.700000",
  "tx_hash": "0x..."
}
```

---

## AGENT (BE-8 / BE-9)

### POST /agent/webhook
Goldsky sends on-chain events here. Internal (no auth, validate via shared secret).

**Body**
```json
{
  "event_type": "ProofSubmitted",
  "financing_id": "uuid",
  "milestone_index": 2,
  "proof_ipfs_hash": "Qm...",
  "submitted_by": "0x...",
  "tx_hash": "0x...",
  "block_number": 12345678
}
```

**Response 200**
```json
{ "queued": true, "queue_id": "uuid" }
```

### GET /agent/status
Agent loop health + queue stats. Internal.

**Response 200**
```json
{
  "status": "running",
  "queue_pending": 2,
  "queue_processing": 1,
  "processed_24h": 14,
  "last_decision_at": "2026-06-02T11:00:00Z"
}
```

### GET /agent/log
Recent agent decisions. Public (audit trail).

**Query params:** `financing_id`, `verdict`, `page`, `limit`

**Response 200**
```json
{
  "items": [
    {
      "id": "uuid",
      "financing_id": "uuid",
      "milestone_index": 2,
      "verdict": "approved",
      "confidence_score": 0.87,
      "reasoning": "All checks passed. Delivery document matches invoice within threshold.",
      "tx_hash": "0x...",
      "created_at": "2026-06-02T11:00:00Z"
    }
  ],
  "total": 42,
  "page": 1,
  "limit": 20
}
```

---

## HEALTH

### GET /health
**Response 200**
```json
{ "status": "ok", "service": "lien-backend", "version": "0.1.0" }
```

---

## Summary

| # | Method | Endpoint | Auth | PRD |
|---|--------|----------|------|-----|
| 1 | GET | `/auth/nonce/{address}` | - | gap |
| 2 | POST | `/auth/verify` | - | gap |
| 3 | GET | `/me` | JWT | gap |
| 4 | POST | `/invoice/upload` | JWT | ✓ |
| 5 | GET | `/invoices` | JWT | gap |
| 6 | GET | `/invoice/{id}` | JWT | gap |
| 7 | POST | `/po/upload` | JWT | ✓ |
| 8 | GET | `/purchase-orders` | JWT | gap |
| 9 | GET | `/po/{id}` | JWT | gap |
| 10 | POST | `/financing` | JWT | gap |
| 11 | GET | `/financing` | JWT | gap |
| 12 | GET | `/financing/{id}/status` | - | ✓ |
| 13 | GET | `/financing/{id}/report` | - | ✓ |
| 14 | POST | `/financing/{id}/fund` | JWT | gap |
| 15 | POST | `/financing/{id}/milestone` | JWT | ✓ |
| 16 | GET | `/marketplace` | - | ✓ |
| 17 | GET | `/portfolio/{address}` | JWT | ✓ |
| 18 | POST | `/repayment/{id}` | JWT | ✓ |
| 19 | POST | `/agent/webhook` | secret | gap |
| 20 | GET | `/agent/status` | - | gap |
| 21 | GET | `/agent/log` | - | gap |
| 22 | GET | `/health` | - | ✓ |

PRD original: 9 endpoints. Total needed: 22. Gap: 13 endpoints.
