"""One assistant-chat turn, end to end.

  1. Store your message (idempotent on client_msg_id). If a reply already
     exists for it — a retry after a dropped connection — return that reply.
  2. Build compact context: profile traits, rolling summary, recent turns,
     approved writing examples.
  3. Ask the AI provider. No database transaction is held during this call.
  4. In one transaction: store the reply (unique per user message), merge
     the validated memory updates into the profile, write an audit entry.
  5. After the response is sent: summarise older turns if needed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from psycopg_pool import AsyncConnectionPool

from app.ai.context import build_context_block, build_messages, transcript
from app.ai.provider import AIProvider
from app.core.config import Settings
from app.db import chat_repo, messaging_repo, profile_repo
from app.domain.memory import merge, sanitize
from app.domain.recurrence import describe
from app.services.scheduler_service import JobRequest, ScheduleError, create_job

log = logging.getLogger("echo.chat")


@dataclass(frozen=True)
class TurnResult:
    user_message: dict[str, Any]
    assistant_message: dict[str, Any]
    replayed: bool


async def run_turn(
    *,
    pool: AsyncConnectionPool,
    provider: AIProvider,
    settings: Settings,
    user_id: UUID,
    client_msg_id: UUID,
    text: str,
) -> TurnResult:
    # 1. Store the user message.
    async with pool.connection() as conn:
        user_row, _created = await chat_repo.insert_user_message(conn, user_id, client_msg_id, text)
        existing = await chat_repo.reply_to(conn, user_row["id"])
    if existing is not None:
        return TurnResult(user_row, existing, replayed=True)

    # 2. Gather context.
    async with pool.connection() as conn:
        traits = await profile_repo.list_traits(conn, user_id, limit=settings.context_max_traits)
        rejected = await profile_repo.rejected_keys(conn, user_id)
        summary = await chat_repo.latest_summary(conn, user_id)
        history = await chat_repo.recent_for_context(
            conn, user_id, limit=settings.context_recent_messages, exclude_id=user_row["id"]
        )
        last_language = next((h.get("language") for h in reversed(history) if h.get("language")), None)
        examples = await profile_repo.examples_for_context(
            conn, user_id, last_language, settings.context_max_examples
        )
        friends = await messaging_repo.list_friends(conn, user_id)

    context_block = build_context_block(
        traits=traits,
        rejected_keys=rejected,
        summary=summary["summary"] if summary else None,
        examples=examples,
        now=datetime.now(UTC),
        timezone=settings.default_timezone,
        friends=friends,
    )
    messages = build_messages(history, user_row["content"])

    # 3. Ask the model (errors propagate; the user message stays stored so a
    #    retry with the same client_msg_id picks up from here).
    turn = await provider.chat_turn(context_block, messages)

    # 4. Persist reply + memory in one transaction.
    proposals = [p for p in sanitize(turn.memory_updates) if p.key not in set(rejected)]
    async with pool.connection() as conn:
        locked = await profile_repo.lock_traits(conn, user_id, [p.key for p in proposals])
        applied: list[dict] = []
        for proposal in proposals:
            new_state = merge(locked.get(proposal.key), proposal)
            if new_state is None:
                continue
            await profile_repo.upsert_trait(conn, user_id, new_state)
            locked[proposal.key] = new_state
            applied.append({
                "id": str(uuid4()),
                "op": proposal.op,
                "category": new_state.category,
                "key": new_state.key,
                "summary": new_state.value or proposal.value,
                "kind": proposal.kind,
                "confidence": new_state.confidence,
            })

        actions = await _apply_schedule_requests(conn, user_id, turn.schedule_requests, friends, settings)

        assistant_row, created = await chat_repo.insert_assistant_message(
            conn,
            user_id=user_id,
            in_reply_to=user_row["id"],
            text=turn.text,
            language=turn.language,
            memory_updates=applied,
            actions=actions,
            model=turn.model,
            input_tokens=turn.usage.input_tokens,
            output_tokens=turn.usage.output_tokens,
            cache_read_tokens=turn.usage.cache_read_tokens,
        )
        if not created:
            # A concurrent retry won the race; undo our profile writes.
            await conn.rollback()
            return TurnResult(user_row, assistant_row, replayed=True)

        await chat_repo.set_user_message_language(conn, user_row["id"], turn.language)
        user_row = {**user_row, "language": turn.language}
        if applied:
            await profile_repo.audit(
                conn, user_id, "echo", "profile.learned", "chat_message", str(assistant_row["id"]),
                {"keys": [a["key"] for a in applied]},
            )

    return TurnResult(user_row, assistant_row, replayed=False)


def match_friend(friends: list[dict], name: str) -> dict | None:
    """Resolve the name the model heard to exactly one friend, or None."""
    needle = " ".join(name.lower().split()).lstrip("@")
    if not needle:
        return None
    checks = (
        lambda f: f["display_name"].lower() == needle or (f.get("username") or "").lower() == needle,
        lambda f: f["display_name"].lower().split()[0] == needle,
        lambda f: f["display_name"].lower().startswith(needle),
    )
    for check in checks:
        hits = [f for f in friends if check(f)]
        if len(hits) == 1:
            return hits[0]
        if len(hits) > 1:
            return None
    return None


async def _apply_schedule_requests(
    conn, user_id: UUID, requests: list[dict], friends: list[dict], settings: Settings
) -> list[dict]:
    """Deterministically validate what the model extracted and create the job.
    The model never creates jobs itself; this code decides."""
    actions: list[dict] = []
    for raw in requests[:1]:  # one schedule per turn keeps confirmations clear
        try:
            friend = match_friend(friends, str(raw.get("friend_name", "")))
            if friend is None:
                raise ScheduleError(f"I couldn't tell which friend \"{raw.get('friend_name', '')}\" is.")
            try:
                local = datetime.fromisoformat(str(raw.get("local_datetime", "")))
            except ValueError as exc:
                raise ScheduleError("I couldn't work out the time.") from exc
            recurrence = str(raw.get("recurrence") or "once")
            async with conn.transaction():  # savepoint: a bad request mustn't abort the turn
                job_id = await create_job(conn, user_id, JobRequest(
                    friend_id=friend["id"],
                    scheduled_at=local.replace(tzinfo=None),
                    timezone=settings.default_timezone,
                    recurrence=recurrence,
                    instruction=str(raw.get("instruction") or ""),
                    automation_enabled=bool(raw.get("send_automatically", True)),
                    created_via="chat",
                ), now=datetime.now(UTC))
            cur = await conn.execute("select next_run_at from scheduled_jobs where id = %s", (job_id,))
            row = await cur.fetchone()
            actions.append({
                "type": "scheduled_job",
                "job_id": str(job_id),
                "recipient_name": friend["display_name"],
                "scheduled_at": row["next_run_at"].isoformat(),
                "time_zone_id": settings.default_timezone,
                "recurrence_title": describe(recurrence, None),
                "automation_enabled": bool(raw.get("send_automatically", True)),
                "instruction": str(raw.get("instruction") or ""),
            })
        except ScheduleError as exc:
            actions.append({"type": "schedule_failed", "reason": str(exc)})
    return actions


async def maybe_summarize(
    *, pool: AsyncConnectionPool, provider: AIProvider, settings: Settings, user_id: UUID
) -> bool:
    """Fold older turns into the rolling summary. Safe to call after every
    turn: it only does work past the threshold and only one runs per user."""
    try:
        async with pool.connection() as conn:
            if not await chat_repo.try_lock_user(conn, user_id, "summarize"):
                return False
            latest = await chat_repo.latest_summary(conn, user_id)
            pending = await chat_repo.messages_after(conn, user_id, latest["covers_until"] if latest else None)
            if len(pending) < settings.summary_trigger_messages:
                return False
            to_fold = pending[: len(pending) - settings.summary_keep_recent]
            result = await provider.summarize(latest["summary"] if latest else None, transcript(to_fold))
            if not result.text:
                return False
            await chat_repo.insert_summary(
                conn,
                user_id=user_id,
                summary=result.text,
                covers_until=to_fold[-1]["created_at"],
                message_count=len(to_fold) + (latest["message_count"] if latest else 0),
                model=result.model,
                input_tokens=result.usage.input_tokens,
                output_tokens=result.usage.output_tokens,
            )
        log.info("summarised chat", extra={"fields": {"user_id": str(user_id), "folded": len(to_fold)}})
        return True
    except Exception:  # background task: log, never crash the server
        log.exception("summarisation failed")
        return False
