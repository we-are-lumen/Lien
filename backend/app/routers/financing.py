"""Financing list & detail endpoints, plus marketplace and milestone options.

Notion contract paths:
- GET /suppliers/financing
- GET /investors/financing
- GET /buyers/financing
- GET /financing/{id}
- GET /financing/{id}/report
- POST /financing/{id}/fund
- POST /financing/{id}/milestone-proof
- GET /marketplace
- GET /milestones/options
"""

from __future__ import annotations

import math
from typing import Optional

from fastapi import APIRouter, Body, Depends, File, Query, UploadFile
from pydantic import BaseModel, Field

from app.core.auth import require_auth
from app.core.errors import BadRequest, Conflict, NotFound
from app.models import schemas
from app.services import repos


router = APIRouter()


def _doc_invoice_number(financing_row: dict) -> str:
    doc = financing_row.get("documents") or {}
    return doc.get("invoice_number") or doc.get("po_number") or ""


def _paginate(total: int, limit: int, page: int) -> schemas.Pagination:
    total_pages = max(1, math.ceil(total / limit)) if limit else 1
    return schemas.Pagination(current_page=page, total_page=total_pages, total_data=total)


@router.get("/suppliers/financing", response_model=schemas.Paginated)
async def list_supplier_financings(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    address: str = Depends(require_auth),
) -> schemas.Paginated:
    user = repos.get_user_by_address(address)
    if not user:
        return schemas.Paginated(data=[], pagination=_paginate(0, limit, page))
    rows, total = repos.list_financings_by_supplier(user["id"], page, limit)
    items = [
        schemas.SupplierFinancingListItem(
            id=r["id"],
            invoice_number=_doc_invoice_number(r),
            requested_fund=float(r["funding_amount"]),
            status=r["status"],
        ).model_dump()
        for r in rows
    ]
    return schemas.Paginated(data=items, pagination=_paginate(total, limit, page))


@router.get("/investors/financing", response_model=schemas.Paginated)
async def list_investor_financings(
    invoice_number: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    address: str = Depends(require_auth),
) -> schemas.Paginated:
    user = repos.get_user_by_address(address)
    if not user:
        return schemas.Paginated(data=[], pagination=_paginate(0, limit, page))
    rows, total = repos.list_financings_by_investor(user["id"], invoice_number, page, limit)
    items = [
        schemas.InvestorFinancingListItem.model_validate(
            {
                "id": r["id"],
                "invoice_number": _doc_invoice_number(r),
                "amount": float(r["amount"]),
                "requested_fund": float(r["funding_amount"]),
                "yield": float(r["expected_yield_amount"]),
                "expected_return_amount": float(r["total_repayment"]),
                "status": r["status"],
            }
        ).model_dump(by_alias=True)
        for r in rows
    ]
    return schemas.Paginated(data=items, pagination=_paginate(total, limit, page))


@router.get("/buyers/financing", response_model=schemas.Paginated)
async def list_buyer_financings(
    invoice_number: Optional[str] = None,
    supplier: Optional[str] = None,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    address: str = Depends(require_auth),
) -> schemas.Paginated:
    user = repos.get_user_by_address(address)
    if not user:
        return schemas.Paginated(data=[], pagination=_paginate(0, limit, page))
    rows, total = repos.list_financings_by_buyer(user["id"], invoice_number, supplier, page, limit)
    items = [
        schemas.BuyerFinancingListItem(
            id=r["id"],
            invoice_number=_doc_invoice_number(r),
            amount=float(r["amount"]),
            supplier_name=(r.get("documents") or {}).get("issuer_name", ""),
            due_date=r["due_date"],
            payment_status=r["payment_status"],
        ).model_dump()
        for r in rows
    ]
    return schemas.Paginated(data=items, pagination=_paginate(total, limit, page))


@router.get("/marketplace", response_model=schemas.Paginated)
async def marketplace(
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
) -> schemas.Paginated:
    rows, total = repos.list_marketplace(page, limit)
    items = [
        schemas.MarketplaceItem(
            id=r["id"],
            invoice_number=_doc_invoice_number(r),
            amount=float(r["amount"]),
            funding_amount=float(r["funding_amount"]),
            yield_rate=float(r["yield_rate"]),
            published_date=r["published_date"] or r["created_at"][:10],
        ).model_dump()
        for r in rows
    ]
    return schemas.Paginated(data=items, pagination=_paginate(total, limit, page))


