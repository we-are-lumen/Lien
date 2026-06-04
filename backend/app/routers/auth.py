"""Auth routes — /auth/nonce, /auth/verify, /me."""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core import auth as auth_core
from app.core.config import get_settings
from app.core.errors import BadRequest, Unauthorized
from app.models import schemas
from app.services import repos


router = APIRouter()


@router.get("/auth/nonce/{address}", response_model=schemas.NonceResponse)
async def get_nonce(address: str) -> schemas.NonceResponse:
    try:
        normalised = auth_core.normalize_address(address)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc
    settings = get_settings()
    nonce_value = auth_core.generate_nonce()
    expires = repos.create_nonce(normalised, nonce_value, settings.nonce_expires_seconds)
    return schemas.NonceResponse(
        nonce=auth_core.build_message(nonce_value),
        expires_at=expires,
    )


@router.post("/auth/verify", response_model=schemas.VerifyResponse)
async def verify(payload: schemas.VerifyRequest) -> schemas.VerifyResponse:
    try:
        address = auth_core.normalize_address(payload.address)
    except ValueError as exc:
        raise BadRequest(str(exc)) from exc

    # The nonce is the value embedded in the signed message. We accept the
    # full message back from FE and extract the nonce token.
    # Stored nonces are the raw hex; consume_nonce takes the raw hex back
    # after FE strips the prefix server-side. For simplicity we accept
    # either form.

    # Find any valid nonce for this address (small TTL so this list is short).
    from app.core.db import get_supabase
    from datetime import datetime, timezone
    sb = get_supabase()
    rows = (
        sb.table("auth_nonces")
        .select("nonce")
        .eq("address", address)
        .eq("used", False)
        .gt("expires_at", datetime.now(timezone.utc).isoformat())
        .execute()
    )
    matched = None
    for r in rows.data or []:
        message = auth_core.build_message(r["nonce"])
        if auth_core.verify_signature(address, message, payload.signature):
            matched = r["nonce"]
            break
    if not matched:
        raise Unauthorized("Signature does not match any active nonce")

    repos.consume_nonce(address, matched)
    repos.get_or_create_user(address)
    token = auth_core.issue_token(address)
    return schemas.VerifyResponse(access_token=token, address=address)


@router.get("/me", response_model=schemas.MeResponse)
async def me(address: str = Depends(auth_core.require_auth)) -> schemas.MeResponse:
    user = repos.get_user_by_address(address)
    role = "unknown"
    if user:
        # Resolve role by usage. A wallet that ever uploaded a doc is
        # treated as supplier; one that funded as investor; one that ever
        # appeared as buyer as buyer. This is good enough for MVP.
        from app.core.db import get_supabase
        sb = get_supabase()
        if sb.table("documents").select("id").eq("supplier_id", user["id"]).limit(1).execute().data:
            role = "supplier"
        elif sb.table("fundings").select("id").eq("investor_id", user["id"]).limit(1).execute().data:
            role = "investor"
        elif sb.table("financings").select("id").eq("buyer_id", user["id"]).limit(1).execute().data:
            role = "buyer"
    return schemas.MeResponse(address=address, role=role)
