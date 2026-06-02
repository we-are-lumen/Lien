-- LIEN Initial Schema
-- Run this in Supabase SQL editor or via supabase db push

-- ============================================================
-- EXTENSIONS
-- ============================================================
create extension if not exists "uuid-ossp";

-- ============================================================
-- ENUMS
-- ============================================================
create type document_type as enum ('invoice', 'purchase_order');
create type document_status as enum (
  'pending',       -- uploaded, not yet verified
  'verifying',     -- AI pipeline running
  'verified',      -- passed all 4 stages
  'rejected',      -- failed verification
  'financed'       -- active financing
);
create type financing_status as enum (
  'open',          -- accepting investors
  'funded',        -- fully funded, escrow locked
  'active',        -- milestones in progress
  'completed',     -- fully repaid
  'defaulted'
);
create type milestone_status as enum (
  'pending',
  'submitted',     -- borrower submitted proof
  'verified',      -- AI + agent verified
  'released'       -- funds released to borrower
);
create type repayment_status as enum (
  'scheduled',
  'paid',
  'overdue'
);
create type risk_tier as enum ('low', 'medium', 'high');
create type agent_decision_verdict as enum ('approved', 'rejected', 'flagged', 'escalated');

-- ============================================================
-- TABLES
-- ============================================================

-- 1. Invoices
create table invoices (
  id                  uuid primary key default uuid_generate_v4(),
  owner_address       text not null,                    -- borrower wallet
  ipfs_hash           text not null unique,             -- Pinata CID
  registry_id         text,                             -- on-chain InvoiceRegistry ID (set after BC tx)
  invoice_number      text,
  issuer_name         text,
  counterparty_name   text,
  amount              numeric(20, 6),                   -- in USDT0
  currency            text default 'USDT0',
  due_date            date,
  status              document_status default 'pending',
  ai_stage_a          jsonb,                            -- OCR + anomaly result
  ai_stage_b          jsonb,                            -- counterparty check
  ai_stage_c          jsonb,                            -- relationship check
  ai_stage_d          jsonb,                            -- double-financing check
  ai_score            numeric(5, 4),                    -- 0.0000 - 1.0000
  risk_tier           risk_tier,
  rejection_reason    text,
  created_at          timestamptz default now(),
  updated_at          timestamptz default now()
);

-- 2. Purchase Orders
create table purchase_orders (
  id                  uuid primary key default uuid_generate_v4(),
  owner_address       text not null,
  ipfs_hash           text not null unique,
  registry_id         text,
  po_number           text,
  issuer_name         text,
  counterparty_name   text,
  total_amount        numeric(20, 6),
  currency            text default 'USDT0',
  delivery_date       date,
  buyer_confirmed     boolean default false,            -- Q10: optional, deduct 10pts if false
  status              document_status default 'pending',
  ai_stage_a          jsonb,
  ai_stage_b          jsonb,
  ai_stage_c          jsonb,
  ai_stage_d          jsonb,
  ai_score            numeric(5, 4),
  risk_tier           risk_tier,
  rejection_reason    text,
  created_at          timestamptz default now(),
  updated_at          timestamptz default now()
);

-- 3. Financing Requests
create table financing_requests (
  id                  uuid primary key default uuid_generate_v4(),
  document_type       document_type not null,
  document_id         uuid not null,                    -- invoice.id or purchase_order.id
  borrower_address    text not null,
  requested_amount    numeric(20, 6) not null,
  funded_amount       numeric(20, 6) default 0,
  interest_rate_bps   integer not null,                 -- basis points, e.g. 500 = 5%
  tenure_days         integer not null,
  advance_rate        numeric(5, 4),                    -- PO: 0.70-0.80 by risk tier; Invoice: 1.0
  status              financing_status default 'open',
  pool_address        text,                             -- FundingPool contract address
  token_id            bigint,                           -- ERC-1155 FinancingToken ID
  tx_hash_fund        text,                             -- funding tx
  tx_hash_close       text,                             -- repayment/close tx
  funded_at           timestamptz,
  completed_at        timestamptz,
  created_at          timestamptz default now(),
  updated_at          timestamptz default now()
);

-- 4. Milestones (Invoice: M1-M3 at 30/50/20%; PO: M1-M4 at 30/30/20/20%)
create table milestones (
  id                  uuid primary key default uuid_generate_v4(),
  financing_id        uuid not null references financing_requests(id) on delete cascade,
  milestone_index     smallint not null,                -- 1-4
  description         text,
  release_percentage  numeric(5, 2) not null,           -- % of total to release
  release_amount      numeric(20, 6),
  status              milestone_status default 'pending',
  proof_ipfs_hash     text,                             -- borrower-submitted proof
  ai_verification     jsonb,                            -- agent verification result
  tx_hash_release     text,
  due_date            date,
  completed_at        timestamptz,
  created_at          timestamptz default now(),
  updated_at          timestamptz default now(),
  unique (financing_id, milestone_index)
);

-- 5. Repayments
create table repayments (
  id                  uuid primary key default uuid_generate_v4(),
  financing_id        uuid not null references financing_requests(id) on delete cascade,
  borrower_address    text not null,
  amount              numeric(20, 6) not null,
  status              repayment_status default 'scheduled',
  due_date            date not null,
  paid_at             timestamptz,
  tx_hash             text,
  created_at          timestamptz default now(),
  updated_at          timestamptz default now()
);

