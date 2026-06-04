"""Per-role stats endpoints.

Notion contract paths:
- GET /suppliers/stats/:user_id
- GET /investors/stats/:user_id
- GET /buyers/stats/:user_id

We don't enforce that the requesting wallet matches user_id — that's a
later concern (RBAC). For MVP, anyone with a token can view any user's stats.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.core.auth import require_auth
from app.core.errors import NotFound
from app.models import schemas
from app.services import repos


router = APIRouter()


def _resolve_user(user_id: str):
    user = repos.get_user_by_id(user_id)
    if not user:
        raise NotFound(f"User {user_id} not found")
    return user


@router.get("/suppliers/stats/{user_id}", response_model=schemas.SupplierStats)
async def get_supplier_stats(user_id: str, _: str = Depends(require_auth)) -> schemas.SupplierStats:
    _resolve_user(user_id)
    return schemas.SupplierStats(**repos.supplier_stats(user_id))


@router.get("/investors/stats/{user_id}", response_model=schemas.InvestorStats)
async def get_investor_stats(user_id: str, _: str = Depends(require_auth)) -> schemas.InvestorStats:
    _resolve_user(user_id)
    return schemas.InvestorStats(**repos.investor_stats(user_id))


@router.get("/buyers/stats/{user_id}", response_model=schemas.BuyerStats)
async def get_buyer_stats(user_id: str, _: str = Depends(require_auth)) -> schemas.BuyerStats:
    _resolve_user(user_id)
    return schemas.BuyerStats(**repos.buyer_stats(user_id))
