"""Instagram account, friends, consent and message history."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

FRIEND_COLUMNS = """
    f.id, f.igsid, f.display_name, coalesce(f.username, '') as username,
    f.relationship_preset as style, coalesce(f.language, '') as language,
    f.ai_reply_enabled, f.consent_status as consent,
    f.last_inbound_message_at as last_inbound_at, f.manual_override_until,
    f.instagram_account_id, f.disclosure_sent_at
"""


ACCOUNT_COLUMNS = """
    id, user_id, ig_user_id, username, account_type, provider, status, token_expires_at,
    webhook_subscribed_at, last_webhook_at, last_synced_at, last_error, connected_at
"""


async def get_account(conn: AsyncConnection, user_id: UUID) -> dict[str, Any] | None:
    """The account messages go out from: the active one, else the latest."""
    cur = await conn.execute(
        f"""
        select {ACCOUNT_COLUMNS} from instagram_accounts
        where user_id = %s order by (status = 'active') desc, connected_at desc limit 1
        """,
        (user_id,),
    )
    return await cur.fetchone()


async def get_active_account(conn: AsyncConnection, user_id: UUID) -> dict[str, Any] | None:
    return await get_account(conn, user_id)


async def account_by_ig_id(conn: AsyncConnection, ig_user_id: str) -> dict[str, Any] | None:
    cur = await conn.execute(
        f"select {ACCOUNT_COLUMNS} from instagram_accounts where ig_user_id = %s", (ig_user_id,)
    )
    return await cur.fetchone()


async def ensure_mock_account(conn: AsyncConnection, user_id: UUID) -> dict[str, Any]:
    cur = await conn.execute(
        """
        insert into instagram_accounts (user_id, ig_user_id, username, account_type, provider, status)
        values (%s, %s, 'echo_mock', 'MEDIA_CREATOR', 'mock', 'active')
        on conflict (ig_user_id) do update set status = 'active'
        returning id, user_id, ig_user_id, username, provider, status
        """,
        (user_id, f"mock-{user_id}"),
    )
    row = await cur.fetchone()
    assert row is not None
    return row


async def list_friends(conn: AsyncConnection, user_id: UUID) -> list[dict[str, Any]]:
    """Friends on your currently active Instagram account (real or mock)."""
    cur = await conn.execute(
        f"""
        select {FRIEND_COLUMNS} from friends f
        join instagram_accounts a on a.id = f.instagram_account_id and a.status = 'active'
        where f.user_id = %s
        order by f.last_inbound_message_at desc nulls last, f.display_name
        """,
        (user_id,),
    )
    return await cur.fetchall()


async def get_friend(conn: AsyncConnection, user_id: UUID, friend_id: UUID) -> dict[str, Any] | None:
    cur = await conn.execute(
        f"select {FRIEND_COLUMNS} from friends f where f.user_id = %s and f.id = %s", (user_id, friend_id)
    )
    return await cur.fetchone()


async def upsert_friend(
    conn: AsyncConnection, *, user_id: UUID, account_id: UUID, igsid: str, display_name: str,
    username: str | None, style: str, language: str | None, ai_reply_enabled: bool, consent: str,
) -> dict[str, Any]:
    cur = await conn.execute(
        """
        insert into friends (user_id, instagram_account_id, igsid, display_name, username,
                             relationship_preset, language, ai_reply_enabled, consent_status)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (instagram_account_id, igsid) do update set display_name = excluded.display_name
        returning id
        """,
        (user_id, account_id, igsid, display_name, username, style, language, ai_reply_enabled, consent),
    )
    row = await cur.fetchone()
    assert row is not None
    friend = await get_friend(conn, user_id, row["id"])
    assert friend is not None
    return friend


async def update_friend(conn: AsyncConnection, user_id: UUID, friend_id: UUID, changes: dict[str, Any]) -> None:
    columns = {
        "ai_reply_enabled": "ai_reply_enabled",
        "style": "relationship_preset",
        "language": "language",
        "consent": "consent_status",
        "manual_override_until": "manual_override_until",
    }
    sets = [f"{columns[k]} = %s" for k in changes if k in columns]
    if not sets:
        return
    values = [changes[k] for k in changes if k in columns]
    await conn.execute(
        f"update friends set {', '.join(sets)} where id = %s and user_id = %s",
        (*values, friend_id, user_id),
    )


async def record_consent(
    conn: AsyncConnection, friend_id: UUID, status: str, method: str, evidence: str | None
) -> None:
    await conn.execute(
        "insert into consent_events (friend_id, status, method, evidence) values (%s, %s, %s, %s)",
        (friend_id, status, method, evidence),
    )


async def record_inbound(
    conn: AsyncConnection, *, user_id: UUID, friend_id: UUID, text: str, mid: str, at: datetime
) -> None:
    await conn.execute(
        """
        insert into ig_messages (user_id, friend_id, direction, ig_message_id, content, sent_by, platform_time)
        values (%s, %s, 'inbound', %s, %s, 'friend', %s)
        on conflict (ig_message_id) do nothing
        """,
        (user_id, friend_id, mid, text, at),
    )
    await conn.execute(
        """
        update friends set last_inbound_message_at = greatest(coalesce(last_inbound_message_at, %s), %s),
                           last_inbound_message_id = %s
        where id = %s
        """,
        (at, at, mid, friend_id),
    )


async def record_outbound(
    conn: AsyncConnection, *, user_id: UUID, friend_id: UUID, text: str, mid: str, sent_by: str, at: datetime
) -> None:
    await conn.execute(
        """
        insert into ig_messages (user_id, friend_id, direction, ig_message_id, content, sent_by, platform_time)
        values (%s, %s, 'outbound', %s, %s, %s, %s)
        on conflict (ig_message_id) do update set sent_by = excluded.sent_by
            where ig_messages.sent_by = 'user_manual' and excluded.sent_by in ('echo_auto', 'echo_scheduled')
        """,
        (user_id, friend_id, mid, text, sent_by, at),
    )


async def recent_thread(conn: AsyncConnection, friend_id: UUID, limit: int = 12) -> list[dict[str, Any]]:
    cur = await conn.execute(
        """
        select id, direction, sent_by, content, platform_time from ig_messages
        where friend_id = %s and content is not null
        order by platform_time desc limit %s
        """,
        (friend_id, limit),
    )
    rows = await cur.fetchall()
    rows.reverse()
    return rows


async def conversations(conn: AsyncConnection, user_id: UUID, per_friend: int = 30) -> list[dict[str, Any]]:
    friends = await list_friends(conn, user_id)
    result = []
    for friend in friends:
        thread = await recent_thread(conn, friend["id"], per_friend)
        result.append({"friend": friend, "messages": thread})
    return result


SENDER_FOR_APP = {
    "friend": "friend",
    "user_manual": "me",
    "echo_auto": "echo_auto",
    "echo_scheduled": "echo_scheduled",
}


async def mark_deleted(conn: AsyncConnection, mid: str) -> None:
    await conn.execute("update ig_messages set content = null, is_deleted = true where ig_message_id = %s", (mid,))


async def message_exists(conn: AsyncConnection, mid: str) -> bool:
    cur = await conn.execute("select 1 from ig_messages where ig_message_id = %s", (mid,))
    return await cur.fetchone() is not None


async def purge_old_content(conn: AsyncConnection, older_than) -> int:
    """Minimise retention: drop message text past the retention period."""
    cur = await conn.execute(
        "update ig_messages set content = null where content is not null and platform_time < %s", (older_than,)
    )
    return cur.rowcount
