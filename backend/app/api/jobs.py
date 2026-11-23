from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg import AsyncConnection
from pydantic import BaseModel, Field

from app.core.config import Settings, get_settings
from app.core.security import require_user
from app.domain.safety import fits_instagram
from app.db import jobs_repo, profile_repo
from app.db.pool import get_conn
from app.domain.job_state import RunStatus, can_transition
from app.domain.recurrence import (
    RecurrenceError,
    first_occurrence,
    normalise_rrule,
    to_local_naive,
    zone,
)
from app.services.scheduler_service import (
    JobRequest,
    ScheduleError,
    create_job,
    job_to_out,
    run_to_out,
    utcnow,
)

router = APIRouter(prefix="/v1/jobs", tags=["scheduler"])

Recurrence = Literal["once", "daily", "weekly", "weekdays", "custom"]


class JobCreate(BaseModel):
    friend_id: UUID
    # Aware ISO-8601 instant ("2026-11-12T20:00:00+08:00"), or naive local wall
    # time ("2026-11-12T20:00") interpreted in `timezone`.
    scheduled_at: datetime
    timezone: str | None = None
    recurrence: Recurrence = "once"
    rrule: str | None = None
    instruction: str = Field(default="", max_length=500)
    message_text: str | None = Field(default=None, min_length=1, max_length=1000)
    style_preset: Literal["close_friend", "partner", "family", "classmate", "professional", "custom"] | None = None
    automation_enabled: bool = True


class JobPatch(BaseModel):
    instruction: str | None = Field(default=None, min_length=1, max_length=500)
    message_text: str | None = Field(default=None, max_length=1000)   # "" = let ECHO write it again
    scheduled_at: datetime | None = None
    recurrence: Recurrence | None = None
    rrule: str | None = None
    automation_enabled: bool | None = None
    style_preset: Literal["close_friend", "partner", "family", "classmate", "professional", "custom"] | None = None


class RunAction(BaseModel):
    action: Literal["approve", "dismiss", "sent_manually"]


async def _job_out(conn: AsyncConnection, user_id: UUID, job_id: UUID) -> dict:
    row = await jobs_repo.get_job(conn, user_id, job_id)
    if row is None:
        raise HTTPException(status_code=404, detail="job not found")
    return job_to_out(row)


