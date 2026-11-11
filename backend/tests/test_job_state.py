import pytest

from app.domain.job_state import (
    TERMINAL,
    InvalidTransition,
    RunStatus,
    can_transition,
    retry_delay_seconds,
    transition,
)


def test_happy_path():
    s = RunStatus.SCHEDULED
    for nxt in (RunStatus.RUNNING, RunStatus.SENDING, RunStatus.SENT):
        s = transition(s, nxt)
    assert s is RunStatus.SENT


def test_terminal_states_are_final():
    for terminal in TERMINAL:
        for target in RunStatus:
            assert not can_transition(terminal, target)


def test_cannot_skip_checks():
    with pytest.raises(InvalidTransition):
        transition(RunStatus.SCHEDULED, RunStatus.SENDING)


def test_blocked_is_never_retried():
    assert not can_transition(RunStatus.BLOCKED, RunStatus.RETRY_SCHEDULED)


def test_backoff():
    assert [retry_delay_seconds(n) for n in (1, 2, 3)] == [30, 60, 120]
    assert retry_delay_seconds(20) == 15 * 60
    with pytest.raises(ValueError):
        retry_delay_seconds(0)
