"""Repository layer — thin wrappers around Supabase queries.

Keeps SQL/Supabase syntax in one place so route handlers stay focused on
business logic and HTTP concerns.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from app.core.db import get_supabase


# --- Users ----------------------------------------------------------------

def get_or_create_user(address: str) -> dict:
    sb = get_supabase()
    address = address.lower()
    res = sb.table("users").select("*").eq("address", address).limit(1).execute()
    if res.data:
        return res.data[0]
    ins = sb.table("users").insert({"address": address}).execute()
    return ins.data[0]


def get_user_by_id(user_id: str) -> Optional[dict]:
    sb = get_supabase()
    res = sb.table("users").select("*").eq("id", user_id).limit(1).execute()
    return res.data[0] if res.data else None


def get_user_by_address(address: str) -> Optional[dict]:
    sb = get_supabase()
    res = sb.table("users").select("*").eq("address", address.lower()).limit(1).execute()
    return res.data[0] if res.data else None


# --- Auth nonces ----------------------------------------------------------

def create_nonce(address: str, nonce: str, ttl_seconds: int) -> datetime:
    sb = get_supabase()
    expires = datetime.now(timezone.utc) + timedelta(seconds=ttl_seconds)
    sb.table("auth_nonces").insert(
        {
            "address": address.lower(),
            "nonce": nonce,
            "expires_at": expires.isoformat(),
        }
    ).execute()
    return expires


def consume_nonce(address: str, nonce: str) -> bool:
    """Mark a nonce as used. Returns True if it was valid and unused, False
    otherwise."""
    sb = get_supabase()
    res = (
        sb.table("auth_nonces")
        .select("*")
        .eq("address", address.lower())
        .eq("nonce", nonce)
        .eq("used", False)
        .gt("expires_at", datetime.now(timezone.utc).isoformat())
        .limit(1)
        .execute()
    )
    if not res.data:
        return False
    sb.table("auth_nonces").update({"used": True}).eq("address", address.lower()).eq("nonce", nonce).execute()
    return True


# --- Documents -----------------------------------------------------------

def insert_document(payload: Dict[str, Any]) -> dict:
    sb = get_supabase()
    res = sb.table("documents").insert(payload).execute()
    return res.data[0]


def document_hash_exists(doc_hash: str) -> bool:
    sb = get_supabase()
    res = sb.table("documents").select("id").eq("doc_hash", doc_hash).limit(1).execute()
    return bool(res.data)


# --- Financings ----------------------------------------------------------

def insert_financing(payload: Dict[str, Any]) -> dict:
    sb = get_supabase()
    res = sb.table("financings").insert(payload).execute()
    return res.data[0]


def get_financing(financing_id: str) -> Optional[dict]:
    sb = get_supabase()
    res = sb.table("financings").select("*").eq("id", financing_id).limit(1).execute()
    return res.data[0] if res.data else None


def list_financings_by_supplier(supplier_id: str, page: int, limit: int) -> tuple[List[dict], int]:
    sb = get_supabase()
    start = (page - 1) * limit
    end = start + limit - 1
    res = (
        sb.table("financings")
        .select("*, documents(invoice_number, po_number, buyer_name)", count="exact")
        .eq("supplier_id", supplier_id)
        .order("created_at", desc=True)
        .range(start, end)
        .execute()
    )
    return res.data, res.count or 0


def list_financings_by_investor(investor_id: str, invoice_number: Optional[str], page: int, limit: int) -> tuple[List[dict], int]:
    sb = get_supabase()
    start = (page - 1) * limit
    end = start + limit - 1
    # Pull the financing ids the investor funded, then load full rows.
    fundings = sb.table("fundings").select("financing_id").eq("investor_id", investor_id).execute()
    ids = [f["financing_id"] for f in fundings.data]
    if not ids:
        return [], 0
    q = (
        sb.table("financings")
        .select("*, documents(invoice_number, po_number, buyer_name)", count="exact")
        .in_("id", ids)
    )
    if invoice_number:
        # documents.invoice_number filter goes via join column
        q = q.eq("documents.invoice_number", invoice_number)
    res = q.order("created_at", desc=True).range(start, end).execute()
    return res.data, res.count or 0


def list_financings_by_buyer(buyer_id: str, invoice_number: Optional[str], supplier: Optional[str], page: int, limit: int) -> tuple[List[dict], int]:
    sb = get_supabase()
    start = (page - 1) * limit
    end = start + limit - 1
    q = (
        sb.table("financings")
        .select("*, documents(invoice_number, po_number, buyer_name, issuer_name)", count="exact")
        .eq("buyer_id", buyer_id)
    )
    if invoice_number:
        q = q.eq("documents.invoice_number", invoice_number)
    if supplier:
        q = q.eq("documents.issuer_name", supplier)
    res = q.order("created_at", desc=True).range(start, end).execute()
    return res.data, res.count or 0


def list_marketplace(page: int, limit: int) -> tuple[List[dict], int]:
    sb = get_supabase()
    start = (page - 1) * limit
    end = start + limit - 1
    res = (
        sb.table("financings")
        .select("*, documents(invoice_number, po_number, buyer_name)", count="exact")
        .eq("status", "published")
        .order("published_date", desc=True)
        .range(start, end)
        .execute()
    )
    return res.data, res.count or 0


def get_financing_full(financing_id: str) -> Optional[dict]:
    sb = get_supabase()
    res = (
        sb.table("financings")
        .select("*, documents(*), milestones(*)")
        .eq("id", financing_id)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


# --- Milestones ----------------------------------------------------------

def insert_milestones(rows: List[Dict[str, Any]]) -> List[dict]:
    sb = get_supabase()
    res = sb.table("milestones").insert(rows).execute()
    return res.data


def list_milestone_options(product_type: Optional[str] = None) -> List[dict]:
    sb = get_supabase()
    q = sb.table("milestone_options").select("id, name")
    if product_type:
        q = q.or_(f"product_type.eq.{product_type},product_type.is.null")
    res = q.order("id").execute()
    return res.data


def get_milestone(financing_id: str, idx: int) -> Optional[dict]:
    sb = get_supabase()
    res = (
        sb.table("milestones")
        .select("*")
        .eq("financing_id", financing_id)
        .eq("idx", idx)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def update_milestone(milestone_id: str, patch: Dict[str, Any]) -> dict:
    sb = get_supabase()
    res = sb.table("milestones").update(patch).eq("id", milestone_id).execute()
    return res.data[0]


# --- Fundings ------------------------------------------------------------

def insert_funding(payload: Dict[str, Any]) -> dict:
    sb = get_supabase()
    res = sb.table("fundings").insert(payload).execute()
    return res.data[0]


# --- Stats ---------------------------------------------------------------

def supplier_stats(user_id: str) -> dict:
    sb = get_supabase()
    submitted = sb.table("documents").select("id", count="exact").eq("supplier_id", user_id).execute()
    funded = (
        sb.table("financings")
        .select("id", count="exact")
        .eq("supplier_id", user_id)
        .in_("status", ["funded", "in_progress", "repaid"])
        .execute()
    )
    # Sum of released milestone payouts for the supplier
    released = (
        sb.table("milestones")
        .select("payout_amount, financings!inner(supplier_id)")
        .eq("status", "released")
        .eq("financings.supplier_id", user_id)
        .execute()
    )
    total_payout = sum(float(r["payout_amount"]) for r in (released.data or []))
    return {
        "total_submitted_invoice": submitted.count or 0,
        "total_funded_invoice": funded.count or 0,
        "total_payout_received": total_payout,
    }


def investor_stats(user_id: str) -> dict:
    sb = get_supabase()
    fundings = sb.table("fundings").select("amount, expected_return_amount, financing_id, financings!inner(status)").eq("investor_id", user_id).execute()
    rows = fundings.data or []
    total_funded = sum(float(r["amount"]) for r in rows)
    expected_return = sum(float(r["expected_return_amount"]) for r in rows)
    active = sum(1 for r in rows if r["financings"]["status"] in ("funded", "in_progress"))
    return {
        "total_funded_amount": total_funded,
        "active_investment_count": active,
        "expected_return_amount": expected_return,
    }


def buyer_stats(user_id: str) -> dict:
    sb = get_supabase()
    outstanding = (
        sb.table("financings")
        .select("amount", count="exact")
        .eq("buyer_id", user_id)
        .eq("payment_status", "unpaid")
        .execute()
    )
    total_due = sum(float(r["amount"]) for r in (outstanding.data or []))
    return {
        "outstanding_invoice_count": outstanding.count or 0,
        "total_amount_due": total_due,
    }
