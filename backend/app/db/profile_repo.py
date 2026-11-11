"""Global profile memory (profile_traits) and approved writing examples."""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from psycopg import AsyncConnection

from app.domain.memory import TraitState

_TRAIT_COLUMNS = (
    "id, category, trait_key as key, value, source, confidence::float as confidence, "
    "evidence_count, status, updated_at, last_seen_at"
)
_VISIBLE = "status in ('active', 'confirmed')"


async def list_traits(conn: AsyncConnection, user_id: UUID, limit: int | None = None) -> list[dict[str, Any]]:
    """Visible traits, most trustworthy first: your edits and stated facts,
    then inferences by confidence and recency."""
    cur = await conn.execute(
        f"""
        select {_TRAIT_COLUMNS} from profile_traits
        where user_id = %s and {_VISIBLE}
        order by case source when 'user_edited' then 0 when 'user_stated' then 1 else 2 end,
                 confidence desc, last_seen_at desc
        limit %s
        """,
        (user_id, limit),
    )
    return await cur.fetchall()


async def rejected_keys(conn: AsyncConnection, user_id: UUID) -> list[str]:
    cur = await conn.execute(
        "select trait_key from profile_traits where user_id = %s and status = 'rejected' order by trait_key",
        (user_id,),
    )
    return [r["trait_key"] for r in await cur.fetchall()]


async def lock_traits(conn: AsyncConnection, user_id: UUID, keys: list[str]) -> dict[str, TraitState]:
    """Load (and row-lock) the traits a turn wants to update."""
    if not keys:
        return {}
    cur = await conn.execute(
        """
        select category, trait_key, value, source, confidence::float as confidence, evidence_count, status
        from profile_traits where user_id = %s and trait_key = any(%s)
        for update
        """,
        (user_id, keys),
    )
    return {
        r["trait_key"]: TraitState(
            category=r["category"],
            key=r["trait_key"],
            value=r["value"],
            source=r["source"],
            confidence=r["confidence"],
            evidence_count=r["evidence_count"],
            status=r["status"],
        )
        for r in await cur.fetchall()
    }


async def upsert_trait(conn: AsyncConnection, user_id: UUID, state: TraitState) -> None:
    await conn.execute(
        """
        insert into profile_traits (user_id, category, trait_key, value, source, confidence,
                                    evidence_count, status)
        values (%s, %s, %s, %s, %s, %s, %s, %s)
        on conflict (user_id, category, trait_key) do update set
            value = excluded.value,
            source = excluded.source,
            confidence = excluded.confidence,
            evidence_count = excluded.evidence_count,
            status = excluded.status,
            last_seen_at = now()
        """,
        (user_id, state.category, state.key, state.value, state.source, state.confidence,
         state.evidence_count, state.status),
    )


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:50] or "item"


async def add_manual_trait(conn: AsyncConnection, user_id: UUID, category: str, value: str) -> dict[str, Any]:
    key = f"{category}.manual_{_slug(value)}"
    cur = await conn.execute(
        f"""
        insert into profile_traits (user_id, category, trait_key, value, source, confidence, status)
        values (%s, %s, %s, %s, 'user_edited', 1.0, 'confirmed')
        on conflict (user_id, category, trait_key) do update set
            value = excluded.value, source = 'user_edited', confidence = 1.0,
            status = 'confirmed', last_seen_at = now()
        returning {_TRAIT_COLUMNS}
        """,
        (user_id, category, key, value),
    )
    row = await cur.fetchone()
    assert row is not None
    return row


async def edit_trait(
    conn: AsyncConnection, user_id: UUID, trait_id: UUID, *, value: str | None, category: str | None
) -> dict[str, Any] | None:
    cur = await conn.execute(
        f"""
        update profile_traits set
            value = coalesce(%s, value),
            category = coalesce(%s, category),
            source = 'user_edited', confidence = 1.0, status = 'confirmed'
        where id = %s and user_id = %s and {_VISIBLE}
        returning {_TRAIT_COLUMNS}
        """,
        (value, category, trait_id, user_id),
    )
    return await cur.fetchone()


async def reject_trait(conn: AsyncConnection, user_id: UUID, trait_id: UUID) -> bool:
    """Delete the content but keep a tombstone so it isn't re-learned."""
    cur = await conn.execute(
        """
        update profile_traits set status = 'rejected', value = '', confidence = 0
        where id = %s and user_id = %s and status <> 'rejected'
        """,
        (trait_id, user_id),
    )
    return cur.rowcount > 0


async def delete_all_traits(conn: AsyncConnection, user_id: UUID) -> None:
    await conn.execute("delete from profile_traits where user_id = %s", (user_id,))


# --- Writing examples ----------------------------------------------------------

_EXAMPLE_COLUMNS = "id, content as text, language, source, created_at"


async def list_examples(conn: AsyncConnection, user_id: UUID, limit: int | None = None) -> list[dict[str, Any]]:
    cur = await conn.execute(
        f"""
        select {_EXAMPLE_COLUMNS} from writing_examples
        where user_id = %s and approved and friend_id is null
        order by created_at desc limit %s
        """,
        (user_id, limit),
    )
    return await cur.fetchall()


async def examples_for_context(
    conn: AsyncConnection, user_id: UUID, language: str | None, limit: int
) -> list[dict[str, Any]]:
    """Prefer examples in the language you're currently writing in."""
    cur = await conn.execute(
        """
        select content as text, language from writing_examples
        where user_id = %s and approved and friend_id is null
        order by (language is not distinct from %s) desc, created_at desc
        limit %s
        """,
        (user_id, language, limit),
    )
    return await cur.fetchall()


async def add_example(
    conn: AsyncConnection, user_id: UUID, text: str, language: str | None, source: str
) -> dict[str, Any]:
    cur = await conn.execute(
        f"""
        insert into writing_examples (user_id, content, language, source, approved)
        values (%s, %s, %s, %s, true)
        returning {_EXAMPLE_COLUMNS}
        """,
        (user_id, text, language, source),
    )
    row = await cur.fetchone()
    assert row is not None
    return row


async def delete_example(conn: AsyncConnection, user_id: UUID, example_id: UUID) -> bool:
    cur = await conn.execute(
        "delete from writing_examples where id = %s and user_id = %s", (example_id, user_id)
    )
    return cur.rowcount > 0


async def delete_all_examples(conn: AsyncConnection, user_id: UUID) -> None:
    await conn.execute("delete from writing_examples where user_id = %s and friend_id is null", (user_id,))


# --- Audit -----------------------------------------------------------------------

async def audit(
    conn: AsyncConnection, user_id: UUID, actor: str, action: str,
    entity_type: str | None = None, entity_id: str | None = None, details: dict | None = None,
) -> None:
    from psycopg.types.json import Jsonb

    await conn.execute(
        """
        insert into audit_log (user_id, actor, action, entity_type, entity_id, details)
        values (%s, %s, %s, %s, %s, %s)
        """,
        (user_id, actor, action, entity_type, entity_id, Jsonb(details or {})),
    )
