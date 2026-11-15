"""Sending an AI-written message to a friend, with the ledger and disclosure.

Used by the auto-reply worker and by "send this draft" in the review queue.
Every send:
  1. reserves a row in outbound_messages under a stable idempotency key
     (and, for replies, the inbound mid — unique, so one reply per message);
  2. sends the AI disclosure first if the friend hasn't seen it in 30 days;
  3. sends the text and records it in the thread as ECHO's.
No database transaction is held during network calls.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from app.db import jobs_repo, messaging_repo
from app.messaging.provider import (
    MessagingProvider,
    PermanentSendError,
    TransientSendError,
    UnknownOutcome,
)

DISCLOSURE_REFRESH = timedelta(days=30)


@dataclass(frozen=True)
class Recipient:
    user_id: UUID
    friend_id: UUID
    display_name: str
    ig_user_id: str          # your account
    igsid: str               # the friend
    disclosure_sent_at: datetime | None
    disclosure_text: str | None


@dataclass(frozen=True)
class Delivered:
    message_id: str
    outbound_id: UUID
    already_sent: bool = False


async def deliver(
    pool: AsyncConnectionPool,
    messaging: MessagingProvider,
    to: Recipient,
    text: str,
    *,
    idempotency_key: str,
    sent_by: str,
    clock: Callable[[], datetime],
    reply_to_inbound_id: str | None = None,
) -> Delivered:
    """Raises TransientSendError / PermanentSendError / UnknownOutcome after
    recording the outcome in the ledger."""
    async with pool.connection() as conn:
        outbound = await jobs_repo.reserve_outbound(
            conn, user_id=to.user_id, friend_id=to.friend_id, idempotency_key=idempotency_key,
            content=text, job_run_id=None, reply_to_inbound_id=reply_to_inbound_id,
        )
        if outbound["status"] == "sent":
            return Delivered(outbound["ig_message_id"] or "", outbound["id"], already_sent=True)
        if outbound["status"] == "unknown":
            raise UnknownOutcome("an earlier attempt may have been delivered")
        await conn.execute("update outbound_messages set status = 'sending' where id = %s", (outbound["id"],))

    now = clock()
    needs_disclosure = bool(to.disclosure_text) and (
        to.disclosure_sent_at is None or now - to.disclosure_sent_at > DISCLOSURE_REFRESH)
    try:
        if needs_disclosure:
            disclosure = await messaging.send_text(
                account_ig_id=to.ig_user_id, recipient_igsid=to.igsid, text=to.disclosure_text or "",
                idempotency_key=f"{idempotency_key}:disclosure",
            )
            async with pool.connection() as conn:
                await messaging_repo.record_outbound(
                    conn, user_id=to.user_id, friend_id=to.friend_id, text=to.disclosure_text or "",
                    mid=disclosure.message_id, sent_by=sent_by, at=clock(),
                )
                await conn.execute("update friends set disclosure_sent_at = %s where id = %s",
                                   (clock(), to.friend_id))
        result = await messaging.send_text(
            account_ig_id=to.ig_user_id, recipient_igsid=to.igsid, text=text, idempotency_key=idempotency_key,
        )
    except (TransientSendError, PermanentSendError) as exc:
        async with pool.connection() as conn:
            await jobs_repo.finish_outbound(conn, outbound["id"], status="failed", error=str(exc)[:300])
        raise
    except UnknownOutcome as exc:
        async with pool.connection() as conn:
            await jobs_repo.finish_outbound(conn, outbound["id"], status="unknown", error=str(exc)[:300])
        raise

    async with pool.connection() as conn:
        await jobs_repo.finish_outbound(conn, outbound["id"], status="sent", ig_message_id=result.message_id)
        await messaging_repo.record_outbound(conn, user_id=to.user_id, friend_id=to.friend_id, text=text,
                                             mid=result.message_id, sent_by=sent_by, at=clock())
    return Delivered(result.message_id, outbound["id"])
