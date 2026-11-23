"""Learning how you write to each friend from the messages you type yourself.

Only `user_manual` messages count as your writing (never ECHO's own replies).
When a friend has at least MIN_NEW new messages from you since the last pass,
Claude (the small model) refines a per-friend style card, stored in
friend_style_profiles.learned, and may propose a few general style traits for
your profile. Those go through the same deterministic rules as everything
else the profile learns: PII is dropped, inferred traits stay tentative,
your own edits win and anything you rejected is never re-added.

Off when app_users.learn_from_chats is false. "Forget" wipes the card.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

from app.ai.compose_context import _escape
from app.ai.provider import AIError, AIProvider
from app.db import profile_repo
from app.domain.memory import contains_pii, merge, sanitize

log = logging.getLogger("echo.style")

MIN_NEW = 5               # new messages from you before ECHO looks again
FRIENDS_PER_PASS = 5      # keeps each hourly pass cheap
TRANSCRIPT_MESSAGES = 60
TRANSCRIPT_CHARS = 6000


def clean_learned(raw: dict) -> dict:
    """Deterministic shape + PII guard for what the model says it learned."""
    def text(value: Any, limit: int) -> str:
        out = " ".join(str(value or "").split())[:limit]
        return "" if contains_pii(out) else out

    def level(value: Any) -> int:
        try:
            return max(0, min(4, int(value)))
        except (TypeError, ValueError):
            return 2

    def items(value: Any, n: int, limit: int) -> list[str]:
        out: list[str] = []
        for v in value if isinstance(value, list) else []:
            t = text(v, limit)
            if t and t not in out:
                out.append(t)
        return out[:n]

    length = text(raw.get("typical_length"), 20).lower()
    return {
        "tone": text(raw.get("tone"), 60),
        "formality": level(raw.get("formality")),
        "emoji_level": level(raw.get("emoji_level")),
        "typical_length": length if length in ("very short", "short", "medium", "long") else "short",
        "favourite_emoji": items(raw.get("favourite_emoji"), 5, 8),
        "phrases": items(raw.get("phrases"), 8, 40),
        "humor": text(raw.get("humor"), 60),
        "language": text(raw.get("language"), 40),
        "notes": text(raw.get("notes"), 160),
    }


async def candidates(conn: AsyncConnection, user_id: UUID | None = None, limit: int = FRIENDS_PER_PASS) -> list[dict]:
    """Friends with enough new messages from you since the last pass."""
    cur = await conn.execute(
        """
        select f.id as friend_id, f.user_id, f.display_name, count(m.id) as new_count, max(m.platform_time) as newest
        from friends f
        join app_users u on u.id = f.user_id and u.learn_from_chats
        join ig_messages m on m.friend_id = f.id and m.direction = 'outbound' and m.sent_by = 'user_manual'
             and m.content is not null
             and m.platform_time > coalesce(f.style_learned_through, '-infinity'::timestamptz)
        where (%s::uuid is null or f.user_id = %s)
        group by f.id
        having count(m.id) >= %s
        order by max(m.platform_time) desc
        limit %s
        """,
        (user_id, user_id, MIN_NEW, limit),
    )
    return list(await cur.fetchall())


async def transcript(conn: AsyncConnection, friend_id: UUID, previous: dict | None) -> tuple[str, int]:
    cur = await conn.execute(
        """
        select direction, sent_by, content from ig_messages
        where friend_id = %s and content is not null order by platform_time desc limit %s
        """,
        (friend_id, TRANSCRIPT_MESSAGES),
    )
    rows = list(reversed(await cur.fetchall()))
    lines: list[str] = []
    mine = 0
    for r in rows:
        content = " ".join(str(r["content"]).split())[:300]
        if r["direction"] == "inbound":
            lines.append(f"Them: {_escape(content)}")
        elif r["sent_by"] == "user_manual" and not content.startswith("["):
            lines.append(f"Me: {_escape(content)}")
            mine += 1
        # ECHO's own messages are not your writing: left out.
    body = "\n".join(lines)[-TRANSCRIPT_CHARS:]
    prev = "\n".join(f"{k}: {v}" for k, v in (previous or {}).items() if v not in ("", [], None))
    block = f"<previous>\n{prev or '(nothing yet)'}\n</previous>\n\n<messages>\n{body}\n</messages>"
    return block, mine


async def learn_friend(pool: AsyncConnectionPool, ai: AIProvider, user_id: UUID, friend_id: UUID,
                       now: datetime) -> dict | None:
    """One pass for one friend. Returns the new card, or None if there was nothing to learn from."""
    async with pool.connection() as conn:
        cur = await conn.execute("select learned from friend_style_profiles where friend_id = %s", (friend_id,))
        row = await cur.fetchone()
        block, mine = await transcript(conn, friend_id, row["learned"] if row else None)
        cur = await conn.execute(
            "select max(platform_time) as t from ig_messages where friend_id = %s and sent_by = 'user_manual'",
            (friend_id,))
        through = (await cur.fetchone())["t"]
    if mine < 3:   # mostly stickers/photos: nothing to learn, don't look at these again
        async with pool.connection() as conn:
            await conn.execute("update friends set style_learned_through = %s where id = %s",
                               (through or now, friend_id))
        return None
    result = await ai.learn_style(block)          # no DB connection held while Claude thinks
    learned = clean_learned(result.friend_style)
    proposals = sanitize([{**u, "kind": "observation", "confidence": 0.5} for u in result.profile_updates])[:4]

    async with pool.connection() as conn, conn.transaction():
        await conn.execute(
            """
            insert into friend_style_profiles (friend_id, learned, learned_at, learned_message_count, updated_at)
            values (%s, %s, %s, %s, now())
            on conflict (friend_id) do update set learned = excluded.learned, learned_at = excluded.learned_at,
                learned_message_count = friend_style_profiles.learned_message_count + excluded.learned_message_count,
                updated_at = now()
            """,
            (friend_id, Jsonb(learned), now, mine),
        )
        await conn.execute("update friends set style_learned_through = %s where id = %s", (through or now, friend_id))
        rejected = set(await profile_repo.rejected_keys(conn, user_id))
        proposals = [p for p in proposals if p.key not in rejected]
        locked = await profile_repo.lock_traits(conn, user_id, [p.key for p in proposals])
        for proposal in proposals:
            state = merge(locked.get(proposal.key), proposal)
            if state is not None:
                await profile_repo.upsert_trait(conn, user_id, state)
        await profile_repo.audit(conn, user_id, "system", "friend.style_learned", "friend", str(friend_id),
                                 {"messages": mine, "profile_updates": len(proposals)})
    return learned


async def learn_pending(pool: AsyncConnectionPool, ai: AIProvider, now: datetime) -> int:
    """Hourly: refresh the cards that have enough new material."""
    async with pool.connection() as conn:
        todo = await candidates(conn)
    done = 0
    for c in todo:
        try:
            if await learn_friend(pool, ai, c["user_id"], c["friend_id"], now) is not None:
                done += 1
        except AIError as exc:
            log.info("style learning skipped", extra={"fields": {"error": type(exc).__name__}})
    return done
