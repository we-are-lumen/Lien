"""Wallet-based authentication.

Flow:
1. FE calls GET /auth/nonce/:address -> backend stores nonce in DB
2. FE asks wallet to sign the nonce
3. FE calls POST /auth/verify with address + signature
4. Backend verifies signature, marks nonce used, returns JWT
5. Subsequent calls send `Authorization: Bearer <jwt>`
"""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from eth_account.messages import encode_defunct
from eth_account import Account
from fastapi import Depends, Header

from app.core.config import get_settings
from app.core.errors import Unauthorized


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def build_message(nonce: str) -> str:
    """The exact string the wallet signs. Kept stable so FE doesn't drift."""
    return f"Sign this to login to LIEN: {nonce}"


def generate_nonce() -> str:
    return secrets.token_hex(8)


def normalize_address(address: str) -> str:
    """Lower-cased, 0x-prefixed wallet address."""
    if not address.startswith("0x") or len(address) != 42:
        raise ValueError(f"Invalid address: {address}")
    return address.lower()


def verify_signature(address: str, message: str, signature: str) -> bool:
    """Recover signer from signature and compare to claimed address."""
    try:
        encoded = encode_defunct(text=message)
        recovered = Account.recover_message(encoded, signature=signature)
        return recovered.lower() == address.lower()
    except Exception:
        return False


def issue_token(address: str) -> str:
    settings = get_settings()
    now = _utcnow()
    payload = {
        "sub": address.lower(),
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.jwt_expires_seconds)).timestamp()),
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_token(token: str) -> dict:
    settings = get_settings()
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError as exc:
        raise Unauthorized(f"Invalid token: {exc}") from exc


async def require_auth(authorization: Optional[str] = Header(default=None)) -> str:
    """FastAPI dependency. Returns the caller's wallet address."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("Missing bearer token")
    token = authorization.split(" ", 1)[1].strip()
    payload = decode_token(token)
    address = payload.get("sub")
    if not address:
        raise Unauthorized("Token missing subject")
    return address


async def require_auth_optional(
    authorization: Optional[str] = Header(default=None),
) -> Optional[str]:
    """Same as require_auth but returns None when no/invalid token is present.

    For endpoints that surface different content per viewer (e.g. buyer name
    de-anonymization for the supplier and buyer) but are also browseable
    unauthenticated.
    """
    if not authorization or not authorization.lower().startswith("bearer "):
        return None
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = decode_token(token)
    except Unauthorized:
        return None
    address = payload.get("sub")
    return address if isinstance(address, str) else None
