-- Tender Bhai schema. Run once in the Supabase SQL editor (or psql).
-- Idempotent: safe to re-run.

create extension if not exists pgcrypto;

create table if not exists tenders (
  id                  uuid primary key default gen_random_uuid(),
  portal              text not null,
  tender_id           text not null,
  dedup_hash          text not null,
  title               text not null,
  buyer_org           text not null default '',
  department          text,
  items               jsonb not null default '[]'::jsonb,
  items_text          text not null default '',
  estimated_value_inr numeric,
  emd_inr             numeric,
  deadline            timestamptz,
  opening_date        timestamptz,
  state               text,
  location            text,
  contact             jsonb not null default '{}'::jsonb,
  portal_url          text not null,
  document_url        text,
  eligibility         jsonb not null default '{}'::jsonb,
  fit                 text not null default 'possible' check (fit in ('strong','possible','skip')),
  fit_reason          text not null default '',
  summary             text not null default '',
  raw_text            text not null default '',
  first_seen_at       timestamptz not null default now(),
  sent_at             timestamptz,
  status              text not null default 'new' check (status in ('new','sent','closed','ignored')),
  updated_at          timestamptz not null default now(),
  unique (portal, tender_id)
);
create index if not exists tenders_dedup_hash_idx on tenders (dedup_hash);
create index if not exists tenders_deadline_idx   on tenders (deadline);
create index if not exists tenders_unsent_idx     on tenders (status, sent_at) where sent_at is null;
create index if not exists tenders_first_seen_idx on tenders (first_seen_at desc);

create table if not exists runs (
  id             uuid primary key default gen_random_uuid(),
  started_at     timestamptz not null default now(),
  finished_at    timestamptz,
  status         text not null default 'running' check (status in ('running','ok','partial','failed')),
  dry_run        boolean not null default false,
  portal_counts  jsonb not null default '{}'::jsonb,
  candidates     integer not null default 0,
  extracted      integer not null default 0,
  new_tenders    integer not null default 0,
  errors         jsonb not null default '[]'::jsonb
);
create index if not exists runs_started_idx on runs (started_at desc);

create table if not exists messages (
  id             bigint generated always as identity primary key,
  direction      text not null check (direction in ('in','out')),
  phone          text not null,
  text           text not null,
  wa_message_id  text,
  created_at     timestamptz not null default now()
);
create index if not exists messages_phone_created_idx on messages (phone, created_at desc);
create unique index if not exists messages_wa_id_idx on messages (wa_message_id) where wa_message_id is not null;

create table if not exists preferences (
  key        text primary key,
  value      jsonb,
  updated_at timestamptz not null default now()
);

-- keep updated_at fresh on tenders
create or replace function set_updated_at() returns trigger language plpgsql as $$
begin
  new.updated_at = now();
  return new;
end $$;
drop trigger if exists tenders_set_updated_at on tenders;
create trigger tenders_set_updated_at before update on tenders
  for each row execute function set_updated_at();

-- Service role bypasses RLS; enable RLS so the anon key sees nothing.
alter table tenders     enable row level security;
alter table runs        enable row level security;
alter table messages    enable row level security;
alter table preferences enable row level security;

-- Default preferences (edit from WhatsApp: "only above 10 lakh", "ignore north-east")
insert into preferences (key, value) values
  ('min_value', '600000'::jsonb),
  ('ignore_states', '[]'::jsonb),
  ('extra_keywords', '[]'::jsonb),
  ('ignore_keywords', '[]'::jsonb)
on conflict (key) do nothing;
