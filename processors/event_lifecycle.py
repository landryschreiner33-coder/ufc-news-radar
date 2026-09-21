"""Event lifecycle: deciding whether an event is upcoming, live or finished.

The rule this module exists to enforce:

    **A date that has passed is not evidence that an event happened.**

The first version of this app had no lifecycle at all, so the interface fell
back on comparing ``event_date`` with today - which reported a scheduled event
as finished the moment its date arrived.  Every one of those shortcuts is
banned here:

    * ``event_date < today``        -> NOT completed
    * ``event_date == today``       -> NOT completed
    * ``scheduled_start`` has passed-> NOT completed
    * an article written in the past tense -> NOT completed

An event becomes COMPLETED only when something reliable says it finished.
Without that evidence a past event stays UNKNOWN, which is the honest answer.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from models.types import EventStatus, StatusConfidence
from utils.timeutil import parse_iso, to_iso, utcnow

#: How long a UFC card runs from the first prelim to the final decision.
#: Used only when the official page gives a start but no end.
DEFAULT_EVENT_DURATION_HOURS = 7.0

#: Evidence kinds a caller can supply.  Only the two completion kinds may ever
#: produce COMPLETED, and only when the event was actually due to have started.
EVIDENCE_CANCELLED = "cancelled"
EVIDENCE_POSTPONED = "postponed"
EVIDENCE_COMPLETED = "completed"
EVIDENCE_LIVE = "live"

#: Source types trusted to establish that an event finished.
RELIABLE_COMPLETION_SOURCES = {"OFFICIAL", "MAJOR_NEWS", "ESTABLISHED_JOURNALIST",
                               "TRUSTED_REPORTER"}


@dataclass
class EventEvidence:
    """One piece of evidence about an event's state."""

    kind: str
    source_type: str = "UNKNOWN"
    source_name: Optional[str] = None
    source_url: Optional[str] = None
    reported_at: Optional[str] = None
    official: bool = False
    detail: Optional[str] = None

    @property
    def is_reliable(self) -> bool:
        return self.official or str(self.source_type).upper() in RELIABLE_COMPLETION_SOURCES


@dataclass
class LifecycleResult:
    status: str
    confidence: str
    source: str
    reasons: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    scheduled_start_utc: Optional[str] = None
    scheduled_end_utc: Optional[str] = None
    official_source_url: Optional[str] = None

    def as_updates(self, now: Optional[datetime] = None) -> Dict[str, Any]:
        """The columns to write back onto the events row."""
        return {
            "event_status": self.status,
            "status_source": self.source,
            "status_confidence": self.confidence,
            "status_updated_at": to_iso(now or utcnow()),
        }


def scheduled_window(event: Dict[str, Any]) -> tuple:
    """(start, end) as datetimes, from the strongest schedule data available."""
    start = parse_iso(event.get("scheduled_start_utc"))
    end = parse_iso(event.get("scheduled_end_utc"))
    if start is None:
        # A date with no time tells us the day but not when the card begins.
        # We deliberately do NOT invent a start time from it.
        return None, end
    if end is None:
        end = start + timedelta(hours=DEFAULT_EVENT_DURATION_HOURS)
    return start, end


def _pick(evidence: List[EventEvidence], kind: str) -> List[EventEvidence]:
    return [item for item in evidence if item.kind == kind]


