"""Notification outbox.

Every event worth telling you about becomes a row in notification_outbox,
filtered by your preferences (suppressed rows are kept for the activity feed
but never pushed). A sender delivers pending rows. Phase 3 ships a logging
sender; Phase 6 adds APNs behind the same interface.
"""

from __future__ import annotations

import logging
from typing import Protocol
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

log = logging.getLogger("echo.notify")

_PREF_COLUMN = {
    "message_sent": "message_sent",
    "job_blocked": "job_blocked",
    "delivery_failed": "delivery_failed",
    "needs_attention": "needs_attention",
    "automation_changed": "automation_changed",
    "connection_error": "connection_error",
    "schedule_reminder": "schedule_reminder",
}


async def notify(
    conn: AsyncConnection,
    user_id: UUID,
    category: str,
    title: str,
    body: str,
    payload: dict | None = None,
) -> None:
    column = _PREF_COLUMN[category]
    cur = await conn.execute(
        f"select {column} as enabled from notification_preferences where user_id = %s", (user_id,)
    )
    row = await cur.fetchone()
    enabled = True if row is None else bool(row["enabled"])
    await conn.execute(
        """
        insert into notification_outbox (user_id, category, title, body, payload, status)
        values (%s, %s, %s, %s, %s, %s)
        """,
        (user_id, category, title[:120], body[:500], Jsonb(payload or {}), "pending" if enabled else "suppressed"),
    )


class NotificationSender(Protocol):
    async def send(self, user_id: UUID, category: str, title: str, body: str, payload: dict) -> None: ...


class LogNotificationSender:
    """Phase 3: logs instead of pushing. Swap for APNs in Phase 6."""

    async def send(self, user_id: UUID, category: str, title: str, body: str, payload: dict) -> None:
        log.info("notification", extra={"fields": {"category": category, "title": title, "body": body}})


async def dispatch_pending(conn: AsyncConnection, sender: NotificationSender, limit: int = 50) -> int:
    cur = await conn.execute(
        """
        select id, user_id, category, title, body, payload from notification_outbox
        where status = 'pending' order by created_at limit %s
        for update skip locked
        """,
        (limit,),
    )
    rows = await cur.fetchall()
    for row in rows:
        try:
            await sender.send(row["user_id"], row["category"], row["title"], row["body"], row["payload"])
            await conn.execute(
                "update notification_outbox set status = 'sent', sent_at = now(), attempts = attempts + 1 where id = %s",
                (row["id"],),
            )
        except Exception:
            log.exception("notification send failed")
            await conn.execute(
                """
                update notification_outbox set attempts = attempts + 1,
                    status = case when attempts + 1 >= 5 then 'failed' else 'pending' end
                where id = %s
                """,
                (row["id"],),
            )
    return len(rows)
