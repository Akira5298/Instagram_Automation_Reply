"""Friends (contacts), per-friend controls, conversations, automation pause,
and the in-app activity feed."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from app.core.security import require_user
from app.db import messaging_repo, profile_repo
from app.db.pool import get_conn
from app.domain.messaging_window import EligibilityInput, evaluate_send_eligibility
from app.messaging.provider import SendError
from app.services.notifications import notify

router = APIRouter(prefix="/v1", tags=["friends"])

Style = Literal["close_friend", "classmate", "professional", "custom"]
Consent = Literal["unknown", "requested", "granted", "revoked"]


def friend_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "igsid": row["igsid"],
        "display_name": row["display_name"],
        "username": row["username"],
        "style": row["style"],
        "language": row["language"],
        "ai_reply_enabled": row["ai_reply_enabled"],
        "consent": row["consent"],
        "last_inbound_at": row["last_inbound_at"],
        "manual_override_until": row["manual_override_until"],
    }


def message_out(row: dict) -> dict:
    return {
        "id": row["id"],
        "sender": messaging_repo.SENDER_FOR_APP.get(row["sent_by"], "friend"),
        "text": row["content"],
        "sent_at": row["platform_time"],
    }


class FriendPatch(BaseModel):
    ai_reply_enabled: bool | None = None
    style: Style | None = None
    language: str | None = Field(default=None, max_length=16)
    consent: Consent | None = None
    consent_evidence: str | None = Field(default=None, max_length=300)
    manual_override_until: datetime | None = None


class ManualMessage(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class AutomationState(BaseModel):
    paused: bool


@router.get("/friends")
async def list_friends(user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    return {"friends": [friend_out(f) for f in await messaging_repo.list_friends(conn, user_id)]}


@router.patch("/friends/{friend_id}")
async def update_friend(friend_id: UUID, body: FriendPatch, user_id: UUID = Depends(require_user),
                        conn: AsyncConnection = Depends(get_conn)):
    friend = await messaging_repo.get_friend(conn, user_id, friend_id)
    if friend is None:
        raise HTTPException(status_code=404, detail="friend not found")
    changes = body.model_dump(exclude_unset=True, exclude={"consent_evidence"})
    await messaging_repo.update_friend(conn, user_id, friend_id, changes)
    if body.consent is not None and body.consent != friend["consent"] and body.consent != "unknown":
        # You record consent you obtained (e.g. they said yes in person or in a DM).
        await messaging_repo.record_consent(conn, friend_id, body.consent,
                                            "in_person" if body.consent == "granted" else "other",
                                            body.consent_evidence)
    await profile_repo.audit(conn, user_id, "user", "friend.updated", "friend", str(friend_id),
                             {"fields": sorted(changes)})
    updated = await messaging_repo.get_friend(conn, user_id, friend_id)
    return friend_out(updated)


@router.get("/conversations")
async def conversations(user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    items = await messaging_repo.conversations(conn, user_id)
    return {
        "conversations": [
            {"id": c["friend"]["id"], "friend": friend_out(c["friend"]),
             "messages": [message_out(m) for m in c["messages"]], "unread_count": 0}
            for c in items
        ]
    }


@router.post("/friends/{friend_id}/messages", status_code=201)
async def send_manual(friend_id: UUID, body: ManualMessage, request: Request,
                      user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    """A reply you typed yourself. Still subject to Meta's 24-hour window."""
    friend = await messaging_repo.get_friend(conn, user_id, friend_id)
    account = await messaging_repo.get_account(conn, user_id)
    if friend is None or account is None:
        raise HTTPException(status_code=404, detail="friend not found")
    now = datetime.now(UTC)
    check = evaluate_send_eligibility(EligibilityInput(
        now=now, last_inbound_at=friend["last_inbound_at"], consent_status="granted",  # human-written
        friend_ai_enabled=True, global_pause=False, account_connected=account["status"] == "active",
    ))
    if not check.eligible:
        raise HTTPException(status_code=409, detail=(
            f"Instagram only allows replies within 24 hours of {friend['display_name']}'s last message. "
            "Open Instagram to message them."))
    try:
        result = await request.app.state.messaging.send_text(
            account_ig_id=account["ig_user_id"], recipient_igsid=friend["igsid"], text=body.text.strip(),
            idempotency_key=f"manual:{friend_id}:{now.timestamp()}",
        )
    except SendError as exc:
        raise HTTPException(status_code=502, detail=f"Couldn't send: {exc}") from exc
    await messaging_repo.record_outbound(conn, user_id=user_id, friend_id=friend_id, text=body.text.strip(),
                                         mid=result.message_id, sent_by="user_manual", at=now)
    thread = await messaging_repo.recent_thread(conn, friend_id, 1)
    return message_out(thread[-1])


@router.get("/automation")
async def get_automation(user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    cur = await conn.execute("select automation_paused, automation_paused_at from app_users where id = %s", (user_id,))
    row = await cur.fetchone()
    return {"paused": row["automation_paused"], "paused_at": row["automation_paused_at"]}


@router.put("/automation")
async def set_automation(body: AutomationState, user_id: UUID = Depends(require_user),
                         conn: AsyncConnection = Depends(get_conn)):
    """Global emergency stop. The worker checks this before every send."""
    await conn.execute(
        """
        update app_users set automation_paused = %s,
            automation_paused_at = case when %s then now() else null end
        where id = %s
        """,
        (body.paused, body.paused, user_id),
    )
    await profile_repo.audit(conn, user_id, "user", "automation.paused" if body.paused else "automation.resumed")
    await notify(conn, user_id, "automation_changed",
                 "Automation paused" if body.paused else "Automation resumed",
                 "ECHO won't send anything until you resume." if body.paused
                 else "Scheduled messages will send again when eligible.")
    return await get_automation(user_id, conn)


@router.get("/notifications")
async def notifications(limit: int = Query(default=50, ge=1, le=200),
                        user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    cur = await conn.execute(
        """
        select id, category, title, body, payload, created_at, read_at from notification_outbox
        where user_id = %s order by created_at desc limit %s
        """,
        (user_id, limit),
    )
    rows = await cur.fetchall()
    unread = sum(1 for r in rows if r["read_at"] is None)
    return {"notifications": rows, "unread_count": unread}


@router.post("/notifications/read", status_code=204)
async def mark_read(user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    await conn.execute("update notification_outbox set read_at = now() where user_id = %s and read_at is null",
                       (user_id,))
