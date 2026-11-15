"""Apply database migrations to your local PostgreSQL.

    python -m app.migrate            # apply anything pending
    python -m app.migrate --status   # show what's applied

Applied versions are recorded in `schema_migrations`. If you applied some
migrations by hand earlier (psql -f …), they are detected from the objects
they created and recorded without being re-run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import psycopg

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

# Something each migration creates, used to recognise ones applied by hand.
SENTINELS = {
    "0001_initial_schema.sql": "select to_regclass('public.app_users') is not null",
    "0002_chat_memory.sql": "select exists (select 1 from information_schema.columns "
                            "where table_name = 'chat_messages' and column_name = 'in_reply_to')",
    "0003_scheduler.sql": "select exists (select 1 from information_schema.columns "
                          "where table_name = 'instagram_accounts' and column_name = 'provider')",
    "0004_instagram.sql": "select to_regclass('public.oauth_states') is not null",
    "0005_local_dev.sql": "select exists (select 1 from information_schema.columns "
                          "where table_name = 'oauth_states' and column_name = 'redirect_uri')",
}


def migration_files() -> list[Path]:
    return sorted(MIGRATIONS_DIR.glob("*.sql"))


def _ensure_table(conn: psycopg.Connection) -> None:
    conn.execute(
        """
        create table if not exists schema_migrations (
            version     text primary key,
            applied_at  timestamptz not null default now(),
            how         text not null default 'applied'
        )
        """
    )


def status(url: str) -> list[tuple[str, str | None]]:
    with psycopg.connect(url, autocommit=True) as conn:
        _ensure_table(conn)
        done = dict(conn.execute("select version, how from schema_migrations").fetchall())
    return [(f.name, done.get(f.name)) for f in migration_files()]


def pending(url: str) -> list[str]:
    return [name for name, how in status(url) if how is None]


def migrate(url: str, *, verbose: bool = False) -> list[str]:
    """Apply pending migrations, each in its own transaction. Returns the
    names applied (or detected)."""
    changed: list[str] = []
    with psycopg.connect(url, autocommit=True) as conn:
        _ensure_table(conn)
        done = {r[0] for r in conn.execute("select version from schema_migrations").fetchall()}
        for path in migration_files():
            name = path.name
            if name in done:
                continue
            sentinel = SENTINELS.get(name)
            if sentinel and conn.execute(sentinel).fetchone()[0]:
                conn.execute("insert into schema_migrations (version, how) values (%s, 'detected')", (name,))
                changed.append(name)
                if verbose:
                    print(f"  ✓ {name} (already applied earlier — recorded)")
                continue
            with conn.transaction():
                conn.execute(path.read_text())
                conn.execute("insert into schema_migrations (version, how) values (%s, 'applied')", (name,))
            changed.append(name)
            if verbose:
                print(f"  ✓ {name} applied")
    return changed


def main() -> None:
    from app.core.config import get_settings

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--status", action="store_true", help="show applied/pending migrations")
    args = parser.parse_args()

    settings = get_settings()
    if settings.database_url is None:
        sys.exit("DATABASE_URL is not set (backend/.env).")
    url = settings.database_url.get_secret_value()
    try:
        if args.status:
            for name, how in status(url):
                print(f"  {'✓' if how else '·'} {name}{f' ({how})' if how else ' — pending'}")
            return
        changed = migrate(url, verbose=True)
        print("Database is up to date." if not changed else f"Done: {len(changed)} migration(s).")
    except psycopg.OperationalError as exc:
        sys.exit(f"Can't connect to PostgreSQL: {exc}\nIs it running?  brew services start postgresql@16")


if __name__ == "__main__":
    main()