-- ============================================================
-- INDEXES
-- ============================================================
create index idx_invoices_owner on invoices(owner_address);
create index idx_invoices_status on invoices(status);
create index idx_pos_owner on purchase_orders(owner_address);
create index idx_pos_status on purchase_orders(status);
create index idx_financing_borrower on financing_requests(borrower_address);
create index idx_financing_status on financing_requests(status);
create index idx_financing_document on financing_requests(document_type, document_id);
create index idx_milestones_financing on milestones(financing_id);
create index idx_repayments_financing on repayments(financing_id);
create index idx_repayments_borrower on repayments(borrower_address);

-- ============================================================
-- UPDATED_AT TRIGGER
-- ============================================================
create or replace function set_updated_at()
returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

create trigger trg_invoices_updated_at
  before update on invoices
  for each row execute function set_updated_at();

create trigger trg_pos_updated_at
  before update on purchase_orders
  for each row execute function set_updated_at();

create trigger trg_financing_updated_at
  before update on financing_requests
  for each row execute function set_updated_at();

create trigger trg_milestones_updated_at
  before update on milestones
  for each row execute function set_updated_at();

create trigger trg_repayments_updated_at
  before update on repayments
  for each row execute function set_updated_at();

-- ============================================================
-- ROW LEVEL SECURITY
-- ============================================================
alter table invoices enable row level security;
alter table purchase_orders enable row level security;
alter table financing_requests enable row level security;
alter table milestones enable row level security;
alter table repayments enable row level security;

-- Invoices: owner can see own, everyone can see financed (marketplace)
create policy "invoices_owner_select" on invoices
  for select using (
    owner_address = current_setting('app.wallet_address', true)
    or status = 'financed'
  );
create policy "invoices_owner_insert" on invoices
  for insert with check (
    owner_address = current_setting('app.wallet_address', true)
  );
create policy "invoices_owner_update" on invoices
  for update using (
    owner_address = current_setting('app.wallet_address', true)
  );

-- Purchase orders: same pattern
create policy "pos_owner_select" on purchase_orders
  for select using (
    owner_address = current_setting('app.wallet_address', true)
    or status = 'financed'
  );
create policy "pos_owner_insert" on purchase_orders
  for insert with check (
    owner_address = current_setting('app.wallet_address', true)
  );
create policy "pos_owner_update" on purchase_orders
  for update using (
    owner_address = current_setting('app.wallet_address', true)
  );

-- Financing requests: borrower sees own, investors see open/funded/active
create policy "financing_select" on financing_requests
  for select using (
    borrower_address = current_setting('app.wallet_address', true)
    or status in ('open', 'funded', 'active')
  );
create policy "financing_borrower_insert" on financing_requests
  for insert with check (
    borrower_address = current_setting('app.wallet_address', true)
  );
create policy "financing_borrower_update" on financing_requests
  for update using (
    borrower_address = current_setting('app.wallet_address', true)
  );

-- Milestones: visible if financing is visible
create policy "milestones_select" on milestones
  for select using (
    exists (
      select 1 from financing_requests fr
      where fr.id = milestones.financing_id
      and (
        fr.borrower_address = current_setting('app.wallet_address', true)
        or fr.status in ('open', 'funded', 'active')
      )
    )
  );

-- Repayments: borrower sees own
create policy "repayments_select" on repayments
  for select using (
    borrower_address = current_setting('app.wallet_address', true)
  );
create policy "repayments_borrower_insert" on repayments
  for insert with check (
    borrower_address = current_setting('app.wallet_address', true)
  );

-- NOTE: Backend uses SUPABASE_SERVICE_KEY which bypasses RLS entirely.
-- RLS here is defense-in-depth for direct DB access or anon key usage.

-- ============================================================
-- AGENT LOOP TABLES (BE-8: autonomous on-chain agent)
-- ============================================================

-- 6. Agent Queue - on-chain events pending AI processing
create table agent_queue (
  id                  uuid primary key default uuid_generate_v4(),
  event_type          text not null,                    -- e.g. 'ProofSubmitted', 'FinancingFunded'
  financing_id        uuid references financing_requests(id),
  milestone_index     smallint,
  proof_ipfs_hash     text,
  submitted_by        text,                             -- wallet that submitted proof
  tx_hash             text,                             -- on-chain event tx
  block_number        bigint,
  status              text default 'pending'            -- pending | processing | done | failed
    check (status in ('pending', 'processing', 'done', 'failed')),
  picked_up_at        timestamptz,
  created_at          timestamptz default now(),
  updated_at          timestamptz default now()
);

-- 7. Agent Decisions - audit trail of all AI verdicts
create table agent_decisions (
  id                  uuid primary key default uuid_generate_v4(),
  queue_id            uuid references agent_queue(id),
  financing_id        uuid references financing_requests(id),
  milestone_index     smallint,
  verdict             agent_decision_verdict not null,
  confidence_score    numeric(5, 4) not null,           -- 0.0000 - 1.0000
  reasoning           text,                             -- Claude's reasoning summary
  stage_results       jsonb,                            -- full per-stage breakdown
  tx_hash             text,                             -- releaseMilestone() tx if approved
  verifier_address    text,                             -- AI Verifier wallet address
  created_at          timestamptz default now()
);

create index idx_agent_queue_status on agent_queue(status);
create index idx_agent_queue_financing on agent_queue(financing_id);
create index idx_agent_decisions_financing on agent_decisions(financing_id);

create trigger trg_agent_queue_updated_at
  before update on agent_queue
  for each row execute function set_updated_at();

alter table agent_queue enable row level security;
alter table agent_decisions enable row level security;

-- Agent queue: only BE service role reads/writes (bypass via service key)
-- Agent decisions: public read for transparency (audit trail)
create policy "agent_decisions_public_select" on agent_decisions
  for select using (true);

