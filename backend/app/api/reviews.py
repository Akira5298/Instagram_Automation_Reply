"""Review queue: drafts ECHO held back for you (Phase 5).

Both kinds live here:
  * auto replies held by the safety gate or "review first";
  * scheduled messages held by the safety gate.

Actions:
  send           send ECHO's draft, or your edited version. Meta's rules still
                 apply (24-hour window, consent, connected account), checked
                 deterministically right now. Auto replies go out immediately;
                 scheduled messages are queued for the worker.
  dismiss        drop it.
  sent_manually  you answered in Instagram yourself.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from app.core.security import require_user
from app.db import jobs_repo, profile_repo
from app.db.pool import get_conn
from app.domain.messaging_window import (
    STANDARD_WINDOW,
    EligibilityInput,
    evaluate_send_eligibility,
)
from app.domain.safety import REASON_TEXT, fits_instagram
from app.messaging.provider import (
    PermanentSendError,
    TransientSendError,
    UnknownOutcome,
)
from app.services.delivery import Recipient, deliver

router = APIRouter(prefix="/v1/reviews", tags=["reviews"])


class ReviewAction(BaseModel):
    action: Literal["send", "dismiss", "sent_manually"]
    text: str | None = Field(default=None, min_length=1, max_length=1000)


_SELECT = """
    select r.id, r.origin, r.reason_code, r.draft_text, r.status, r.created_at, r.inbound_message_id,
           r.auto_reply_id, r.job_run_id, r.friend_id,
           f.display_name, f.igsid, f.last_inbound_message_at, f.consent_status, f.disclosure_sent_at,
           a.ig_user_id, a.status as account_status, u.ai_disclosure_text,
           (select content from ig_messages m where m.ig_message_id = r.inbound_message_id) as inbound_text
    from review_items r
    join friends f on f.id = r.friend_id
    join instagram_accounts a on a.id = f.instagram_account_id
    join app_users u on u.id = r.user_id
"""


def _out(row: dict) -> dict:
    window = row["last_inbound_message_at"] + STANDARD_WINDOW if row["last_inbound_message_at"] else None
    return {
        "id": row["id"],
        "origin": row["origin"],
        "friend_id": row["friend_id"],
        "friend_name": row["display_name"],
        "reason": row["reason_code"],
        "reason_text": REASON_TEXT.get(row["reason_code"], row["reason_code"]),
        "draft": row["draft_text"],
        "inbound_text": row["inbound_text"],
        "created_at": row["created_at"],
        "window_expires_at": window,
        "status": row["status"],
    }


async def _expire_closed_windows(conn: AsyncConnection, user_id: UUID) -> None:
    """An auto-reply draft is useless once Instagram's reply window has closed."""
    await conn.execute(
        """
        with expired as (
            update review_items r set status = 'expired', resolved_at = now()
            from friends f
            where r.friend_id = f.id and r.user_id = %s and r.status = 'open' and r.origin = 'auto_reply'
              and (f.last_inbound_message_at is null or f.last_inbound_message_at + interval '24 hours' < now())
            returning r.auto_reply_id
        )
        update auto_replies set status = 'blocked', reason = 'outside_standard_window'
        where id in (select auto_reply_id from expired) and status = 'needs_attention'
        """,
        (user_id,),
    )


