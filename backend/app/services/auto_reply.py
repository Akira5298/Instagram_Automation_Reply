"""What happens the moment a DM arrives or you reply yourself (Phase 5).

Inbound message (called by the webhook processor and the dev simulator):
  * "STOP" (and a few equivalents) → consent revoked, AI replies turned off for
    that friend, nothing sent back. Deterministic; no model involved.
  * "YES" while consent is "requested" → consent granted (with the message id as
    evidence). ECHO doesn't reply to the YES itself.
  * Otherwise, if the friend has AI replies on and consent granted → a pending
    auto reply, due after the friend's reply delay. A newer message replaces the
    pending one, so a burst of messages gets one reply.

Your own reply (typed in ECHO, or in the Instagram app and echoed by Meta):
  * cancels any waiting auto reply for that friend, and
  * pauses automation for that friend for `takeover_minutes` (default 30), so
    ECHO doesn't talk over you.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from app.db import messaging_repo, profile_repo
from app.services.notifications import notify

_STOP = re.compile(r"^(stop|stop ai|no ai|unsubscribe|opt out|optout|berhenti|stop please|please stop)$")
_YES = re.compile(r"^(yes|y|yeah|yep|yup|yes please|ok|okay|ok sure|sure|sure thing|ya|boleh|ok boleh|yes ok)$")


def normalise_keyword(text: str | None) -> str:
    """Lower-case, strip punctuation/emoji/extra spaces: "Yes!! 👍" → "yes"."""
    if not text:
        return ""
    cleaned = re.sub(r"[^\w\s]", " ", text.lower(), flags=re.UNICODE)
    return " ".join(cleaned.split())


def keyword(text: str | None) -> str | None:
    word = normalise_keyword(text)
    if _STOP.match(word):
        return "stop"
    if _YES.match(word):
        return "yes"
    return None


async def on_inbound(
    conn: AsyncConnection, *, user_id: UUID, friend: dict[str, Any], mid: str, text: str | None,
    at: datetime, now: datetime,
) -> str:
    """Returns what happened: consent_revoked | consent_granted | queued | ignored."""
    kw = keyword(text)
    name = friend["display_name"]
    if kw == "stop" and (friend["ai_reply_enabled"] or friend["consent"] in ("granted", "requested")):
        await messaging_repo.update_friend(conn, user_id, friend["id"],
                                           {"consent": "revoked", "ai_reply_enabled": False})
        await messaging_repo.record_consent(conn, friend["id"], "revoked", "dm_keyword_stop", mid)
        await cancel_pending(conn, friend["id"], now, "consent_revoked")
        await profile_repo.audit(conn, user_id, "echo", "consent.revoked_by_keyword", "friend",
                                 str(friend["id"]), {"message_id": mid})
        await notify(conn, user_id, "automation_changed", f"{name} turned off AI replies",
                     f"{name} replied STOP. ECHO won't send them automated messages; you can still reply "
                     "yourself.", {"friend_id": str(friend["id"])})
        return "consent_revoked"

    if kw == "yes" and friend["consent"] == "requested":
        await messaging_repo.update_friend(conn, user_id, friend["id"], {"consent": "granted"})
        await messaging_repo.record_consent(conn, friend["id"], "granted", "dm_reply", mid)
        await profile_repo.audit(conn, user_id, "echo", "consent.granted_by_reply", "friend",
                                 str(friend["id"]), {"message_id": mid})
        body = f"{name} replied YES to your request."
        body += (" ECHO can now reply for you." if friend["ai_reply_enabled"]
                 else " Turn on AI replies for them in Messages when you're ready.")
        await notify(conn, user_id, "automation_changed", f"{name} agreed to AI-assisted replies", body,
                     {"friend_id": str(friend["id"])})
        return "consent_granted"

    if not friend["ai_reply_enabled"] or friend["consent"] != "granted":
        return "ignored"

    # Newest message wins: a waiting reply is replaced, so a burst gets one answer.
    await conn.execute(
        """
        update auto_replies set status = 'superseded', reason = 'newer_message', executed_at = %s
        where friend_id = %s and status = 'pending'
        """,
        (now, friend["id"]),
    )
    delay = timedelta(seconds=int(friend.get("auto_reply_delay_seconds") or 0))
    cur = await conn.execute(
        """
        insert into auto_replies (user_id, friend_id, inbound_message_id, inbound_at, due_at)
        values (%s, %s, %s, %s, %s)
        on conflict (inbound_message_id) do nothing
        """,
        (user_id, friend["id"], mid, at, now + delay),
    )
    return "queued" if cur.rowcount else "ignored"


async def on_manual_outbound(conn: AsyncConnection, *, user_id: UUID, friend_id: UUID, at: datetime) -> None:
    """You replied yourself: step back for a while."""
    await cancel_pending(conn, friend_id, at, "you_replied")
    cur = await conn.execute("select takeover_minutes from app_users where id = %s", (user_id,))
    row = await cur.fetchone()
    minutes = int(row["takeover_minutes"]) if row else 30
    if minutes > 0:
        await conn.execute(
            """
            update friends set manual_override_until = greatest(coalesce(manual_override_until, %s), %s)
            where id = %s
            """,
            (at, at + timedelta(minutes=minutes), friend_id),
        )


async def cancel_pending(conn: AsyncConnection, friend_id: UUID, now: datetime, reason: str) -> int:
    cur = await conn.execute(
        """
        update auto_replies set status = 'cancelled', reason = %s, executed_at = %s
        where friend_id = %s and status = 'pending'
        """,
        (reason, now, friend_id),
    )
    return cur.rowcount
