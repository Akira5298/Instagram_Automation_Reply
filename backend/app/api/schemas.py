"""Wire format shared with the iOS app (snake_case JSON)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

from app.domain.memory import CATEGORIES

Category = Literal[
    "languages", "vocabulary", "tone", "slang", "emoji", "humour",
    "sentence_style", "social", "interests", "friend_preferences",
]
assert set(Category.__args__) == set(CATEGORIES)  # type: ignore[attr-defined]


class MemoryUpdateOut(BaseModel):
    id: UUID
    op: Literal["set", "remove"] = "set"
    category: Category
    key: str
    summary: str
    kind: Literal["observation", "stated_fact"]
    confidence: float


class ChatMessageOut(BaseModel):
    id: UUID
    role: Literal["user", "assistant"]
    text: str
    language: str | None = None
    created_at: datetime
    memory_updates: list[MemoryUpdateOut] = []
    # Things ECHO did during this turn, e.g. {"type": "scheduled_job", ...}.
    actions: list[dict[str, Any]] = []

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> ChatMessageOut:
        # The app keys its own messages by client_msg_id, so return that id
        # for user messages; it keeps local and server copies in sync.
        msg_id = row["client_msg_id"] if row["role"] == "user" and row.get("client_msg_id") else row["id"]
        return cls(
            id=msg_id,
            role=row["role"],
            text=row["content"],
            language=row.get("language"),
            created_at=row["created_at"],
            memory_updates=row.get("memory_updates") or [],
            actions=row.get("actions") or [],
        )


class ChatRequest(BaseModel):
    client_msg_id: UUID
    text: str = Field(min_length=1, max_length=4000)

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("text is blank")
        return v.strip()


class ChatTurnOut(BaseModel):
    user_message: ChatMessageOut
    message: ChatMessageOut
    replayed: bool = False


class ChatHistoryOut(BaseModel):
    messages: list[ChatMessageOut]
    has_more: bool


class TraitOut(BaseModel):
    id: UUID
    category: Category
    key: str
    value: str
    source: Literal["user_stated", "inferred", "user_edited"]
    confidence: float
    evidence_count: int
    updated_at: datetime


class ExampleOut(BaseModel):
    id: UUID
    text: str
    language: str | None = None
    created_at: datetime


class ProfileOut(BaseModel):
    traits: list[TraitOut]
    examples: list[ExampleOut]
    rejected_keys: list[str] = []


class TraitCreate(BaseModel):
    category: Category
    value: str = Field(min_length=1, max_length=160)


class TraitPatch(BaseModel):
    category: Category | None = None
    value: str | None = Field(default=None, min_length=1, max_length=160)


class ExampleCreate(BaseModel):
    text: str = Field(min_length=1, max_length=1000)
    language: str | None = Field(default=None, max_length=16)
    source: Literal["chat", "manual"] = "chat"
