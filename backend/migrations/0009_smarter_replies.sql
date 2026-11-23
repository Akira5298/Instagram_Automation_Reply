-- =============================================================================
-- ECHO — smarter replies: suggestions, reply mode/length, style learning,
-- personal context (location + calendar), reactions, relationship presets,
-- exact-text scheduled messages.
-- =============================================================================

-- Account-wide reply settings.
alter table app_users
  add column auto_reply_mode text not null default 'auto'
    check (auto_reply_mode in ('auto', 'review')),            -- review = every reply waits in "Needs you"
  add column reply_length text not null default 'adaptive'
    check (reply_length in ('short', 'medium', 'long', 'adaptive')),
  add column learn_from_chats boolean not null default true;  -- study the replies you write yourself

-- Tap-to-send options shown with a held draft.
alter table review_items add column suggestions jsonb not null default '[]'::jsonb;

-- What ECHO learned about how you write to this friend (separate from the
-- rules you set; your rules always win).
alter table friend_style_profiles
  add column learned jsonb,
  add column learned_at timestamptz,
  add column learned_message_count integer not null default 0;
alter table friends add column style_learned_through timestamptz;  -- newest of your messages already studied

-- Auto replies can be a ❤️ reaction instead of a text.
alter table auto_replies
  add column reply_kind text not null default 'text' check (reply_kind in ('text', 'reaction'));

-- New relationship presets.
alter table friends drop constraint friends_relationship_preset_check;
alter table friends add constraint friends_relationship_preset_check
  check (relationship_preset in ('close_friend', 'partner', 'family', 'classmate', 'professional', 'custom'));

-- A scheduled message can be exact text you picked (from AI suggestions or typed);
-- otherwise ECHO writes it from the instruction at send time, as before.
alter table scheduled_jobs add column message_text text check (char_length(message_text) <= 1000);

-- Your current context, uploaded by the app only when you allow it.
-- Coarse place names, never coordinates; calendar only for the next ~36 hours.
create table user_context (
  user_id              uuid primary key references app_users(id) on delete cascade,
  locality             text,
  region               text,
  country              text,
  location_updated_at  timestamptz,
  events               jsonb,                -- [{start, end, all_day, busy, title?}]
  calendar_updated_at  timestamptz,
  updated_at           timestamptz not null default now()
);
alter table user_context enable row level security;
