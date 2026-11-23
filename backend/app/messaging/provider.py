"""Outbound messaging transport.

`MessagingProvider` is what the worker calls to deliver a DM. Phase 3 ships
`MockMessagingProvider` so the whole scheduler can be exercised end to end;
Phase 4 adds the Instagram Graph API implementation behind the same interface.

Error contract (the worker relies on it):
  * TransientSendError — nothing was delivered; safe to retry later.
  * PermanentSendError — nothing was delivered; retrying won't help.
  * UnknownOutcome     — the request may have been delivered (timeout after
                         sending). Never retried automatically: a duplicate
                         message is worse than a missing one.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal, Protocol


class SendError(Exception):
    pass


class TransientSendError(SendError):
    pass


class PermanentSendError(SendError):
    pass


class UnknownOutcome(SendError):
    pass


@dataclass(frozen=True)
class SendResult:
    message_id: str


class MessagingProvider(Protocol):
    name: str

    async def send_text(
        self, *, account_ig_id: str, recipient_igsid: str, text: str, idempotency_key: str
    ) -> SendResult: ...

    async def send_reaction(
        self, *, account_ig_id: str, recipient_igsid: str, message_id: str, idempotency_key: str
    ) -> None: ...


FailureMode = Literal["none", "transient_once", "transient_always", "permanent", "unknown"]


@dataclass
class SentRecord:
    recipient_igsid: str
    text: str
    idempotency_key: str
    message_id: str


@dataclass
class MockMessagingProvider:
    """In-memory stand-in for Instagram. Deduplicates on idempotency_key like
    a well-behaved API would, and can simulate failures for testing."""

    name: str = "mock"
    failure_mode: FailureMode = "none"
    sent: list[SentRecord] = field(default_factory=list)
    reactions: list[tuple[str, str]] = field(default_factory=list)
    _by_key: dict[str, str] = field(default_factory=dict)
    _transient_used: bool = False

    async def send_text(
        self, *, account_ig_id: str, recipient_igsid: str, text: str, idempotency_key: str
    ) -> SendResult:
        if idempotency_key in self._by_key:
            return SendResult(self._by_key[idempotency_key])
        mode = self.failure_mode
        if mode == "transient_once" and not self._transient_used:
            self._transient_used = True
            raise TransientSendError("mock: temporary error (simulated)")
        if mode == "transient_always":
            raise TransientSendError("mock: temporary error (simulated)")
        if mode == "permanent":
            raise PermanentSendError("mock: recipient can't receive messages (simulated)")
        if mode == "unknown":
            raise UnknownOutcome("mock: timed out after sending (simulated)")
        message_id = f"mock.mid.{uuid.uuid4().hex[:16]}"
        self._by_key[idempotency_key] = message_id
        self.sent.append(SentRecord(recipient_igsid, text, idempotency_key, message_id))
        return SendResult(message_id)

    async def send_reaction(
        self, *, account_ig_id: str, recipient_igsid: str, message_id: str, idempotency_key: str
    ) -> None:
        if self.failure_mode in ("transient_always", "permanent", "unknown"):
            await self.send_text(account_ig_id=account_ig_id, recipient_igsid=recipient_igsid,
                                 text="", idempotency_key=idempotency_key)   # raises the simulated error
        self.reactions.append((recipient_igsid, message_id))

    def set_failure_mode(self, mode: FailureMode) -> None:
        self.failure_mode = mode
        self._transient_used = False