@router.get("")
async def list_reviews(user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    await _expire_closed_windows(conn, user_id)
    cur = await conn.execute(_SELECT + " where r.user_id = %s and r.status = 'open' order by r.created_at desc",
                             (user_id,))
    items = [_out(r) for r in await cur.fetchall()]
    return {"reviews": items, "count": len(items)}


@router.post("/{review_id}")
async def resolve(review_id: UUID, body: ReviewAction, request: Request, user_id: UUID = Depends(require_user),
                  conn: AsyncConnection = Depends(get_conn)):
    cur = await conn.execute(_SELECT + " where r.user_id = %s and r.id = %s for update of r", (user_id, review_id))
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="review not found")
    if row["status"] != "open":
        raise HTTPException(status_code=409, detail=f"already {row['status']}")
    now = datetime.now(UTC)

    if body.action != "send":
        status = "dismissed" if body.action == "dismiss" else "sent_manually"
        await _close(conn, review_id, status)
        if row["auto_reply_id"]:
            await conn.execute("update auto_replies set status = 'cancelled', reason = %s where id = %s",
                               ("dismissed" if body.action == "dismiss" else "you_replied", row["auto_reply_id"]))
        elif row["job_run_id"]:
            await jobs_repo.update_run(conn, row["job_run_id"], {
                "status": "cancelled", "blocked_reason": status})
        await profile_repo.audit(conn, user_id, "user", f"review.{body.action}", "review_item", str(review_id))
        return {"id": review_id, "status": status}

    text = (body.text or row["draft_text"] or "").strip()
    if not text:
        raise HTTPException(status_code=422, detail="Write the reply first.")
    if not fits_instagram(text):
        raise HTTPException(status_code=422, detail="Instagram messages can be at most 1000 bytes.")

    if row["origin"] == "scheduled":
        # Queue it for the worker, which re-runs Meta's eligibility check.
        await jobs_repo.update_run(conn, row["job_run_id"], {
            "status": "retry_scheduled", "approved_by_user": True, "next_attempt_at": now,
            "blocked_reason": None, "generated_text": text,
        })
        await _close(conn, review_id, "approved")
        await profile_repo.audit(conn, user_id, "user", "review.send", "review_item", str(review_id),
                                 {"edited": body.text is not None})
        return {"id": review_id, "status": "approved", "queued": True}

    # Auto reply: check Meta's rules now and send.
    check = evaluate_send_eligibility(EligibilityInput(
        now=now, last_inbound_at=row["last_inbound_message_at"], consent_status=row["consent_status"],
        friend_ai_enabled=True,      # you're approving this one yourself
        global_pause=False, account_connected=row["account_status"] == "active",
    ))
    if not check.eligible:
        reasons = {r.value for r in check.reasons}
        if "no_consent" in reasons:
            detail = f"{row['display_name']} hasn't agreed to AI-assisted replies. Send it yourself in Instagram."
        elif "account_disconnected" in reasons:
            detail = "Instagram isn't connected."
        else:
            detail = "Instagram's 24-hour reply window has closed. Reply in the Instagram app instead."
        raise HTTPException(status_code=409, detail=detail)

    # Committed before sending so the request connection holds no locks during the network call.
    await conn.commit()
    to = Recipient(user_id=user_id, friend_id=row["friend_id"], display_name=row["display_name"],
                   ig_user_id=row["ig_user_id"], igsid=row["igsid"],
                   disclosure_sent_at=row["disclosure_sent_at"], disclosure_text=row["ai_disclosure_text"])
    try:
        delivered = await deliver(
            request.app.state.pool, request.app.state.messaging, to, text,
            idempotency_key=f"auto:{row['inbound_message_id']}", sent_by="echo_auto",
            clock=lambda: datetime.now(UTC), reply_to_inbound_id=row["inbound_message_id"],
        )
    except TransientSendError as exc:
        raise HTTPException(status_code=503, detail=f"Instagram is busy; try again. ({exc})") from exc
    except PermanentSendError as exc:
        raise HTTPException(status_code=409, detail=f"Instagram rejected it: {exc}") from exc
    except UnknownOutcome as exc:
        raise HTTPException(status_code=502, detail=(
            "ECHO isn't sure the reply was delivered. Check the chat in Instagram before trying again.")) from exc

    await _close(conn, review_id, "approved")
    await conn.execute(
        """
        update auto_replies set status = 'sent', approved_by_user = true, generated_text = %s,
            outbound_message_id = %s, executed_at = now()
        where id = %s
        """,
        (text, delivered.outbound_id, row["auto_reply_id"]),
    )
    await profile_repo.audit(conn, user_id, "user", "review.send", "review_item", str(review_id),
                             {"edited": body.text is not None, "message_id": delivered.message_id})
    return {"id": review_id, "status": "approved", "message_id": delivered.message_id}


async def _close(conn: AsyncConnection, review_id: UUID, status: str) -> None:
    await conn.execute("update review_items set status = %s, resolved_at = now() where id = %s",
                       (status, review_id))