@router.get("/financing/{financing_id}", response_model=schemas.FinancingDetail)
async def get_financing_detail(financing_id: str) -> schemas.FinancingDetail:
    row = repos.get_financing_full(financing_id)
    if not row:
        raise NotFound("Financing not found")

    doc = row.get("documents") or {}
    milestones = sorted(row.get("milestones") or [], key=lambda m: m["idx"])
    return schemas.FinancingDetail(
        id=row["id"],
        invoice_number=doc.get("invoice_number") or doc.get("po_number") or "",
        amount=float(row["amount"]),
        buyer_name=doc.get("buyer_name", ""),
        due_date=row["due_date"],
        payment_status=row["payment_status"],
        yield_rate=float(row["yield_rate"]),
        funding_amount=float(row["funding_amount"]),
        expected_yield_amount=float(row["expected_yield_amount"]),
        platform_fee=float(row["platform_fee"]),
        total_repayment=float(row["total_repayment"]),
        milestones=[
            schemas.MilestoneOut(
                name=m["name"],
                percentage=m["percentage"],
                payout_amount=float(m["payout_amount"]),
                release_trigger=m.get("release_trigger"),
                status=m["status"],
            )
            for m in milestones
        ],
    )


@router.get("/financing/{financing_id}/report")
async def get_financing_report(financing_id: str) -> dict:
    """Returns the full AI verification record + per-milestone verdicts.

    Public endpoint per PRD — investors use this to make funding decisions.
    """
    row = repos.get_financing_full(financing_id)
    if not row:
        raise NotFound("Financing not found")
    doc = row.get("documents") or {}
    return {
        "financing_id": row["id"],
        "document_verification": doc.get("ai_verification"),
        "risk_score": row["risk_score"],
        "risk_tier": row["risk_tier"],
        "milestones": [
            {
                "idx": m["idx"],
                "name": m["name"],
                "status": m["status"],
                "verification": m.get("ai_verification"),
            }
            for m in sorted(row.get("milestones") or [], key=lambda m: m["idx"])
        ],
    }


class FundFinancingBody(BaseModel):
    """Optional body for /financing/{id}/fund.

    In real Mantle mode, the FE must supply ``tx_hash`` — the wagmi receipt
    hash from the investor's on-chain ``FundingPool.fund()`` call. The
    Goldsky ``Funded`` event handler later matches this hash to write
    ``financings.token_id``. Without it, the off-chain UUID never gets
    linked to the on-chain token and the agent loop can't resolve
    ProofSubmitted webhooks.

    In mock mode this field is ignored — the BE mints a deterministic
    tx_hash via ``MockChainClient.fund()``.
    """
    tx_hash: Optional[str] = Field(
        default=None,
        min_length=66,
        max_length=66,
        description="On-chain fund tx_hash (0x + 64 hex). Required in real mode.",
    )


