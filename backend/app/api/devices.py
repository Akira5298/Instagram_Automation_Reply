"""Push devices and notification preferences (Phase 6)."""

from __future__ import annotations

import re
from datetime import time
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg import AsyncConnection
from pydantic import BaseModel, Field, field_validator

from app.core.config import Settings, get_settings
from app.core.security import require_user
from app.db import profile_repo
from app.db.pool import get_conn
from app.services.notifications import in_quiet_hours
from app.services.push import ApnsNotificationSender, apns_configured

router = APIRouter(prefix="/v1", tags=["notifications"])

_HEX = re.compile(r"^[0-9a-fA-F]{64,200}$")


class DeviceIn(BaseModel):
    token: str = Field(min_length=64, max_length=200)
    environment: Literal["sandbox", "production"]
    app_version: str | None = Field(default=None, max_length=32)

    @field_validator("token")
    @classmethod
    def hex_token(cls, value: str) -> str:
        if not _HEX.match(value):
            raise ValueError("token must be the hex APNs device token")
        return value.lower()


@router.post("/devices", status_code=201)
async def register_device(body: DeviceIn, user_id: UUID = Depends(require_user),
                          conn: AsyncConnection = Depends(get_conn), settings: Settings = Depends(get_settings)):
    """Called by the app after iOS hands it a push token (every launch; tokens can change)."""
    await conn.execute(
        """
        insert into devices (user_id, token, environment, app_version)
        values (%s, %s, %s, %s)
        on conflict (token) do update set user_id = excluded.user_id, environment = excluded.environment,
            app_version = excluded.app_version, last_seen_at = now(), disabled_at = null, last_error = null
        """,
        (user_id, body.token, body.environment, body.app_version),
    )
    return {"registered": True, "push_configured": apns_configured(settings)}


@router.delete("/devices/{token}", status_code=204)
async def unregister_device(token: str, user_id: UUID = Depends(require_user),
                            conn: AsyncConnection = Depends(get_conn)):
    await conn.execute("delete from devices where user_id = %s and token = %s", (user_id, token.lower()))


@router.post("/devices/test")
async def test_push(request: Request, user_id: UUID = Depends(require_user),
                    settings: Settings = Depends(get_settings)):
    """Sends a test notification to every registered device and reports what APNs said."""
    if not apns_configured(settings):
        raise HTTPException(status_code=409, detail=(
            "Push isn't set up on the server. Add APNS_KEY_PATH, APNS_KEY_ID, APNS_TEAM_ID and APNS_BUNDLE_ID "
            "to backend/.env (docs/TESTFLIGHT.md §3) and restart."))
    worker = getattr(request.app.state, "worker", None)
    sender = getattr(worker, "notifier", None)
    if not isinstance(sender, ApnsNotificationSender):
        raise HTTPException(status_code=409, detail="Push failed to start — run `python -m app.doctor` for details.")
    results = await sender.push(user_id, "automation_changed", "ECHO test", "Push notifications work. 🎉", {})
    if not results:
        raise HTTPException(status_code=409, detail="No device registered yet. Allow notifications in the app first.")
    return {"results": [r.__dict__ for r in results]}


# ------------------------------------------------------------ preferences

class Preferences(BaseModel):
    message_sent: bool = True
    job_blocked: bool = True
    delivery_failed: bool = True
    needs_attention: bool = True
    automation_changed: bool = True
    connection_error: bool = True
    schedule_reminder: bool = False
    quiet_hours_start: time | None = None
    quiet_hours_end: time | None = None
    quiet_hours_hold_replies: bool = False


_FIELDS = list(Preferences.model_fields)


@router.get("/notifications/preferences")
async def get_preferences(user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    cur = await conn.execute(
        f"""
        select {", ".join("p." + f for f in _FIELDS)}, u.default_timezone
        from app_users u left join notification_preferences p on p.user_id = u.id where u.id = %s
        """,
        (user_id,),
    )
    row = await cur.fetchone() or {}
    defaults = Preferences().model_dump()
    out = {f: (row.get(f) if row.get(f) is not None else defaults[f]) for f in _FIELDS}
    out["quiet_hours_start"] = row.get("quiet_hours_start")
    out["quiet_hours_end"] = row.get("quiet_hours_end")
    out["timezone"] = row.get("default_timezone")
    out["quiet_now"] = in_quiet_hours(out["quiet_hours_start"], out["quiet_hours_end"], out["timezone"])
    return out


@router.put("/notifications/preferences")
async def put_preferences(body: Preferences, user_id: UUID = Depends(require_user),
                          conn: AsyncConnection = Depends(get_conn)):
    values = body.model_dump()
    if (values["quiet_hours_start"] is None) != (values["quiet_hours_end"] is None):
        raise HTTPException(status_code=422, detail="Set both quiet-hours times, or neither.")
    cols = ", ".join(_FIELDS)
    await conn.execute(
        f"""
        insert into notification_preferences (user_id, {cols})
        values (%s, {", ".join(["%s"] * len(_FIELDS))})
        on conflict (user_id) do update set {", ".join(f"{f} = excluded.{f}" for f in _FIELDS)}, updated_at = now()
        """,
        (user_id, *[values[f] for f in _FIELDS]),
    )
    await profile_repo.audit(conn, user_id, "user", "notifications.preferences", None, None,
                             {"quiet_hours": bool(values["quiet_hours_start"])})
    return await get_preferences(user_id, conn)
