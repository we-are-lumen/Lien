# LIEN Contracts

Smart contracts for the LIEN invoice/PO financing platform on Mantle.

## Stack

- Solidity 0.8.24
- Foundry (forge + cast + anvil)
- OpenZeppelin v5

## Contracts

| Contract | Purpose |
|----------|---------|
| `InvoiceRegistry.sol` | On-chain registry of document hashes. Prevents double-financing. |
| `FinancingToken.sol`  | ERC-1155 token. One id per financing. Held by investor; metadata in IPFS. |
| `FundingPool.sol`     | Escrow + milestone-gated disbursement. Origination + performance fees. |
| `ReputationOracle.sol`| Soulbound reputation tracking for suppliers and the AI verifier. |

## Roles

- **owner** — deployer; can rotate AI verifier and treasury.
- **AI verifier** — backend wallet allowed to release milestones M2..M4.
- **treasury** — receives the 1.5% origination + 10% performance fees.
- **investor** — funds via `FundingPool.fund()`, holds the financing token.
- **supplier** — receives milestone payouts; settles via `FundingPool.repay()`.

## Agent-loop integration

`FundingPool` exposes an event-driven hook for the off-chain AI agent:

| Step | On-chain | Off-chain |
|------|----------|-----------|
| Upload | — | FE POSTs file to `/financing/{id}/milestone-proof`, BE returns CID |
| Signal | Supplier calls `FundingPool.submitProof(tokenId, milestoneIdx, ipfsCid)` | — |
| | Contract emits `ProofSubmitted(tokenId, milestoneIdx, ipfsCid, supplier)` | Goldsky subgraph forwards to `POST /agent/webhook` |
| Verify | — | Agent fetches file, runs Claude Vision |
| Release | Agent EOA calls `FundingPool.releaseMilestone(tokenId, milestoneIdx)` | Audit row written to `agent_decisions` |

`submitProof` constraints:
- Pure signaling — no state change, no token transfer.
- Only the deal's `supplier` can call it. Investors, AI verifier, and randoms revert with `NotSupplier`.
- `milestoneIdx == 1` reverts with `CannotSubmitM1` (M1 auto-releases inside `fund()`).
- Already-released milestones revert with `MilestoneAlreadyReleased`.
- Unknown `tokenId` reverts with `UnknownDeal`.

## Build + test

```bash
forge install
forge build
forge test -vv
```

## Deploy

```bash
export DEPLOYER_PRIVATE_KEY=0x...
export TREASURY_ADDRESS=0x...
export AI_VERIFIER_ADDRESS=0x...
export USDT_ADDRESS=0x...
export MANTLE_RPC_URL=https://rpc.sepolia.mantle.xyz

forge script script/Deploy.s.sol \
  --rpc-url mantle_sepolia \
  --broadcast \
  --verify
```

## Fees

- Origination: **1.5%** of funded amount, deducted at `fund()` time → treasury.
- Performance: **10%** of yield, deducted at `repay()` time → treasury.
- Gas: paid by caller.

## Milestone splits

Sum to 100 per financing. Default by product type:

- Invoice (3 milestones): 30 / 50 / 20
- PO (4 milestones): 30 / 30 / 20 / 20

The caller of `fund()` supplies the split as a calldata array so different
deals can use different splits if needed in the future.

## Tests

30 tests across 4 suites — pass with `forge test -vv`.

```
InvoiceRegistry:  6 tests + fuzz (256 runs on register)
FinancingToken:   7 tests
FundingPool:      10 tests (fund, milestone release, repay, default, double-fund, bad splits)
ReputationOracle: 7 tests (writers, score formula, default penalty)
```
