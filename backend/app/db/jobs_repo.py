"""scheduled_jobs (definitions), job_runs (occurrences), outbound ledger, reviews."""

from __future__ import annotations

from datetime import datetime, time
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb

JOB_COLUMNS = """
    j.id, j.user_id, j.friend_id, j.recipient_igsid, j.recipient_display_name, j.instruction,
    j.style_preset, j.language, j.first_run_at, j.timezone, j.local_time, j.rrule, j.next_run_at,
    j.automation_enabled, j.status, j.created_via, j.created_at, j.last_run_at, j.last_run_status,
    j.run_count, f.relationship_preset as friend_style, f.consent_status as friend_consent
"""

LATEST_RUN = """
    left join lateral (
        select r.id as run_id, r.status as run_status, r.scheduled_for as run_scheduled_for,
               r.eligibility as run_eligibility, r.blocked_reason, r.failure_reason,
               r.generated_text, r.executed_at, r.safety_flags
        from job_runs r where r.job_id = j.id order by r.scheduled_for desc, r.created_at desc limit 1
    ) lr on true
"""

RUN_FIELDS = (
    "id", "job_id", "scheduled_for", "idempotency_key", "status", "attempt", "claimed_by",
    "lease_expires_at", "next_attempt_at", "generated_text", "draft_language", "eligibility",
    "blocked_reason", "failure_reason", "safety_flags", "approved_by_user", "executed_at",
    "created_at", "updated_at",
)
RUN_COLUMNS = ", ".join(RUN_FIELDS)
RUN_COLUMNS_R = ", ".join(f"r.{name}" for name in RUN_FIELDS)


async def create_job(
    conn: AsyncConnection, *, user_id: UUID, friend: dict, instruction: str, style_preset: str | None,
    first_run_at: datetime, timezone: str, local_time: time, rrule: str | None, next_run_at: datetime,
    automation_enabled: bool, created_via: str,
) -> UUID:
    cur = await conn.execute(
        """
        insert into scheduled_jobs (user_id, friend_id, recipient_igsid, recipient_display_name, instruction,
            style_preset, first_run_at, timezone, local_time, rrule, next_run_at, automation_enabled,
            status, created_via)
        values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s)
        returning id
        """,
        (user_id, friend["id"], friend["igsid"], friend["display_name"], instruction, style_preset,
         first_run_at, timezone, local_time, rrule, next_run_at, automation_enabled, created_via),
    )
    row = await cur.fetchone()
    assert row is not None
    return row["id"]


async def list_jobs(conn: AsyncConnection, user_id: UUID, include_cancelled: bool = True) -> list[dict[str, Any]]:
    cur = await conn.execute(
        f"""
        select {JOB_COLUMNS}, lr.* from scheduled_jobs j
        join friends f on f.id = j.friend_id
        {LATEST_RUN}
        where j.user_id = %s and (%s or j.status <> 'cancelled')
        order by coalesce(j.next_run_at, lr.run_scheduled_for, j.first_run_at)
        """,
        (user_id, include_cancelled),
    )
    return await cur.fetchall()


async def get_job(conn: AsyncConnection, user_id: UUID, job_id: UUID, *, lock: bool = False) -> dict[str, Any] | None:
    cur = await conn.execute(
        f"""
        select {JOB_COLUMNS}, lr.* from scheduled_jobs j
        join friends f on f.id = j.friend_id
        {LATEST_RUN}
        where j.user_id = %s and j.id = %s
        {"for update of j" if lock else ""}
        """,
        (user_id, job_id),
    )
    return await cur.fetchone()


async def update_job(conn: AsyncConnection, job_id: UUID, changes: dict[str, Any]) -> None:
    if not changes:
        return
    sets = ", ".join(f"{column} = %s" for column in changes)
    await conn.execute(f"update scheduled_jobs set {sets} where id = %s", (*changes.values(), job_id))


async def cancel_open_runs(conn: AsyncConnection, job_id: UUID, reason: str) -> int:
    cur = await conn.execute(
        """
        update job_runs set status = 'cancelled', blocked_reason = %s
        where job_id = %s and status in ('scheduled', 'retry_scheduled')
        """,
        (reason, job_id),
    )
    return cur.rowcount


