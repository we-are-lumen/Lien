"""Blockchain service.

Mock returns deterministic tx hashes. Real implementation uses web3.py
against Mantle Network.

Set CHAIN_MOCK_MODE=false and provide:
  MANTLE_RPC_URL             — defaults to https://rpc.sepolia.mantle.xyz
  AI_VERIFIER_PRIVATE_KEY    — EOA that signs txs (AI verifier wallet)
  INVOICE_REGISTRY_ADDRESS   — deployed InvoiceRegistry contract
  FINANCING_TOKEN_ADDRESS    — deployed FinancingToken contract
  FUNDING_POOL_ADDRESS       — deployed FundingPool contract
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.core.config import get_settings

log = logging.getLogger(__name__)


class PriorMilestoneNotReleasedError(Exception):
    """Raised when releaseMilestone() reverts with PriorMilestoneNotReleased.

    This is a retriable condition — the prior milestone hasn't been released yet
    (out-of-order webhook delivery). The agent should re-enqueue the job rather
    than permanently failing it.
    """


class MilestoneAlreadyReleasedError(Exception):
    """Raised when releaseMilestone() reverts with MilestoneAlreadyReleased.

    Indicates a prior agent run successfully released the milestone on-chain but
    crashed before persisting release_tx_hash. The agent should recover the
    tx_hash from chain logs and complete the audit row rather than fail.
    """


# 4-byte selectors for the custom errors we want to distinguish.
# keccak256("PriorMilestoneNotReleased()")[:4] = 0x3a9a1d28
# keccak256("MilestoneAlreadyReleased()")[:4]  = 0x81b57dde
# Hardcoded so detection works even before eth_utils is importable. Runtime
# assertion below catches any future drift between the constants and the actual
# contract signatures.
_PRIOR_MILESTONE_NOT_RELEASED_SELECTOR = "0x3a9a1d28"
_MILESTONE_ALREADY_RELEASED_SELECTOR = "0x81b57dde"

try:
    from eth_utils import keccak as _keccak
    _computed_prior = "0x" + _keccak(text="PriorMilestoneNotReleased()").hex()[:8]
    _computed_already = "0x" + _keccak(text="MilestoneAlreadyReleased()").hex()[:8]
    assert _computed_prior == _PRIOR_MILESTONE_NOT_RELEASED_SELECTOR, (
        f"selector mismatch: hardcoded={_PRIOR_MILESTONE_NOT_RELEASED_SELECTOR} computed={_computed_prior}"
    )
    assert _computed_already == _MILESTONE_ALREADY_RELEASED_SELECTOR, (
        f"selector mismatch: hardcoded={_MILESTONE_ALREADY_RELEASED_SELECTOR} computed={_computed_already}"
    )
except ImportError:  # pragma: no cover — eth_utils ships with web3
    pass


def _is_prior_milestone_not_released(error_str: str) -> bool:
    """Detect the PriorMilestoneNotReleased revert (name OR selector form)."""
    if "PriorMilestoneNotReleased" in error_str:
        return True
    if _PRIOR_MILESTONE_NOT_RELEASED_SELECTOR in error_str.lower():
        return True
    return False


def _is_milestone_already_released(error_str: str) -> bool:
    """Detect the MilestoneAlreadyReleased revert (name OR selector form)."""
    if "MilestoneAlreadyReleased" in error_str:
        return True
    if _MILESTONE_ALREADY_RELEASED_SELECTOR in error_str.lower():
        return True
    return False

# ---------------------------------------------------------------------------
# ABI loading — read from Foundry build artifacts
# ---------------------------------------------------------------------------
_ARTIFACTS_DIR = Path(__file__).resolve().parents[4] / "contracts" / "out"


def _load_abi(contract: str, file: str) -> list:
    path = _ARTIFACTS_DIR / file / f"{contract}.json"
    if not path.exists():
        # Fallback: minimal ABI so the service can still start without artifacts
        log.warning("ABI artifact not found at %s — using empty ABI", path)
        return []
    return json.loads(path.read_text())["abi"]


INVOICE_REGISTRY_ABI = _load_abi("InvoiceRegistry", "InvoiceRegistry.sol")
FUNDING_POOL_ABI = _load_abi("FundingPool", "FundingPool.sol")
FINANCING_TOKEN_ABI = _load_abi("FinancingToken", "FinancingToken.sol")


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass
class ChainResult:
    tx_hash: str
    block_number: Optional[int] = None


# ---------------------------------------------------------------------------
# Base interface
# ---------------------------------------------------------------------------

class ChainClient:
    async def register_invoice(self, doc_hash: str) -> ChainResult:
        raise NotImplementedError

    async def is_registered(self, doc_hash: str) -> bool:
        raise NotImplementedError

    async def mint_financing_token(
        self,
        to: str,
        product_type: str,
        milestone_count: int,
        advance_rate: int,
        ipfs_uri: str,
        issuer: str,
        nominal: int,
        due_date: int,
    ) -> ChainResult:
        raise NotImplementedError

    async def release_milestone(self, financing_id: str, milestone_idx: int) -> ChainResult:
        raise NotImplementedError

    async def fund(self, financing_id: str) -> str:
        """Mock-only helper: simulate the investor's on-chain `fund()` tx that
        atomically deposits USDT0 and auto-releases M1 to the supplier.

        Returns the deterministic tx_hash representing this combined operation.

        In real (Mantle) mode, the investor signs and broadcasts `fund()` from
        their own wallet via wagmi — the BE never sends this tx. The real
        client raises NotImplementedError to make accidental BE-side calls fail
        loudly instead of double-paying gas or stalling.
        """
        raise NotImplementedError

    async def find_milestone_released_tx(
        self, financing_id: str, milestone_idx: int
    ) -> Optional[ChainResult]:
        """Recovery hook: look up the on-chain MilestoneReleased event for this
        token+idx, used when a retry hits MilestoneAlreadyReleased and the agent
        needs to reconstruct the original tx_hash. Default: not implemented."""
        raise NotImplementedError

    async def mark_defaulted(self, financing_id: str) -> ChainResult:
        """B1: mark a financing token as defaulted on-chain.

        Called by the nightly auto-default cron when due_date + 44 days has
        passed without full repayment. Emits an on-chain event for indexers and
        freezes further milestone releases on the smart contract side.

        The contract function is FundingPool.markDefaulted(tokenId).
        """
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Mock (deterministic, no network)
# ---------------------------------------------------------------------------

def _mock_tx(seed: str) -> str:
    return "0x" + hashlib.sha256(seed.encode()).hexdigest()


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
        to: str,
        product_type: str,
        milestone_count: int,
        advance_rate: int,
        ipfs_uri: str,
        issuer: str,
        nominal: int,
        due_date: int,
    ) -> ChainResult:
        return ChainResult(tx_hash=_mock_tx(f"mint:{to}:{ipfs_uri}"))

    async def release_milestone(self, financing_id: str, milestone_idx: int) -> ChainResult:
        return ChainResult(tx_hash=_mock_tx(f"release:{financing_id}:{milestone_idx}"))

    async def fund(self, financing_id: str) -> str:
        """Mock the investor's fund() tx — atomic deposit + auto-release of M1.

        The single tx_hash returned represents both the USDT0 deposit and the
        M1 release (mirrors FundingPool.fund's behaviour at FundingPool.sol:155).
        """
        return _mock_tx(f"fund:{financing_id}")

    async def find_milestone_released_tx(
        self, financing_id: str, milestone_idx: int
    ) -> Optional[ChainResult]:
        # Mock: deterministic recovery — return the same tx_hash release_milestone would.
        return ChainResult(tx_hash=_mock_tx(f"release:{financing_id}:{milestone_idx}"))

    async def mark_defaulted(self, financing_id: str) -> ChainResult:
        """Mock: return deterministic tx_hash for auto-default. No state change needed."""
        return ChainResult(tx_hash=_mock_tx(f"default:{financing_id}"))


# ---------------------------------------------------------------------------
# Real — web3.py + Mantle
# ---------------------------------------------------------------------------

class MantleChainClient(ChainClient):
    """Real chain client. Reads config from Settings (env vars).

    All write methods sign + broadcast transactions using the AI verifier
    wallet (AI_VERIFIER_PRIVATE_KEY). Read-only calls (is_registered) use
    a plain eth_call — no gas needed.
    """

    def __init__(self) -> None:
        from web3 import AsyncWeb3
        from web3.middleware import ExtraDataToPOAMiddleware

        settings = get_settings()

        self._w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(settings.mantle_rpc_url))
        # Mantle uses POA consensus — inject middleware
        self._w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

        private_key = settings.ai_verifier_private_key
        if not private_key:
            raise RuntimeError(
                "AI_VERIFIER_PRIVATE_KEY is not set — cannot use real chain client"
            )
        self._account = self._w3.eth.account.from_key(private_key)
        self._address = self._account.address
        log.info("MantleChainClient: AI verifier wallet = %s", self._address)

        # Contract instances
        self._registry = self._w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(settings.invoice_registry_address),
            abi=INVOICE_REGISTRY_ABI,
        )
        self._pool = self._w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(settings.funding_pool_address),
            abi=FUNDING_POOL_ABI,
        )
        self._token = self._w3.eth.contract(
            address=AsyncWeb3.to_checksum_address(settings.financing_token_address),
            abi=FINANCING_TOKEN_ABI,
        )

    async def _send(self, fn) -> ChainResult:
        """Build, sign, and broadcast a contract function call."""
        nonce = await self._w3.eth.get_transaction_count(self._address)
        gas_price = await self._w3.eth.gas_price

        tx = await fn.build_transaction({
            "from": self._address,
            "nonce": nonce,
            "gasPrice": gas_price,
        })
        # Estimate gas with 20% buffer.
        # IMPORTANT: estimate_gas reverts with the contract error BEFORE the tx is sent,
        # which is cheaper and more reliable than post-tx eth_call replay on non-archive nodes.
        # Surface PriorMilestoneNotReleased here so the agent can re-queue without spending gas.
        try:
            estimated = await self._w3.eth.estimate_gas(tx)
            tx["gas"] = int(estimated * 1.2)
        except Exception as est_exc:
            est_msg = str(est_exc)
            if _is_prior_milestone_not_released(est_msg):
                raise PriorMilestoneNotReleasedError(
                    f"Prior milestone not released (pre-flight estimate_gas): {est_msg}"
                )
            if _is_milestone_already_released(est_msg):
                raise MilestoneAlreadyReleasedError(
                    f"Milestone already released on-chain (pre-flight estimate_gas): {est_msg}"
                )
            tx["gas"] = 500_000  # safe fallback for other estimate errors

        signed = self._w3.eth.account.sign_transaction(tx, private_key=self._account.key)
        tx_hash = await self._w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = await self._w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)

        if receipt["status"] != 1:
            # Try to surface the revert reason via eth_call replay at the mined block.
            revert_msg = await self._decode_revert(tx, receipt["blockNumber"])
            if _is_prior_milestone_not_released(revert_msg):
                raise PriorMilestoneNotReleasedError(
                    f"Prior milestone not released (tx={tx_hash.hex()}): {revert_msg}"
                )
            if _is_milestone_already_released(revert_msg):
                raise MilestoneAlreadyReleasedError(
                    f"Milestone already released on-chain (tx={tx_hash.hex()}): {revert_msg}"
                )
            raise RuntimeError(f"Transaction reverted: {tx_hash.hex()} — {revert_msg}")

        return ChainResult(
            tx_hash=tx_hash.hex(),
            block_number=receipt["blockNumber"],
        )

    async def _decode_revert(self, tx: dict, block_number: int) -> str:
        """Best-effort revert reason extraction via eth_call replay at mined block."""
        try:
            await self._w3.eth.call(tx, block_identifier=block_number)
            return "unknown revert"
        except Exception as exc:
            return str(exc)

    async def register_invoice(self, doc_hash: str) -> ChainResult:
        """Register a document hash in InvoiceRegistry (double-financing guard)."""
        # doc_hash is a hex string — convert to bytes32
        hash_bytes = bytes.fromhex(doc_hash.removeprefix("0x").zfill(64))
        fn = self._registry.functions.register(hash_bytes)
        return await self._send(fn)

    async def is_registered(self, doc_hash: str) -> bool:
        """Read-only check — no gas."""
        hash_bytes = bytes.fromhex(doc_hash.removeprefix("0x").zfill(64))
        return await self._registry.functions.isRegistered(hash_bytes).call()

    async def mint_financing_token(
        self,
        to: str,
        product_type: str,
        milestone_count: int,
        advance_rate: int,
        ipfs_uri: str,
        issuer: str,
        nominal: int,
        due_date: int,
    ) -> ChainResult:
        """Mint an ERC-1155 financing token for a new deal.

        CONTRACT DESIGN GAP — CANNOT BE CALLED BY AI VERIFIER WALLET:
        FinancingToken.mint() is onlyOwner, and the owner is FundingPool.
        The AI verifier wallet is not the owner and will get NotOwner() revert.
        Resolution needed: add a mintAndFund() wrapper on FundingPool that
        mints the token and calls fund() atomically, callable by the deployer,
        OR expose a separate minter role on FinancingToken.
        Until the contract is updated, this method will always revert in real mode.
        Tracked as BC-7.
        """
        raise NotImplementedError(
            "mint_financing_token: FinancingToken.mint() is onlyOwner (owner=FundingPool). "
            "AI verifier wallet cannot call it directly. See BC-7."
        )

    async def release_milestone(self, financing_id: str, milestone_idx: int) -> ChainResult:
        """Call FundingPool.releaseMilestone() — releases USDT0 to supplier.

        Reads the on-chain token_id from the financings.token_id DB column, which
        is populated when the financing token is minted at deal creation time.
        Raises RuntimeError if token_id is not set (deal was never minted on-chain).
        """
        from app.core.db import get_supabase
        import asyncio as _asyncio

        def _lookup_token_id() -> int:
            rows = (
                get_supabase()
                .table("financings")
                .select("token_id")
                .eq("id", financing_id)
                .limit(1)
                .execute()
            )
            if not rows.data or not rows.data[0].get("token_id"):  # type: ignore[union-attr]
                raise RuntimeError(
                    f"financings.token_id not set for {financing_id} — "
                    "deal was not minted on-chain or token_id was not stored"
                )
            return int(rows.data[0]["token_id"])  # type: ignore[index]

        token_id = await _asyncio.to_thread(_lookup_token_id)
        fn = self._pool.functions.releaseMilestone(token_id, milestone_idx)
        return await self._send(fn)

    async def find_milestone_released_tx(
        self, financing_id: str, milestone_idx: int
    ) -> Optional[ChainResult]:
        """Look up the historical MilestoneReleased event for this token+idx.

        Used by the agent's recovery path when a re-issued releaseMilestone()
        reverts with MilestoneAlreadyReleased — we need to find the original
        tx_hash so the audit row can be written and the job can be marked done.

        Returns the most recent matching event as a ChainResult, or None if not found.
        """
        from app.core.db import get_supabase
        import asyncio as _asyncio

        def _lookup_token_id() -> Optional[int]:
            rows = (
                get_supabase()
                .table("financings")
                .select("token_id")
                .eq("id", financing_id)
                .limit(1)
                .execute()
            )
            if not rows.data or not rows.data[0].get("token_id"):  # type: ignore[union-attr]
                return None
            return int(rows.data[0]["token_id"])  # type: ignore[index]

        token_id = await _asyncio.to_thread(_lookup_token_id)
        if token_id is None:
            return None

        try:
            # Use get_logs (not create_filter) — create_filter relies on the RPC
            # provider maintaining server-side filter state and fails with
            # "filter not found" on many providers. get_logs is a one-shot query.
            events = await self._pool.events.MilestoneReleased.get_logs(
                from_block=0,
                argument_filters={"tokenId": token_id, "milestoneIdx": milestone_idx},
            )
        except Exception as exc:
            log.warning("find_milestone_released_tx: event scan failed: %s", exc)
            return None

        if not events:
            return None

        latest = events[-1]
        return ChainResult(
            tx_hash=latest["transactionHash"].hex(),
            block_number=latest["blockNumber"],
        )

    async def mark_defaulted(self, financing_id: str) -> ChainResult:
        """B1: call FundingPool.markDefaulted(tokenId) on-chain.

        Looks up the financing's token_id from DB, then broadcasts the tx.
        The contract must emit a FinancingDefaulted event that Goldsky indexes.
        """
        from app.core.db import get_supabase
        import asyncio as _asyncio

        def _lookup_token_id() -> Optional[int]:
            rows = (
                get_supabase()
                .table("financings")
                .select("token_id")
                .eq("id", financing_id)
                .limit(1)
                .execute()
            )
            if not rows.data or not rows.data[0].get("token_id"):  # type: ignore[union-attr]
                return None
            return int(rows.data[0]["token_id"])  # type: ignore[index]

        token_id = await _asyncio.to_thread(_lookup_token_id)
        if token_id is None:
            raise ValueError(
                f"Cannot mark_defaulted: financing {financing_id} has no token_id"
            )

        fn = self._pool.functions.markDefaulted(token_id)
        return await self._send(fn)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

_mock_singleton: Optional[ChainClient] = None
_real_singleton: Optional[ChainClient] = None


def get_chain_client() -> ChainClient:
    global _mock_singleton, _real_singleton
    settings = get_settings()
    if settings.chain_mock_mode:
        if _mock_singleton is None:
            _mock_singleton = MockChainClient()
        return _mock_singleton
    if _real_singleton is None:
        _real_singleton = MantleChainClient()
    return _real_singleton
