-- =============================================================================
-- ECHO — Phase 4: real Instagram (Business Login for Instagram)
-- =============================================================================

-- OAuth "state" values (CSRF protection), single use, short-lived.
create table oauth_states (
  state        text primary key,
  user_id      uuid not null references app_users(id) on delete cascade,
  created_at   timestamptz not null default now(),
  expires_at   timestamptz not null,
  used_at      timestamptz
);

alter table instagram_accounts
  add column profile_name          text,
  add column token_refreshed_at    timestamptz,
  add column webhook_subscribed_at timestamptz,
  add column last_webhook_at       timestamptz,
  add column last_synced_at        timestamptz;

-- Profile details fetched with the User Profile API.
alter table friends add column profile_fetched_at timestamptz;

-- Deleted-by-sender messages keep the row (for ordering) but lose content.
alter table ig_messages add column is_deleted boolean not null default false;

alter table webhook_events
  add column entry_ig_id text,
  add column attempts    integer not null default 0;

-- Meta's data-deletion callback: you can look the status up by code.
create table data_deletion_requests (
  confirmation_code text primary key,
  ig_user_id        text not null,
  requested_at      timestamptz not null default now(),
  completed_at      timestamptz,
  status            text not null default 'pending' check (status in ('pending', 'completed'))
);

alter table oauth_states            enable row level security;
alter table data_deletion_requests  enable row level security;