@router.post("/financing/{financing_id}/fund")
async def fund_financing(
    financing_id: str,
    body: Optional[FundFinancingBody] = Body(default=None),
    address: str = Depends(require_auth),
) -> dict:
    """Investor funds a financing.

    In the real flow the investor signs the on-chain ``fund()`` tx directly
    via wagmi, which inside the same tx auto-releases M1 to the supplier
    (``FundingPool.fund → _releaseMilestone(tokenId, 1)`` at
    contracts/src/FundingPool.sol:155). This endpoint exists for the BE to
    record the bookkeeping after the tx confirms, OR (in mock mode) to
    simulate the funding flow end-to-end without a chain.

    DO NOT call ``chain.release_milestone(_, 1)`` from here — in real mode
    it reverts ``MilestoneAlreadyReleased`` because ``FundingPool.fund``
    already did the M1 release inside the investor's tx. Both M1 release
    and the fund deposit share the same tx_hash.

    Modes:
      - ``chain_mock_mode=True`` (local/dev): the BE mints a deterministic
        ``fund_tx_hash`` via ``MockChainClient.fund(financing_id)``. Body
        ``tx_hash`` is ignored.
      - ``chain_mock_mode=False`` (real Mantle): the FE must supply the
        wagmi receipt ``tx_hash`` in the body. The Goldsky ``Funded`` event
        handler later matches this hash to write ``financings.token_id``.
        Missing ``tx_hash`` raises 400 — without it the off-chain UUID
        never gets linked to the on-chain token.
    """
    fin = repos.get_financing(financing_id)
    if not fin:
        raise NotFound("Financing not found")
    if fin["status"] != "published":
        raise Conflict(f"Cannot fund a financing in status '{fin['status']}'")

    user = repos.get_user_by_address(address)
    if not user:
        raise BadRequest("User not registered")

    funding = repos.insert_funding(
        {
            "financing_id": financing_id,
            "investor_id": user["id"],
            "amount": fin["funding_amount"],
            "expected_return_amount": fin["total_repayment"],
        }
    )

    # Resolve fund_tx_hash by mode.
    from app.services.chain import get_chain_client
    from app.core.config import get_settings
    chain = get_chain_client()
    settings = get_settings()
    if settings.chain_mock_mode:
        fund_tx_hash = await chain.fund(financing_id)
    else:
        # Real Mantle: require the FE-supplied receipt hash so the Goldsky
        # Funded handler can later resolve token_id by matching this value.
        if body is None or not body.tx_hash:
            raise BadRequest(
                "tx_hash required in real chain mode — supply the wagmi "
                "receipt hash from the investor's FundingPool.fund() tx."
            )
        fund_tx_hash = body.tx_hash

    # Mark M1 released — same tx_hash as fund (atomic on-chain).
    m1 = repos.get_milestone(financing_id, 1)
    if m1:
        repos.update_milestone(
            m1["id"],
            {"status": "released", "release_tx_hash": fund_tx_hash},
        )

    # Flip financing status.
    from app.core.db import get_supabase
    sb = get_supabase()
    sb.table("financings").update(
        {"status": "funded", "fund_tx_hash": fund_tx_hash}
    ).eq("id", financing_id).execute()

    return {"funding_id": funding["id"], "fund_tx_hash": fund_tx_hash}


@router.post("/financing/{financing_id}/milestone-proof")
async def upload_milestone_proof(
    financing_id: str,
    file: UploadFile = File(...),
    milestone_idx: int = Query(..., ge=2, le=4, description="Milestone index 2-4 (M1 auto-releases on fund)"),
    address: str = Depends(require_auth),
) -> dict:
    """Supplier uploads proof to IPFS. Returns the CID.

    After this returns, the FE must call ``FundingPool.submitProof(tokenId,
    milestoneIdx, cid)`` on-chain via wagmi/viem. That emits ``ProofSubmitted``,
    which Goldsky forwards to ``/agent/webhook``; the autonomous agent loop then
    runs Claude Vision verification and calls ``releaseMilestone()`` on success.

    This endpoint does NOT verify the proof or touch the chain. The agent loop
    is the single authority on those steps. FE polls
    ``GET /agent/decisions/{financing_id}`` to see the verdict.
    """
    fin = repos.get_financing(financing_id)
    if not fin:
        raise NotFound("Financing not found")

    milestone = repos.get_milestone(financing_id, milestone_idx)
    if not milestone:
        raise NotFound(f"Milestone {milestone_idx} not found for this financing")
    if milestone["status"] == "released":
        raise Conflict("Milestone already released")

    file_bytes = await file.read()
    if not file_bytes:
        raise BadRequest("Empty file upload")

    # Upload to IPFS (Pinata in real mode, deterministic mock in dev).
    from app.services.ipfs import get_ipfs_client
    ipfs = get_ipfs_client()
    upload = await ipfs.upload_bytes(file_bytes, file.filename or "proof.pdf")

    # Persist the CID on the milestone row so it survives FE refreshes between
    # upload and the on-chain submitProof() call.
    repos.update_milestone(
        milestone["id"],
        {
            "proof_file_url": upload.url,
            "proof_ipfs_cid": upload.cid,
            "status": "proof_uploaded",
        },
    )

    return {
        "milestone_id": milestone["id"],
        "cid": upload.cid,
        "url": upload.url,
        "status": "proof_uploaded",
        "next_step": (
            "Call FundingPool.submitProof(tokenId, milestoneIdx, cid) on-chain "
            "to trigger AI verification. Poll /agent/decisions/{financing_id} "
            "for the result."
        ),
    }


@router.get("/milestones/options", response_model=list[schemas.MilestoneOption])
async def get_milestone_options(
    product_type: Optional[str] = Query(None, regex="^(invoice|po)$"),
) -> list[schemas.MilestoneOption]:
    rows = repos.list_milestone_options(product_type)
    return [schemas.MilestoneOption(id=r["id"], name=r["name"]) for r in rows]
