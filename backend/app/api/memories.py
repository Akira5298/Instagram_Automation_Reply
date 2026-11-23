"""What ECHO remembers about each friend (Phase 6).

Only *approved*, unexpired memories reach Claude (up to 5 per reply).
Sensitive ones (health, family matters, money, where someone is…) must have an
expiry date — at most 90 days — so they can't linger.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from app.core.security import require_user
from app.db import messaging_repo, profile_repo
from app.db.pool import get_conn

router = APIRouter(prefix="/v1", tags=["memories"])

MAX_SENSITIVE = timedelta(days=90)
DEFAULT_SENSITIVE = timedelta(days=30)
_COLUMNS = "id, friend_id, content, source, sensitivity, approved, expires_at, created_at, updated_at"


class MemoryIn(BaseModel):
    content: str = Field(min_length=2, max_length=300)
    sensitivity: Literal["normal", "sensitive"] = "normal"
    expires_at: datetime | None = None


class MemoryPatch(BaseModel):
    content: str | None = Field(default=None, min_length=2, max_length=300)
    sensitivity: Literal["normal", "sensitive"] | None = None
    approved: bool | None = None
    expires_at: datetime | None = None
    clear_expiry: bool = False


def _expiry(sensitivity: str, expires_at: datetime | None, now: datetime) -> datetime | None:
    if expires_at is not None and expires_at <= now:
        raise HTTPException(status_code=422, detail="The expiry date must be in the future.")
    if sensitivity == "sensitive":
        if expires_at is None:
            return now + DEFAULT_SENSITIVE
        if expires_at - now > MAX_SENSITIVE:
            raise HTTPException(status_code=422, detail="Sensitive memories can be kept for at most 90 days.")
    return expires_at


@router.get("/friends/{friend_id}/memories")
async def list_memories(friend_id: UUID, user_id: UUID = Depends(require_user),
                        conn: AsyncConnection = Depends(get_conn)):
    if await messaging_repo.get_friend(conn, user_id, friend_id) is None:
        raise HTTPException(status_code=404, detail="friend not found")
    await conn.execute("delete from friend_memories where friend_id = %s and expires_at < now()", (friend_id,))
    cur = await conn.execute(
        f"select {_COLUMNS} from friend_memories where friend_id = %s order by approved, created_at desc",
        (friend_id,),
    )
    return {"memories": await cur.fetchall()}


@router.post("/friends/{friend_id}/memories", status_code=201)
async def add_memory(friend_id: UUID, body: MemoryIn, user_id: UUID = Depends(require_user),
                     conn: AsyncConnection = Depends(get_conn)):
    if await messaging_repo.get_friend(conn, user_id, friend_id) is None:
        raise HTTPException(status_code=404, detail="friend not found")
    now = datetime.now(UTC)
    cur = await conn.execute(
        f"""
        insert into friend_memories (user_id, friend_id, content, source, sensitivity, approved, expires_at)
        values (%s, %s, %s, 'user_stated', %s, true, %s)
        returning {_COLUMNS}
        """,
        (user_id, friend_id, body.content.strip(), body.sensitivity, _expiry(body.sensitivity, body.expires_at, now)),
    )
    row = await cur.fetchone()
    await profile_repo.audit(conn, user_id, "user", "memory.added", "friend_memory", str(row["id"]))
    return row


@router.patch("/memories/{memory_id}")
async def edit_memory(memory_id: UUID, body: MemoryPatch, user_id: UUID = Depends(require_user),
                      conn: AsyncConnection = Depends(get_conn)):
    cur = await conn.execute(f"select {_COLUMNS} from friend_memories where id = %s and user_id = %s",
                             (memory_id, user_id))
    row = await cur.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="memory not found")
    now = datetime.now(UTC)
    sensitivity = body.sensitivity or row["sensitivity"]
    expires = None if body.clear_expiry else (body.expires_at or row["expires_at"])
    expires = _expiry(sensitivity, expires, now)
    content = body.content.strip() if body.content else row["content"]
    approved = row["approved"] if body.approved is None else body.approved
    source = "user_edited" if body.content and body.content.strip() != row["content"] else row["source"]
    cur = await conn.execute(
        f"""
        update friend_memories set content = %s, sensitivity = %s, approved = %s, expires_at = %s, source = %s
        where id = %s returning {_COLUMNS}
        """,
        (content, sensitivity, approved, expires, source, memory_id),
    )
    updated = await cur.fetchone()
    await profile_repo.audit(conn, user_id, "user", "memory.edited", "friend_memory", str(memory_id))
    return updated


@router.delete("/memories/{memory_id}", status_code=204)
async def delete_memory(memory_id: UUID, user_id: UUID = Depends(require_user),
                        conn: AsyncConnection = Depends(get_conn)):
    cur = await conn.execute("delete from friend_memories where id = %s and user_id = %s", (memory_id, user_id))
    if cur.rowcount == 0:
        raise HTTPException(status_code=404, detail="memory not found")
    await profile_repo.audit(conn, user_id, "user", "memory.deleted", "friend_memory", str(memory_id))
