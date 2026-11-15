"""Development-only tools for the mock messaging provider. Disabled unless
ECHO_ENV=development and ECHO_MESSAGING_PROVIDER=mock."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.security import require_user
from app.db import messaging_repo
from app.db.pool import get_conn
from app.services import auto_reply


def _dev_only(settings: Settings = Depends(get_settings)) -> None:
    if settings.echo_env != "development" or settings.echo_messaging_provider != "mock":
        raise HTTPException(status_code=404, detail="not found")


router = APIRouter(prefix="/v1/dev", tags=["dev"], dependencies=[Depends(_dev_only)])

SAMPLE_FRIENDS = [
    # display, username, style, language, ai, consent, hours since last message, thread
    ("Kenji Tanaka", "kenji.t", "close_friend", "ja", True, "granted", 2,
     [("friend", "おつかれ！週末なにしてる？", 3.0), ("me", "まだ決めてない笑 なんで？", 2.9),
      ("friend", "新しいラーメン屋行こうよ🍜", 2.0)]),
    ("Giulia Rossi", "giulia.rossi", "classmate", "it", True, "requested", 30,
     [("friend", "Ciao! Hai gli appunti di ieri?", 30.0), ("me", "Sì, te li mando stasera!", 29.0)]),
    ("Mei Lin", "meilin_", "close_friend", "zh-Hans", False, "unknown", 5,
     [("friend", "明天的presentation准备好了吗？", 5.0)]),
    ("Daniel Ong", "daniel.ong.studio", "professional", "en", False, "unknown", 72,
     [("friend", "Thanks for the shoot last week — sending the invoice details soon.", 72.0)]),
]


class InboundMessage(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class FailureMode(BaseModel):
    mode: Literal["none", "transient_once", "transient_always", "permanent", "unknown"]


@router.post("/seed")
async def seed(user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    """Create a mock Instagram account with four sample friends and threads.
    Safe to call repeatedly."""
    account = await messaging_repo.ensure_mock_account(conn, user_id)
    now = datetime.now(UTC)
    created = []
    for i, (name, username, style, lang, ai, consent, hours, thread) in enumerate(SAMPLE_FRIENDS, start=1):
        friend = await messaging_repo.upsert_friend(
            conn, user_id=user_id, account_id=account["id"], igsid=f"mock-igsid-{1000 + i}",
            display_name=name, username=username, style=style, language=lang,
            ai_reply_enabled=ai, consent=consent,
        )
        if friend["last_inbound_at"] is None:
            for j, (sender, text, ago) in enumerate(thread):
                at = now - timedelta(hours=ago)
                if sender == "friend":
                    await messaging_repo.record_inbound(conn, user_id=user_id, friend_id=friend["id"], text=text,
                                                        mid=f"mock.seed.{i}.{j}", at=at)
                else:
                    await messaging_repo.record_outbound(conn, user_id=user_id, friend_id=friend["id"], text=text,
                                                         mid=f"mock.seed.{i}.{j}", sent_by="user_manual", at=at)
        created.append(friend["display_name"])
    return {"account": account["username"], "friends": created}


@router.post("/friends/{friend_id}/inbound", status_code=201)
async def simulate_inbound(friend_id: UUID, body: InboundMessage, user_id: UUID = Depends(require_user),
                           conn: AsyncConnection = Depends(get_conn)):
    """Pretend the friend just sent you a DM (opens Meta's 24-hour window)."""
    friend = await messaging_repo.get_friend(conn, user_id, friend_id)
    if friend is None:
        raise HTTPException(status_code=404, detail="friend not found")
    mid, now = f"mock.in.{uuid.uuid4().hex[:12]}", datetime.now(UTC)
    await messaging_repo.record_inbound(conn, user_id=user_id, friend_id=friend_id, text=body.text, mid=mid, at=now)
    outcome = await auto_reply.on_inbound(conn, user_id=user_id, friend=friend, mid=mid, text=body.text,
                                          at=now, now=now)
    return {"ok": True, "auto_reply": outcome}


@router.put("/mock-provider")
async def set_failure_mode(body: FailureMode, request: Request, _: UUID = Depends(require_user)):
    """Make the mock Instagram fail on purpose to test retries and alerts."""
    request.app.state.messaging.set_failure_mode(body.mode)
    return {"mode": body.mode}


@router.post("/worker/tick")
async def tick(request: Request, _: UUID = Depends(require_user)):
    """Run one worker cycle now instead of waiting for the next interval."""
    worker = request.app.state.worker
    if worker is None:
        raise HTTPException(status_code=409, detail="worker not running in this process")
    stats = await worker.run_once()
    return stats.__dict__
