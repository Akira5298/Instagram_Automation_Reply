from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

from fastapi import Request
from psycopg import AsyncConnection
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool


def create_pool(database_url: str, min_size: int = 1, max_size: int = 5) -> AsyncConnectionPool:
    """Async connection pool.

    `prepare_threshold=None` disables server-side prepared statements so the
    pool also works behind connection poolers such as pgbouncer.
    """
    return AsyncConnectionPool(
        conninfo=database_url,
        min_size=min_size,
        max_size=max_size,
        open=False,
        kwargs={"row_factory": dict_row, "prepare_threshold": None, "autocommit": False},
    )


async def ensure_owner(pool: AsyncConnectionPool, user_id: UUID, timezone: str) -> None:
    async with pool.connection() as conn:
        await conn.execute(
            """
            insert into app_users (id, default_timezone) values (%s, %s)
            on conflict (id) do nothing
            """,
            (user_id, timezone),
        )
        await conn.execute(
            "insert into notification_preferences (user_id) values (%s) on conflict do nothing",
            (user_id,),
        )


def get_pool(request: Request) -> AsyncConnectionPool:
    return request.app.state.pool


async def get_conn(request: Request) -> AsyncIterator[AsyncConnection]:
    """FastAPI dependency: one connection per request, committed on success."""
    async with request.app.state.pool.connection() as conn:
        yield conn
