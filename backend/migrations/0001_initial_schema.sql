-- =============================================================================
-- ECHO — initial schema (PostgreSQL 15+ / Supabase)
--
-- Design notes
--  * The iOS app NEVER talks to this database directly. Only the FastAPI
--    backend (service role) does. Row Level Security is enabled on every
--    table with no policies, which denies the anon/authenticated roles by
--    default — a safety net if a Supabase key ever leaks to a client.
--  * Enumerations are TEXT + CHECK constraints (easier to evolve than PG enums).
--  * All timestamps are timestamptz (UTC on the wire). User-facing schedule
--    times also keep the IANA timezone + local wall-clock time so recurrence
--    stays correct across daylight-saving changes.
--  * Messaging eligibility is decided by deterministic backend code, using
--    friends.last_inbound_message_at + consent + pause flags. The language
--    model never decides whether a message may be sent.
-- =============================================================================

create extension if not exists pgcrypto;

-- updated_at helper ------------------------------------------------------------
create or replace function set_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

-- =============================================================================
-- 1. Users, devices, preferences
-- =============================================================================

-- id equals the Supabase auth user id (auth.users.id). No FK to the auth
-- schema so the migration also runs on plain PostgreSQL for local tests.
create table app_users (
  id                     uuid primary key default gen_random_uuid(),
  display_name           text,
  default_timezone       text not null default 'Asia/Kuala_Lumpur',
  automation_paused      boolean not null default false,   -- global emergency pause
  automation_paused_at   timestamptz,
  ai_disclosure_text     text not null default 'Some replies in this chat may be written by my AI assistant.',
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now()
);

create table user_devices (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references app_users(id) on delete cascade,
  apns_token     text not null unique,
  apns_env       text not null check (apns_env in ('sandbox', 'production')),
  bundle_id      text not null,
  last_seen_at   timestamptz not null default now(),
  revoked_at     timestamptz,
  created_at     timestamptz not null default now()
);
create index user_devices_user_idx on user_devices(user_id) where revoked_at is null;

create table notification_preferences (
  user_id                 uuid primary key references app_users(id) on delete cascade,
  message_sent            boolean not null default true,
  job_blocked             boolean not null default true,
  delivery_failed         boolean not null default true,
  needs_attention         boolean not null default true,
  automation_changed      boolean not null default true,
  connection_error        boolean not null default true,
  schedule_reminder       boolean not null default false,
  quiet_hours_start       time,
  quiet_hours_end         time,
  updated_at              timestamptz not null default now()
);

-- =============================================================================
-- 2. Assistant chat + global profile memory (Feature 1 & 6)
-- =============================================================================

create table chat_messages (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references app_users(id) on delete cascade,
  role          text not null check (role in ('user', 'assistant')),
  content       text not null,
  language      text,                          -- BCP-47, e.g. 'en', 'ja', 'it', 'zh-Hans'
  client_msg_id uuid,                          -- idempotency key from the iOS app
  created_at    timestamptz not null default now(),
  unique (user_id, client_msg_id)
);
create index chat_messages_user_time_idx on chat_messages(user_id, created_at desc);

-- Rolling summaries so we never send the full history to Claude.
create table chat_summaries (
  id                 uuid primary key default gen_random_uuid(),
  user_id            uuid not null references app_users(id) on delete cascade,
  covers_until       timestamptz not null,     -- messages up to here are summarised
  summary            text not null,
  created_at         timestamptz not null default now()
);
create index chat_summaries_user_idx on chat_summaries(user_id, covers_until desc);

-- Global communication profile, one row per learned trait.
create table profile_traits (
  id               uuid primary key default gen_random_uuid(),
  user_id          uuid not null references app_users(id) on delete cascade,
  category         text not null check (category in (
                     'languages', 'vocabulary', 'tone', 'slang', 'emoji', 'humour',
                     'sentence_style', 'social', 'interests', 'friend_preferences')),
  trait_key        text not null,              -- normalised key, e.g. 'emoji_frequency'
  value            text not null,              -- human-readable value shown in the dashboard
  source           text not null check (source in ('user_stated', 'inferred', 'user_edited')),
  confidence       numeric(3,2) not null default 0.50 check (confidence between 0 and 1),
  evidence_count   integer not null default 1 check (evidence_count >= 0),
  status           text not null default 'active'
                     check (status in ('active', 'confirmed', 'rejected', 'archived')),
  first_seen_at    timestamptz not null default now(),
  last_seen_at     timestamptz not null default now(),
  updated_at       timestamptz not null default now(),
  unique (user_id, category, trait_key)
);