def resolve_event_status(
    event: Dict[str, Any],
    evidence: Optional[List[EventEvidence]] = None,
    now: Optional[datetime] = None,
) -> LifecycleResult:
    """Decide an event's lifecycle status from its schedule plus evidence."""
    now = now or utcnow()
    evidence = list(evidence or [])
    reasons: List[str] = []
    conflicts: List[str] = []
    start, end = scheduled_window(event)

    result = LifecycleResult(
        status=EventStatus.UNKNOWN.value,
        confidence=StatusConfidence.LOW.value,
        source="derived",
        scheduled_start_utc=to_iso(start),
        scheduled_end_utc=to_iso(end),
    )

    # -- 1. Official cancellation / postponement outrank everything ----------
    cancelled = [item for item in _pick(evidence, EVIDENCE_CANCELLED) if item.is_reliable]
    postponed = [item for item in _pick(evidence, EVIDENCE_POSTPONED) if item.is_reliable]
    if cancelled:
        best = max(cancelled, key=lambda item: (item.official, item.is_reliable))
        result.status = EventStatus.CANCELLED.value
        result.confidence = (StatusConfidence.OFFICIAL.value if best.official
                             else StatusConfidence.REPORTED.value)
        result.source = best.source_name or best.source_type
        result.official_source_url = best.source_url
        reasons.append(f"Cancellation reported by {result.source}.")
        if postponed:
            conflicts.append("Other sources describe this event as postponed, not cancelled.")
        result.reasons, result.conflicts = reasons, conflicts
        return result
    if postponed:
        best = max(postponed, key=lambda item: (item.official, item.is_reliable))
        result.status = EventStatus.POSTPONED.value
        result.confidence = (StatusConfidence.OFFICIAL.value if best.official
                             else StatusConfidence.REPORTED.value)
        result.source = best.source_name or best.source_type
        result.official_source_url = best.source_url
        reasons.append(f"Postponement reported by {result.source}.")
        result.reasons, result.conflicts = reasons, conflicts
        return result

    # Unreliable cancellation chatter is preserved as a conflict, never applied.
    weak_cancel = [item for item in _pick(evidence, EVIDENCE_CANCELLED) if not item.is_reliable]
    if weak_cancel:
        conflicts.append(
            "Cancellation has been reported but is not officially confirmed "
            f"({', '.join(sorted({item.source_name or item.source_type for item in weak_cancel}))})."
        )

    # -- 2. Completion needs evidence, never a calendar ---------------------
    completions = [item for item in _pick(evidence, EVIDENCE_COMPLETED) if item.is_reliable]
    if completions:
        # A result cannot pre-date the event it claims to report on.
        if start is not None:
            completions = [
                item for item in completions
                if parse_iso(item.reported_at) is None or parse_iso(item.reported_at) >= start
            ]
        if completions:
            best = max(completions, key=lambda item: (item.official, item.is_reliable))
            result.status = EventStatus.COMPLETED.value
            result.confidence = (StatusConfidence.OFFICIAL.value if best.official
                                 else StatusConfidence.REPORTED.value)
            result.source = best.source_name or best.source_type
            result.official_source_url = best.source_url
            reasons.append(f"Completion evidence collected from {result.source}.")
            result.reasons, result.conflicts = reasons, conflicts
            return result
        conflicts.append(
            "Completion was claimed by a source dated before the event was due to start; ignored."
        )

    # -- 3. No schedule means no derivation is possible ----------------------
    if start is None:
        day = str(event.get("event_date") or "")[:10]
        if day and len(day) == 10:
            today = now.strftime("%Y-%m-%d")
            if day > today:
                result.status = EventStatus.UPCOMING.value
                result.confidence = StatusConfidence.LOW.value
                reasons.append(
                    f"Scheduled for {day}, which is still in the future. "
                    "No start time has been collected, so the exact window is unknown."
                )
                result.reasons, result.conflicts = reasons, conflicts
                return result
            reasons.append(
                f"Scheduled for {day} but no start time was collected, so the app cannot tell "
                "whether it has started or finished. Status stays unknown rather than guessing."
            )
        else:
            reasons.append("No date or start time has been collected for this event.")
        result.reasons, result.conflicts = reasons, conflicts
        return result

    # -- 4. Derive from the official schedule and the clock ------------------
    result.source = "official schedule"
    result.confidence = StatusConfidence.DERIVED.value
    result.official_source_url = event.get("official_source_url") or event.get("ufc_url")

    if now < start:
        result.status = EventStatus.UPCOMING.value
        reasons.append(f"Scheduled start {to_iso(start)} has not arrived yet.")
    elif start <= now < end:
        result.status = EventStatus.LIVE.value
        reasons.append(
            f"Inside the scheduled window ({to_iso(start)} to {to_iso(end)})."
        )
        live_evidence = _pick(evidence, EVIDENCE_LIVE)
        if live_evidence:
            result.confidence = StatusConfidence.REPORTED.value
            reasons.append("Live coverage was collected for this event.")
    else:
        # Past the window with nothing saying it finished.  This is the case
        # the old code got wrong by declaring the event completed.
        result.status = EventStatus.UNKNOWN.value
        result.confidence = StatusConfidence.LOW.value
        reasons.append(
            f"The scheduled window ended at {to_iso(end)}, but no reliable source has been "
            "collected confirming the event took place. The app will not assume it did."
        )

    result.reasons, result.conflicts = reasons, conflicts
    return result


