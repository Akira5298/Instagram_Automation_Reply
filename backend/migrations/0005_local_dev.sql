-- =============================================================================
-- ECHO — local-first setup (Mac + Cloudflare Tunnel)
-- =============================================================================

-- The public address can change between runs (quick tunnels), so each login
-- remembers the exact redirect URI it was started with; Instagram requires the
-- same value when the code is exchanged.
alter table oauth_states add column redirect_uri text;
