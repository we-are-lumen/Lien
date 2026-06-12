"""Document upload endpoint.

POST /documents/upload — multipart/form-data

Flow:
1. Validate metadata
2. Compute doc_hash and check on-chain registry (mock or real)
3. Run AI verification (mock or real)
4. If approved, upload file + metadata to IPFS
5. Persist document row + financing row + milestone rows
6. Register doc_hash on-chain
7. Return summary (financing_id, risk_score, etc.)
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, UploadFile

from app.core.auth import require_auth
from app.core.errors import BadRequest, Conflict
from app.models import schemas
from app.services import repos
from app.services.ai_verifier import get_ai_verifier
from app.services.chain import get_chain_client
from app.services.doc_hash import compute_doc_hash
from app.services.ipfs import get_ipfs_client
from app.services.milestones import for_product
from app.services.pricing import advance_rate_for, pool_cap_for, price


router = APIRouter()


@router.post("/documents/upload", response_model=schemas.DocumentUploadResponse)
async def upload_document(
    file: UploadFile = File(...),
    document_type: str = Form(..., regex="^(invoice|po)$"),
    issuer_name: str = Form(...),
    buyer_name: str = Form(...),
    total_amount: str = Form(...),
    invoice_date: str = Form(...),
    due_date: str = Form(...),
    invoice_number: Optional[str] = Form(None),
    po_number: Optional[str] = Form(None),
    address: str = Depends(require_auth),
) -> schemas.DocumentUploadResponse:
    if document_type == "invoice" and not invoice_number:
        raise BadRequest("invoice_number is required when document_type=invoice")
    if document_type == "po" and not po_number:
        raise BadRequest("po_number is required when document_type=po")

    try:
        nominal = Decimal(total_amount)
        if nominal <= 0:
            raise ValueError
    except Exception as exc:
        raise BadRequest("total_amount must be a positive number") from exc

    try:
        invoice_dt = datetime.strptime(invoice_date, "%Y-%m-%d").date()
        due_dt = datetime.strptime(due_date, "%Y-%m-%d").date()
    except ValueError as exc:
        raise BadRequest("Dates must be YYYY-MM-DD") from exc
    if due_dt <= invoice_dt:
        raise BadRequest("due_date must be after invoice_date")

    document_number = invoice_number if document_type == "invoice" else po_number
    assert document_number  # placated by checks above

    # Compute the on-chain hash.
    doc_hash = compute_doc_hash(
        buyer_name=buyer_name,
        nominal=str(nominal),
        due_date=due_date,
        document_number=document_number,
    )

    # Reject duplicates fast — both off-chain (DB) and on-chain (registry).
    if repos.document_hash_exists(doc_hash):
        raise Conflict(f"Document already submitted (hash {doc_hash[:18]}...)")
    chain = get_chain_client()
    if await chain.is_registered(doc_hash):
        raise Conflict(f"Document already registered on-chain (hash {doc_hash[:18]}...)")

    # AI verification.
    file_bytes = await file.read()
    verifier = get_ai_verifier()
    verdict = await verifier.verify_document(
        file_bytes=file_bytes,
        document_type=document_type,  # type: ignore[arg-type]
        metadata={
            "buyer_name": buyer_name,
            "issuer_name": issuer_name,
            "nominal": str(nominal),
            "due_date": due_date,
            "invoice_date": invoice_date,
        },
    )

    if verdict.risk_tier == "reject":
        raise BadRequest(
            f"Document rejected by AI verifier (risk score {verdict.risk_score}). "
            "See flags for details."
        )

    # Pool cap check (PRD v3.0 §Risk Tiers — Pool Caps).
    # Validate against the cap before any external side-effects
    # (IPFS upload, on-chain registration).
    advance_rate_early = advance_rate_for(document_type, verdict.risk_tier)  # type: ignore[arg-type]
    face_early = Decimal(total_amount) * Decimal(advance_rate_early) / Decimal(100)
    cap = pool_cap_for(document_type, verdict.risk_tier)
    if cap is not None and face_early > cap:
        raise BadRequest(
            f"Funding amount {float(face_early):,.2f} exceeds the pool cap of "
            f"{float(cap):,.2f} for {verdict.risk_tier}-risk {document_type} financings. "
            "Please split the financing into smaller tranches."
        )

    # Upload file and metadata to IPFS.
    ipfs = get_ipfs_client()
    file_upload = await ipfs.upload_bytes(file_bytes, file.filename or f"{document_type}.pdf")
    metadata_upload = await ipfs.upload_json(
        {
            "document_type": document_type,
            "issuer_name": issuer_name,
            "buyer_name": buyer_name,
            "total_amount": str(nominal),
            "invoice_date": invoice_date,
            "due_date": due_date,
            "document_number": document_number,
            "ai_verification": verdict.to_dict(),
            "file": {"url": file_upload.url, "cid": file_upload.cid},
        },
    )

    # Resolve supplier user.
    supplier = repos.get_or_create_user(address)

    import hashlib
    file_sha = hashlib.sha256(file_bytes).hexdigest()

    document_row = repos.insert_document(
        {
            "supplier_id": supplier["id"],
            "document_type": document_type,
            "file_url": file_upload.url,
            "ipfs_cid": metadata_upload.cid,
            "file_sha256": file_sha,
            "doc_hash": doc_hash,
            "invoice_number": invoice_number,
            "po_number": po_number,
            "issuer_name": issuer_name,
            "buyer_name": buyer_name,
            "total_amount": float(nominal),
            "invoice_date": invoice_date,
            "due_date": due_date,
            "ai_verification": verdict.to_dict(),
            "status": "approved",
        }
    )

    # Pricing.
    tenor_days = (due_dt - invoice_dt).days
    pricing = price(document_type, verdict.risk_score, nominal, tenor_days)  # type: ignore[arg-type]

    financing_row = repos.insert_financing(
        {
            "document_id": document_row["id"],
            "supplier_id": supplier["id"],
            "product_type": document_type,
            "milestone_config": len(for_product(document_type)),  # type: ignore[arg-type]
            "advance_rate": pricing.advance_rate,
            "amount": float(nominal),
            "funding_amount": float(pricing.funding_amount),
            "yield_rate": float(pricing.yield_rate * 100),  # store as percent
            "expected_yield_amount": float(pricing.expected_yield_amount),
            "platform_fee": float(pricing.platform_fee),
            "total_repayment": float(pricing.total_repayment),
            "risk_score": verdict.risk_score,
            "risk_tier": verdict.risk_tier,
            "status": "published",
            "payment_status": "unpaid",
            "published_date": datetime.utcnow().date().isoformat(),
            "due_date": due_date,
        }
    )

    # Create milestone rows.
    milestone_specs = for_product(document_type)  # type: ignore[arg-type]
    milestone_rows = [
        {
            "financing_id": financing_row["id"],
            "idx": spec.idx,
            "name": spec.name,
            "percentage": spec.percentage,
            "payout_amount": float(pricing.funding_amount) * spec.percentage / 100,
            "release_trigger": spec.release_trigger,
            "status": "pending",
        }
        for spec in milestone_specs
    ]
    repos.insert_milestones(milestone_rows)

    # Register on-chain.
    register = await chain.register_invoice(doc_hash)
    from app.core.db import get_supabase
    sb = get_supabase()
    sb.table("financings").update({"registry_tx_hash": register.tx_hash}).eq(
        "id", financing_row["id"]
    ).execute()

    return schemas.DocumentUploadResponse(
        document_id=document_row["id"],
        financing_id=financing_row["id"],
        risk_score=verdict.risk_score,
        risk_tier=verdict.risk_tier,
        doc_hash=doc_hash,
        status="approved",
    )
