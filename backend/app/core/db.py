"""Thin Supabase client wrapper.

Centralises client construction so we can swap the backend (or stub it in
tests) without touching call sites.
"""

from __future__ import annotations

from functools import lru_cache

from supabase import Client, create_client

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_supabase() -> Client:
    settings = get_settings()
    if not settings.supabase_url or not settings.supabase_service_key:
        raise RuntimeError(
            "Supabase credentials missing. Set SUPABASE_URL and "
            "SUPABASE_SERVICE_KEY in your environment."
        )
    return create_client(settings.supabase_url, settings.supabase_service_key)
