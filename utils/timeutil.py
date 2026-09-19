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


def format_display(value: Any, fmt: str = "%b %d, %Y %H:%M UTC") -> str:
    parsed = parse_iso(value)
    return parsed.strftime(fmt) if parsed else "unknown"


# ------------------------------------------------------- plausibility ------
#: Anything outside this range is a broken feed date, not a real timestamp.
EARLIEST_PLAUSIBLE = datetime(1993, 11, 12, tzinfo=timezone.utc)  # UFC 1
#: How far ahead a *publication* date may sit before we treat it as broken.
#: Feeds do occasionally post a few minutes into the future via clock skew.
FUTURE_TOLERANCE_MINUTES = 90


def is_future(value: Any, tolerance_minutes: float = 0.0,
              now: Optional[datetime] = None) -> bool:
    parsed = parse_iso(value)
    if parsed is None:
        return False
    return parsed > (now or utcnow()) + timedelta(minutes=tolerance_minutes)


def is_plausible_timestamp(value: Any, now: Optional[datetime] = None,
                           future_years: int = 5) -> bool:
    """Reject dates that cannot be real, rather than sorting the feed by them."""
    parsed = parse_iso(value)
    if parsed is None:
        return False
    reference = now or utcnow()
    return EARLIEST_PLAUSIBLE <= parsed <= reference + timedelta(days=365 * future_years)


def sanitize_published_at(value: Any, collected_at: Any = None,
                          now: Optional[datetime] = None) -> Optional[str]:
    """Clean a publisher's date before it is trusted for ordering or freshness.

    A feed that stamps items in the future would otherwise pin them to the top
    of the feed permanently, and one stamped in 1970 would look ancient. Both
    fall back to when we collected the item, which we do know.
    """
    reference = now or utcnow()
    parsed = parse_iso(value)
    if parsed is None:
        return to_iso(parse_iso(collected_at)) if collected_at else None
    if parsed > reference + timedelta(minutes=FUTURE_TOLERANCE_MINUTES):
        return to_iso(parse_iso(collected_at) or reference)
    if parsed < EARLIEST_PLAUSIBLE:
        return to_iso(parse_iso(collected_at) or reference)
    return to_iso(parsed)


# ----------------------------------------------------------- timezones -----
def to_timezone(value: Any, zone: Optional[str]) -> Optional[datetime]:
    """Convert a stored UTC timestamp into a venue's local time.

    ``zoneinfo`` applies the right DST offset for that specific instant, which
    is why events are stored in UTC and converted only for display.
    """
    parsed = parse_iso(value)
    if parsed is None:
        return None
    if not zone:
        return parsed
    try:
        from zoneinfo import ZoneInfo

        return parsed.astimezone(ZoneInfo(str(zone)))
    except Exception:
        # An unknown or misspelled zone must not break the page.
        return parsed


def format_local(value: Any, zone: Optional[str],
                 fmt: str = "%a %d %b %Y · %H:%M") -> str:
    """Local time with its abbreviation, e.g. 'Sat 19 Sep 2026 · 19:00 PDT'."""
    local = to_timezone(value, zone)
    if local is None:
        return "unknown"
    label = local.strftime("%Z") or ("UTC" if not zone else str(zone))
    return f"{local.strftime(fmt)} {label}".strip()


def humanize_age(value: Any, now: Optional[datetime] = None) -> str:
    """'3h ago' style label, and 'in 3h' for anything still ahead of us.

    A future timestamp used to read as "just now", which hid feed dates that
    were plainly wrong.
    """
    hours = age_hours(value, now)
    if hours is None:
        return "unknown time"
    ahead = hours < 0
    minutes = abs(hours) * 60
    magnitude = abs(hours)
    if minutes < 1:
        return "just now"
    if minutes < 60:
        text = f"{int(minutes)}m"
    elif magnitude < 24:
        text = f"{int(magnitude)}h"
    elif magnitude / 24 < 7:
        text = f"{int(magnitude / 24)}d"
    elif magnitude / 24 / 7 < 6:
        text = f"{int(magnitude / 24 / 7)}w"
    else:
        text = f"{int(magnitude / 24 / 30)}mo"
    return f"in {text}" if ahead else f"{text} ago"