# ------------------------------------------------ one status explanation --
#: What the app actually knows about when the event starts.
SCHEDULE_EXACT = "EXACT_START_KNOWN"
SCHEDULE_DATE_ONLY = "DATE_KNOWN_TIME_UNKNOWN"
SCHEDULE_NONE = "NO_SCHEDULE_COLLECTED"


def schedule_state(event: Dict[str, Any]) -> str:
    """Which of the three schedule situations this event is in."""
    if parse_iso(event.get("scheduled_start_utc")) is not None:
        return SCHEDULE_EXACT
    day = str(event.get("event_date") or "")[:10]
    if len(day) == 10:
        return SCHEDULE_DATE_ONLY
    return SCHEDULE_NONE


def status_explanation(event: Dict[str, Any]) -> str:
    """One sentence about this event's status, true of *this* event's data.

    Built from the fields that are actually present rather than from a fixed
    sentence per status. The previous build printed "The start time collected
    from the official schedule is still in the future" directly above "No
    start time has been collected" - two statements that cannot both hold.
    """
    status = str(event.get("event_status") or EventStatus.UNKNOWN.value).upper()
    schedule = schedule_state(event)
    start = to_iso(parse_iso(event.get("scheduled_start_utc")))
    day = str(event.get("event_date") or "")[:10]

    if status == EventStatus.CANCELLED.value:
        return "Cancelled according to the collected sources. It is not going ahead."
    if status == EventStatus.POSTPONED.value:
        return ("Postponed according to the collected sources. No new date has been collected."
                if schedule != SCHEDULE_EXACT else
                f"Postponed according to the collected sources. The schedule still reads {start}.")
    if status == EventStatus.COMPLETED.value:
        return ("Reliable evidence was collected that this event finished. "
                "A date that has passed is never enough on its own.")
    if status == EventStatus.LIVE.value:
        return (f"The scheduled start ({start}) has passed and the event is inside its "
                "expected running window.")
    if status == EventStatus.UPCOMING.value:
        if schedule == SCHEDULE_EXACT:
            return f"Scheduled to start at {start}, which is still in the future."
        if schedule == SCHEDULE_DATE_ONLY:
            return (f"Scheduled for {day}, which is still in the future. No start time has been "
                    "collected, so the exact window is unknown.")
        return "Described as upcoming in the collected sources, with no date collected yet."
    # UNKNOWN
    if schedule == SCHEDULE_EXACT:
        return (f"The scheduled window that began at {start} has passed, and no reliable source "
                "has been collected confirming the event took place. The app will not assume it did.")
    if schedule == SCHEDULE_DATE_ONLY:
        return (f"Scheduled for {day}, but no start time was collected, so the app cannot tell "
                "whether it has started or finished. It will not guess.")
    return "No date or start time has been collected for this event, so its status is unknown."


def is_upcoming(status: Optional[str]) -> bool:

    return str(status or "").upper() in (EventStatus.UPCOMING.value, EventStatus.LIVE.value)


def countdown_parts(scheduled_start_utc: Optional[str],
                    now: Optional[datetime] = None) -> Optional[Dict[str, int]]:
    """Days/hours/minutes until an event starts, or None when it is not ahead."""
    start = parse_iso(scheduled_start_utc)
    if start is None:
        return None
    delta = start - (now or utcnow())
    total = int(delta.total_seconds())
    if total <= 0:
        return None
    return {
        "days": total // 86400,
        "hours": (total % 86400) // 3600,
        "minutes": (total % 3600) // 60,
        "total_seconds": total,
    }


def format_countdown(scheduled_start_utc: Optional[str],
                     now: Optional[datetime] = None) -> Optional[str]:
    parts = countdown_parts(scheduled_start_utc, now)
    if parts is None:
        return None
    if parts["days"]:
        return f"{parts['days']}d {parts['hours']}h"
    if parts["hours"]:
        return f"{parts['hours']}h {parts['minutes']}m"
    return f"{parts['minutes']}m"