-- Approved writing examples used for retrieval (few-shot style anchoring).
-- Phase 2 may add a pgvector embedding column for semantic retrieval.
create table writing_examples (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references app_users(id) on delete cascade,
  friend_id     uuid,                                         -- FK added below
  content       text not null,
  language      text,
  tags          text[] not null default '{}',
  source        text not null check (source in ('chat', 'manual', 'sent_message')),
  approved      boolean not null default false,
  created_at    timestamptz not null default now()
);
create index writing_examples_user_idx on writing_examples(user_id) where approved;

-- =============================================================================
-- 3. Instagram account, friends, consent (Feature 2 & 5)
-- =============================================================================

create table instagram_accounts (
  id                       uuid primary key default gen_random_uuid(),
  user_id                  uuid not null references app_users(id) on delete cascade,
  ig_user_id               text not null unique,       -- professional account <IG_ID>
  username                 text,
  account_type             text check (account_type in ('BUSINESS', 'MEDIA_CREATOR')),
  access_token_ciphertext  bytea,                      -- encrypted at the app layer, never plaintext
  token_expires_at         timestamptz,
  granted_scopes           text[] not null default '{}',
  status                   text not null default 'active'
                             check (status in ('active', 'expired', 'revoked', 'error')),
  last_error               text,
  connected_at             timestamptz not null default now(),
  updated_at               timestamptz not null default now()
);

create table friends (
  id                        uuid primary key default gen_random_uuid(),
  user_id                   uuid not null references app_users(id) on delete cascade,
  instagram_account_id      uuid not null references instagram_accounts(id) on delete cascade,
  igsid                     text not null,             -- Instagram-scoped ID of the friend
  display_name              text not null,
  username                  text,
  relationship_preset       text not null default 'custom'
                              check (relationship_preset in ('close_friend', 'classmate', 'professional', 'custom')),
  language                  text,
  ai_reply_enabled          boolean not null default false,   -- explicit opt-in per friend
  consent_status            text not null default 'unknown'
                              check (consent_status in ('unknown', 'requested', 'granted', 'revoked')),
  manual_override_until     timestamptz,               -- "I'm taking over" — automation off until
  last_inbound_message_at   timestamptz,               -- opens Meta's standard messaging window
  last_inbound_message_id   text,
  disclosure_sent_at        timestamptz,               -- when the AI disclosure was last shown
  created_at                timestamptz not null default now(),
  updated_at                timestamptz not null default now(),
  unique (instagram_account_id, igsid)
);
create index friends_user_idx on friends(user_id);

alter table writing_examples
  add constraint writing_examples_friend_fk foreign key (friend_id) references friends(id) on delete cascade;

create table friend_style_profiles (
  friend_id            uuid primary key references friends(id) on delete cascade,
  tone                 text,                           -- e.g. 'casual, playful'
  formality            smallint not null default 2 check (formality between 0 and 4),
  emoji_level          smallint not null default 2 check (emoji_level between 0 and 4),
  max_sentences        smallint check (max_sentences > 0),
  slang_allowed        boolean not null default true,
  custom_instructions  text,
  boundaries           jsonb not null default '{}'::jsonb,   -- topics to avoid, never-say list, etc.
  updated_at           timestamptz not null default now()
);

-- Friend-specific memory. Keep it minimal; sensitive items need explicit approval.
create table friend_memories (
  id             uuid primary key default gen_random_uuid(),
  user_id        uuid not null references app_users(id) on delete cascade,
  friend_id      uuid not null references friends(id) on delete cascade,
  content        text not null,
  source         text not null check (source in ('user_stated', 'inferred', 'user_edited')),
  sensitivity    text not null default 'normal' check (sensitivity in ('normal', 'sensitive')),
  approved       boolean not null default false,
  expires_at     timestamptz,
  created_at     timestamptz not null default now()
);
create index friend_memories_friend_idx on friend_memories(friend_id);

-- Append-only consent evidence. friends.consent_status is the current value.
create table consent_events (
  id             uuid primary key default gen_random_uuid(),
  friend_id      uuid not null references friends(id) on delete cascade,
  status         text not null check (status in ('requested', 'granted', 'revoked')),
  method         text not null check (method in ('dm_reply', 'dm_keyword_stop', 'in_person', 'other')),
  evidence       text,                         -- e.g. the IG message id where they agreed
  recorded_at    timestamptz not null default now()
);
create index consent_events_friend_idx on consent_events(friend_id, recorded_at desc);

-- =============================================================================
-- 4. Conversations, messages, webhooks
-- =============================================================================

