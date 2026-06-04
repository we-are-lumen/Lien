"""Pydantic request/response schemas.

Schemas match the Notion API contract exactly so the FE doesn't have to
guess. Fields that are not in the contract are kept internal and never
serialized back to clients.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, List, Literal, Optional

from pydantic import BaseModel, Field


# --- Auth ----------------------------------------------------------------

class NonceResponse(BaseModel):
    nonce: str
    expires_at: datetime


class VerifyRequest(BaseModel):
    address: str
    signature: str


class VerifyResponse(BaseModel):
    access_token: str
    address: str


class MeResponse(BaseModel):
    address: str
    role: Literal["supplier", "investor", "buyer", "unknown"] = "unknown"


# --- Stats ----------------------------------------------------------------

class SupplierStats(BaseModel):
    total_submitted_invoice: int
    total_funded_invoice: int
    total_payout_received: float


class InvestorStats(BaseModel):
    total_funded_amount: float
    active_investment_count: int
    expected_return_amount: float


class BuyerStats(BaseModel):
    outstanding_invoice_count: int
    total_amount_due: float


# --- Pagination ----------------------------------------------------------

class Pagination(BaseModel):
    current_page: int
    total_page: int
    total_data: int


class Paginated(BaseModel):
    data: List[Any]
    pagination: Pagination


# --- Financing lists ----------------------------------------------------

class SupplierFinancingListItem(BaseModel):
    id: str
    invoice_number: str
    requested_fund: float
    status: str


class InvestorFinancingListItem(BaseModel):
    id: str
    invoice_number: str
    amount: float
    requested_fund: float
    yield_: float = Field(..., alias="yield")
    expected_return_amount: float
    status: str

    model_config = {"populate_by_name": True}


class BuyerFinancingListItem(BaseModel):
    id: str
    invoice_number: str
    amount: float
    supplier_name: str
    due_date: date
    payment_status: str


class MarketplaceItem(BaseModel):
    id: str
    invoice_number: str
    amount: float
    funding_amount: float
    yield_rate: float
    published_date: date


# --- Financing detail ----------------------------------------------------

class MilestoneOut(BaseModel):
    name: str
    percentage: int
    payout_amount: float
    release_trigger: Optional[str] = None
    status: str


class FinancingDetail(BaseModel):
    id: str
    invoice_number: str
    amount: float
    buyer_name: str
    due_date: date
    payment_status: str
    yield_rate: float
    funding_amount: float
    expected_yield_amount: float
    platform_fee: float
    total_repayment: float
    milestones: List[MilestoneOut]


# --- Milestone options ---------------------------------------------------

class MilestoneOption(BaseModel):
    id: int
    name: str


# --- Document upload -----------------------------------------------------

class DocumentUploadResponse(BaseModel):
    document_id: str
    financing_id: str
    risk_score: int
    risk_tier: str
    doc_hash: str
    status: str
