"""Local-first setup: migration runner, public URL resolution."""

from __future__ import annotations

import os

import psycopg
import pytest

from app.core.public_url import meta_dashboard_urls, oauth_redirect_uri, public_base_url
from app.migrate import migrate, migration_files, pending, status
from tests.conftest import make_settings

MIG_DB = os.environ.get("ECHO_TEST_MIGRATIONS_DATABASE_URL")


def test_public_url_prefers_env_then_file(tmp_path):
    url_file = tmp_path / ".public_url"
    s = make_settings(echo_public_url_file=str(url_file))
    assert public_base_url(s) is None and oauth_redirect_uri(s) is None

    url_file.write_text("https://rivers-catalog-pending-smile.trycloudflare.com/\n")
    assert public_base_url(s) == "https://rivers-catalog-pending-smile.trycloudflare.com"
    assert oauth_redirect_uri(s).endswith(".trycloudflare.com/v1/instagram/oauth/callback")

    url_file.write_text("http://insecure.example.com")
    assert public_base_url(s) is None, "Meta requires https"

    named = make_settings(echo_public_url_file=str(url_file), echo_public_base_url="https://echo.example.com")
    assert public_base_url(named) == "https://echo.example.com"
    assert meta_dashboard_urls("https://echo.example.com")["Webhook callback URL"] == "https://echo.example.com/webhooks/meta"


@pytest.mark.skipif(not MIG_DB, reason="ECHO_TEST_MIGRATIONS_DATABASE_URL not set")
def test_migrate_fresh_then_idempotent():
    with psycopg.connect(MIG_DB, autocommit=True) as conn:
        conn.execute("drop schema if exists public cascade; create schema public")
    applied = migrate(MIG_DB)
    assert applied == [f.name for f in migration_files()]
    assert migrate(MIG_DB) == [] and pending(MIG_DB) == []


@pytest.mark.skipif(not MIG_DB, reason="ECHO_TEST_MIGRATIONS_DATABASE_URL not set")
def test_migrate_detects_migrations_applied_by_hand():
    # Simulates Phases 2–3: 0001–0003 applied with psql, no schema_migrations table.
    with psycopg.connect(MIG_DB, autocommit=True) as conn:
        conn.execute("drop schema if exists public cascade; create schema public")
        for path in migration_files()[:3]:
            conn.execute(path.read_text())
    changed = migrate(MIG_DB)
    assert len(changed) == len(migration_files())
    hows = dict(status(MIG_DB))
    assert hows["0001_initial_schema.sql"] == "detected" and hows["0003_scheduler.sql"] == "detected"
    assert hows["0004_instagram.sql"] == "applied" and hows["0005_local_dev.sql"] == "applied"
