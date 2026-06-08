-- Migration 005: F1 retry freeze + B1 auto-default support
--
-- F1 (PRD §Milestone Retry Policy):
--   If a supplier's proof is REJECTED 3 times within a 7-day rolling window
--   for the same (financing_id, milestone_idx), the system freezes further
--   submissions for 48 hours from the third rejection.
--
-- B1 (PRD §Default Conditions):
--   Any financing overdue by more than 44 calendar days is automatically
--   marked as defaulted by a nightly cron job.
--
-- This migration adds:
--   - financings.frozen_until  — non-null when F1 freeze is active
--   - financings.defaulted_at  — timestamp when B1 auto-default triggered

alter table financings
  add column if not exists frozen_until timestamptz default null,
  add column if not exists defaulted_at timestamptz default null;

comment on column financings.frozen_until is
  'F1: set to now() + 48h after 3 REJECTED AI decisions within 7 days on the same milestone. '
  'New proof uploads and agent queue processing are blocked until this timestamp passes.';

comment on column financings.defaulted_at is
  'B1: timestamp when the nightly auto-default cron triggered (financing overdue > 44 days). '
  'Null for non-defaulted financings.';

-- Index to speed up the nightly B1 cron query
-- (find all published/in_progress financings with due_date < today - 44 days)
create index if not exists financings_due_date_status_idx
  on financings (due_date, status)
  where status in ('published', 'in_progress');
