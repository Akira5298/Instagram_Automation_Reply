"""Autonomous replies to inbound DMs (Phase 5).

Each tick the worker claims due auto replies (`FOR UPDATE SKIP LOCKED`, with a
lease) and runs every gate in order. Cheap deterministic checks come before
the Claude call, so no tokens are spent on replies that can't be sent.

  1. Stale?           a newer message arrived → superseded (the newer one is queued)
                      someone already replied after it → skipped
  2. Allowed at all?  global pause, AI off, no consent, you took over → skipped
                      older than 2 hours (server was offline) → blocked + notify
  3. Daily limits     per friend and per account → blocked + notify
  4. Draft            Claude decides reply / no_reply / needs_user
  5. Safety gate      model flags + deterministic keyword pass on the friend's
                      messages and the draft; attachments ECHO can't see;
                      "review first" friends → needs_attention + review item
  6. Stale? (again)   drafting takes seconds; re-check before sending
  7. Meta eligibility evaluate_send_eligibility() right before sending
  8. Send             disclosure if due, then the reply (one per inbound mid)

Retries: transient AI or Instagram errors back off (30 s → 15 min, 5 tries),
re-running every gate. An unknown delivery outcome is never retried.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.ai.compose_context import build_compose_context
from app.ai.provider import AIError, AIProvider
from app.core.config import Settings
from app.db import messaging_repo, profile_repo
from app.domain.job_state import MAX_ATTEMPTS, retry_delay_seconds
from app.domain.messaging_window import (
    BlockReason,
    EligibilityInput,
    evaluate_send_eligibility,
)
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
from app.services.delivery import Recipient, deliver
from app.services.notifications import notify

log = logging.getLogger("echo.auto_reply")

MAX_REPLY_AGE = timedelta(hours=2)   # don't answer messages the server missed hours ago
_ATTACHMENT = "attachment"

SKIP_TEXT = {
    "superseded": "a newer message arrived",
    "you_replied": "you replied yourself",
    "already_replied": "a reply was already sent",
    "global_pause": "automation is paused",
    "ai_off": "AI replies are off for this friend",
    "no_consent": "they haven't agreed to AI-assisted replies",
    "you_took_over": "you've taken over this conversation",
    "no_reply_needed": "nothing needed an answer",
}

BLOCK_TEXT = {
    "too_late": "the message arrived while ECHO was offline, over 2 hours ago",
    "daily_limit": "the daily auto-reply limit was reached",
    BlockReason.OUTSIDE_STANDARD_WINDOW.value: "Instagram's 24-hour reply window has closed",
    BlockReason.ACCOUNT_DISCONNECTED.value: "Instagram isn't connected",
    BlockReason.NO_INBOUND_MESSAGE.value: "there's no message to reply to",
}


@dataclass
class ReplyStats:
    claimed: int = 0
    sent: int = 0
    held: int = 0        # needs your attention
    skipped: int = 0
    blocked: int = 0
    retried: int = 0
    failed: int = 0
    recovered: int = 0


class AutoReplyProcessor:
    def __init__(self, *, pool: AsyncConnectionPool, ai: AIProvider, messaging: MessagingProvider,
                 settings: Settings, clock: Callable[[], datetime], worker_id: str,
                 lease: timedelta = timedelta(minutes=5)) -> None:
        self.pool = pool
        self.ai = ai
        self.messaging = messaging
        self.settings = settings
        self.clock = clock
        self.worker_id = worker_id
        self.lease = lease

    async def run(self, now: datetime) -> ReplyStats:
        stats = ReplyStats()
        await self._recover(now, stats)
        for reply_id in await self._claim(now):
            stats.claimed += 1
            try:
                await self._process(reply_id, stats)
            except Exception:
                log.exception("auto reply crashed", extra={"fields": {"auto_reply_id": str(reply_id)}})
                await self._retry_or_fail_id(reply_id, "internal_error", stats)
        return stats

    # ----------------------------------------------------------- claiming

    async def _claim(self, now: datetime) -> list[UUID]:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                update auto_replies set status = 'running', claimed_by = %s, lease_expires_at = %s,
                                        attempt = attempt + 1, next_attempt_at = null
                where id in (
                    select id from auto_replies
                    where status = 'pending' and coalesce(next_attempt_at, due_at) <= %s
                    order by coalesce(next_attempt_at, due_at)
                    limit %s
                    for update skip locked
                )
                returning id
                """,
                (self.worker_id, now + self.lease, now, self.settings.worker_batch_size),
            )
            return [r["id"] for r in await cur.fetchall()]

    async def _recover(self, now: datetime, stats: ReplyStats) -> None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                select r.id, r.status, r.attempt, r.user_id, r.friend_id, f.display_name
                from auto_replies r join friends f on f.id = r.friend_id
                where r.status in ('running', 'sending') and r.lease_expires_at < %s
                for update of r skip locked
                """,
                (now,),
            )
            for row in await cur.fetchall():
                stats.recovered += 1
                if row["status"] == "sending":
                    await self._set(conn, row["id"], status="failed", reason="outcome_unknown", executed_at=now)
                    await notify(conn, row["user_id"], "delivery_failed", f"Check your chat with {row['display_name']}",
                                 "ECHO lost track of a reply. It may or may not have been delivered, so it "
                                 "won't retry automatically.", {"friend_id": str(row["friend_id"])})
                else:
                    await self._requeue(conn, row["id"], row["friend_id"], row["attempt"], now, "worker_lost")

    # ------------------------------------------------------------ process

    async def _load(self, reply_id: UUID) -> dict[str, Any] | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                select r.id, r.status, r.claimed_by, r.attempt, r.inbound_message_id, r.inbound_at,
                       r.generated_text, r.safety_flags, r.draft_language,
                       r.user_id, r.friend_id,
                       f.igsid, f.display_name, f.relationship_preset as style, f.language,
                       f.ai_reply_enabled, f.consent_status, f.last_inbound_message_at, f.manual_override_until,
                       f.disclosure_sent_at, f.auto_reply_require_review, f.auto_reply_daily_limit,
                       a.ig_user_id, a.status as account_status,
                       u.automation_paused, u.ai_disclosure_text, u.default_timezone,
                       u.auto_reply_daily_limit as account_daily_limit
                from auto_replies r
                join friends f on f.id = r.friend_id
                join instagram_accounts a on a.id = f.instagram_account_id
                join app_users u on u.id = r.user_id
                where r.id = %s
                """,
                (reply_id,),
            )
            return await cur.fetchone()

    async def _process(self, reply_id: UUID, stats: ReplyStats) -> None:
        b = await self._load(reply_id)
        if b is None or b["status"] != "running" or b["claimed_by"] != self.worker_id:
            return
        now = self.clock()

        # 1. Stale?
        stale = await self._staleness(b)
        if stale:
            await self._skip(b, now, stale, stats, status="superseded" if stale == "superseded" else "skipped")
            return

        # 2. Allowed at all? (cheap, before any tokens are spent)
        if b["automation_paused"]:
            await self._skip(b, now, "global_pause", stats)
            return
        if not b["ai_reply_enabled"]:
            await self._skip(b, now, "ai_off", stats)
            return
        if b["consent_status"] != "granted":
            await self._skip(b, now, "no_consent", stats)
            return
        if b["manual_override_until"] and b["manual_override_until"] > now:
            await self._skip(b, now, "you_took_over", stats)
            return
        if now - b["inbound_at"] > MAX_REPLY_AGE:
            await self._block(b, now, ["too_late"], None, stats)
            return

        # 3. Daily limits.
        if await self._over_limit(b, now):
            await self._block(b, now, ["daily_limit"], None, stats)
            return

        # 4. Draft (reused on retries so the text doesn't change between attempts).
        text: str | None = b["generated_text"]
        flags = set(b["safety_flags"] or [])
        if text is None:
            async with self.pool.connection() as conn:
                thread = await messaging_repo.recent_thread(conn, b["friend_id"], 12)
            unanswered = _unanswered(thread)
            try:
                draft = await self.ai.draft_reply(await self._context(b, thread, now))
            except AIError as exc:
                await self._retry_or_fail(b, now, f"draft_failed: {exc}", stats)
                return
            if draft.action == "no_reply":
                await self._skip(b, now, "no_reply_needed", stats, note=draft.note)
                return
            text = draft.text.strip()
            flags = normalise_flags(draft.flags) | keyword_flags(*unanswered, text)
            if draft.action == "needs_user":
                flags.add("needs_you")
            if any(_is_attachment(m) for m in unanswered):
                flags.add(_ATTACHMENT)
            if not text or not fits_instagram(text):
                flags.add("too_long" if text else "needs_you")
            async with self.pool.connection() as conn:
                await self._set(conn, b["id"], generated_text=text, draft_language=draft.language,
                                safety_flags=sorted(flags), model_note=draft.note or None)

        # 5. Safety gate / review-first.
        if flags or b["auto_reply_require_review"]:
            reason = _primary_reason(flags) if flags else "review_first"
            await self._needs_attention(b, now, reason, text, stats)
            return

        # 6. Stale again? (Claude took a few seconds; you or they may have written since.)
        stale = await self._staleness(b)
        if stale:
            await self._skip(b, now, stale, stats, status="superseded" if stale == "superseded" else "skipped")
            return

        # 7. Deterministic Meta eligibility, immediately before sending.
        b2 = await self._load(b["id"]) or b   # fresh friend/account state
        eligibility = evaluate_send_eligibility(EligibilityInput(
            now=self.clock(),
            last_inbound_at=b2["last_inbound_message_at"],
            consent_status=b2["consent_status"],
            friend_ai_enabled=b2["ai_reply_enabled"],
            global_pause=b2["automation_paused"],
            account_connected=b2["account_status"] == "active",
            manual_override_until=b2["manual_override_until"],
        ))
        if not eligibility.eligible:
            codes = [r.value for r in eligibility.reasons]
            if set(codes) & set(BLOCK_TEXT):
                await self._block(b, now, codes, eligibility.snapshot(), stats, draft=text)
            else:  # pause / AI off / consent / takeover changed while drafting
                await self._skip(b, now, _skip_code(codes), stats)
            return

        # 8. Send.
        await self._send(b2, text, eligibility.snapshot(), stats)

    async def _send(self, b: dict, text: str, snapshot: dict, stats: ReplyStats) -> None:
        async with self.pool.connection() as conn:
            if not await self._set(conn, b["id"], owner=True, status="sending", eligibility=Jsonb(snapshot)):
                return
        try:
            delivered = await deliver(
                self.pool, self.messaging, _recipient(b), text,
                idempotency_key=f"auto:{b['inbound_message_id']}", sent_by="echo_auto", clock=self.clock,
                reply_to_inbound_id=b["inbound_message_id"],
            )
        except TransientSendError as exc:
            async with self.pool.connection() as conn:
                await self._set(conn, b["id"], status="running")
            await self._retry_or_fail(b, self.clock(), f"send_failed: {exc}", stats)
            return
        except PermanentSendError as exc:
            await self._fail(b, self.clock(), f"rejected: {exc}", stats)
            return
        except UnknownOutcome:
            await self._fail(b, self.clock(), "outcome_unknown", stats)
            return

        now = self.clock()
        async with self.pool.connection() as conn:
            await self._set(conn, b["id"], status="sent", executed_at=now, claimed_by=None,
                            outbound_message_id=delivered.outbound_id)
            await profile_repo.audit(conn, b["user_id"], "worker", "auto_reply.sent", "auto_reply", str(b["id"]),
                                     {"message_id": delivered.message_id})
            await notify(conn, b["user_id"], "message_sent", f"Replied to {b['display_name']}", text,
                         {"friend_id": str(b["friend_id"]), "auto_reply_id": str(b["id"])})
        stats.sent += 1

    # ------------------------------------------------------------ helpers

    async def _staleness(self, b: dict) -> str | None:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                select
                  exists (select 1 from ig_messages where friend_id = %(f)s and direction = 'inbound'
                          and platform_time > %(at)s and ig_message_id <> %(mid)s) as newer_inbound,
                  (select sent_by from ig_messages where friend_id = %(f)s and direction = 'outbound'
                          and platform_time >= %(at)s order by platform_time desc limit 1) as replied_by
                """,
                {"f": b["friend_id"], "at": b["inbound_at"], "mid": b["inbound_message_id"]},
            )
            row = await cur.fetchone()
        if row["replied_by"] == "user_manual":
            return "you_replied"
        if row["replied_by"] is not None:
            return "already_replied"
        if row["newer_inbound"]:
            return "superseded"
        return None

    async def _over_limit(self, b: dict, now: datetime) -> bool:
        async with self.pool.connection() as conn:
            cur = await conn.execute(
                """
                select count(*) filter (where friend_id = %s) as friend_count, count(*) as total
                from outbound_messages
                where user_id = %s and reply_to_inbound_id is not null and status = 'sent' and sent_at > %s
                """,
                (b["friend_id"], b["user_id"], now - timedelta(hours=24)),
            )
            row = await cur.fetchone()
        return row["friend_count"] >= b["auto_reply_daily_limit"] or row["total"] >= b["account_daily_limit"]

    async def _context(self, b: dict, thread: list[dict], now: datetime) -> str:
        async with self.pool.connection() as conn:
            traits = await profile_repo.list_traits(conn, b["user_id"], limit=30)
            examples = await profile_repo.examples_for_context(conn, b["user_id"], b["language"] or None, 4)
            cur = await conn.execute(
                """
                select tone, formality, emoji_level, max_sentences, slang_allowed, custom_instructions, boundaries
                from friend_style_profiles where friend_id = %s
                """,
                (b["friend_id"],),
            )
            style = await cur.fetchone()
            cur = await conn.execute(
                """
                select content from friend_memories
                where friend_id = %s and approved and (expires_at is null or expires_at > %s)
                order by created_at desc limit 5
                """,
                (b["friend_id"], now),
            )
            memories = [r["content"] for r in await cur.fetchall()]
        return build_compose_context(
            traits=traits, examples=examples,
            friend={"display_name": b["display_name"], "style": b["style"], "language": b["language"]},
            thread=thread, now=now, timezone=b["default_timezone"],
            friend_style=style, memories=memories, mark_unanswered=True,
        )

    async def _set(self, conn: AsyncConnection, reply_id: UUID, *, owner: bool = False, **changes: Any) -> bool:
        sets = ", ".join(f"{k} = %s" for k in changes)
        sql = f"update auto_replies set {sets} where id = %s"
        params: list[Any] = [*changes.values(), reply_id]
        if owner:
            sql += " and claimed_by = %s and status = 'running'"
            params.append(self.worker_id)
        cur = await conn.execute(sql, params)
        return cur.rowcount == 1

    async def _requeue(self, conn: AsyncConnection, reply_id: UUID, friend_id: UUID, attempt: int,
                       when: datetime, reason: str) -> str:
        """Back to pending for a retry — unless a newer reply is already waiting."""
        if attempt >= MAX_ATTEMPTS:
            await self._set(conn, reply_id, status="failed", reason=reason[:300], claimed_by=None)
            return "failed"
        cur = await conn.execute(
            """
            update auto_replies set status = 'pending', next_attempt_at = %s, reason = %s, claimed_by = null
            where id = %s and not exists (
                select 1 from auto_replies where friend_id = %s and status = 'pending' and id <> %s)
            """,
            (when, reason[:300], reply_id, friend_id, reply_id),
        )
        if cur.rowcount:
            return "retry"
        await self._set(conn, reply_id, status="superseded", reason="newer_message", claimed_by=None)
        return "superseded"

    async def _retry_or_fail(self, b: dict, now: datetime, reason: str, stats: ReplyStats) -> None:
        when = now + timedelta(seconds=retry_delay_seconds(b["attempt"]))
        async with self.pool.connection() as conn:
            outcome = await self._requeue(conn, b["id"], b["friend_id"], b["attempt"], when, reason)
        if outcome == "retry":
            stats.retried += 1
        elif outcome == "failed":
            await self._fail(b, now, reason, stats)

    async def _retry_or_fail_id(self, reply_id: UUID, reason: str, stats: ReplyStats) -> None:
        b = await self._load(reply_id)
        if b is not None and b["status"] == "running":
            await self._retry_or_fail(b, self.clock(), reason, stats)

    async def _skip(self, b: dict, now: datetime, code: str, stats: ReplyStats, *,
                    status: str = "skipped", note: str | None = None) -> None:
        async with self.pool.connection() as conn:
            await self._set(conn, b["id"], status=status, reason=code, executed_at=now, claimed_by=None,
                            **({"model_note": note} if note else {}))
        stats.skipped += 1

    async def _block(self, b: dict, now: datetime, codes: list[str], snapshot: dict | None, stats: ReplyStats,
                     draft: str | None = None) -> None:
        human = "; ".join(BLOCK_TEXT.get(c, c) for c in codes)
        async with self.pool.connection() as conn:
            await self._set(conn, b["id"], status="blocked", reason=",".join(codes), executed_at=now,
                            claimed_by=None, **({"eligibility": Jsonb(snapshot)} if snapshot else {}))
            if "daily_limit" in codes and await self._limit_already_notified(conn, b, now):
                pass
            else:
                body = f"ECHO didn't reply because {human}."
                if draft:
                    body += " The draft is saved if you want to send it yourself."
                await notify(conn, b["user_id"], "job_blocked", f"Didn't reply to {b['display_name']}", body,
                             {"friend_id": str(b["friend_id"]), "auto_reply_id": str(b["id"])})
            await profile_repo.audit(conn, b["user_id"], "worker", "auto_reply.blocked", "auto_reply", str(b["id"]),
                                     {"reasons": codes})
        stats.blocked += 1

    async def _limit_already_notified(self, conn: AsyncConnection, b: dict, now: datetime) -> bool:
        cur = await conn.execute(
            """
            select 1 from auto_replies where user_id = %s and status = 'blocked' and reason = 'daily_limit'
              and id <> %s and executed_at > %s limit 1
            """,
            (b["user_id"], b["id"], now - timedelta(hours=24)),
        )
        return await cur.fetchone() is not None

    async def _needs_attention(self, b: dict, now: datetime, reason: str, draft: str, stats: ReplyStats) -> None:
        async with self.pool.connection() as conn:
            await self._set(conn, b["id"], status="needs_attention", reason=reason, executed_at=now, claimed_by=None)
            await conn.execute(
                """
                insert into review_items (user_id, friend_id, origin, inbound_message_id, auto_reply_id,
                                          reason_code, draft_text)
                values (%s, %s, 'auto_reply', %s, %s, %s, %s)
                """,
                (b["user_id"], b["friend_id"], b["inbound_message_id"], b["id"], reason, draft or None),
            )
            await notify(conn, b["user_id"], "needs_attention", f"{b['display_name']} needs your reply",
                         f"ECHO drafted a reply but held it because it {REASON_TEXT.get(reason, reason)}.",
                         {"friend_id": str(b["friend_id"]), "auto_reply_id": str(b["id"])})
        stats.held += 1

    async def _fail(self, b: dict, now: datetime, reason: str, stats: ReplyStats) -> None:
        async with self.pool.connection() as conn:
            await self._set(conn, b["id"], status="failed", reason=reason[:300], executed_at=now, claimed_by=None)
            body = ("Instagram may or may not have received it — check the chat before replying again."
                    if reason == "outcome_unknown" else f"Delivery failed ({reason[:120]}).")
            await notify(conn, b["user_id"], "delivery_failed", f"Couldn't reply to {b['display_name']}", body,
                         {"friend_id": str(b["friend_id"]), "auto_reply_id": str(b["id"])})
        stats.failed += 1


# ----------------------------------------------------------------- utilities

_REASON_ORDER = ("money", "commitment", "sensitive", "emotional", "needs_you", "unknown_info", _ATTACHMENT,
                 "too_long")


def _primary_reason(flags: set[str]) -> str:
    for reason in _REASON_ORDER:
        if reason in flags:
            return reason
    return sorted(flags)[0]


def _skip_code(codes: list[str]) -> str:
    mapping = {
        BlockReason.GLOBAL_PAUSE.value: "global_pause",
        BlockReason.FRIEND_AUTOMATION_OFF.value: "ai_off",
        BlockReason.NO_CONSENT.value: "no_consent",
        BlockReason.MANUAL_OVERRIDE.value: "you_took_over",
    }
    for code in codes:
        if code in mapping:
            return mapping[code]
    return codes[0] if codes else "ineligible"


def _unanswered(thread: list[dict]) -> list[str]:
    last_outbound = max((i for i, m in enumerate(thread) if m["direction"] != "inbound"), default=-1)
    return [str(m["content"] or "") for i, m in enumerate(thread) if i > last_outbound and m["direction"] == "inbound"]


def _is_attachment(text: str) -> bool:
    t = text.strip()
    return t.startswith("[") and t.endswith("]") and len(t) < 60


def _recipient(b: dict) -> Recipient:
    return Recipient(
        user_id=b["user_id"], friend_id=b["friend_id"], display_name=b["display_name"],
        ig_user_id=b["ig_user_id"], igsid=b["igsid"], disclosure_sent_at=b["disclosure_sent_at"],
        disclosure_text=b["ai_disclosure_text"],
    )
