"""Tests for ChainClient.fund() — the atomic deposit + M1 release contract.

Regression: prior /financing/{id}/fund called release_milestone(_, 1)
unconditionally, which reverts MilestoneAlreadyReleased in real mode
because FundingPool.fund auto-releases M1 inside the investor's wagmi tx.

The fix adds a fund() method:
- Mock: returns a deterministic tx_hash for the combined operation.
- Real (Mantle): raises NotImplementedError so accidental BE-side calls
  fail loud — the investor's wallet must send this tx.
"""

from __future__ import annotations

import pytest

from app.services.chain import ChainClient, MockChainClient


@pytest.mark.asyncio
async def test_mock_fund_returns_deterministic_tx_hash():
    """MockChainClient.fund() must return the same tx_hash for the same
    financing_id every time — required for idempotent retries in mock mode.
    """
    chain = MockChainClient()
    tx1 = await chain.fund("fin-123")
    tx2 = await chain.fund("fin-123")
    assert tx1 == tx2, "fund() must be deterministic per financing_id"
    assert tx1.startswith("0x") and len(tx1) == 66, (
        f"tx_hash should be 32-byte hex, got {tx1!r}"
    )


@pytest.mark.asyncio
async def test_mock_fund_differs_per_financing():
    """Different financings must produce different tx_hashes — otherwise
    DB rows for separate deals would collide on fund_tx_hash.
    """
    chain = MockChainClient()
    tx_a = await chain.fund("fin-A")
    tx_b = await chain.fund("fin-B")
    assert tx_a != tx_b


@pytest.mark.asyncio
async def test_mock_fund_does_not_call_release_milestone():
    """Critical: fund() must NOT internally invoke release_milestone.

    PRD: FundingPool.fund atomically deposits + releases M1 inside one tx.
    The BE's fund() must mirror that — returning the combined tx_hash without
    making a separate release_milestone call (which would double-release M1
    in real mode and revert MilestoneAlreadyReleased).

    Verify by checking that calling fund() then release_milestone(_, 1)
    returns DIFFERENT tx_hashes (proving fund did not internally call release).
    """
    chain = MockChainClient()
    fund_tx = await chain.fund("fin-X")
    release_tx = (await chain.release_milestone("fin-X", 1)).tx_hash
    assert fund_tx != release_tx, (
        "fund() and release_milestone(_, 1) must produce distinct tx_hashes "
        "to prove fund() does not internally invoke release_milestone — "
        "otherwise real mode would double-release M1."
    )


@pytest.mark.asyncio
async def test_base_chain_client_fund_raises_not_implemented():
    """The ABC's fund() must raise NotImplementedError so the real Mantle client
    inherits this behaviour by default — investors send the wagmi tx, not BE.

    This is the safety net for the real-mode double-release bug: even if a
    future ChainClient subclass forgets to override fund(), the call fails
    loudly rather than silently sending a duplicate tx.
    """
    client = ChainClient()
    with pytest.raises(NotImplementedError):
        await client.fund("fin-Y")
