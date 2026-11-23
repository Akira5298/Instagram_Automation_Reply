"""Notification outbox.

Every event worth telling you about becomes a row in notification_outbox,
filtered by your preferences (suppressed rows are kept for the activity feed
but never pushed). A sender delivers pending rows: APNs push (app/services/push.py) when
configured, otherwise a logging sender.

Quiet hours (notification_preferences.quiet_hours_start/end, in your
timezone) silence push: rows are stored as "suppressed" and still show in the
activity feed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, time
from typing import Protocol
from uuid import UUID
from zoneinfo import ZoneInfo

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
    now: datetime | None = None,
) -> None:
    column = _PREF_COLUMN[category]
    cur = await conn.execute(
        f"""
        select p.{column} as enabled, p.quiet_hours_start, p.quiet_hours_end, u.default_timezone
        from app_users u left join notification_preferences p on p.user_id = u.id
        where u.id = %s
        """,
        (user_id,),
    )
    row = await cur.fetchone()
    enabled = True if row is None or row["enabled"] is None else bool(row["enabled"])
    if enabled and row is not None and in_quiet_hours(
            row["quiet_hours_start"], row["quiet_hours_end"], row["default_timezone"], now):
        enabled = False
    await conn.execute(
        """
        insert into notification_outbox (user_id, category, title, body, payload, status)
        values (%s, %s, %s, %s, %s, %s)
        """,
        (user_id, category, title[:120], body[:500], Jsonb(payload or {}), "pending" if enabled else "suppressed"),
    )


def in_quiet_hours(start: time | None, end: time | None, tz: str | None, now: datetime | None = None) -> bool:
    """True when the local time is inside [start, end). Handles ranges across
    midnight (23:00–07:00). Off when either end is unset or they're equal."""
    if start is None or end is None or start == end:
        return False
    local = (now or datetime.now(UTC)).astimezone(ZoneInfo(tz or "Asia/Kuala_Lumpur")).time().replace(tzinfo=None)
    if start < end:
        return start <= local < end
    return local >= start or local < end


class NotificationSender(Protocol):
    async def send(self, user_id: UUID, category: str, title: str, body: str, payload: dict) -> None: ...


class LogNotificationSender:
    """Used when APNs isn't configured: logs instead of pushing."""

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
