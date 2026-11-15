"""Durable worker: scheduled messages and (Phase 5) automatic replies.

Each tick:
  0. Process stored webhooks, then due automatic replies (app/worker/auto_reply.py).
  1. Recover runs whose worker died (expired leases).
  2. Materialise due jobs into job_runs (one row per occurrence, idempotent).
     Occurrences missed while the server was down are skipped; only the most
     recent one runs, and if it's over 15 minutes late it is *blocked*, not sent.
  3. Claim due runs with SELECT … FOR UPDATE SKIP LOCKED (safe with many
     workers) and process each:
        job still active? → global pause? → draft (Claude) → safety gate
        → deterministic Meta eligibility check, immediately before sending
        → send through the MessagingProvider → record + notify.
  4. Deliver pending notifications.

No database transaction is held while calling Claude or the messaging API.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg_pool import AsyncConnectionPool

from app.ai.compose_context import build_compose_context
from app.ai.provider import AIError, AIProvider
from app.core.config import Settings
from app.db import jobs_repo, messaging_repo, profile_repo
from app.domain.job_state import (
    MAX_ATTEMPTS,
    RunStatus,
    can_transition,
    retry_delay_seconds,
)
from app.domain.messaging_window import (
    BlockReason,
    EligibilityInput,
    evaluate_send_eligibility,
)
from app.domain.recurrence import LATE_LIMIT, latest_due, to_local_naive, zone
from app.domain.safety import (
    REASON_TEXT,
    fits_instagram,
    keyword_flags,
    normalise_flags,
)
from app.messaging.provider import (
    MessagingProvider,
    PermanentSendError,
    TransientSendError,
    UnknownOutcome,
)
from app.services.notifications import (
    LogNotificationSender,
    NotificationSender,
    dispatch_pending,
    notify,
)

log = logging.getLogger("echo.worker")

DISCLOSURE_REFRESH = timedelta(days=30)

BLOCK_TEXT = {
    BlockReason.GLOBAL_PAUSE: "automation is paused",
    BlockReason.FRIEND_AUTOMATION_OFF: "AI messages are turned off for {name}",
    BlockReason.MANUAL_OVERRIDE: "you've taken over this conversation",
    BlockReason.NO_CONSENT: "{name} hasn't agreed to AI-assisted messages",
    BlockReason.NO_INBOUND_MESSAGE: "{name} hasn't messaged you yet, and Instagram only allows replies",
    BlockReason.OUTSIDE_STANDARD_WINDOW: "{name} hasn't messaged you in the last 24 hours",
    BlockReason.ACCOUNT_DISCONNECTED: "Instagram isn't connected",
    "late_dispatch": "it was more than 15 minutes overdue (the server was offline)",
}


@dataclass
class WorkerStats:
    recovered: int = 0
    materialized: int = 0
    processed: int = 0
    sent: int = 0
    blocked: int = 0
    needs_attention: int = 0
    retried: int = 0
    failed: int = 0
    cancelled: int = 0
    notifications: int = 0
    webhooks: int = 0
    replies_sent: int = 0
    replies_held: int = 0
    replies_skipped: int = 0
    replies_blocked: int = 0
    replies_failed: int = 0


class Worker:
    def __init__(
        self,
        *,
        pool: AsyncConnectionPool,
        ai: AIProvider,
        messaging: MessagingProvider,
        settings: Settings,
        notifier: NotificationSender | None = None,
        clock: Callable[[], datetime] | None = None,
        webhooks: Any = None,
        instagram: Any = None,
        worker_id: str | None = None,
        lease: timedelta = timedelta(minutes=5),
    ) -> None:
        self.pool = pool
        self.ai = ai
        self.messaging = messaging
        self.settings = settings
        self.notifier = notifier or LogNotificationSender()
        self.clock = clock or (lambda: datetime.now(UTC))
        self.worker_id = worker_id or f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
        self.lease = lease
        self.webhooks = webhooks          # WebhookProcessor (Phase 4)
        self.instagram = instagram        # InstagramService, for token refresh
        self._last_maintenance: datetime | None = None
        from app.worker.auto_reply import AutoReplyProcessor

        self.auto_replies = AutoReplyProcessor(
            pool=pool, ai=ai, messaging=messaging, settings=settings, clock=self.clock,
            worker_id=self.worker_id, lease=lease,
        )

    # ------------------------------------------------------------------ loop

    async def run_forever(self, stop: asyncio.Event) -> None:
        log.info("worker started", extra={"fields": {"worker_id": self.worker_id}})
        while not stop.is_set():
            try:
                stats = await self.run_once()
                if (stats.processed or stats.materialized or stats.recovered or stats.webhooks
                        or stats.replies_sent or stats.replies_held or stats.replies_blocked):
                    log.info("worker tick", extra={"fields": stats.__dict__})
            except Exception:
                log.exception("worker tick failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.settings.worker_interval_seconds)
            except TimeoutError:
                pass

    async def run_once(self) -> WorkerStats:
        stats = WorkerStats()
        now = self.clock()
        if self.webhooks is not None:
            stats.webhooks = await self.webhooks.process_pending()
        await self._maintenance(now)
        replies = await self.auto_replies.run(self.clock())
        stats.replies_sent, stats.replies_held = replies.sent, replies.held
        stats.replies_skipped, stats.replies_blocked = replies.skipped, replies.blocked
        stats.replies_failed = replies.failed
        await self._recover_expired_leases(now, stats)
        await self._materialize_due_jobs(now, stats)
        for run_id in await self._claim_runs(now):
            stats.processed += 1
            try:
                await self._process(run_id, stats)
            except Exception:
                log.exception("run processing crashed", extra={"fields": {"run_id": str(run_id)}})
                await self._release_after_crash(run_id, stats)
        async with self.pool.connection() as conn:
            stats.notifications = await dispatch_pending(conn, self.notifier)
        return stats

    async def _maintenance(self, now: datetime) -> None:
        """At startup, then hourly: refresh expiring Instagram tokens, re-subscribe
        webhooks (Meta drops them after ~1 h of failed deliveries, e.g. while the
        Mac slept) and purge old message text."""
        if self._last_maintenance and now - self._last_maintenance < timedelta(hours=1):
            return
        self._last_maintenance = now
        try:
            if self.instagram is not None:
                await self.instagram.refresh_expiring_tokens(self.pool)
                await self.instagram.ensure_webhook_subscriptions(self.pool)
            async with self.pool.connection() as conn:
                await messaging_repo.purge_old_content(
                    conn, now - timedelta(days=self.settings.message_retention_days))
        except Exception:
            log.exception("maintenance failed")

    # ------------------------------------------------------- materialise

    async def _materialize_due_jobs(self, now: datetime, stats: WorkerStats) -> None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                select id, user_id, rrule, timezone, first_run_at, next_run_at from scheduled_jobs
                where status = 'active' and next_run_at <= %s
                order by next_run_at limit 100
                for update skip locked
                """,
                (now,),
            )
            for job in await cur.fetchall():
                tz = zone(job["timezone"])
                dtstart = to_local_naive(job["first_run_at"], tz)
                occurrence, following, skipped = latest_due(job["rrule"], dtstart, tz, job["next_run_at"], now)
                if await jobs_repo.insert_run(conn, job["id"], occurrence):
                    stats.materialized += 1
                changes: dict[str, Any] = {"next_run_at": following}
                if following is None:
                    changes["status"] = "completed"
                await jobs_repo.update_job(conn, job["id"], changes)
                if skipped:
                    await profile_repo.audit(conn, job["user_id"], "worker", "job.occurrences_skipped",
                                             "scheduled_job", str(job["id"]), {"skipped": skipped})

    # --------------------------------------------------------------- claim

    async def _claim_runs(self, now: datetime) -> list[UUID]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                update job_runs set status = 'running', claimed_by = %s, lease_expires_at = %s,
                                    attempt = attempt + 1, next_attempt_at = null
                where id in (
                    select id from job_runs
                    where status in ('scheduled', 'retry_scheduled')
                      and coalesce(next_attempt_at, scheduled_for) <= %s
                    order by coalesce(next_attempt_at, scheduled_for)
                    limit %s
                    for update skip locked
                )
                returning id
                """,
                (self.worker_id, now + self.lease, now, self.settings.worker_batch_size),
            )
            return [r["id"] for r in await cur.fetchall()]

    async def _recover_expired_leases(self, now: datetime, stats: WorkerStats) -> None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                select r.id, r.status, r.attempt, j.user_id, j.recipient_display_name as name, j.id as job_id
                from job_runs r join scheduled_jobs j on j.id = r.job_id
                where r.status in ('running', 'sending') and r.lease_expires_at < %s
                for update of r skip locked
                """,
                (now,),
            )
            for run in await cur.fetchall():
                stats.recovered += 1
                if run["status"] == "sending":
                    # We may or may not have delivered it. Never resend blindly.
                    await jobs_repo.update_run(conn, run["id"], {
                        "status": "failed", "failure_reason": "outcome_unknown", "claimed_by": None,
                        "executed_at": now,
                    })
                    await conn.execute(
                        "update outbound_messages set status = 'unknown' where job_run_id = %s and status = 'sending'",
                        (run["id"],),
                    )
                    await self._touch_job(conn, run["job_id"], "failed", now)
                    await notify(conn, run["user_id"], "delivery_failed", f"Check your chat with {run['name']}",
                                 "ECHO lost track of a send. It may or may not have been delivered, so it "
                                 "won't retry automatically.", {"run_id": str(run["id"])})
                elif run["attempt"] < MAX_ATTEMPTS:
                    await jobs_repo.update_run(conn, run["id"], {
                        "status": "retry_scheduled", "claimed_by": None, "next_attempt_at": now,
                    })
                else:
                    await jobs_repo.update_run(conn, run["id"], {
                        "status": "failed", "failure_reason": "worker_lost", "claimed_by": None, "executed_at": now,
                    })
                    await self._touch_job(conn, run["job_id"], "failed", now)

    async def _release_after_crash(self, run_id: UUID, stats: WorkerStats) -> None:
        async with self.pool.connection() as conn:
            await conn.execute(
                """
                update job_runs set status = case when attempt < %s then 'retry_scheduled' else 'failed' end,
                    next_attempt_at = now() + interval '1 minute', failure_reason = 'internal_error',
                    claimed_by = null
                where id = %s and status = 'running' and claimed_by = %s
                """,
                (MAX_ATTEMPTS, run_id, self.worker_id),
            )

    # ------------------------------------------------------------- process

    async def _load(self, run_id: UUID) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                select r.id as run_id, r.status, r.attempt, r.scheduled_for, r.idempotency_key, r.generated_text,
                       r.safety_flags, r.approved_by_user, r.claimed_by,
                       j.id as job_id, j.user_id, j.instruction, j.status as job_status, j.automation_enabled,
                       f.id as friend_id, f.igsid, f.display_name, f.relationship_preset as style, f.language,
                       f.ai_reply_enabled, f.consent_status, f.last_inbound_message_at, f.manual_override_until,
                       f.disclosure_sent_at,
                       a.ig_user_id, a.status as account_status,
                       u.automation_paused, u.ai_disclosure_text, u.default_timezone
                from job_runs r
                join scheduled_jobs j on j.id = r.job_id
                join friends f on f.id = j.friend_id
                join instagram_accounts a on a.id = f.instagram_account_id
                join app_users u on u.id = j.user_id
                where r.id = %s
                """,
                (run_id,),
            )
            return await cur.fetchone()

    async def _process(self, run_id: UUID, stats: WorkerStats) -> None:
        b = await self._load(run_id)
        if b is None or b["status"] != "running" or b["claimed_by"] != self.worker_id:
            return
        now = self.clock()
        name = b["display_name"]

        # 1. Job paused/cancelled after this run was queued.
        if b["job_status"] in ("paused", "cancelled"):
            await self._finish(b, RunStatus.CANCELLED, now, blocked_reason=f"job_{b['job_status']}")
            stats.cancelled += 1
            return

        # 2. Never send stale messages.
        if b["attempt"] == 1 and not b["approved_by_user"] and now - b["scheduled_for"] > LATE_LIMIT:
            await self._block(b, now, ["late_dispatch"], None, stats)
            return

        # 3. Emergency stop: don't even spend tokens on a draft.
        if b["automation_paused"]:
            await self._block(b, now, [BlockReason.GLOBAL_PAUSE], None, stats)
            return

        # 4. Draft (reused on retries so the text never changes between attempts).
        text: str | None = b["generated_text"]
        flags = set(b["safety_flags"] or [])
        if text is None:
            try:
                composed = await self._compose(b, now)
            except AIError as exc:
                await self._retry_or_fail(b, now, f"draft_failed: {exc}", stats)
                return
            text = composed.text
            flags = normalise_flags(composed.flags) | keyword_flags(b["instruction"], text)
            if not fits_instagram(text):
                flags.add("too_long")
            async with self.pool.connection() as conn:
                await jobs_repo.update_run(conn, b["run_id"], {
                    "generated_text": text, "draft_language": composed.language, "safety_flags": sorted(flags),
                }, owner=self.worker_id)

        # 5. Safety gate (you can approve a paused draft from the app).
        if not b["automation_enabled"]:
            await self._needs_attention(b, now, "automation_off", text, stats)
            return
        if flags and not b["approved_by_user"]:
            reason = sorted(flags)[0]
            await self._needs_attention(b, now, reason, text, stats)
            return

        # 6. Deterministic Meta eligibility, immediately before dispatch.
        eligibility = evaluate_send_eligibility(EligibilityInput(
            now=now,
            last_inbound_at=b["last_inbound_message_at"],
            consent_status=b["consent_status"],
            friend_ai_enabled=b["ai_reply_enabled"],
            global_pause=b["automation_paused"],
            account_connected=b["account_status"] == "active",
            manual_override_until=b["manual_override_until"],
        ))
        if not eligibility.eligible:
            await self._block(b, now, list(eligibility.reasons), eligibility.snapshot(), stats, draft=text)
            return

        # 7. Send.
        await self._send(b, now, text, eligibility.snapshot(), stats)

    async def _compose(self, b: dict, now: datetime):
        async with self.pool.connection() as conn:
            traits = await profile_repo.list_traits(conn, b["user_id"], limit=30)
            examples = await profile_repo.examples_for_context(conn, b["user_id"], b["language"] or None, 4)
            thread = await messaging_repo.recent_thread(conn, b["friend_id"], 12)
        context = build_compose_context(
            traits=traits,
            examples=examples,
            friend={"display_name": b["display_name"], "style": b["style"], "language": b["language"]},
            thread=thread,
            now=now,
            timezone=b["default_timezone"],
        )
        return await self.ai.compose_message(context, b["instruction"])

    async def _send(self, b: dict, now: datetime, text: str, snapshot: dict, stats: WorkerStats) -> None:
        key = b["idempotency_key"]
        async with self.pool.connection() as conn:
            outbound = await jobs_repo.reserve_outbound(
                conn, user_id=b["user_id"], friend_id=b["friend_id"], idempotency_key=key,
                content=text, job_run_id=b["run_id"],
            )
            if outbound["status"] == "unknown":
                await self._fail(b, now, "outcome_unknown", stats, conn=conn)
                return
            if outbound["status"] == "sent":
                # Delivered on an earlier attempt; just record it.
                await self._finish(b, RunStatus.SENT, now, conn=conn, eligibility=snapshot)
                stats.sent += 1
                return
            if not await jobs_repo.update_run(conn, b["run_id"], {"status": "sending", "eligibility": snapshot},
                                              owner=self.worker_id):
                return
            needs_disclosure = (b["disclosure_sent_at"] is None
                                or now - b["disclosure_sent_at"] > DISCLOSURE_REFRESH)

        try:
            if needs_disclosure and b["ai_disclosure_text"]:
                disclosure = await self.messaging.send_text(
                    account_ig_id=b["ig_user_id"], recipient_igsid=b["igsid"],
                    text=b["ai_disclosure_text"], idempotency_key=f"{key}:disclosure",
                )
                async with self.pool.connection() as conn:
                    await messaging_repo.record_outbound(
                        conn, user_id=b["user_id"], friend_id=b["friend_id"], text=b["ai_disclosure_text"],
                        mid=disclosure.message_id, sent_by="echo_scheduled", at=self.clock(),
                    )
                    await conn.execute("update friends set disclosure_sent_at = %s where id = %s",
                                       (self.clock(), b["friend_id"]))
            result = await self.messaging.send_text(
                account_ig_id=b["ig_user_id"], recipient_igsid=b["igsid"], text=text, idempotency_key=key,
            )
        except TransientSendError as exc:
            async with self.pool.connection() as conn:
                await jobs_repo.finish_outbound(conn, outbound["id"], status="failed", error=str(exc))
                await jobs_repo.update_run(conn, b["run_id"], {"status": "running"}, owner=self.worker_id)
            await self._retry_or_fail(b, now, f"send_failed: {exc}", stats)
            return
        except PermanentSendError as exc:
            async with self.pool.connection() as conn:
                await jobs_repo.finish_outbound(conn, outbound["id"], status="failed", error=str(exc))
                await self._fail(b, now, f"rejected: {exc}", stats, conn=conn)
            return
        except UnknownOutcome as exc:
            async with self.pool.connection() as conn:
                await jobs_repo.finish_outbound(conn, outbound["id"], status="unknown", error=str(exc))
                await self._fail(b, now, "outcome_unknown", stats, conn=conn)
            return

        sent_at = self.clock()
        async with self.pool.connection() as conn:
            await jobs_repo.finish_outbound(conn, outbound["id"], status="sent", ig_message_id=result.message_id)
            await messaging_repo.record_outbound(
                conn, user_id=b["user_id"], friend_id=b["friend_id"], text=text, mid=result.message_id,
                sent_by="echo_scheduled", at=sent_at,
            )
            await self._finish(b, RunStatus.SENT, sent_at, conn=conn, eligibility=snapshot)
            await profile_repo.audit(conn, b["user_id"], "worker", "scheduled_message.sent", "job_run",
                                     str(b["run_id"]), {"message_id": result.message_id})
            await notify(conn, b["user_id"], "message_sent", f"Sent to {b['display_name']}", text,
                         {"job_id": str(b["job_id"]), "run_id": str(b["run_id"])})
        stats.sent += 1

    # ------------------------------------------------------------- outcomes

    async def _finish(self, b: dict, status: RunStatus, now: datetime, *, conn=None, **fields: Any) -> None:
        changes: dict[str, Any] = {"status": status.value, "executed_at": now, "claimed_by": None, **fields}
        if conn is None:
            async with self.pool.connection() as c:
                await self._finish(b, status, now, conn=c, **fields)
            return
        await jobs_repo.update_run(conn, b["run_id"], changes)
        await self._touch_job(conn, b["job_id"], status.value, now)

    async def _touch_job(self, conn, job_id: UUID, status: str, now: datetime) -> None:
        await conn.execute(
            """
            update scheduled_jobs set last_run_at = %s, last_run_status = %s, run_count = run_count + 1
            where id = %s
            """,
            (now, status, job_id),
        )

    async def _block(self, b: dict, now: datetime, reasons: list, snapshot: dict | None,
                     stats: WorkerStats, draft: str | None = None) -> None:
        codes = [str(getattr(r, "value", r)) for r in reasons]
        snapshot = snapshot or {"eligible": False, "reasons": codes, "window_expires_at": None}
        human = "; ".join(BLOCK_TEXT.get(r, str(r)).format(name=b["display_name"]) for r in reasons)
        async with self.pool.connection() as conn:
            await self._finish(b, RunStatus.BLOCKED, now, conn=conn, eligibility=snapshot,
                               blocked_reason=",".join(codes))
            body = f"Not sent because {human}."
            if draft:
                body += " Your draft is ready to send yourself in Instagram."
            await notify(conn, b["user_id"], "job_blocked", f"Message to {b['display_name']} blocked", body,
                         {"job_id": str(b["job_id"]), "run_id": str(b["run_id"])})
            await profile_repo.audit(conn, b["user_id"], "worker", "scheduled_message.blocked", "job_run",
                                     str(b["run_id"]), {"reasons": codes})
        stats.blocked += 1

    async def _needs_attention(self, b: dict, now: datetime, reason: str, draft: str, stats: WorkerStats) -> None:
        async with self.pool.connection() as conn:
            await self._finish(b, RunStatus.NEEDS_ATTENTION, now, conn=conn, blocked_reason=reason)
            await jobs_repo.open_review(conn, user_id=b["user_id"], friend_id=b["friend_id"],
                                        run_id=b["run_id"], reason_code=reason, draft=draft)
            await notify(
                conn, b["user_id"], "needs_attention", f"Review message to {b['display_name']}",
                f"ECHO paused this message because it {REASON_TEXT.get(reason, reason)}.",
                {"job_id": str(b["job_id"]), "run_id": str(b["run_id"])},
            )
        stats.needs_attention += 1

    async def _retry_or_fail(self, b: dict, now: datetime, reason: str, stats: WorkerStats) -> None:
        if b["attempt"] < MAX_ATTEMPTS:
            assert can_transition(RunStatus.RUNNING, RunStatus.RETRY_SCHEDULED)
            async with self.pool.connection() as conn:
                await jobs_repo.update_run(conn, b["run_id"], {
                    "status": "retry_scheduled",
                    "next_attempt_at": now + timedelta(seconds=retry_delay_seconds(b["attempt"])),
                    "failure_reason": reason[:300],
                    "claimed_by": None,
                }, owner=self.worker_id)
            stats.retried += 1
        else:
            await self._fail(b, now, reason, stats)

    async def _fail(self, b: dict, now: datetime, reason: str, stats: WorkerStats, *, conn=None) -> None:
        if conn is None:
            async with self.pool.connection() as c:
                await self._fail(b, now, reason, stats, conn=c)
            return
        await self._finish(b, RunStatus.FAILED, now, conn=conn, failure_reason=reason[:300])
        body = ("Instagram may or may not have received it — check the chat before resending."
                if reason == "outcome_unknown" else f"Delivery failed ({reason[:120]}).")
        await notify(conn, b["user_id"], "delivery_failed", f"Couldn't send to {b['display_name']}", body,
                     {"job_id": str(b["job_id"]), "run_id": str(b["run_id"])})
        stats.failed += 1
