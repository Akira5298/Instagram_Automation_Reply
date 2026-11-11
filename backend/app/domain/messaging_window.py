"""Deterministic send-eligibility rules.

This module — not the language model — decides whether ECHO may send an
automated Instagram message. Every automated send (auto reply or scheduled
job) must call `evaluate_send_eligibility` immediately before dispatch.

Meta rules encoded here (Instagram API with Instagram Login, verified Nov 2026):
  * You can only message someone after they message the professional account.
  * The standard messaging window is 24 hours from their latest message.
  * The HUMAN_AGENT tag (7-day window) is for replies written by a human.
    ECHO never uses it for AI-generated or unattended messages.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

STANDARD_WINDOW = timedelta(hours=24)
# Don't start a send in the last few minutes of the window: generation +
# network time could push delivery past the deadline.
SAFETY_MARGIN = timedelta(minutes=5)


class BlockReason(StrEnum):
    GLOBAL_PAUSE = "global_pause"
    FRIEND_AUTOMATION_OFF = "friend_automation_off"
    MANUAL_OVERRIDE = "manual_override"
    NO_CONSENT = "no_consent"
    NO_INBOUND_MESSAGE = "no_inbound_message"
    OUTSIDE_STANDARD_WINDOW = "outside_standard_window"
    ACCOUNT_DISCONNECTED = "account_disconnected"


@dataclass(frozen=True)
class EligibilityInput:
    now: datetime
    last_inbound_at: datetime | None
    consent_status: str            # 'unknown' | 'requested' | 'granted' | 'revoked'
    friend_ai_enabled: bool
    global_pause: bool
    account_connected: bool = True
    manual_override_until: datetime | None = None


@dataclass(frozen=True)
class EligibilityResult:
    eligible: bool
    reasons: tuple[BlockReason, ...]
    window_expires_at: datetime | None

    def snapshot(self) -> dict:
        """Stored in job_runs.eligibility for the audit trail."""
        return {
            "eligible": self.eligible,
            "reasons": [r.value for r in self.reasons],
            "window_expires_at": self.window_expires_at.isoformat() if self.window_expires_at else None,
        }


def _require_aware(dt: datetime | None, name: str) -> None:
    if dt is not None and dt.tzinfo is None:
        raise ValueError(f"{name} must be timezone-aware")


def evaluate_send_eligibility(inp: EligibilityInput) -> EligibilityResult:
    _require_aware(inp.now, "now")
    _require_aware(inp.last_inbound_at, "last_inbound_at")
    _require_aware(inp.manual_override_until, "manual_override_until")

    reasons: list[BlockReason] = []

    if inp.global_pause:
        reasons.append(BlockReason.GLOBAL_PAUSE)
    if not inp.account_connected:
        reasons.append(BlockReason.ACCOUNT_DISCONNECTED)
    if not inp.friend_ai_enabled:
        reasons.append(BlockReason.FRIEND_AUTOMATION_OFF)
    if inp.manual_override_until is not None and inp.manual_override_until > inp.now:
        reasons.append(BlockReason.MANUAL_OVERRIDE)
    if inp.consent_status != "granted":
        reasons.append(BlockReason.NO_CONSENT)

    window_expires_at: datetime | None = None
    if inp.last_inbound_at is None:
        reasons.append(BlockReason.NO_INBOUND_MESSAGE)
    else:
        window_expires_at = inp.last_inbound_at + STANDARD_WINDOW
        if inp.now >= window_expires_at - SAFETY_MARGIN:
            reasons.append(BlockReason.OUTSIDE_STANDARD_WINDOW)

    return EligibilityResult(
        eligible=not reasons,
        reasons=tuple(reasons),
        window_expires_at=window_expires_at,
    )
