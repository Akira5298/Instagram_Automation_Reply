"""Friends (contacts), per-friend controls, conversations, automation pause,
and the in-app activity feed."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.core.security import require_user
from app.db import messaging_repo, profile_repo
from app.db.pool import get_conn
from app.domain.messaging_window import EligibilityInput, evaluate_send_eligibility
from app.messaging.provider import SendError
from app.services import auto_reply
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
        "reply_delay_seconds": row.get("auto_reply_delay_seconds", 45),
        "review_first": row.get("auto_reply_require_review", False),
        "daily_reply_limit": row.get("auto_reply_daily_limit", 20),
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
    reply_delay_seconds: int | None = Field(default=None, ge=0, le=3600)
    review_first: bool | None = None
    daily_reply_limit: int | None = Field(default=None, ge=1, le=200)


class TakeoverBody(BaseModel):
    minutes: int | None = Field(default=None, ge=1, le=1440)


class StyleProfile(BaseModel):
    tone: str | None = Field(default=None, max_length=120)
    formality: int = Field(default=2, ge=0, le=4)
    emoji_level: int = Field(default=2, ge=0, le=4)
    max_sentences: int | None = Field(default=None, ge=1, le=10)
    slang_allowed: bool = True
    custom_instructions: str | None = Field(default=None, max_length=600)
    avoid_topics: list[str] = Field(default_factory=list, max_length=20)


class ManualMessage(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class AutomationState(BaseModel):
    paused: bool | None = None
    takeover_minutes: int | None = Field(default=None, ge=0, le=1440)
    daily_reply_limit: int | None = Field(default=None, ge=1, le=1000)
    consent_request_text: str | None = Field(default=None, min_length=10, max_length=500)
    ai_disclosure_text: str | None = Field(default=None, min_length=10, max_length=300)


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
    if changes.get("ai_reply_enabled") is False or changes.get("consent", "granted") != "granted":
        await auto_reply.cancel_pending(conn, friend_id, datetime.now(UTC), "settings_changed")
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
    await auto_reply.on_manual_outbound(conn, user_id=user_id, friend_id=friend_id, at=now)
    thread = await messaging_repo.recent_thread(conn, friend_id, 1)
    return message_out(thread[-1])


_SETTINGS_COLUMNS = {
    "takeover_minutes": "takeover_minutes",
    "daily_reply_limit": "auto_reply_daily_limit",
    "consent_request_text": "consent_request_text",
    "ai_disclosure_text": "ai_disclosure_text",
}


@router.get("/automation")
async def get_automation(user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    cur = await conn.execute(
        """
        select automation_paused, automation_paused_at, takeover_minutes, auto_reply_daily_limit,
               consent_request_text, ai_disclosure_text
        from app_users where id = %s
        """,
        (user_id,),
    )
    row = await cur.fetchone()
    return {"paused": row["automation_paused"], "paused_at": row["automation_paused_at"],
            "takeover_minutes": row["takeover_minutes"], "daily_reply_limit": row["auto_reply_daily_limit"],
            "consent_request_text": row["consent_request_text"], "ai_disclosure_text": row["ai_disclosure_text"]}


@router.put("/automation")
async def set_automation(body: AutomationState, user_id: UUID = Depends(require_user),
                         conn: AsyncConnection = Depends(get_conn)):
    """Global emergency stop plus account-wide auto-reply settings. The worker
    checks the pause flag before every send."""
    fields = body.model_dump(exclude_unset=True, exclude_none=True)
    if "paused" in fields:
        paused = fields.pop("paused")
        await conn.execute(
            """
            update app_users set automation_paused = %s,
                automation_paused_at = case when %s then now() else null end
            where id = %s
            """,
            (paused, paused, user_id),
        )
        await profile_repo.audit(conn, user_id, "user", "automation.paused" if paused else "automation.resumed")
        await notify(conn, user_id, "automation_changed",
                     "Automation paused" if paused else "Automation resumed",
                     "ECHO won't send anything until you resume." if paused
                     else "Automatic replies and scheduled messages will send again when eligible.")
    if fields:
        sets = ", ".join(f"{_SETTINGS_COLUMNS[k]} = %s" for k in fields)
        await conn.execute(f"update app_users set {sets} where id = %s", (*fields.values(), user_id))
        await profile_repo.audit(conn, user_id, "user", "automation.settings", None, None, {"fields": sorted(fields)})
    return await get_automation(user_id, conn)


@router.post("/friends/{friend_id}/takeover")
async def take_over(friend_id: UUID, body: TakeoverBody | None = None, user_id: UUID = Depends(require_user),
                    conn: AsyncConnection = Depends(get_conn)):
    """"I'll handle this one": ECHO stays out of this chat for a while."""
    friend = await messaging_repo.get_friend(conn, user_id, friend_id)
    if friend is None:
        raise HTTPException(status_code=404, detail="friend not found")
    minutes = body.minutes if body and body.minutes else None
    if minutes is None:
        cur = await conn.execute("select takeover_minutes from app_users where id = %s", (user_id,))
        minutes = max(60, (await cur.fetchone())["takeover_minutes"])
    now = datetime.now(UTC)
    await messaging_repo.update_friend(conn, user_id, friend_id,
                                       {"manual_override_until": now + timedelta(minutes=minutes)})
    cancelled = await auto_reply.cancel_pending(conn, friend_id, now, "you_took_over")
    await profile_repo.audit(conn, user_id, "user", "friend.takeover", "friend", str(friend_id),
                             {"minutes": minutes, "cancelled": cancelled})
    return friend_out(await messaging_repo.get_friend(conn, user_id, friend_id))


@router.delete("/friends/{friend_id}/takeover")
async def hand_back(friend_id: UUID, user_id: UUID = Depends(require_user),
                    conn: AsyncConnection = Depends(get_conn)):
    if await messaging_repo.get_friend(conn, user_id, friend_id) is None:
        raise HTTPException(status_code=404, detail="friend not found")
    await messaging_repo.update_friend(conn, user_id, friend_id, {"manual_override_until": None})
    await profile_repo.audit(conn, user_id, "user", "friend.handback", "friend", str(friend_id))
    return friend_out(await messaging_repo.get_friend(conn, user_id, friend_id))


@router.post("/friends/{friend_id}/consent-request")
async def request_consent(friend_id: UUID, request: Request, user_id: UUID = Depends(require_user),
                          conn: AsyncConnection = Depends(get_conn)):
    """Send your consent request (Settings → Automation) to the friend. They
    answer YES or STOP; ECHO records the answer automatically."""
    friend = await messaging_repo.get_friend(conn, user_id, friend_id)
    account = await messaging_repo.get_account(conn, user_id)
    if friend is None or account is None:
        raise HTTPException(status_code=404, detail="friend not found")
    if friend["consent"] == "granted":
        raise HTTPException(status_code=409, detail=f"{friend['display_name']} already agreed.")
    if friend["consent"] == "revoked":
        raise HTTPException(status_code=409, detail=(
            f"{friend['display_name']} replied STOP. Ask them yourself before turning AI replies back on."))
    now = datetime.now(UTC)
    check = evaluate_send_eligibility(EligibilityInput(
        now=now, last_inbound_at=friend["last_inbound_at"], consent_status="granted",  # it *is* the request
        friend_ai_enabled=True, global_pause=False, account_connected=account["status"] == "active",
    ))
    if not check.eligible:
        raise HTTPException(status_code=409, detail=(
            f"Instagram only allows messages within 24 hours of {friend['display_name']}'s last message. "
            "Ask them next time they write, or in person."))
    cur = await conn.execute("select consent_request_text from app_users where id = %s", (user_id,))
    text = (await cur.fetchone())["consent_request_text"]
    try:
        result = await request.app.state.messaging.send_text(
            account_ig_id=account["ig_user_id"], recipient_igsid=friend["igsid"], text=text,
            idempotency_key=f"consent:{friend_id}:{int(now.timestamp())}",
        )
    except SendError as exc:
        raise HTTPException(status_code=502, detail=f"Couldn't send: {exc}") from exc
    await messaging_repo.record_outbound(conn, user_id=user_id, friend_id=friend_id, text=text,
                                         mid=result.message_id, sent_by="user_manual", at=now)
    await messaging_repo.update_friend(conn, user_id, friend_id, {"consent": "requested"})
    await messaging_repo.record_consent(conn, friend_id, "requested", "other", result.message_id)
    await profile_repo.audit(conn, user_id, "user", "consent.requested", "friend", str(friend_id))
    return friend_out(await messaging_repo.get_friend(conn, user_id, friend_id))


@router.get("/friends/{friend_id}/style")
async def get_style(friend_id: UUID, user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    if await messaging_repo.get_friend(conn, user_id, friend_id) is None:
        raise HTTPException(status_code=404, detail="friend not found")
    cur = await conn.execute(
        """
        select tone, formality, emoji_level, max_sentences, slang_allowed, custom_instructions, boundaries
        from friend_style_profiles where friend_id = %s
        """,
        (friend_id,),
    )
    row = await cur.fetchone()
    if row is None:
        return StyleProfile().model_dump()
    out = {k: row[k] for k in ("tone", "formality", "emoji_level", "max_sentences", "slang_allowed",
                               "custom_instructions")}
    out["avoid_topics"] = list((row["boundaries"] or {}).get("avoid_topics") or [])
    return out


@router.put("/friends/{friend_id}/style")
async def put_style(friend_id: UUID, body: StyleProfile, user_id: UUID = Depends(require_user),
                    conn: AsyncConnection = Depends(get_conn)):
    """How ECHO should write to this friend, on top of your general profile."""
    if await messaging_repo.get_friend(conn, user_id, friend_id) is None:
        raise HTTPException(status_code=404, detail="friend not found")
    topics = [t.strip()[:60] for t in body.avoid_topics if t.strip()]
    await conn.execute(
        """
        insert into friend_style_profiles (friend_id, tone, formality, emoji_level, max_sentences, slang_allowed,
                                           custom_instructions, boundaries, updated_at)
        values (%s, %s, %s, %s, %s, %s, %s, %s, now())
        on conflict (friend_id) do update set tone = excluded.tone, formality = excluded.formality,
            emoji_level = excluded.emoji_level, max_sentences = excluded.max_sentences,
            slang_allowed = excluded.slang_allowed, custom_instructions = excluded.custom_instructions,
            boundaries = excluded.boundaries, updated_at = now()
        """,
        (friend_id, (body.tone or "").strip() or None, body.formality, body.emoji_level, body.max_sentences,
         body.slang_allowed, (body.custom_instructions or "").strip() or None, Jsonb({"avoid_topics": topics})),
    )
    await profile_repo.audit(conn, user_id, "user", "friend.style_updated", "friend", str(friend_id))
    return await get_style(friend_id, user_id, conn)


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
