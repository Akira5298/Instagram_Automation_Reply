"""Scheduler worker against real PostgreSQL with a controllable clock."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from psycopg.rows import dict_row

from app.ai.mock_provider import MockAIProvider
from app.db import messaging_repo
from app.db.pool import create_pool, ensure_owner
from app.messaging.provider import MockMessagingProvider
from app.services.scheduler_service import JobRequest, create_job
from app.worker.runner import Worker
from tests.conftest import make_settings, requires_db

pytestmark = requires_db

OWNER = UUID("00000000-0000-0000-0000-000000000001")
T0 = datetime(2026, 11, 12, 12, 0, tzinfo=UTC)  # 8 PM in Kuala Lumpur


class Clock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kw) -> None:
        self.now += timedelta(**kw)


class CountingAI(MockAIProvider):
    def __init__(self):
        self.compose_calls = 0

    async def compose_message(self, context_block, instruction):
        self.compose_calls += 1
        return await super().compose_message(context_block, instruction)


@pytest.fixture
async def env(db_url):
    pool = create_pool(db_url, 1, 4)
    await pool.open(wait=True)
    await ensure_owner(pool, OWNER, "Asia/Kuala_Lumpur")
    clock = Clock(T0 - timedelta(hours=1))
    ai = CountingAI()
    messaging = MockMessagingProvider()
    settings = make_settings()
    worker = Worker(pool=pool, ai=ai, messaging=messaging, settings=settings, clock=clock, worker_id="w1")

    async def add_friend(*, consent="granted", ai_on=True, inbound_hours_ago: float | None = 1.0, name="Kenji Tanaka"):
        async with pool.connection() as conn:
            account = await messaging_repo.ensure_mock_account(conn, OWNER)
            friend = await messaging_repo.upsert_friend(
                conn, user_id=OWNER, account_id=account["id"], igsid=f"igsid-{name}", display_name=name,
                username=name.split()[0].lower(), style="close_friend", language="en",
                ai_reply_enabled=ai_on, consent=consent,
            )
            if inbound_hours_ago is not None:
                await messaging_repo.record_inbound(
                    conn, user_id=OWNER, friend_id=friend["id"], text="hey!", mid=f"in-{name}",
                    at=T0 - timedelta(hours=inbound_hours_ago),
                )
            return friend

    async def add_job(friend, *, at=T0, recurrence="once", instruction="ask if he's free this weekend",
                      automation=True):
        async with pool.connection() as conn:
            return await create_job(conn, OWNER, JobRequest(
                friend_id=friend["id"], scheduled_at=at, timezone="Asia/Kuala_Lumpur", recurrence=recurrence,
                instruction=instruction, automation_enabled=automation,
            ), now=clock())

    async def query(sql, *params):
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(sql, params)
            return await cur.fetchall()

    class Env:
        pass

    e = Env()
    e.pool, e.clock, e.ai, e.messaging, e.worker = pool, clock, ai, messaging, worker
    e.add_friend, e.add_job, e.query, e.settings = add_friend, add_job, query, settings
    yield e
    await pool.close()


async def runs_for(env, job_id):
    return await env.query("select * from job_runs where job_id = %s order by scheduled_for", job_id)


async def test_nothing_happens_before_due(env):
    friend = await env.add_friend()
    await env.add_job(friend)
    env.clock.now = T0 - timedelta(minutes=1)
    stats = await env.worker.run_once()
    assert stats.materialized == 0 and env.messaging.sent == []


async def test_sends_when_eligible_with_disclosure_first(env):
    friend = await env.add_friend()
    job_id = await env.add_job(friend)
    env.clock.now = T0 + timedelta(seconds=10)
    stats = await env.worker.run_once()

    assert stats.sent == 1
    texts = [s.text for s in env.messaging.sent]
    assert texts[0].startswith("Some replies in this chat may be written by my AI assistant")
    assert "free this weekend" in texts[1]
    run = (await runs_for(env, job_id))[0]
    assert run["status"] == "sent" and run["eligibility"]["eligible"] is True
    job = (await env.query("select * from scheduled_jobs where id = %s", job_id))[0]
    assert job["status"] == "completed" and job["last_run_status"] == "sent"
    outbound = await env.query("select sent_by from ig_messages where direction = 'outbound'")
    assert [o["sent_by"] for o in outbound] == ["echo_scheduled", "echo_scheduled"]
    notes = await env.query("select category, status from notification_outbox")
    assert notes == [{"category": "message_sent", "status": "sent"}]

    # Disclosure isn't repeated for the next message.
    job2 = await env.add_job(friend, at=T0 + timedelta(hours=1))
    env.clock.now = T0 + timedelta(hours=1, seconds=5)
    await env.worker.run_once()
    assert len(env.messaging.sent) == 3
    assert (await runs_for(env, job2))[0]["status"] == "sent"


async def test_blocked_outside_24h_window_keeps_draft(env):
    friend = await env.add_friend(inbound_hours_ago=30)
    job_id = await env.add_job(friend)
    env.clock.now = T0 + timedelta(seconds=5)
    stats = await env.worker.run_once()

    assert stats.blocked == 1 and env.messaging.sent == []
    run = (await runs_for(env, job_id))[0]
    assert run["status"] == "blocked"
    assert "outside_standard_window" in run["eligibility"]["reasons"]
    assert run["generated_text"], "a draft is kept so you can send it yourself"
    note = (await env.query("select category, body from notification_outbox"))[0]
    assert note["category"] == "job_blocked" and "last 24 hours" in note["body"]


async def test_never_messaged_and_no_consent(env):
    friend = await env.add_friend(consent="requested", inbound_hours_ago=None)
    job_id = await env.add_job(friend)
    env.clock.now = T0 + timedelta(seconds=5)
    await env.worker.run_once()
    reasons = (await runs_for(env, job_id))[0]["eligibility"]["reasons"]
    assert set(reasons) == {"no_consent", "no_inbound_message"}


async def test_money_needs_attention_then_approval_sends(env):
    friend = await env.add_friend()
    job_id = await env.add_job(friend, instruction="ask him to pay me back the RM40 for tickets")
    env.clock.now = T0 + timedelta(seconds=5)
    stats = await env.worker.run_once()
    assert stats.needs_attention == 1 and env.messaging.sent == []
    run = (await runs_for(env, job_id))[0]
    assert run["status"] == "needs_attention" and run["blocked_reason"] == "money"
    reviews = await env.query("select status, reason_code from review_items")
    assert reviews == [{"status": "open", "reason_code": "money"}]

    # You approve it in the app ("send anyway"): eligibility still applies.
    async with env.pool.connection() as conn:
        await conn.execute(
            "update job_runs set status = 'retry_scheduled', approved_by_user = true, next_attempt_at = %s where id = %s",
            (env.clock(), run["id"]),
        )
    stats = await env.worker.run_once()
    assert stats.sent == 1
    assert env.ai.compose_calls == 1, "the approved draft is sent unchanged"
    assert "pay me back" in env.messaging.sent[-1].text


async def test_draft_only_job(env):
    friend = await env.add_friend()
    job_id = await env.add_job(friend, automation=False)
    env.clock.now = T0 + timedelta(seconds=5)
    await env.worker.run_once()
    run = (await runs_for(env, job_id))[0]
    assert run["status"] == "needs_attention" and run["blocked_reason"] == "automation_off"
    assert env.messaging.sent == []


async def test_global_pause_blocks_without_spending_tokens(env):
    friend = await env.add_friend()
    job_id = await env.add_job(friend)
    async with env.pool.connection() as conn:
        await conn.execute("update app_users set automation_paused = true where id = %s", (OWNER,))
    env.clock.now = T0 + timedelta(seconds=5)
    await env.worker.run_once()
    run = (await runs_for(env, job_id))[0]
    assert run["status"] == "blocked" and run["blocked_reason"] == "global_pause"
    assert env.ai.compose_calls == 0 and env.messaging.sent == []


async def test_transient_failure_retries_with_backoff(env):
    friend = await env.add_friend()
    job_id = await env.add_job(friend)
    env.messaging.set_failure_mode("transient_once")
    env.clock.now = T0 + timedelta(seconds=5)
    stats = await env.worker.run_once()
    assert stats.retried == 1
    run = (await runs_for(env, job_id))[0]
    assert run["status"] == "retry_scheduled"
    assert run["next_attempt_at"] == env.clock() + timedelta(seconds=30)

    env.clock.advance(seconds=10)
    assert (await env.worker.run_once()).processed == 0, "respects backoff"
    env.clock.advance(seconds=25)
    stats = await env.worker.run_once()
    assert stats.sent == 1
    run = (await runs_for(env, job_id))[0]
    assert run["status"] == "sent" and run["attempt"] == 2


async def test_permanent_failure(env):
    friend = await env.add_friend()
    job_id = await env.add_job(friend)
    env.messaging.set_failure_mode("permanent")
    env.clock.now = T0 + timedelta(seconds=5)
    stats = await env.worker.run_once()
    assert stats.failed == 1
    assert (await runs_for(env, job_id))[0]["status"] == "failed"
    cats = [n["category"] for n in await env.query("select category from notification_outbox")]
    assert cats == ["delivery_failed"]


async def test_unknown_outcome_is_never_retried(env):
    friend = await env.add_friend()
    async with env.pool.connection() as conn:  # skip disclosure for clarity
        await conn.execute("update friends set disclosure_sent_at = %s", (T0,))
    job_id = await env.add_job(friend)
    env.messaging.set_failure_mode("unknown")
    env.clock.now = T0 + timedelta(seconds=5)
    await env.worker.run_once()
    run = (await runs_for(env, job_id))[0]
    assert run["status"] == "failed" and run["failure_reason"] == "outcome_unknown"
    env.messaging.set_failure_mode("none")
    env.clock.advance(hours=1)
    await env.worker.run_once()
    assert env.messaging.sent == []
    outbound = await env.query("select status from outbound_messages")
    assert outbound == [{"status": "unknown"}]


async def test_overdue_runs_are_blocked_not_sent(env):
    friend = await env.add_friend()
    job_id = await env.add_job(friend)
    env.clock.now = T0 + timedelta(minutes=20)  # server was down
    await env.worker.run_once()
    run = (await runs_for(env, job_id))[0]
    assert run["status"] == "blocked" and run["blocked_reason"] == "late_dispatch"
    assert env.messaging.sent == []


async def test_daily_job_runs_each_day_and_skips_backlog(env):
    friend = await env.add_friend(inbound_hours_ago=0.5)
    job_id = await env.add_job(friend, recurrence="daily")
    env.clock.now = T0 + timedelta(seconds=5)
    await env.worker.run_once()
    # Keep the window open: the friend replies every day.
    for day in (1, 2):
        async with env.pool.connection() as conn:
            await messaging_repo.record_inbound(conn, user_id=OWNER, friend_id=friend["id"], text="ok",
                                                mid=f"in-day{day}", at=T0 + timedelta(days=day, hours=-1))
        env.clock.now = T0 + timedelta(days=day, seconds=5)
        await env.worker.run_once()
    runs = await runs_for(env, job_id)
    assert [r["status"] for r in runs] == ["sent", "sent", "sent"]
    assert [r["scheduled_for"].astimezone(UTC).hour for r in runs] == [12, 12, 12]

    # Worker offline for 4 days: only the latest occurrence is considered (and it's late → blocked).
    env.clock.now = T0 + timedelta(days=6, minutes=30)
    await env.worker.run_once()
    runs = await runs_for(env, job_id)
    assert len(runs) == 4 and runs[-1]["status"] == "blocked"
    skipped = await env.query("select details from audit_log where action = 'job.occurrences_skipped'")
    assert skipped[0]["details"]["skipped"] == 3
    job = (await env.query("select next_run_at, status from scheduled_jobs where id = %s", job_id))[0]
    assert job["status"] == "active" and job["next_run_at"] == T0 + timedelta(days=7)


async def test_paused_job_cancels_queued_run(env):
    friend = await env.add_friend()
    job_id = await env.add_job(friend)
    env.clock.now = T0 + timedelta(seconds=5)
    # Materialise the run, then pause the job before the run is processed.
    async with env.pool.connection() as conn:
        await conn.execute("insert into job_runs (job_id, scheduled_for, idempotency_key) values (%s, %s, 'k')",
                           (job_id, T0))
        await conn.execute("update scheduled_jobs set status = 'paused', next_run_at = null where id = %s", (job_id,))
    await env.worker.run_once()
    run = (await runs_for(env, job_id))[0]
    assert run["status"] == "cancelled" and run["blocked_reason"] == "job_paused"


async def test_crashed_send_is_not_resent(env):
    friend = await env.add_friend()
    job_id = await env.add_job(friend)
    async with env.pool.connection() as conn:
        await conn.execute(
            """insert into job_runs (job_id, scheduled_for, idempotency_key, status, claimed_by, lease_expires_at, attempt)
               values (%s, %s, 'k2', 'sending', 'dead-worker', %s, 1)""",
            (job_id, T0, T0),
        )
        await conn.execute("update scheduled_jobs set next_run_at = null, status = 'completed' where id = %s", (job_id,))
    env.clock.now = T0 + timedelta(minutes=10)
    stats = await env.worker.run_once()
    assert stats.recovered == 1
    run = (await runs_for(env, job_id))[0]
    assert run["status"] == "failed" and run["failure_reason"] == "outcome_unknown"
    assert env.messaging.sent == []


async def test_two_workers_never_double_send(env):
    friend = await env.add_friend()
    for i in range(5):
        f = await env.add_friend(name=f"Friend {i}")
        await env.add_job(f)
    await env.add_job(friend)
    env.clock.now = T0 + timedelta(seconds=5)
    other = Worker(pool=env.pool, ai=env.ai, messaging=env.messaging, settings=env.settings,
                   clock=env.clock, worker_id="w2")
    await asyncio.gather(env.worker.run_once(), other.run_once(), env.worker.run_once())
    keys = [s.idempotency_key for s in env.messaging.sent if not s.idempotency_key.endswith(":disclosure")]
    assert len(keys) == 6 == len(set(keys))
    statuses = await env.query("select status, count(*) from job_runs group by status")
    assert statuses == [{"status": "sent", "count": 6}]
