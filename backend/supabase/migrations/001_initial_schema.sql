-- LIEN initial schema
-- Reference: docs/api-spec.md (Notion contract) + PRD v3.0
--
-- Design notes:
-- * `documents` holds raw uploaded invoices/POs with AI verification record.
-- * `financings` are the tokenized financing entities exposed on the marketplace.
--   One document maps to at most one financing.
-- * `users` are wallet-addressed actors. A user can play multiple roles
--   (supplier, investor, buyer) so role is stored on the relevant relation,
--   not on the user.
-- * RLS is enabled but no public policies are defined. All access goes
--   through the FastAPI backend using the service role key. This keeps the
--   surface area small for MVP; user-facing policies can be layered later.

-- Extensions ----------------------------------------------------------------

create extension if not exists "uuid-ossp";
create extension if not exists "pgcrypto";

-- updated_at trigger helper -------------------------------------------------

create or replace function set_updated_at() returns trigger as $$
begin
  new.updated_at = now();
  return new;
end;
$$ language plpgsql;

-- Enums ---------------------------------------------------------------------

create type document_type as enum ('invoice', 'po');
create type document_status as enum ('pending', 'approved', 'rejected');

create type financing_status as enum (
  'draft',         -- created but not yet tokenized
  'published',     -- visible on marketplace
  'funded',        -- investor funded, M1 released
  'in_progress',   -- mid-milestones
  'repaid',        -- fully settled
  'defaulted',
  'frozen',
  'blacklisted'
);

create type payment_status as enum ('unpaid', 'partial', 'paid');
create type risk_tier as enum ('low', 'medium', 'high', 'reject');

create type milestone_status as enum (
  'pending',          -- not yet eligible
  'proof_uploaded',   -- supplier uploaded, awaiting AI verdict
  'approved',         -- AI approved, awaiting on-chain release
  'rejected',         -- AI rejected, supplier can retry
  'released',         -- funds released on-chain
  'escalated'         -- exceeded retry budget, manual review
);

-- users ---------------------------------------------------------------------

create table users (
  id uuid primary key default uuid_generate_v4(),
  address text not null unique,            -- 0x-prefixed wallet
  display_name text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger users_updated_at before update on users
  for each row execute function set_updated_at();

-- auth_nonces ---------------------------------------------------------------
-- One-time nonces signed by the wallet to authenticate. Cleared after use.

create table auth_nonces (
  address text not null,
  nonce text not null,
  expires_at timestamptz not null,
  used boolean not null default false,
  created_at timestamptz not null default now(),
  primary key (address, nonce)
);

create index auth_nonces_expires_idx on auth_nonces (expires_at);

-- milestone_options ---------------------------------------------------------
-- Seedable list of milestone descriptions for the dropdown in FE.

create table milestone_options (
  id serial primary key,
  name text not null unique,
  description text,
  product_type document_type,            -- null = applicable to both
  created_at timestamptz not null default now()
);

-- documents -----------------------------------------------------------------

create table documents (
  id uuid primary key default uuid_generate_v4(),
  supplier_id uuid not null references users (id) on delete restrict,
  document_type document_type not null,

  -- File storage
  file_url text not null,                -- IPFS gateway URL
  ipfs_cid text,                          -- raw CID for on-chain reference
  file_sha256 text not null,             -- file content hash

  -- Hash used for on-chain double-financing registry
  -- keccak256(buyer || nominal || due_date || nomor_dokumen)
  doc_hash text not null unique,

  -- Document fields
  invoice_number text,                   -- required if type = invoice
  po_number text,                         -- required if type = po
  issuer_name text not null,
  buyer_name text not null,
  buyer_address text,                    -- 0x... if buyer is on-platform
  total_amount numeric(20, 2) not null check (total_amount > 0),
  invoice_date date,
  due_date date,

  -- AI verification record (full pipeline output)
  ai_verification jsonb,                 -- {risk_score, doc_score, counterparty_score, relationship_score, unique, flags[], stages: {...}}

  status document_status not null default 'pending',

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  -- Type-specific required field enforcement
  constraint documents_type_fields check (
    (document_type = 'invoice' and invoice_number is not null)
    or (document_type = 'po' and po_number is not null)
  )
);

create trigger documents_updated_at before update on documents
  for each row execute function set_updated_at();

create index documents_supplier_idx on documents (supplier_id);
create index documents_status_idx on documents (status);

-- financings ----------------------------------------------------------------

create table financings (
  id uuid primary key default uuid_generate_v4(),
  document_id uuid not null unique references documents (id) on delete restrict,
  supplier_id uuid not null references users (id) on delete restrict,
  buyer_id uuid references users (id) on delete set null,

  product_type document_type not null,
  milestone_config smallint not null check (milestone_config in (3, 4)),
  advance_rate smallint not null check (advance_rate between 1 and 100),

  -- Pricing
  amount numeric(20, 2) not null,             -- face value (from document.total_amount)
  funding_amount numeric(20, 2) not null,     -- discounted amount investor commits
  yield_rate numeric(6, 2) not null,          -- APR in %
  expected_yield_amount numeric(20, 2) not null,
  platform_fee numeric(20, 2) not null,       -- 1.5% origination + 10% performance projected
  total_repayment numeric(20, 2) not null,

  -- Risk
  risk_score smallint not null check (risk_score between 0 and 100),
  risk_tier risk_tier not null,

  -- Lifecycle
  status financing_status not null default 'draft',
  payment_status payment_status not null default 'unpaid',
  published_date date,
  due_date date not null,

  -- On-chain references
  token_id text,                              -- ERC-1155 token id
  registry_tx_hash text,
  fund_tx_hash text,
  repay_tx_hash text,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger financings_updated_at before update on financings
  for each row execute function set_updated_at();

create index financings_supplier_idx on financings (supplier_id);
create index financings_buyer_idx on financings (buyer_id);
create index financings_status_idx on financings (status);
create index financings_published_idx on financings (published_date desc);

-- milestones ----------------------------------------------------------------

create table milestones (
  id uuid primary key default uuid_generate_v4(),
  financing_id uuid not null references financings (id) on delete cascade,
  idx smallint not null check (idx between 1 and 4),
  name text not null,
  percentage smallint not null check (percentage between 0 and 100),
  payout_amount numeric(20, 2) not null,
  release_trigger text,
  milestone_option_id integer references milestone_options (id),

  status milestone_status not null default 'pending',
  proof_file_url text,
  proof_ipfs_cid text,
  ai_verification jsonb,
  retry_count smallint not null default 0,

  released_at timestamptz,
  release_tx_hash text,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  unique (financing_id, idx)
);

create trigger milestones_updated_at before update on milestones
  for each row execute function set_updated_at();

create index milestones_financing_idx on milestones (financing_id);
create index milestones_status_idx on milestones (status);

-- fundings (investor → financing) -------------------------------------------

create table fundings (
  id uuid primary key default uuid_generate_v4(),
  financing_id uuid not null references financings (id) on delete restrict,
  investor_id uuid not null references users (id) on delete restrict,
  amount numeric(20, 2) not null check (amount > 0),
  expected_return_amount numeric(20, 2) not null,
  tx_hash text,
  created_at timestamptz not null default now()
);

create index fundings_financing_idx on fundings (financing_id);
create index fundings_investor_idx on fundings (investor_id);

-- Lock everything down — backend is the only consumer via service role -----

alter table users enable row level security;
alter table auth_nonces enable row level security;
alter table documents enable row level security;
alter table financings enable row level security;
alter table milestones enable row level security;
alter table milestone_options enable row level security;
alter table fundings enable row level security;
