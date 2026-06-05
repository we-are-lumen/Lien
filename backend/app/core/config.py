"""Centralised application settings.

Loaded once at import time. All env vars live here so the rest of the code
does not touch ``os.environ`` directly.
"""

from __future__ import annotations

from functools import lru_cache
from typing import List

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Supabase
    supabase_url: str = ""
    supabase_service_key: str = ""

    # Auth
    jwt_secret: str = "changeme"
    jwt_algorithm: str = "HS256"
    jwt_expires_seconds: int = 86_400
    nonce_expires_seconds: int = 300

    # Feature switches (mock vs real services)
    ai_mock_mode: bool = True
    ipfs_mock_mode: bool = True
    chain_mock_mode: bool = True

    # External services
    pinata_jwt: str = ""
    mantle_rpc_url: str = "https://rpc.sepolia.mantle.xyz"

    # Contract addresses
    invoice_registry_address: str = ""
    financing_token_address: str = ""
    funding_pool_address: str = ""
    reputation_oracle_address: str = ""
    ai_verifier_private_key: str = ""

    # Server
    cors_origins: str = "http://localhost:3000"
    log_level: str = "INFO"

    @property
    def cors_origin_list(self) -> List[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