@router.get("")
async def list_jobs(user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    return {"jobs": [job_to_out(r) for r in await jobs_repo.list_jobs(conn, user_id)]}


@router.post("", status_code=201)
async def create(body: JobCreate, user_id: UUID = Depends(require_user),
                 conn: AsyncConnection = Depends(get_conn), settings: Settings = Depends(get_settings)):
    try:
        job_id = await create_job(conn, user_id, JobRequest(
            friend_id=body.friend_id,
            scheduled_at=body.scheduled_at,
            timezone=body.timezone or settings.default_timezone,
            recurrence=body.recurrence,
            rrule=body.rrule,
            instruction=body.instruction,
            message_text=body.message_text,
            style_preset=body.style_preset,
            automation_enabled=body.automation_enabled,
        ), now=utcnow())
    except ScheduleError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return await _job_out(conn, user_id, job_id)


@router.get("/{job_id}")
async def get(job_id: UUID, user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    return await _job_out(conn, user_id, job_id)


@router.get("/{job_id}/runs")
async def runs(job_id: UUID, user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    await _job_out(conn, user_id, job_id)  # ownership check
    return {"runs": [run_to_out(r) for r in await jobs_repo.list_runs(conn, job_id)]}


@router.patch("/{job_id}")
async def update(job_id: UUID, body: JobPatch, user_id: UUID = Depends(require_user),
                 conn: AsyncConnection = Depends(get_conn)):
    job = await jobs_repo.get_job(conn, user_id, job_id, lock=True)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] in ("cancelled", "completed") and body.scheduled_at is None:
        raise HTTPException(status_code=409, detail=f"job is {job['status']}")

    changes: dict = {}
    if body.instruction is not None:
        changes["instruction"] = " ".join(body.instruction.split())
    if body.message_text is not None:
        exact = body.message_text.strip() or None
        if exact is not None and not fits_instagram(exact):
            raise HTTPException(status_code=422, detail="Instagram messages can be at most 1000 bytes.")
        changes["message_text"] = exact
    if body.automation_enabled is not None:
        changes["automation_enabled"] = body.automation_enabled
    if body.style_preset is not None:
        changes["style_preset"] = body.style_preset

    if body.scheduled_at is not None or body.recurrence is not None:
        try:
            tz = zone(job["timezone"])
            rule = normalise_rrule(body.recurrence, body.rrule) if body.recurrence else job["rrule"]
            start = body.scheduled_at or job["next_run_at"] or job["first_run_at"]
            local_start = (start if start.tzinfo is None else to_local_naive(start, tz)).replace(second=0, microsecond=0)
            first = first_occurrence(local_start, tz, rule)
        except RecurrenceError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if first is None or first <= utcnow():
            raise HTTPException(status_code=422, detail="Pick a time in the future.")
        changes.update(first_run_at=first, next_run_at=first, local_time=local_start.time(), rrule=rule)
        if job["status"] in ("completed",):
            changes["status"] = "active"
        await jobs_repo.cancel_open_runs(conn, job_id, "rescheduled")

    await jobs_repo.update_job(conn, job_id, changes)
    await profile_repo.audit(conn, user_id, "user", "job.updated", "scheduled_job", str(job_id),
                             {"fields": sorted(changes)})
    return await _job_out(conn, user_id, job_id)


@router.post("/{job_id}/pause")
async def pause(job_id: UUID, user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    job = await jobs_repo.get_job(conn, user_id, job_id, lock=True)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] != "active":
        raise HTTPException(status_code=409, detail=f"job is {job['status']}")
    await jobs_repo.update_job(conn, job_id, {"status": "paused"})
    await jobs_repo.cancel_open_runs(conn, job_id, "job_paused")
    await profile_repo.audit(conn, user_id, "user", "job.paused", "scheduled_job", str(job_id))
    return await _job_out(conn, user_id, job_id)


@router.post("/{job_id}/resume")
async def resume(job_id: UUID, user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    job = await jobs_repo.get_job(conn, user_id, job_id, lock=True)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] != "paused":
        raise HTTPException(status_code=409, detail=f"job is {job['status']}")
    from app.domain.recurrence import next_occurrence

    tz = zone(job["timezone"])
    now = utcnow()
    nxt = job["next_run_at"]
    if nxt is None or nxt <= now:
        # Don't fire what was missed while paused; continue with the next occurrence.
        nxt = next_occurrence(job["rrule"], to_local_naive(job["first_run_at"], tz), tz, now)
    if nxt is None:
        await jobs_repo.update_job(conn, job_id, {"status": "completed", "next_run_at": None})
    else:
        await jobs_repo.update_job(conn, job_id, {"status": "active", "next_run_at": nxt})
    await profile_repo.audit(conn, user_id, "user", "job.resumed", "scheduled_job", str(job_id))
    return await _job_out(conn, user_id, job_id)


@router.delete("/{job_id}", status_code=204)
async def cancel(job_id: UUID, user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    job = await jobs_repo.get_job(conn, user_id, job_id, lock=True)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    await jobs_repo.update_job(conn, job_id, {"status": "cancelled", "next_run_at": None})
    await jobs_repo.cancel_open_runs(conn, job_id, "job_cancelled")
    await profile_repo.audit(conn, user_id, "user", "job.cancelled", "scheduled_job", str(job_id))
    return Response(status_code=204)


@router.post("/{job_id}/run-now")
async def run_now(job_id: UUID, user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    """Queue an extra occurrence right now (handy for testing). It goes through
    exactly the same safety and eligibility checks as a scheduled one."""
    job = await jobs_repo.get_job(conn, user_id, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="job not found")
    if job["status"] in ("cancelled", "paused"):
        raise HTTPException(status_code=409, detail=f"job is {job['status']}")
    run = await jobs_repo.insert_run(conn, job_id, utcnow())
    await profile_repo.audit(conn, user_id, "user", "job.run_now", "scheduled_job", str(job_id))
    return {"run_id": run["id"] if run else None}


@router.post("/runs/{run_id}")
async def resolve_run(run_id: UUID, body: RunAction, user_id: UUID = Depends(require_user),
                      conn: AsyncConnection = Depends(get_conn)):
    """Resolve a run that needs your attention: approve ("send anyway" — the
    Meta eligibility check still applies), dismiss, or mark as sent manually."""
    run = await jobs_repo.get_run_for_user(conn, user_id, run_id, lock=True)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")
    current = RunStatus(run["status"])
    if body.action == "approve":
        if not can_transition(current, RunStatus.RETRY_SCHEDULED):
            raise HTTPException(status_code=409, detail=f"run is {run['status']}")
        await jobs_repo.update_run(conn, run_id, {
            "status": "retry_scheduled", "approved_by_user": True, "next_attempt_at": utcnow(),
            "blocked_reason": None,
        })
        await jobs_repo.close_reviews(conn, run_id, "dismissed")
    else:
        if not can_transition(current, RunStatus.CANCELLED) and current is not RunStatus.BLOCKED:
            raise HTTPException(status_code=409, detail=f"run is {run['status']}")
        if current is not RunStatus.BLOCKED:
            await jobs_repo.update_run(conn, run_id, {
                "status": "cancelled",
                "blocked_reason": "sent_manually" if body.action == "sent_manually" else "dismissed",
            })
        await jobs_repo.close_reviews(conn, run_id, "sent_manually" if body.action == "sent_manually" else "dismissed")
    await profile_repo.audit(conn, user_id, "user", f"run.{body.action}", "job_run", str(run_id))
    return run_to_out(await jobs_repo.get_run_for_user(conn, user_id, run_id))
