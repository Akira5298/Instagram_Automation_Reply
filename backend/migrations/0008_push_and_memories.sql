-- =============================================================================
-- ECHO Phase 6 — push notifications, quiet hours, friend memories
-- =============================================================================

-- iPhones that receive push notifications (APNs device tokens).
create table devices (
  id            uuid primary key default gen_random_uuid(),
  user_id       uuid not null references app_users(id) on delete cascade,
  token         text not null unique,                 -- hex APNs device token
  environment   text not null check (environment in ('sandbox', 'production')),
  app_version   text,
  created_at    timestamptz not null default now(),
  last_seen_at  timestamptz not null default now(),
  disabled_at   timestamptz,                          -- APNs said the token is gone (410 / BadDeviceToken)
  last_error    text
);
create index devices_user_idx on devices (user_id) where disabled_at is null;
alter table devices enable row level security;

-- Quiet hours already live in notification_preferences (start/end, local time
-- in app_users.default_timezone). Push is always silenced during them; this
-- optionally holds automatic replies too.
alter table notification_preferences
  add column quiet_hours_hold_replies boolean not null default false;

-- Memories you can edit in the app.
alter table friend_memories add column updated_at timestamptz not null default now();
create trigger friend_memories_updated_at before update on friend_memories
  for each row execute function set_updated_at();
