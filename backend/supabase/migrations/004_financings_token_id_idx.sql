-- Webhook handler resolves token_id -> financing_id on every ProofSubmitted event.
-- Index speeds up that lookup. financings.token_id is unique in practice (one
-- ERC-1155 token per financing) but the schema does not enforce it; partial
-- index on non-null values is enough for the lookup pattern.

create index if not exists financings_token_id_idx
  on financings (token_id)
  where token_id is not null;
