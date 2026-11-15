-- =============================================================================
-- ECHO — Instagram API with Unified Login
-- =============================================================================
-- New Meta apps only offer Unified Login (Facebook Login for Business with a
-- configuration). Each connected account remembers which login it used, so
-- the server calls the right host (graph.facebook.com vs graph.instagram.com)
-- and never tries to refresh a token that can't be refreshed.

alter table instagram_accounts
  add column login_mode   text not null default 'instagram'
                          check (login_mode in ('instagram', 'unified')),
  -- The Meta user behind the token (`/me` id): a business system user for
  -- system-user configurations. Used to match deauthorize / data-deletion calls.
  add column meta_user_id text;

create index instagram_accounts_meta_user_id_idx on instagram_accounts (meta_user_id);
