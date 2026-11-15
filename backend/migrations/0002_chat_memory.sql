-- =============================================================================
-- ECHO — Phase 2: chat persistence, AI usage tracking, memory bookkeeping
-- =============================================================================

-- One assistant reply per user message (makes retries idempotent) and the
-- memory updates that reply produced, so the app can show "Learned 2 things"
-- after a reinstall.
alter table chat_messages
  add column in_reply_to       uuid references chat_messages(id) on delete cascade,
  add column memory_updates    jsonb not null default '[]'::jsonb,
  add column model             text,
  add column input_tokens      integer,
  add column output_tokens     integer,
  add column cache_read_tokens integer,
  add constraint chat_messages_one_reply unique (in_reply_to);

alter table chat_summaries
  add column message_count  integer not null default 0,
  add column model          text,
  add column input_tokens   integer,
  add column output_tokens  integer;

-- Deleted traits keep a tombstone (status 'rejected', empty value) so they are
-- never re-learned automatically.
create index profile_traits_user_active_idx
  on profile_traits(user_id, last_seen_at desc)
  where status in ('active', 'confirmed');
