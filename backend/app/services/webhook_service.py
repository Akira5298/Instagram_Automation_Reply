"""Instagram messaging webhooks.

Ingestion (inside the HTTP request — must be fast):
  verify signature → split into events → INSERT webhook_events ON CONFLICT
  (event_key) DO NOTHING → 200 OK. Meta retries failed deliveries, so the
  unique event_key makes duplicates harmless.

Processing (worker, outside the request):
  * is_self (test messages)                  → ignored
  * is_echo (sent from your account)         → recorded once. Our own API sends
    are already stored under the same mid, so they are recognised and skipped:
    ECHO never reacts to its own messages (loop guard).
  * is_deleted                               → message text removed
  * normal inbound                           → friend found/created (AI off,
    consent unknown), message stored, 24-hour window opened.
  * everything else (reactions, seen, …)     → ignored for now
Automatic replies to inbound messages arrive in Phase 5.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from typing import Any

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.db import messaging_repo
from app.services.instagram_service import InstagramService

log = logging.getLogger("echo.webhooks")

MAX_ATTEMPTS = 5


def split_events(payload: dict) -> list[tuple[str, str, dict]]:
    """→ [(event_key, entry_ig_id, messaging_event)]"""
    events: list[tuple[str, str, dict]] = []
    if payload.get("object") != "instagram":
        return events
    for entry in payload.get("entry") or []:
        entry_id = str(entry.get("id") or "")
        for event in entry.get("messaging") or []:
            message = event.get("message") or {}
            if message.get("mid"):
                suffix = "deleted" if message.get("is_deleted") else ("echo" if message.get("is_echo") else "msg")
                key = f"ig:{message['mid']}:{suffix}"
            else:
                digest = hashlib.sha256(json.dumps(event, sort_keys=True).encode()).hexdigest()[:32]
                key = f"ig:other:{digest}"
            events.append((key, entry_id, event))
    return events


async def ingest(conn: AsyncConnection, payload: dict) -> int:
    stored = 0
    for key, entry_id, event in split_events(payload):
        cur = await conn.execute(
            """
            insert into webhook_events (provider, event_key, entry_ig_id, payload)
            values ('meta', %s, %s, %s) on conflict (event_key) do nothing
            """,
            (key, entry_id, Jsonb(event)),
        )
        stored += cur.rowcount
    return stored


class WebhookProcessor:
    def __init__(self, *, pool: AsyncConnectionPool, instagram: InstagramService | None,
                 clock=lambda: datetime.now(UTC)) -> None:
        self.pool = pool
        self.instagram = instagram
        self.clock = clock

    async def process_pending(self, limit: int = 50) -> int:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                update webhook_events set processing_status = 'processing', attempts = attempts + 1
                where id in (
                    select id from webhook_events where processing_status = 'pending'
                    order by received_at limit %s for update skip locked
                )
                returning id, entry_ig_id, payload, attempts
                """,
                (limit,),
            )
            claimed = await cur.fetchall()
        for row in claimed:
            try:
                async with self.pool.connection() as conn:
                    status = await self._handle(conn, row["entry_ig_id"], row["payload"])
                    await conn.execute(
                        "update webhook_events set processing_status = %s, processed_at = now(), error = null where id = %s",
                        (status, row["id"]),
                    )
            except Exception as exc:
                log.exception("webhook processing failed")
                async with self.pool.connection() as conn:
                    await conn.execute(
                        "update webhook_events set processing_status = %s, error = %s where id = %s",
                        ("error" if row["attempts"] >= MAX_ATTEMPTS else "pending", str(exc)[:300], row["id"]),
                    )
        return len(claimed)

    async def _handle(self, conn: AsyncConnection, entry_ig_id: str, event: dict[str, Any]) -> str:
        account = await messaging_repo.account_by_ig_id(conn, entry_ig_id)
        if account is None or account["status"] != "active":
            return "ignored"
        await conn.execute("update instagram_accounts set last_webhook_at = %s where id = %s",
                           (self.clock(), account["id"]))

        message = event.get("message")
        if not message or not message.get("mid"):
            return "ignored"  # reactions, seen receipts, postbacks: not used yet
        if message.get("is_self"):
            return "ignored"
        mid = str(message["mid"])

        if message.get("is_deleted"):
            await messaging_repo.mark_deleted(conn, mid)
            return "done"

        sender = str((event.get("sender") or {}).get("id") or "")
        recipient = str((event.get("recipient") or {}).get("id") or "")
        at = datetime.fromtimestamp(int(event.get("timestamp") or 0) / 1000, tz=UTC) if event.get("timestamp") \
            else self.clock()
        text = message.get("text")
        if text is None and message.get("attachments"):
            kinds = ", ".join(sorted({a.get("type", "attachment") for a in message["attachments"]}))
            text = f"[{kinds}]"
        if message.get("is_unsupported"):
            text = text or "[unsupported message]"

        token = await self.instagram.token_for(conn, account["ig_user_id"]) if self.instagram else None

        if message.get("is_echo"):
            # Sent from your account. If ECHO sent it, we already stored this mid.
            if await messaging_repo.message_exists(conn, mid):
                return "ignored"
            friend = await self.instagram.ensure_friend(conn, account=account, igsid=recipient, token=token) \
                if self.instagram else None
            if friend:
                await messaging_repo.record_outbound(conn, user_id=account["user_id"], friend_id=friend["id"],
                                                     text=text or "", mid=mid, sent_by="user_manual", at=at)
            return "done"

        if not sender or sender == account["ig_user_id"]:
            return "ignored"
        friend = await self.instagram.ensure_friend(conn, account=account, igsid=sender, token=token) \
            if self.instagram else None
        if friend is None:
            return "ignored"
        await messaging_repo.record_inbound(conn, user_id=account["user_id"], friend_id=friend["id"],
                                            text=text or "", mid=mid, at=at)
        return "done"
