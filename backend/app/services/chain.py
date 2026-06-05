"""Blockchain service.

Mock returns deterministic tx hashes. Real implementation will use web3.py
against Mantle.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Optional

from app.core.config import get_settings


@dataclass
class ChainResult:
    tx_hash: str
    block_number: Optional[int] = None


def _mock_tx(seed: str) -> str:
    return "0x" + hashlib.sha256(seed.encode()).hexdigest()


class ChainClient:
    async def register_invoice(self, doc_hash: str) -> ChainResult:
        raise NotImplementedError

    async def is_registered(self, doc_hash: str) -> bool:
        raise NotImplementedError

    async def mint_financing_token(
        self,
        supplier: str,
        ipfs_uri: str,
        product_type: str,
        nominal: int,
    ) -> ChainResult:
        raise NotImplementedError

    async def release_milestone(self, financing_id: str, milestone_idx: int) -> ChainResult:
        raise NotImplementedError


class MockChainClient(ChainClient):
    """In-memory tracker for the registry so duplicate doc_hash uploads are
    rejected during local FE testing without needing a real chain."""

    def __init__(self) -> None:
        self._registry: set[str] = set()

    async def register_invoice(self, doc_hash: str) -> ChainResult:
        if doc_hash in self._registry:
            raise ValueError("Already registered")
        self._registry.add(doc_hash)
        return ChainResult(tx_hash=_mock_tx(f"register:{doc_hash}"))

    async def is_registered(self, doc_hash: str) -> bool:
        return doc_hash in self._registry

    async def mint_financing_token(
        self,
        supplier: str,
        ipfs_uri: str,
        product_type: str,
        nominal: int,
    ) -> ChainResult:
        return ChainResult(tx_hash=_mock_tx(f"mint:{supplier}:{ipfs_uri}"))

    async def release_milestone(self, financing_id: str, milestone_idx: int) -> ChainResult:
        return ChainResult(tx_hash=_mock_tx(f"release:{financing_id}:{milestone_idx}"))


_mock_singleton: Optional[ChainClient] = None


def get_chain_client() -> ChainClient:
    global _mock_singleton
    settings = get_settings()
    if settings.chain_mock_mode:
        if _mock_singleton is None:
            _mock_singleton = MockChainClient()
        return _mock_singleton
    raise NotImplementedError("On-chain client not wired yet — set CHAIN_MOCK_MODE=true")