create table ig_messages (
  id                uuid primary key default gen_random_uuid(),
  user_id           uuid not null references app_users(id) on delete cascade,
  friend_id         uuid not null references friends(id) on delete cascade,
  direction         text not null check (direction in ('inbound', 'outbound')),
  ig_message_id     text unique,                  -- Meta "mid"
  content           text,
  attachments       jsonb not null default '[]'::jsonb,
  sent_by           text not null check (sent_by in ('friend', 'user_manual', 'echo_auto', 'echo_scheduled')),
  platform_time     timestamptz not null,         -- timestamp from Meta
  received_at       timestamptz not null default now(),
  purge_after       timestamptz                   -- retention: content wiped after this
);
create index ig_messages_friend_time_idx on ig_messages(friend_id, platform_time desc);

-- Raw webhook deliveries; event_key makes processing idempotent.
create table webhook_events (
  id                 uuid primary key default gen_random_uuid(),
  provider           text not null default 'meta',
  event_key          text not null unique,        -- mid, or sha256 of the entry when no mid
  payload            jsonb not null,
  received_at        timestamptz not null default now(),
  processing_status  text not null default 'pending'
                       check (processing_status in ('pending', 'processing', 'done', 'ignored', 'error')),
  processed_at       timestamptz,
  error              text
);
create index webhook_events_pending_idx on webhook_events(received_at) where processing_status = 'pending';

-- =============================================================================
-- 5. Scheduling (Feature 4)
-- =============================================================================

-- A job is the *definition* (who, what, when, how often).
create table scheduled_jobs (
  id                     uuid primary key default gen_random_uuid(),
  user_id                uuid not null references app_users(id) on delete cascade,
  friend_id              uuid not null references friends(id) on delete cascade,
  recipient_igsid        text not null,
  recipient_display_name text not null,
  instruction            text not null,            -- "ask if they're free this weekend"
  style_preset           text,                     -- null = use the friend's profile
  language               text,
  first_run_at           timestamptz not null,
  timezone               text not null default 'Asia/Kuala_Lumpur',  -- IANA, validated in app code
  local_time             time not null,            -- wall-clock time in `timezone` (DST-safe recurrence)
  rrule                  text,                     -- RFC 5545 RRULE; null = one-time
  next_run_at            timestamptz,
  automation_enabled     boolean not null default true,
  status                 text not null default 'active'
                           check (status in ('active', 'paused', 'cancelled', 'completed')),
  created_via            text not null default 'ui' check (created_via in ('ui', 'chat')),
  created_at             timestamptz not null default now(),
  updated_at             timestamptz not null default now()
);
create index scheduled_jobs_due_idx on scheduled_jobs(next_run_at) where status = 'active';

-- A run is one occurrence of a job. Workers claim runs with
--   SELECT ... FOR UPDATE SKIP LOCKED
-- and hold a lease so a crashed worker's run is picked up again safely.
create table job_runs (
  id                 uuid primary key default gen_random_uuid(),
  job_id             uuid not null references scheduled_jobs(id) on delete cascade,
  scheduled_for      timestamptz not null,
  idempotency_key    text not null unique,         -- job_id + scheduled_for
  status             text not null default 'scheduled' check (status in (
                       'scheduled', 'running', 'needs_attention', 'sending',
                       'sent', 'blocked', 'failed', 'retry_scheduled', 'cancelled')),
  attempt            integer not null default 0,
  claimed_by         text,
  lease_expires_at   timestamptz,
  next_attempt_at    timestamptz,
  generated_text     text,
  eligibility        jsonb,                        -- snapshot of the deterministic checks
  blocked_reason     text,                         -- e.g. 'outside_standard_window', 'no_consent'
  failure_reason     text,
  outbound_message_id uuid,                        -- FK added below
  executed_at        timestamptz,
  created_at         timestamptz not null default now(),
  updated_at         timestamptz not null default now(),
  unique (job_id, scheduled_for)
);
create index job_runs_claimable_idx on job_runs(coalesce(next_attempt_at, scheduled_for))
  where status in ('scheduled', 'retry_scheduled');

-- =============================================================================
-- 6. Outbound delivery ledger, review queue, notifications, audit
-- =============================================================================

-- Every message ECHO tries to send goes through here (auto replies and
-- scheduled jobs). Unique keys prevent duplicates and reply loops.
create table outbound_messages (
  id                    uuid primary key default gen_random_uuid(),
  user_id               uuid not null references app_users(id) on delete cascade,
  friend_id             uuid not null references friends(id) on delete cascade,
  idempotency_key       text not null unique,
  reply_to_inbound_id   text unique,               -- at most ONE automated reply per inbound mid
  job_run_id            uuid references job_runs(id) on delete set null,
  content               text not null,
  status                text not null default 'pending'
                          check (status in ('pending', 'sending', 'sent', 'failed', 'unknown')),
  ig_message_id         text unique,
  attempt_count         integer not null default 0,
  last_error            text,
  created_at            timestamptz not null default now(),
  sent_at               timestamptz
);
create index outbound_unknown_idx on outbound_messages(created_at) where status = 'unknown';

