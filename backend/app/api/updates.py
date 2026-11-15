"""Live updates for the app (long polling).

The app keeps one request open: `GET /v1/updates?since=<cursor>&wait=25`.
The server answers as soon as anything the app shows has changed — a new
message (webhook or poll), a reply ECHO sent, a held draft, a friend setting,
a notification — or after `wait` seconds with `changed: false`. The app then
reloads and asks again.

One open request instead of frequent polling keeps traffic tiny, which matters
on a free tunnel (each request counts) and on battery.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.core.security import require_user

router = APIRouter(prefix="/v1", tags=["updates"])

_CURSOR_SQL = """
select greatest(
  (select max(received_at) from ig_messages where user_id = %(u)s),
  (select max(coalesce(resolved_at, created_at)) from review_items where user_id = %(u)s),
  (select max(updated_at) from friends where user_id = %(u)s),
  (select max(created_at) from notification_outbox where user_id = %(u)s),
  (select max(coalesce(executed_at, created_at)) from auto_replies where user_id = %(u)s)
) as cursor
"""


async def current_cursor(pool, user_id: UUID) -> str:
    async with pool.connection() as conn:
        cur = await conn.execute(_CURSOR_SQL, {"u": user_id})
        row = await cur.fetchone()
    value: datetime | None = row["cursor"] if row else None
    return value.isoformat() if value else ""


@router.get("/updates")
async def updates(request: Request, since: str | None = Query(default=None, max_length=64),
                  wait: int = Query(default=25, ge=0, le=50), user_id: UUID = Depends(require_user)):
    pool = getattr(request.app.state, "pool", None)
    if pool is None:
        raise HTTPException(status_code=503, detail="database unavailable")
    loop = asyncio.get_running_loop()
    deadline = loop.time() + wait
    cursor = await current_cursor(pool, user_id)
    while since is not None and cursor == since and loop.time() < deadline:
        if await request.is_disconnected():
            break
        await asyncio.sleep(1.0)
        cursor = await current_cursor(pool, user_id)
    return {"cursor": cursor, "changed": since is None or cursor != since}
