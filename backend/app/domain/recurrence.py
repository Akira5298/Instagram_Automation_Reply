"""Timezone- and DST-correct recurrence.

Jobs are defined in *local wall-clock time* in an IANA zone ("8 PM every
Friday in Asia/Kuala_Lumpur") and executed at absolute UTC instants. The
RRULE is expanded over naive local datetimes, then each occurrence is
localised, so 8 PM stays 8 PM across daylight-saving changes.

Edge cases (zones with DST):
  * A local time that doesn't exist (spring-forward gap, e.g. 02:30) runs at
    the equivalent instant just after the gap (03:30).
  * A local time that happens twice (fall-back) runs once, at the first one.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dateutil.rrule import rrulestr

PRESETS: dict[str, str | None] = {
    "once": None,
    "daily": "FREQ=DAILY",
    "weekly": "FREQ=WEEKLY",
    "weekdays": "FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR",
}

_ALLOWED_FREQ = {"DAILY", "WEEKLY", "MONTHLY"}
_ALLOWED_PARTS = {"FREQ", "INTERVAL", "BYDAY", "BYMONTHDAY", "COUNT", "UNTIL"}
_MAX_SKIP = 5000


class RecurrenceError(ValueError):
    pass


def zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise RecurrenceError(f"unknown timezone {name!r}") from exc


def normalise_rrule(recurrence: str, rrule: str | None = None) -> str | None:
    """Turn a preset ("weekly") or a custom RRULE into a validated RRULE
    string, or None for one-time jobs. Sub-daily frequencies are refused so
    a typo can't make ECHO message someone every minute."""
    if recurrence != "custom":
        if recurrence not in PRESETS:
            raise RecurrenceError(f"unknown recurrence {recurrence!r}")
        return PRESETS[recurrence]
    if not rrule:
        raise RecurrenceError("custom recurrence needs an rrule")
    text = rrule.strip().removeprefix("RRULE:").upper()
    parts: dict[str, str] = {}
    for chunk in text.split(";"):
        if "=" not in chunk:
            raise RecurrenceError(f"bad rrule part {chunk!r}")
        key, value = chunk.split("=", 1)
        if key not in _ALLOWED_PARTS:
            raise RecurrenceError(f"rrule part {key} is not supported")
        parts[key] = value
    if parts.get("FREQ") not in _ALLOWED_FREQ:
        raise RecurrenceError("FREQ must be DAILY, WEEKLY or MONTHLY")
    if "INTERVAL" in parts and not (parts["INTERVAL"].isdigit() and 1 <= int(parts["INTERVAL"]) <= 52):
        raise RecurrenceError("INTERVAL must be 1-52")
    try:
        rrulestr(text, dtstart=datetime(2026, 1, 1, 9, 0))
    except (ValueError, TypeError) as exc:
        raise RecurrenceError(f"invalid rrule: {exc}") from exc
    return text


def localize(local_naive: datetime, tz: ZoneInfo) -> datetime:
    """Naive local wall time -> aware UTC instant (fold=0 handles both DST edges)."""
    return local_naive.replace(tzinfo=tz, fold=0).astimezone(UTC)


def to_local_naive(instant: datetime, tz: ZoneInfo) -> datetime:
    if instant.tzinfo is None:
        raise ValueError("instant must be timezone-aware")
    return instant.astimezone(tz).replace(tzinfo=None)


def first_occurrence(local_start: datetime, tz: ZoneInfo, rrule: str | None) -> datetime | None:
    """First run at or after `local_start` (naive local)."""
    if rrule is None:
        return localize(local_start, tz)
    nxt = rrulestr(rrule, dtstart=local_start).after(local_start, inc=True)
    return localize(nxt, tz) if nxt else None


def next_occurrence(
    rrule: str | None, dtstart_local: datetime, tz: ZoneInfo, after: datetime
) -> datetime | None:
    """Next run strictly after the instant `after`, or None when finished."""
    if rrule is None:
        return None
    rule = rrulestr(rrule, dtstart=dtstart_local)
    cursor = to_local_naive(after, tz)
    for _ in range(_MAX_SKIP):
        nxt = rule.after(cursor, inc=False)
        if nxt is None:
            return None
        instant = localize(nxt, tz)
        if instant > after:
            return instant
        cursor = nxt  # local time repeated by a fall-back; move on
    return None


def latest_due(
    rrule: str | None, dtstart_local: datetime, tz: ZoneInfo, current: datetime, now: datetime
) -> tuple[datetime, datetime | None, int]:
    """Given the job's pending occurrence `current` (<= now), skip past any
    occurrences missed while the server was down. Returns (occurrence to run,
    the one after it, number skipped). Only the latest missed occurrence runs —
    ECHO never sends a backlog of stale messages."""
    skipped = 0
    following = next_occurrence(rrule, dtstart_local, tz, current)
    while following is not None and following <= now and skipped < _MAX_SKIP:
        current = following
        following = next_occurrence(rrule, dtstart_local, tz, current)
        skipped += 1
    return current, following, skipped


def describe(recurrence: str, rrule: str | None) -> str:
    return {
        "once": "One time",
        "daily": "Every day",
        "weekly": "Every week",
        "weekdays": "Weekdays",
    }.get(recurrence, f"Custom ({rrule})" if rrule else "Custom")


def recurrence_name(rrule: str | None) -> str:
    """Map a stored RRULE back to the preset name the app shows."""
    if rrule is None:
        return "once"
    for name, preset in PRESETS.items():
        if preset == rrule:
            return name
    return "custom"


LATE_LIMIT = timedelta(minutes=15)
