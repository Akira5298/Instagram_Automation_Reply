"""Your personal context: rough location and upcoming calendar (opt-in).

The iPhone app uploads this only after you allow Location / Calendar access
and switch it on in Settings → Personal context. Only place names (city,
region, country) are stored, never coordinates, plus your calendar for the
next ~36 hours (event titles only if you allow them).

ECHO uses it to answer things like "are you free tonight?" accurately.
Drafts that would reveal where you are or what's on your calendar are held
for your OK (see worker/auto_reply.py: personal_info).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

from fastapi import APIRouter, Depends
from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from pydantic import BaseModel, Field

from app.core.security import require_user
from app.db.pool import get_conn

router = APIRouter(prefix="/v1", tags=["context"])

LOCATION_FRESH = timedelta(hours=6)
CALENDAR_FRESH = timedelta(hours=12)
MAX_EVENTS = 30


class Place(BaseModel):
    locality: str | None = Field(default=None, max_length=80)
    region: str | None = Field(default=None, max_length=80)
    country: str | None = Field(default=None, max_length=80)


class Event(BaseModel):
    start: datetime
    end: datetime
    all_day: bool = False
    busy: bool = True
    title: str | None = Field(default=None, max_length=120)


class ContextIn(BaseModel):
    location: Place | None = None
    events: list[Event] | None = Field(default=None, max_length=MAX_EVENTS)
    clear_location: bool = False
    clear_calendar: bool = False


@router.put("/context")
async def put_context(body: ContextIn, user_id: UUID = Depends(require_user),
                      conn: AsyncConnection = Depends(get_conn)):
    now = datetime.now(UTC)
    await conn.execute("insert into user_context (user_id) values (%s) on conflict do nothing", (user_id,))
    if body.clear_location:
        await conn.execute("update user_context set locality = null, region = null, country = null, "
                           "location_updated_at = null, updated_at = now() where user_id = %s", (user_id,))
    elif body.location is not None:
        await conn.execute(
            "update user_context set locality = %s, region = %s, country = %s, location_updated_at = %s, "
            "updated_at = now() where user_id = %s",
            (body.location.locality, body.location.region, body.location.country, now, user_id),
        )
    if body.clear_calendar:
        await conn.execute("update user_context set events = null, calendar_updated_at = null, updated_at = now() "
                           "where user_id = %s", (user_id,))
    elif body.events is not None:
        horizon = now + timedelta(hours=36)
        events = sorted((e for e in body.events if e.end > now and e.start < horizon), key=lambda e: e.start)
        await conn.execute(
            "update user_context set events = %s, calendar_updated_at = %s, updated_at = now() where user_id = %s",
            (Jsonb([e.model_dump(mode="json") for e in events]), now, user_id),
        )
    return await get_context(user_id, conn)


@router.get("/context")
async def get_context(user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    cur = await conn.execute("select * from user_context where user_id = %s", (user_id,))
    row = await cur.fetchone()
    if row is None:
        return {"location": None, "events": None, "location_updated_at": None, "calendar_updated_at": None}
    return {
        "location": {"locality": row["locality"], "region": row["region"], "country": row["country"]}
        if row["location_updated_at"] else None,
        "events": row["events"],
        "location_updated_at": row["location_updated_at"],
        "calendar_updated_at": row["calendar_updated_at"],
    }


@router.delete("/context", status_code=204)
async def delete_context(user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    await conn.execute("delete from user_context where user_id = %s", (user_id,))


async def load_fresh_context(conn: AsyncConnection, user_id: UUID, now: datetime) -> dict | None:
    """Context young enough to trust, for prompts. None when there's nothing usable."""
    cur = await conn.execute("select * from user_context where user_id = %s", (user_id,))
    row = await cur.fetchone()
    if row is None:
        return None
    out: dict = {}
    if row["location_updated_at"] and now - row["location_updated_at"] < LOCATION_FRESH and row["locality"]:
        out["place"] = ", ".join(p for p in (row["locality"], row["region"], row["country"]) if p)
        out["locality"] = row["locality"]
        out["location_age_min"] = int((now - row["location_updated_at"]).total_seconds() // 60)
    if row["calendar_updated_at"] and now - row["calendar_updated_at"] < CALENDAR_FRESH and row["events"] is not None:
        upcoming = []
        for e in row["events"]:
            end = datetime.fromisoformat(e["end"])
            if end > now:
                upcoming.append(e)
        out["events"] = upcoming
    return out or None
