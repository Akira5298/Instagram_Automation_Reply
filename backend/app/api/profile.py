from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Response
from psycopg import AsyncConnection

from app.api.schemas import ExampleCreate, ExampleOut, ProfileOut, TraitCreate, TraitOut, TraitPatch
from app.core.security import require_user
from app.db import chat_repo, profile_repo
from app.db.pool import get_conn
from app.domain.memory import contains_pii

router = APIRouter(prefix="/v1/profile", tags=["profile"])


async def _profile(conn: AsyncConnection, user_id: UUID) -> ProfileOut:
    traits = await profile_repo.list_traits(conn, user_id)
    examples = await profile_repo.list_examples(conn, user_id)
    rejected = await profile_repo.rejected_keys(conn, user_id)
    return ProfileOut(
        traits=[TraitOut(**t) for t in traits],
        examples=[ExampleOut(**e) for e in examples],
        rejected_keys=rejected,
    )


@router.get("", response_model=ProfileOut)
async def get_profile(user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    return await _profile(conn, user_id)


@router.delete("", status_code=204)
async def delete_profile(user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    """Forget everything learned: traits (including deletion tombstones) and
    writing examples. Chat history is separate (DELETE /v1/chat/messages)."""
    await profile_repo.delete_all_traits(conn, user_id)
    await profile_repo.delete_all_examples(conn, user_id)
    await profile_repo.audit(conn, user_id, "user", "profile.deleted_all")
    return Response(status_code=204)


@router.get("/export")
async def export_profile(user_id: UUID = Depends(require_user), conn: AsyncConnection = Depends(get_conn)):
    profile = await _profile(conn, user_id)
    summaries = await chat_repo.list_summaries(conn, user_id)
    return {
        "exported_at": datetime.now(UTC).isoformat(),
        "traits": [t.model_dump(mode="json") for t in profile.traits],
        "writing_examples": [e.model_dump(mode="json") for e in profile.examples],
        "deleted_trait_keys": profile.rejected_keys,
        "conversation_summaries": [
            {"summary": s["summary"], "covers_until": s["covers_until"].isoformat()} for s in summaries
        ],
    }


@router.post("/traits", response_model=TraitOut, status_code=201)
async def add_trait(body: TraitCreate, user_id: UUID = Depends(require_user),
                    conn: AsyncConnection = Depends(get_conn)):
    row = await profile_repo.add_manual_trait(conn, user_id, body.category, body.value.strip())
    await profile_repo.audit(conn, user_id, "user", "profile.trait_added", "profile_trait", str(row["id"]))
    return TraitOut(**row)


@router.patch("/traits/{trait_id}", response_model=TraitOut)
async def edit_trait(trait_id: UUID, body: TraitPatch, user_id: UUID = Depends(require_user),
                     conn: AsyncConnection = Depends(get_conn)):
    row = await profile_repo.edit_trait(
        conn, user_id, trait_id,
        value=body.value.strip() if body.value else None,
        category=body.category,
    )
    if row is None:
        raise HTTPException(status_code=404, detail="trait not found")
    await profile_repo.audit(conn, user_id, "user", "profile.trait_edited", "profile_trait", str(trait_id))
    return TraitOut(**row)


@router.delete("/traits/{trait_id}", status_code=204)
async def delete_trait(trait_id: UUID, user_id: UUID = Depends(require_user),
                       conn: AsyncConnection = Depends(get_conn)):
    if not await profile_repo.reject_trait(conn, user_id, trait_id):
        raise HTTPException(status_code=404, detail="trait not found")
    await profile_repo.audit(conn, user_id, "user", "profile.trait_deleted", "profile_trait", str(trait_id))
    return Response(status_code=204)


@router.post("/examples", response_model=ExampleOut, status_code=201)
async def add_example(body: ExampleCreate, user_id: UUID = Depends(require_user),
                      conn: AsyncConnection = Depends(get_conn)):
    text = body.text.strip()
    if contains_pii(text):
        raise HTTPException(status_code=422, detail="Examples can't contain contact details or ID numbers.")
    row = await profile_repo.add_example(conn, user_id, text, body.language, body.source)
    return ExampleOut(**row)


@router.delete("/examples/{example_id}", status_code=204)
async def delete_example(example_id: UUID, user_id: UUID = Depends(require_user),
                         conn: AsyncConnection = Depends(get_conn)):
    if not await profile_repo.delete_example(conn, user_id, example_id):
        raise HTTPException(status_code=404, detail="example not found")
    return Response(status_code=204)
