-- LIEN agent loop tables
-- Supports the autonomous AI agent: on-chain event → AI verify → releaseMilestone()
--
-- agent_queue  : incoming events from Goldsky webhook, one row per ProofSubmitted event
-- agent_decisions : audit trail of every AI verdict + on-chain tx

-- agent_queue -----------------------------------------------------------------

create type agent_queue_status as enum (
  'pending',     -- waiting for worker to pick up
  'processing',  -- worker locked it
  'done',        -- AI ran, tx sent (or not needed)
  'failed'       -- unrecoverable error, manual review needed
);

create table agent_queue (
  id uuid primary key default uuid_generate_v4(),

  -- Source event (from Goldsky / FundingPool.ProofSubmitted)
  financing_id uuid not null references financings (id) on delete cascade,
  milestone_idx smallint not null check (milestone_idx between 1 and 4),
  ipfs_hash text not null,                -- IPFS CID of the proof file
  submitted_by text not null,             -- supplier wallet address

  -- Worker state
  status agent_queue_status not null default 'pending',
  locked_at timestamptz,                  -- set when worker starts processing
  locked_by text,                         -- worker instance id (for multi-instance safety)

  -- Error tracking
  error_message text,
  attempt_count smallint not null default 0,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger agent_queue_updated_at before update on agent_queue
  for each row execute function set_updated_at();

create index agent_queue_status_idx on agent_queue (status, created_at);
create index agent_queue_financing_idx on agent_queue (financing_id, milestone_idx);

-- agent_decisions -------------------------------------------------------------

create type agent_verdict as enum ('APPROVED', 'REJECTED', 'ESCALATED');

create table agent_decisions (
  id uuid primary key default uuid_generate_v4(),

  -- Source
  queue_id uuid not null references agent_queue (id) on delete restrict,
  financing_id uuid not null references financings (id) on delete restrict,
  milestone_idx smallint not null,

  -- AI output
  verdict agent_verdict not null,
  confidence numeric(4, 3) not null check (confidence between 0 and 1),
  checks jsonb,                          -- per-check breakdown from AI verifier
  fail_reasons text[],
  display_message text,

  -- On-chain action
  tx_hash text,                          -- releaseMilestone() tx, null if rejected
  block_number bigint,

  -- Timing
  ai_latency_ms integer,                 -- how long the AI pipeline took
  created_at timestamptz not null default now()
);

create index agent_decisions_financing_idx on agent_decisions (financing_id, milestone_idx);
create index agent_decisions_verdict_idx on agent_decisions (verdict);

-- RLS -------------------------------------------------------------------------

alter table agent_queue enable row level security;
alter table agent_decisions enable row level security;

-- Service role bypasses RLS automatically (used by BE).
-- These policies allow authenticated users (dashboard) to read their own data.

create policy "agent_queue: service role full access"
  on agent_queue for all
  using (auth.role() = 'service_role');

create policy "agent_decisions: service role full access"
  on agent_decisions for all
  using (auth.role() = 'service_role');
