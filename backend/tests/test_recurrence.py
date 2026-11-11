from datetime import UTC, datetime, timedelta

import pytest

from app.domain.recurrence import (
    RecurrenceError,
    first_occurrence,
    latest_due,
    localize,
    next_occurrence,
    normalise_rrule,
    recurrence_name,
    zone,
)
from app.domain.safety import fits_instagram, keyword_flags

KL = zone("Asia/Kuala_Lumpur")
NY = zone("America/New_York")


def test_one_time_in_kuala_lumpur():
    first = first_occurrence(datetime(2026, 11, 12, 20, 0), KL, None)
    assert first == datetime(2026, 11, 12, 12, 0, tzinfo=UTC)  # UTC+8
    assert next_occurrence(None, datetime(2026, 11, 12, 20, 0), KL, first) is None


def test_weekly_keeps_wall_clock_across_dst():
    # 8 PM every Friday in New York, across the 1 Nov 2026 fall-back.
    rule = normalise_rrule("weekly")
    start = datetime(2026, 10, 23, 20, 0)  # a Friday, EDT (UTC-4)
    first = first_occurrence(start, NY, rule)
    assert first == datetime(2026, 10, 24, 0, 0, tzinfo=UTC)
    second = next_occurrence(rule, start, NY, first)
    third = next_occurrence(rule, start, NY, second)
    assert second == datetime(2026, 10, 31, 0, 0, tzinfo=UTC)   # still EDT
    assert third == datetime(2026, 11, 7, 1, 0, tzinfo=UTC)     # EST (UTC-5): still 8 PM local
    assert third.astimezone(NY).hour == 20


def test_nonexistent_local_time_runs_after_the_gap():
    # 2:30 AM doesn't exist on 8 Mar 2026 in New York (clocks jump 2:00 → 3:00).
    instant = localize(datetime(2026, 3, 8, 2, 30), NY)
    assert instant.astimezone(NY).hour == 3


def test_repeated_local_time_runs_once():
    # 1:30 AM happens twice on 1 Nov 2026 in New York; a daily job fires once that day.
    rule = normalise_rrule("daily")
    start = datetime(2026, 10, 31, 1, 30)
    first = first_occurrence(start, NY, rule)
    second = next_occurrence(rule, start, NY, first)
    third = next_occurrence(rule, start, NY, second)
    assert second.astimezone(NY).date().isoformat() == "2026-11-01"
    assert third.astimezone(NY).date().isoformat() == "2026-11-02"


def test_weekdays_start_on_saturday_moves_to_monday():
    rule = normalise_rrule("weekdays")
    first = first_occurrence(datetime(2026, 11, 14, 9, 0), KL, rule)  # Saturday
    assert first.astimezone(KL).strftime("%a %H:%M") == "Mon 09:00"


def test_latest_due_skips_backlog():
    rule = normalise_rrule("daily")
    start = datetime(2026, 11, 1, 9, 0)
    current = first_occurrence(start, KL, rule)
    now = current + timedelta(days=5, hours=2)
    occurrence, following, skipped = latest_due(rule, start, KL, current, now)
    assert skipped == 5
    assert occurrence == current + timedelta(days=5)
    assert following == current + timedelta(days=6)


def test_count_ends_series():
    rule = normalise_rrule("custom", "FREQ=DAILY;COUNT=2")
    start = datetime(2026, 11, 12, 9, 0)
    first = first_occurrence(start, KL, rule)
    second = next_occurrence(rule, start, KL, first)
    assert next_occurrence(rule, start, KL, second) is None


@pytest.mark.parametrize("bad", ["FREQ=MINUTELY", "FREQ=HOURLY", "FREQ=DAILY;INTERVAL=0", "FREQ=DAILY;BYSECOND=5", "nonsense"])
def test_rejects_spammy_or_invalid_rules(bad):
    with pytest.raises(RecurrenceError):
        normalise_rrule("custom", bad)


def test_unknown_timezone():
    with pytest.raises(RecurrenceError):
        zone("Mars/Olympus_Mons")


def test_recurrence_names_round_trip():
    for name in ("once", "daily", "weekly", "weekdays"):
        assert recurrence_name(normalise_rrule(name)) == name
    assert recurrence_name("FREQ=MONTHLY") == "custom"


def test_safety_keywords():
    assert keyword_flags("can you lend me RM50?") == {"money"}
    assert keyword_flags("I promise I'll be there") == {"commitment"}
    assert keyword_flags("so sorry for your loss") == {"emotional"}
    assert keyword_flags("free this weekend? 🍜") == set()
    assert keyword_flags("借我一点钱") == {"money"}


def test_instagram_length_limit_is_bytes():
    assert fits_instagram("a" * 1000)
    assert not fits_instagram("あ" * 400)  # 3 bytes each in UTF-8
    assert not fits_instagram("")
