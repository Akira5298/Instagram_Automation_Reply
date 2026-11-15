-- =============================================================================
-- ECHO Phase 5 — autonomous replies to inbound DMs
-- =============================================================================
-- One auto_replies row per reply ECHO *might* send. A burst of messages from a
-- friend collapses into a single pending reply (the newest message supersedes
-- the pending one), so ECHO answers the whole burst once.
--
--   pending ──claim──▶ running ──gates pass──▶ sending ──▶ sent
--      │                  ├─ newer message ──▶ superseded
--      │                  ├─ you replied / AI off / no reply needed ──▶ skipped
--      │                  ├─ ineligible (window, consent, limits) ──▶ blocked
--      │                  ├─ safety flag / review-first ──▶ needs_attention ─approve─▶ pending
--      │                  └─ error ──▶ pending (retry) … ──▶ failed
--      └── you take over ──▶ cancelled

create table auto_replies (
  id                   uuid primary key default gen_random_uuid(),
  user_id              uuid not null references app_users(id) on delete cascade,
  friend_id            uuid not null references friends(id) on delete cascade,
  inbound_message_id   text not null unique,       -- newest inbound mid this reply answers
  inbound_at           timestamptz not null,
  status               text not null default 'pending'
                         check (status in ('pending', 'running', 'sending', 'sent', 'skipped', 'superseded',
                                           'blocked', 'needs_attention', 'failed', 'cancelled')),
  due_at               timestamptz not null,       -- inbound time + the friend's reply delay
  attempt              integer not null default 0,
  next_attempt_at      timestamptz,
  claimed_by           text,
  lease_expires_at     timestamptz,
  generated_text       text,
  draft_language       text,
  safety_flags         text[] not null default '{}',
  model_note           text,
  eligibility          jsonb,
  reason               text,                       -- why it was skipped / blocked / failed
  approved_by_user     boolean not null default false,
  outbound_message_id  uuid references outbound_messages(id) on delete set null,
  created_at           timestamptz not null default now(),
  executed_at          timestamptz
);

-- At most one *waiting* reply per friend (bursts collapse into it).
create unique index auto_replies_one_pending_per_friend on auto_replies (friend_id) where status = 'pending';
create index auto_replies_due_idx on auto_replies (coalesce(next_attempt_at, due_at)) where status = 'pending';
create index auto_replies_lease_idx on auto_replies (lease_expires_at) where status in ('running', 'sending');
create index auto_replies_friend_time_idx on auto_replies (friend_id, created_at desc);
alter table auto_replies enable row level security;

-- Per-friend reply controls.
alter table friends
  add column auto_reply_delay_seconds integer not null default 45
    check (auto_reply_delay_seconds between 0 and 3600),
  add column auto_reply_require_review boolean not null default false,   -- every reply waits for your OK
  add column auto_reply_daily_limit integer not null default 20
    check (auto_reply_daily_limit between 1 and 200);

-- Account-wide settings.
alter table app_users
  add column takeover_minutes integer not null default 30
    check (takeover_minutes between 0 and 1440),          -- after you reply yourself, ECHO stays quiet this long
  add column auto_reply_daily_limit integer not null default 100
    check (auto_reply_daily_limit between 1 and 1000),
  add column consent_request_text text not null default
    'Hey! I''m trying an AI assistant that can help me reply to messages. Is it okay if it sometimes replies for me? Reply YES to allow, or STOP anytime to turn it off.';

-- Review items can now point at an auto reply.
alter table review_items add column auto_reply_id uuid references auto_replies(id) on delete cascade;
create index review_items_auto_reply_idx on review_items (auto_reply_id) where auto_reply_id is not null;

-- "approved" = you sent ECHO's draft (possibly edited) from the review queue.
alter table review_items drop constraint review_items_status_check;
alter table review_items add constraint review_items_status_check
  check (status in ('open', 'approved', 'sent_manually', 'dismissed', 'expired'));