alter table job_runs
  add constraint job_runs_outbound_fk foreign key (outbound_message_id) references outbound_messages(id) on delete set null;

-- Messages that paused for the user (money, promises, sensitive, unknown info...).
create table review_items (
  id                  uuid primary key default gen_random_uuid(),
  user_id             uuid not null references app_users(id) on delete cascade,
  friend_id           uuid references friends(id) on delete cascade,
  origin              text not null check (origin in ('auto_reply', 'scheduled')),
  inbound_message_id  text,
  job_run_id          uuid references job_runs(id) on delete cascade,
  reason_code         text not null,               -- 'money', 'commitment', 'sensitive', 'unknown_info', 'emotional', 'policy'
  draft_text          text,
  status              text not null default 'open'
                        check (status in ('open', 'sent_manually', 'dismissed', 'expired')),
  created_at          timestamptz not null default now(),
  resolved_at         timestamptz
);
create index review_items_open_idx on review_items(user_id, created_at desc) where status = 'open';

create table notification_outbox (
  id           uuid primary key default gen_random_uuid(),
  user_id      uuid not null references app_users(id) on delete cascade,
  category     text not null check (category in (
                 'message_sent', 'job_blocked', 'delivery_failed', 'needs_attention',
                 'automation_changed', 'connection_error', 'schedule_reminder')),
  title        text not null,
  body         text not null,
  payload      jsonb not null default '{}'::jsonb,
  status       text not null default 'pending' check (status in ('pending', 'sent', 'failed', 'suppressed')),
  attempts     integer not null default 0,
  created_at   timestamptz not null default now(),
  sent_at      timestamptz
);
create index notification_outbox_pending_idx on notification_outbox(created_at) where status = 'pending';

-- Append-only audit trail of every automated action.
create table audit_log (
  id            bigint generated always as identity primary key,
  user_id       uuid not null references app_users(id) on delete cascade,
  actor         text not null check (actor in ('user', 'echo', 'worker', 'system')),
  action        text not null,                     -- e.g. 'auto_reply.sent', 'job.blocked'
  entity_type   text,
  entity_id     text,
  details       jsonb not null default '{}'::jsonb,
  created_at    timestamptz not null default now()
);
create index audit_log_user_time_idx on audit_log(user_id, created_at desc);

-- =============================================================================
-- 7. Triggers & RLS
-- =============================================================================

create trigger app_users_updated_at before update on app_users
  for each row execute function set_updated_at();
create trigger notification_preferences_updated_at before update on notification_preferences
  for each row execute function set_updated_at();
create trigger profile_traits_updated_at before update on profile_traits
  for each row execute function set_updated_at();
create trigger instagram_accounts_updated_at before update on instagram_accounts
  for each row execute function set_updated_at();
create trigger friends_updated_at before update on friends
  for each row execute function set_updated_at();
create trigger friend_style_profiles_updated_at before update on friend_style_profiles
  for each row execute function set_updated_at();
create trigger scheduled_jobs_updated_at before update on scheduled_jobs
  for each row execute function set_updated_at();
create trigger job_runs_updated_at before update on job_runs
  for each row execute function set_updated_at();

-- Audit rows can't be edited. Deletes stay allowed so "delete my account"
-- (cascade from app_users) can still erase everything.
create or replace function forbid_audit_mutation() returns trigger
language plpgsql as $$
begin
  raise exception 'audit_log is append-only';
end;
$$;
create trigger audit_log_no_update before update on audit_log
  for each row execute function forbid_audit_mutation();

-- Deny-by-default for client roles; the backend uses the service role.
alter table app_users                enable row level security;
alter table user_devices             enable row level security;
alter table notification_preferences enable row level security;
alter table chat_messages            enable row level security;
alter table chat_summaries           enable row level security;
alter table profile_traits           enable row level security;
alter table writing_examples         enable row level security;
alter table instagram_accounts       enable row level security;
alter table friends                  enable row level security;
alter table friend_style_profiles    enable row level security;
alter table friend_memories          enable row level security;
alter table consent_events           enable row level security;
alter table ig_messages              enable row level security;
alter table webhook_events           enable row level security;
alter table scheduled_jobs           enable row level security;
alter table job_runs                 enable row level security;
alter table outbound_messages        enable row level security;
alter table review_items             enable row level security;
alter table notification_outbox      enable row level security;
alter table audit_log                enable row level security;
