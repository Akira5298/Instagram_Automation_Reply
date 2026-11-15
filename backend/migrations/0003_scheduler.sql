-- =============================================================================
-- ECHO — Phase 3: scheduler execution, mock messaging, activity feed
-- =============================================================================

-- Which transport an account uses. 'mock' lets the whole pipeline run
-- end to end before Meta access exists (Phase 4 adds real 'instagram').
alter table instagram_accounts
  add column provider text not null default 'instagram'
    check (provider in ('instagram', 'mock'));

-- Denormalised "last result" so the app can list jobs in one query.
alter table scheduled_jobs
  add column last_run_at      timestamptz,
  add column last_run_status  text,
  add column run_count        integer not null default 0;

alter table job_runs
  add column draft_language  text,
  add column safety_flags    text[] not null default '{}';

create index job_runs_job_time_idx on job_runs(job_id, scheduled_for desc);
create index job_runs_lease_idx on job_runs(lease_expires_at) where status in ('running', 'sending');

-- In-app activity feed reads the outbox; read_at tracks what you've seen.
alter table notification_outbox add column read_at timestamptz;
create index notification_outbox_user_time_idx on notification_outbox(user_id, created_at desc);

-- Actions the assistant took during a chat turn (e.g. a job it scheduled),
-- rendered as cards under the reply.
alter table chat_messages add column actions jsonb not null default '[]'::jsonb;

-- You approved a draft that the safety gate paused ("Send anyway"): the
-- worker skips the safety gate for this run but still checks eligibility.
alter table job_runs add column approved_by_user boolean not null default false;
