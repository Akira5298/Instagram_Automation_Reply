from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from psycopg import AsyncConnection
from psycopg_pool import AsyncConnectionPool

from app.ai.provider import AIProvider
from app.api.schemas import ChatHistoryOut, ChatMessageOut, ChatRequest, ChatTurnOut
from app.core.config import Settings, get_settings
from app.core.security import require_user
from app.db import chat_repo, profile_repo
from app.db.pool import get_conn, get_pool
from app.services.chat_service import maybe_summarize, run_turn

router = APIRouter(prefix="/v1/chat", tags=["chat"])


def get_ai_provider(request: Request) -> AIProvider:
    return request.app.state.ai_provider


@router.post("/messages", response_model=ChatTurnOut)
async def post_message(
    body: ChatRequest,
    background: BackgroundTasks,
    user_id: UUID = Depends(require_user),
    pool: AsyncConnectionPool = Depends(get_pool),
    provider: AIProvider = Depends(get_ai_provider),
    settings: Settings = Depends(get_settings),
) -> ChatTurnOut:
    result = await run_turn(
        pool=pool, provider=provider, settings=settings,
        user_id=user_id, client_msg_id=body.client_msg_id, text=body.text,
    )
    if not result.replayed:
        background.add_task(maybe_summarize, pool=pool, provider=provider, settings=settings, user_id=user_id)
    return ChatTurnOut(
        user_message=ChatMessageOut.from_row(result.user_message),
        message=ChatMessageOut.from_row(result.assistant_message),
        replayed=result.replayed,
    )


@router.get("/messages", response_model=ChatHistoryOut)
async def get_messages(
    limit: int = Query(default=50, ge=1, le=200),
    before: datetime | None = None,
    user_id: UUID = Depends(require_user),
    conn: AsyncConnection = Depends(get_conn),
) -> ChatHistoryOut:
    rows = await chat_repo.list_messages(conn, user_id, limit=limit + 1, before=before)
    has_more = len(rows) > limit
    if has_more:
        rows = rows[1:]
    return ChatHistoryOut(messages=[ChatMessageOut.from_row(r) for r in rows], has_more=has_more)


@router.delete("/messages", status_code=204)
async def clear_messages(
    user_id: UUID = Depends(require_user),
    conn: AsyncConnection = Depends(get_conn),
) -> Response:
    """Deletes the chat transcript and summaries. Your learned profile stays —
    manage it under /v1/profile."""
    await chat_repo.clear_history(conn, user_id)
    await profile_repo.audit(conn, user_id, "user", "chat.cleared")
    return Response(status_code=204)
