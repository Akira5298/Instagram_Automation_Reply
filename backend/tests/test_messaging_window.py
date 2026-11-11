from datetime import UTC, datetime, timedelta

import pytest

from app.domain.messaging_window import (
    BlockReason,
    EligibilityInput,
    evaluate_send_eligibility,
)

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)


def base(**overrides) -> EligibilityInput:
    values = dict(
        now=NOW,
        last_inbound_at=NOW - timedelta(hours=2),
        consent_status="granted",
        friend_ai_enabled=True,
        global_pause=False,
    )
    values.update(overrides)
    return EligibilityInput(**values)


def test_eligible_inside_window():
    result = evaluate_send_eligibility(base())
    assert result.eligible
    assert result.window_expires_at == NOW + timedelta(hours=22)


def test_blocked_after_24_hours():
    result = evaluate_send_eligibility(base(last_inbound_at=NOW - timedelta(hours=24, minutes=1)))
    assert not result.eligible
    assert BlockReason.OUTSIDE_STANDARD_WINDOW in result.reasons


def test_blocked_inside_safety_margin():
    result = evaluate_send_eligibility(base(last_inbound_at=NOW - timedelta(hours=23, minutes=57)))
    assert BlockReason.OUTSIDE_STANDARD_WINDOW in result.reasons


def test_never_messaged_is_blocked():
    result = evaluate_send_eligibility(base(last_inbound_at=None))
    assert result.reasons == (BlockReason.NO_INBOUND_MESSAGE,)


@pytest.mark.parametrize("consent", ["unknown", "requested", "revoked"])
def test_requires_granted_consent(consent):
    assert BlockReason.NO_CONSENT in evaluate_send_eligibility(base(consent_status=consent)).reasons


def test_global_pause_and_friend_toggle_and_override():
    result = evaluate_send_eligibility(
        base(global_pause=True, friend_ai_enabled=False, manual_override_until=NOW + timedelta(hours=1))
    )
    assert set(result.reasons) == {
        BlockReason.GLOBAL_PAUSE,
        BlockReason.FRIEND_AUTOMATION_OFF,
        BlockReason.MANUAL_OVERRIDE,
    }


def test_expired_manual_override_does_not_block():
    assert evaluate_send_eligibility(base(manual_override_until=NOW - timedelta(minutes=1))).eligible


def test_naive_datetimes_rejected():
    with pytest.raises(ValueError):
        evaluate_send_eligibility(base(now=datetime(2026, 11, 10, 12, 0)))


def test_snapshot_is_json_friendly():
    snap = evaluate_send_eligibility(base(last_inbound_at=None)).snapshot()
    assert snap == {"eligible": False, "reasons": ["no_inbound_message"], "window_expires_at": None}
