# LIEN

Invoice/PO financing on Mantle blockchain with AI verification pipeline.

## Stack
- **Backend**: FastAPI + Supabase + Anthropic Claude
- **Smart Contracts**: Solidity/Foundry on Mantle testnet
- **AI**: 4-stage verification pipeline (OCR → counterparty → relationship → double-financing)
- **Agent loop**: Goldsky webhook → Claude → on-chain tx

## Monorepo Layout
```
lien/
├── backend/          # FastAPI app
│   ├── app/
│   │   ├── api/      # route handlers
│   │   ├── ai/       # prompts + pipeline stages
│   │   ├── services/ # supabase, pinata, web3 clients
│   │   └── agent/    # autonomous agent loop
│   ├── requirements.txt
│   └── Dockerfile
└── contracts/        # Solidity + Foundry
    ├── src/          # InvoiceRegistry, FinancingToken, FundingPool, ReputationOracle
    ├── test/         # fuzz tests
    └── script/       # deploy scripts
```

## Setup
1. `cp backend/.env.example backend/.env` and fill in values
2. Install Foundry: `curl -L https://foundry.paradigm.xyz | bash && foundryup`
3. `cd contracts && forge install`
4. `cd backend && pip install -r requirements.txt`
5. `uvicorn app.main:app --reload`

## Contracts (Mantle Testnet)
See `contracts/` — deploy with `forge script script/Deploy.s.sol --rpc-url mantle_testnet --broadcast`
