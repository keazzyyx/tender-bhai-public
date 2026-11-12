# Migration notes

`schema.sql` is idempotent (`create ... if not exists`, `on conflict do nothing`). Re-run it
after pulling a new version; it never drops data.

## Applying

1. Supabase dashboard → SQL Editor → paste `schema.sql` → Run.
2. Or with psql: `psql "$SUPABASE_DB_URL" -f supabase/schema.sql`.

## Conventions

- All timestamps are `timestamptz`; the app writes ISO strings with `+05:30`.
- `tenders (portal, tender_id)` is the hard uniqueness key; `dedup_hash` (sha256 of the
  normalised title + buyer, first 32 hex chars) is a soft key checked in code before insert.
- `preferences` is a key/value store. Keys used by the app:
  `min_value` (rupees, number), `ignore_states` (list), `only_states` (list),
  `extra_keywords` (list), `ignore_keywords` (list), `pending_digest` (object, transient),
  `last_digest_ordinals` (object `{sent_at, ids: [...]}`; maps "details on 2" → tender id).
- Access is through the PostgREST API with the **service role key** only (RLS is on for
  every table so the anon key cannot read tenders or messages).

## Changing the schema later

Add a new numbered file `supabase/migrations/NNN_description.sql` containing only the
delta (`alter table ... add column if not exists ...`) and append the same change to
`schema.sql` so a fresh install still works from one file.