async def list_runs(conn: AsyncConnection, job_id: UUID, limit: int = 50) -> list[dict[str, Any]]:
    cur = await conn.execute(
        f"select {RUN_COLUMNS} from job_runs where job_id = %s order by scheduled_for desc, created_at desc limit %s",
        (job_id, limit),
    )
    return await cur.fetchall()


async def get_run_for_user(conn: AsyncConnection, user_id: UUID, run_id: UUID, *, lock: bool = False) -> dict | None:
    cur = await conn.execute(
        f"""
        select {RUN_COLUMNS_R}, j.user_id from job_runs r
        join scheduled_jobs j on j.id = r.job_id
        where r.id = %s and j.user_id = %s
        {"for update of r" if lock else ""}
        """,
        (run_id, user_id),
    )
    return await cur.fetchone()


async def insert_run(conn: AsyncConnection, job_id: UUID, scheduled_for: datetime) -> dict[str, Any] | None:
    cur = await conn.execute(
        f"""
        insert into job_runs (job_id, scheduled_for, idempotency_key)
        values (%s, %s, %s)
        on conflict (job_id, scheduled_for) do nothing
        returning {RUN_COLUMNS}
        """,
        (job_id, scheduled_for, f"job:{job_id}:{scheduled_for.isoformat()}"),
    )
    return await cur.fetchone()


async def update_run(conn: AsyncConnection, run_id: UUID, changes: dict[str, Any], *, owner: str | None = None) -> bool:
    """Conditional on the lease owner when `owner` is given, so a worker whose
    lease expired can't overwrite the state another worker now owns."""
    payload = {k: (Jsonb(v) if k == "eligibility" and v is not None else v) for k, v in changes.items()}
    sets = ", ".join(f"{column} = %s" for column in payload)
    sql = f"update job_runs set {sets} where id = %s"
    params: list[Any] = [*payload.values(), run_id]
    if owner is not None:
        sql += " and claimed_by = %s"
        params.append(owner)
    cur = await conn.execute(sql, params)
    return cur.rowcount == 1


# --- Outbound ledger -------------------------------------------------------------

async def reserve_outbound(
    conn: AsyncConnection, *, user_id: UUID, friend_id: UUID, idempotency_key: str, content: str,
    job_run_id: UUID | None,
) -> dict[str, Any]:
    cur = await conn.execute(
        """
        insert into outbound_messages (user_id, friend_id, idempotency_key, job_run_id, content, status)
        values (%s, %s, %s, %s, %s, 'sending')
        on conflict (idempotency_key) do update set attempt_count = outbound_messages.attempt_count
        returning id, status, ig_message_id, attempt_count
        """,
        (user_id, friend_id, idempotency_key, job_run_id, content),
    )
    row = await cur.fetchone()
    assert row is not None
    return row


async def finish_outbound(
    conn: AsyncConnection, outbound_id: UUID, *, status: str, ig_message_id: str | None = None,
    error: str | None = None,
) -> None:
    await conn.execute(
        """
        update outbound_messages set status = %s, ig_message_id = coalesce(%s, ig_message_id),
            last_error = %s, attempt_count = attempt_count + 1,
            sent_at = case when %s = 'sent' then now() else sent_at end
        where id = %s
        """,
        (status, ig_message_id, error, status, outbound_id),
    )


# --- Review queue ------------------------------------------------------------------

async def open_review(
    conn: AsyncConnection, *, user_id: UUID, friend_id: UUID, run_id: UUID, reason_code: str, draft: str | None,
) -> None:
    await conn.execute(
        """
        insert into review_items (user_id, friend_id, origin, job_run_id, reason_code, draft_text)
        values (%s, %s, 'scheduled', %s, %s, %s)
        """,
        (user_id, friend_id, run_id, reason_code, draft),
    )


async def close_reviews(conn: AsyncConnection, run_id: UUID, status: str) -> None:
    await conn.execute(
        "update review_items set status = %s, resolved_at = now() where job_run_id = %s and status = 'open'",
        (status, run_id),
    )
