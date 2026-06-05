# Supabase migrations

Migrations are plain SQL, applied in lexicographic order.

## Local apply (when Supabase project is provisioned)

```bash
# Option A: via supabase CLI
supabase db push

# Option B: direct psql
psql "$DATABASE_URL" -f 001_initial_schema.sql
psql "$DATABASE_URL" -f 002_seed_milestone_options.sql
```

## Files

| File | Purpose |
|------|---------|
| `001_initial_schema.sql` | Core tables — users, documents, financings, milestones, fundings, auth_nonces, milestone_options. RLS enabled, backend uses service role. |
| `002_seed_milestone_options.sql` | Seed the dropdown options FE consumes via `GET /milestones/options`. |

## Conventions

- `uuid` PKs by default. Surrogate ids only.
- All tables have `created_at` and `updated_at` (auto-updated via trigger).
- Money columns are `numeric(20, 2)`. USDT0 has 6 decimals on-chain but we
  normalize to 2 in the DB for display; on-chain values are reconstructed
  at submit time.
- Enums are first-class Postgres types. Add a value with `alter type ... add value ...`.
- RLS is on for every table but no public policies — backend uses
  `SUPABASE_SERVICE_KEY` and bypasses RLS. We can layer user-facing policies
  later if we expose Supabase directly.
