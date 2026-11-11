"""Test fixtures.

Database tests run against a real PostgreSQL. Point ECHO_TEST_DATABASE_URL
at an *empty, disposable* database you own (it is wiped), e.g.

    createdb echo_test
    export ECHO_TEST_DATABASE_URL=postgresql://localhost/echo_test

Without it, database tests are skipped and the pure unit tests still run.
"""

from __future__ import annotations

import os
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings

MIGRATIONS = sorted((Path(__file__).resolve().parents[1] / "supabase" / "migrations").glob("*.sql"))
TEST_DB_URL = os.environ.get("ECHO_TEST_DATABASE_URL")
TOKEN = "t0ken"
AUTH = {"Authorization": f"Bearer {TOKEN}"}

requires_db = pytest.mark.skipif(not TEST_DB_URL, reason="ECHO_TEST_DATABASE_URL not set")


def _reset_schema(url: str) -> None:
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute("drop schema if exists public cascade")
        conn.execute("create schema public")
        for migration in MIGRATIONS:
            conn.execute(migration.read_text())


@pytest.fixture(scope="session")
def migrated_db() -> str:
    assert TEST_DB_URL
    _reset_schema(TEST_DB_URL)
    return TEST_DB_URL


@pytest.fixture
def db_url(migrated_db: str) -> str:
    with psycopg.connect(migrated_db, autocommit=True) as conn:
        tables = conn.execute(
            "select string_agg(quote_ident(tablename), ', ') from pg_tables where schemaname = 'public'"
        ).fetchone()[0]
        conn.execute(f"truncate {tables} cascade")
    return migrated_db


def make_settings(**overrides) -> Settings:
    values = dict(
        echo_api_token=TOKEN,
        meta_app_secret="test-app-secret",
        meta_webhook_verify_token="verify-me",
        summary_trigger_messages=6,
        summary_keep_recent=2,
        _env_file=None,
    )
    values.update(overrides)
    return Settings(**values)


@pytest.fixture
def make_client():
    """Factory: make_client(provider=..., database_url=...) -> TestClient (lifespan running)."""
    from app.main import create_app

    clients: list[TestClient] = []

    def _make(provider=None, messaging=None, run_worker=False, instagram_service=None,
              **settings_overrides) -> TestClient:
        app = create_app(settings=make_settings(**settings_overrides), provider=provider,
                         messaging=messaging, run_worker=run_worker, instagram_service=instagram_service)
        client = TestClient(app)
        client.__enter__()
        clients.append(client)
        return client

    yield _make
    for client in clients:
        client.__exit__(None, None, None)
