"""Assistant-chat persistence (chat_messages, chat_summaries)."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

_MSG_COLUMNS = "id, role, content, language, client_msg_id, in_reply_to, memory_updates, actions, created_at"


async def insert_user_message(
    conn: AsyncConnection, user_id: UUID, client_msg_id: UUID, text: str
) -> tuple[dict[str, Any], bool]:
    """Insert the user's message. Returns (row, created). On a retry with the
    same client_msg_id the existing row is returned with created=False."""
    cur = await conn.execute(
        f"""
        insert into chat_messages (user_id, role, content, client_msg_id)
        values (%s, 'user', %s, %s)
        on conflict (user_id, client_msg_id) do nothing
        returning {_MSG_COLUMNS}
        """,
        (user_id, text, client_msg_id),
    )
    row = await cur.fetchone()
    if row is not None:
        return row, True
    cur = await conn.execute(
        f"select {_MSG_COLUMNS} from chat_messages where user_id = %s and client_msg_id = %s",
        (user_id, client_msg_id),
    )
    existing = await cur.fetchone()
    assert existing is not None
    return existing, False


async def reply_to(conn: AsyncConnection, user_message_id: UUID) -> dict[str, Any] | None:
    cur = await conn.execute(
        f"select {_MSG_COLUMNS} from chat_messages where in_reply_to = %s", (user_message_id,)
    )
    return await cur.fetchone()


async def insert_assistant_message(
    conn: AsyncConnection,
    *,
    user_id: UUID,
    in_reply_to: UUID,
    text: str,
    language: str | None,
    memory_updates: list[dict],
    actions: list[dict] | None = None,
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
    cache_read_tokens: int | None,
) -> tuple[dict[str, Any], bool]:
    """Returns (row, created). A concurrent duplicate request loses the race
    on the unique in_reply_to and gets the winner's reply back."""
    cur = await conn.execute(
        f"""
        insert into chat_messages (user_id, role, content, language, in_reply_to, memory_updates, actions,
                                   model, input_tokens, output_tokens, cache_read_tokens)
        values (%s, 'assistant', %s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (in_reply_to) do nothing
        returning {_MSG_COLUMNS}
        """,
        (user_id, text, language, in_reply_to, Jsonb(memory_updates), Jsonb(actions or []), model,
         input_tokens, output_tokens, cache_read_tokens),
    )
    row = await cur.fetchone()
    if row is not None:
        return row, True
    existing = await reply_to(conn, in_reply_to)
    assert existing is not None
    return existing, False


async def set_user_message_language(conn: AsyncConnection, message_id: UUID, language: str | None) -> None:
    await conn.execute("update chat_messages set language = %s where id = %s", (language, message_id))


async def list_messages(
    conn: AsyncConnection, user_id: UUID, *, limit: int, before: datetime | None = None
) -> list[dict[str, Any]]:
    """Newest-first page, returned oldest-first for display."""
    cur = await conn.execute(
        f"""
        select {_MSG_COLUMNS} from chat_messages
        where user_id = %s and (%s::timestamptz is null or created_at < %s)
        order by created_at desc, id desc
        limit %s
        """,
        (user_id, before, before, limit),
    )
    rows = await cur.fetchall()
    rows.reverse()
    return rows


async def recent_for_context(
    conn: AsyncConnection, user_id: UUID, *, limit: int, exclude_id: UUID | None = None
) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        select role, content, language, created_at from chat_messages
        where user_id = %s and (%s::uuid is null or id <> %s)
        order by created_at desc, id desc
        limit %s
        """,
        (user_id, exclude_id, exclude_id, limit),
    )
    rows = await cur.fetchall()
    rows.reverse()
    return rows


async def latest_summary(conn: AsyncConnection, user_id: UUID) -> dict[str, Any] | None:
    cur = await conn.execute(
        """
        select id, summary, covers_until, message_count from chat_summaries
        where user_id = %s order by covers_until desc limit 1
        """,
        (user_id,),
    )
    return await cur.fetchone()


async def messages_after(
    conn: AsyncConnection, user_id: UUID, after: datetime | None
) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        select id, role, content, created_at from chat_messages
        where user_id = %s and (%s::timestamptz is null or created_at > %s)
        order by created_at asc, id asc
        """,
        (user_id, after, after),
    )
    return await cur.fetchall()


async def insert_summary(
    conn: AsyncConnection,
    *,
    user_id: UUID,
    summary: str,
    covers_until: datetime,
    message_count: int,
    model: str | None,
    input_tokens: int | None,
    output_tokens: int | None,
) -> None:
    await conn.execute(
        """
        insert into chat_summaries (user_id, summary, covers_until, message_count, model,
                                    input_tokens, output_tokens)
        values (%s, %s, %s, %s, %s, %s, %s)
        """,
        (user_id, summary, covers_until, message_count, model, input_tokens, output_tokens),
    )


async def list_summaries(conn: AsyncConnection, user_id: UUID) -> list[dict[str, Any]]:
    cur = await conn.execute(
        "select summary, covers_until, created_at from chat_summaries where user_id = %s order by covers_until",
        (user_id,),
    )
    return await cur.fetchall()


async def clear_history(conn: AsyncConnection, user_id: UUID) -> None:
    await conn.execute("delete from chat_messages where user_id = %s", (user_id,))
    await conn.execute("delete from chat_summaries where user_id = %s", (user_id,))


async def try_lock_user(conn: AsyncConnection, user_id: UUID, purpose: str) -> bool:
    """Transaction-scoped advisory lock so only one summariser runs per user."""
    cur = await conn.execute(
        "select pg_try_advisory_xact_lock(hashtextextended(%s, 0)) as ok", (f"{purpose}:{user_id}",)
    )
    row = await cur.fetchone()
    return bool(row and row["ok"])
