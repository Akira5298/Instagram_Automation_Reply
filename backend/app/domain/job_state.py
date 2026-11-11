"""Job-run state machine. Mirrors job_runs.status in the database and
ScheduledJob.Status in the iOS app.

scheduled ──claim──▶ running ──checks pass──▶ sending ──▶ sent
    │                  │  │                       │
    │                  │  └─safety gate──▶ needs_attention
    │                  └─ineligible──▶ blocked     └─error──▶ retry_scheduled ──▶ running
    │                                                    └─retries exhausted──▶ failed
    └──user──▶ cancelled
"""

from __future__ import annotations

from enum import StrEnum


class RunStatus(StrEnum):
    SCHEDULED = "scheduled"
    RUNNING = "running"
    NEEDS_ATTENTION = "needs_attention"
    SENDING = "sending"
    SENT = "sent"
    BLOCKED = "blocked"
    FAILED = "failed"
    RETRY_SCHEDULED = "retry_scheduled"
    CANCELLED = "cancelled"


TERMINAL: frozenset[RunStatus] = frozenset(
    {RunStatus.SENT, RunStatus.BLOCKED, RunStatus.FAILED, RunStatus.CANCELLED}
)

_ALLOWED: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.SCHEDULED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.SENDING,
            RunStatus.BLOCKED,
            RunStatus.NEEDS_ATTENTION,
            RunStatus.RETRY_SCHEDULED,
            RunStatus.FAILED,
            RunStatus.SCHEDULED,  # lease expired / worker crashed -> release
            RunStatus.CANCELLED,  # job paused or cancelled mid-flight
        }
    ),
    RunStatus.SENDING: frozenset({RunStatus.SENT, RunStatus.RETRY_SCHEDULED, RunStatus.FAILED}),
    RunStatus.RETRY_SCHEDULED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    # Resolved by you in the app: dismissed / sent manually (cancelled), or
    # approved ("send anyway") which re-queues it through the eligibility check.
    RunStatus.NEEDS_ATTENTION: frozenset({RunStatus.CANCELLED, RunStatus.BLOCKED, RunStatus.RETRY_SCHEDULED}),
}


class InvalidTransition(ValueError):
    pass


def can_transition(current: RunStatus, target: RunStatus) -> bool:
    return target in _ALLOWED.get(current, frozenset())


def transition(current: RunStatus, target: RunStatus) -> RunStatus:
    if not can_transition(current, target):
        raise InvalidTransition(f"{current} -> {target} is not allowed")
    return target


def retry_delay_seconds(attempt: int, base: float = 30.0, cap: float = 15 * 60) -> float:
    """Exponential backoff (30s, 60s, 120s ...) capped at 15 min.

    Retries never extend past the messaging window: the eligibility check runs
    again on every attempt, so a late retry becomes `blocked`, not `sent`.
    """
    if attempt < 1:
        raise ValueError("attempt starts at 1")
    return min(cap, base * (2 ** (attempt - 1)))


MAX_ATTEMPTS = 5
