"""Time helpers.

Every timestamp in the database is stored as an ISO-8601 UTC string in the
exact format ``YYYY-MM-DDTHH:MM:SSZ``.  A fixed-width format means plain
string comparison in SQL sorts chronologically, and the same strings parse
cleanly into PostgreSQL ``timestamptz`` columns later.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional

ISO_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: Optional[datetime]) -> Optional[str]:
    """Format a datetime as the canonical UTC string used by the database."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).strftime(ISO_FORMAT)


def utcnow_iso() -> str:
    return to_iso(utcnow())  # type: ignore[return-value]


def parse_iso(value: Any) -> Optional[datetime]:
    """Parse a stored timestamp (or any reasonable date string) to aware UTC.

    Returns ``None`` instead of raising: feeds contain a lot of junk dates and
    a bad date must never crash collection.
    """
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    text = str(value).strip()
    try:
        # Fast path for our own canonical format.
        return datetime.strptime(text, ISO_FORMAT).replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    try:  # last resort: RFC-822 style feed dates, e.g. "Tue, 16 Sep 2026 14:03:00 -0400"
        from dateutil import parser as dateutil_parser

        parsed = dateutil_parser.parse(text)
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def struct_time_to_iso(struct_time: Any) -> Optional[str]:
    """Convert feedparser's ``time.struct_time`` (always UTC) to our format."""
    if not struct_time:
        return None
    try:
        import calendar

        stamp = calendar.timegm(struct_time)
        return to_iso(datetime.fromtimestamp(stamp, tz=timezone.utc))
    except Exception:
        return None


def hours_ago_iso(hours: float) -> str:
    return to_iso(utcnow() - timedelta(hours=hours))  # type: ignore[return-value]


def minutes_between(earlier: Any, later: Any) -> Optional[float]:
    start, end = parse_iso(earlier), parse_iso(later)
    if start is None or end is None:
        return None
    return (end - start).total_seconds() / 60.0


def age_hours(value: Any, now: Optional[datetime] = None) -> Optional[float]:
    parsed = parse_iso(value)
    if parsed is None:
        return None
    return ((now or utcnow()) - parsed).total_seconds() / 3600.0


def humanize_age(value: Any, now: Optional[datetime] = None) -> str:
    """'3h ago' style label used all over the dashboard."""
    hours = age_hours(value, now)
    if hours is None:
        return "unknown time"
    minutes = hours * 60
    if minutes < 1:
        return "just now"
    if minutes < 60:
        return f"{int(minutes)}m ago"
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24
    if days < 7:
        return f"{int(days)}d ago"
    weeks = days / 7
    if weeks < 6:
        return f"{int(weeks)}w ago"
    return f"{int(days / 30)}mo ago"


def format_display(value: Any, fmt: str = "%b %d, %Y %H:%M UTC") -> str:
    parsed = parse_iso(value)
    return parsed.strftime(fmt) if parsed else "unknown"
