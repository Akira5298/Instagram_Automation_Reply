from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class AssistantTurn:
    text: str
    language: str
    # Raw, unvalidated proposals from the model. app.domain.memory.sanitize()
    # decides what (if anything) is stored.
    memory_updates: list[dict] = field(default_factory=list)
    # Raw scheduling requests the user made in chat; validated server-side.
    schedule_requests: list[dict] = field(default_factory=list)
    model: str = "mock"
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class SummaryResult:
    text: str
    model: str = "mock"
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class ComposedMessage:
    """A DM drafted in the user's voice for a friend."""

    text: str
    language: str
    # Model-raised concerns (money, commitment, ...). The deterministic
    # safety gate adds its own on top.
    flags: list[str] = field(default_factory=list)
    note: str = ""
    model: str = "mock"
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class ReplyDraft:
    """A reply to a friend's DM in the user's voice."""

    action: str  # "reply" | "react" | "no_reply" | "needs_user"
    text: str
    language: str
    flags: list[str] = field(default_factory=list)
    note: str = ""
    alternatives: list[str] = field(default_factory=list)
    model: str = "mock"
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class MessageIdea:
    text: str
    vibe: str


@dataclass(frozen=True)
class LearnedStyle:
    friend_style: dict
    profile_updates: list[dict]


@dataclass(frozen=True)
class ImageInput:
    media_type: str
    data_b64: str


class AIError(Exception):
    """Base class for provider failures the API maps to friendly errors."""

    status_code = 502
    user_message = "ECHO couldn't reach the AI service. Try again in a moment."


class AIRateLimited(AIError):
    status_code = 503
    user_message = "ECHO is busy right now. Try again in a minute."


class AIRefused(AIError):
    status_code = 422
    user_message = "ECHO can't help with that message."


class AIBadOutput(AIError):
    user_message = "ECHO's reply came back garbled. Try sending again."


class AIProvider(Protocol):
    async def chat_turn(self, context_block: str, messages: list[dict]) -> AssistantTurn:
        """`context_block`: per-turn profile/summary/examples text.
        `messages`: alternating user/assistant turns ending with the user."""
        ...

    async def summarize(self, previous_summary: str | None, transcript: str) -> SummaryResult: ...

    async def compose_message(self, context_block: str, instruction: str) -> ComposedMessage:
        """Draft a message to a friend in the user's voice."""
        ...

    async def draft_reply(self, context_block: str, images: list[ImageInput] | None = None) -> ReplyDraft:
        """Draft a reply to the friend's unanswered messages (or decide not to)."""
        ...

    async def suggest_messages(self, context_block: str, occasion: str | None, count: int) -> list[MessageIdea]:
        """Ideas for a message to schedule to this friend."""
        ...

    async def learn_style(self, transcript_block: str) -> LearnedStyle:
        """How the user writes to one friend, from their own messages."""
        ...
