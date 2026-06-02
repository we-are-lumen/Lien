# LIEN — Project Context for AI Agents

## What is this?
Invoice/PO financing platform on Mantle testnet (EVM L2). AI-verified, blockchain-settled.

## Stack
- FastAPI backend (`backend/`)
- Solidity smart contracts via Foundry (`contracts/`)
- Supabase for off-chain DB
- Claude AI for 4-stage document verification
- IPFS/Pinata for document storage

## Critical Rules
- ALL business logic goes in backend — FE never calls Supabase or AI directly
- AI Verifier wallet key is SERVER-SIDE only, never exposed to client
- USDT0 is the settlement token (not native MNT)
- Contracts: InvoiceRegistry, FinancingToken (ERC-1155), FundingPool, ReputationOracle (soulbound)
- Deploy target: Mantle testnet (chainId 5003)

## AI Verification Pipeline (4 stages, ≤15s total)
- Stage A: OCR + anomaly detection on uploaded doc
- Stage B: Counterparty verification (AHU/OSS)
- Stage C: Relationship validation
- Stage D: Double-financing check

## Hackathon Deadline
Mantle Turing Test 2026 — June 15, 2026
