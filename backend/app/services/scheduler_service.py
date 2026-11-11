"""Creating and presenting scheduled jobs (shared by the jobs API and chat)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from app.db import jobs_repo, messaging_repo, profile_repo
from app.domain.recurrence import (
    RecurrenceError,
    describe,
    first_occurrence,
    normalise_rrule,
    recurrence_name,
    to_local_naive,
    zone,
)

MIN_LEAD = timedelta(seconds=30)


class ScheduleError(ValueError):
    """A request we can't turn into a job; message is shown to the user."""


@dataclass(frozen=True)
class JobRequest:
    friend_id: UUID
    scheduled_at: datetime          # aware instant, or naive = local wall time in `timezone`
    timezone: str
    recurrence: str = "once"
    rrule: str | None = None
    instruction: str = ""
    style_preset: str | None = None
    automation_enabled: bool = True
    created_via: str = "ui"


async def create_job(conn: AsyncConnection, user_id: UUID, req: JobRequest, *, now: datetime) -> UUID:
    friend = await messaging_repo.get_friend(conn, user_id, req.friend_id)
    if friend is None:
        raise ScheduleError("That friend isn't in your list.")
    instruction = " ".join(req.instruction.split())
    if not instruction:
        raise ScheduleError("Tell ECHO what the message should say.")
    if len(instruction) > 500:
        raise ScheduleError("The instruction is too long (max 500 characters).")
    try:
        tz = zone(req.timezone)
        rule = normalise_rrule(req.recurrence, req.rrule)
    except RecurrenceError as exc:
        raise ScheduleError(str(exc)) from exc

    local_start = (req.scheduled_at if req.scheduled_at.tzinfo is None
                   else to_local_naive(req.scheduled_at, tz)).replace(second=0, microsecond=0)
    first = first_occurrence(local_start, tz, rule)
    if first is None:
        raise ScheduleError("That repeat rule never produces a date.")
    if first < now + MIN_LEAD:
        raise ScheduleError("Pick a time in the future.")

    job_id = await jobs_repo.create_job(
        conn,
        user_id=user_id,
        friend=friend,
        instruction=instruction,
        style_preset=req.style_preset,
        first_run_at=first,
        timezone=req.timezone,
        local_time=local_start.time(),
        rrule=rule,
        next_run_at=first,
        automation_enabled=req.automation_enabled,
        created_via=req.created_via,
    )
    await profile_repo.audit(conn, user_id, "user" if req.created_via == "ui" else "echo", "job.created",
                             "scheduled_job", str(job_id), {"first_run_at": first.isoformat(), "rrule": rule})
    return job_id


def _eligibility_label(run: dict[str, Any]) -> str:
    if run.get("run_status") is None:
        return "checked_at_dispatch"
    snapshot = run.get("run_eligibility") or {}
    reasons = set(snapshot.get("reasons") or [])
    blocked = set((run.get("blocked_reason") or "").split(",")) - {""}
    reasons |= blocked
    if snapshot.get("eligible"):
        return "eligible"
    if reasons & {"outside_standard_window", "no_inbound_message"}:
        return "outside_messaging_window"
    if "no_consent" in reasons:
        return "no_consent"
    if reasons & {"global_pause", "friend_automation_off", "manual_override"}:
        return "automation_paused"
    if "late_dispatch" in reasons:
        return "missed"
    if "account_disconnected" in reasons:
        return "not_permitted"
    return "checked_at_dispatch"


_IN_FLIGHT = {"running", "sending", "retry_scheduled", "needs_attention"}

FAILURE_TEXT = {
    "outcome_unknown": "Instagram may or may not have received it",
    "late_dispatch": "Skipped because it was overdue",
}


def job_to_out(row: dict[str, Any]) -> dict[str, Any]:
    """Shape a job + its latest run for the app (mirrors Swift ScheduledJob)."""
    run_status = row.get("run_status")
    if row["status"] == "cancelled":
        status = "cancelled"
    elif row["status"] == "paused":
        status = "paused"
    elif run_status in _IN_FLIGHT:
        status = run_status
    elif row["status"] == "active" and row["next_run_at"] is not None:
        status = "scheduled"
    elif run_status:
        status = run_status
    else:
        status = "scheduled"

    if status == "scheduled" and row["next_run_at"] is not None:
        scheduled_at = row["next_run_at"]
    else:
        scheduled_at = row.get("run_scheduled_for") or row["next_run_at"] or row["first_run_at"]

    reason = row.get("failure_reason") or row.get("blocked_reason")
    if run_status == "needs_attention" and row.get("blocked_reason"):
        from app.domain.safety import REASON_TEXT
        reason = f"Paused: it {REASON_TEXT.get(row['blocked_reason'], row['blocked_reason'])}"
    elif run_status == "blocked" and row.get("blocked_reason"):
        reason = "Blocked: " + ", ".join(r.replace("_", " ") for r in row["blocked_reason"].split(","))
    elif reason in FAILURE_TEXT:
        reason = FAILURE_TEXT[reason]

    return {
        "id": row["id"],
        "friend_id": row["friend_id"],
        "recipient_igsid": row["recipient_igsid"],
        "recipient_name": row["recipient_display_name"],
        "scheduled_at": scheduled_at,
        "time_zone_id": row["timezone"],
        "recurrence": recurrence_name(row["rrule"]),
        "recurrence_title": describe(recurrence_name(row["rrule"]), row["rrule"]),
        "rrule": row["rrule"],
        "instruction": row["instruction"],
        "style": row["style_preset"] or row["friend_style"],
        "automation_enabled": row["automation_enabled"],
        "consent_recorded": row["friend_consent"] == "granted",
        "eligibility": _eligibility_label(row),
        "status": status,
        "job_status": row["status"],
        "last_executed_at": row.get("executed_at") or row.get("last_run_at"),
        "last_run_status": run_status,
        "latest_run_id": row.get("run_id"),
        "failure_reason": reason if run_status not in (None, "sent", "scheduled") else None,
        "draft_text": row.get("generated_text"),
        "created_via": row["created_via"],
    }


def run_to_out(run: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": run["id"],
        "scheduled_for": run["scheduled_for"],
        "status": run["status"],
        "attempt": run["attempt"],
        "executed_at": run["executed_at"],
        "draft_text": run["generated_text"],
        "safety_flags": run["safety_flags"] or [],
        "blocked_reason": run["blocked_reason"],
        "failure_reason": run["failure_reason"],
        "eligibility": run["eligibility"],
        "approved_by_user": run["approved_by_user"],
    }


def utcnow() -> datetime:
    return datetime.now(UTC)
