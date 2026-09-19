"""Event lifecycle regression tests.

The headline case: **UFC 331 is scheduled for 19 September 2026 and must never
be reported as completed just because that date has arrived or passed.**

None of these tests special-case UFC 331 - they exercise the generic lifecycle
rules using it as the worked example, so the same guarantees hold for every
event.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from database import repo_entities as entities_repo
from models.types import EventStatus, StatusConfidence
from processors.event_identity import canonical_key, identity_keys
from processors.event_lifecycle import (
    EVIDENCE_CANCELLED,
    EVIDENCE_COMPLETED,
    EVIDENCE_POSTPONED,
    EventEvidence,
    format_countdown,
    resolve_event_status,
)

# UFC 331: Saturday 19 September 2026, first bout 22:00 local / 02:00 UTC next day.
UFC_331 = {
    "name": "Crypto.com UFC 331",
    "event_date": "2026-09-19",
    "scheduled_start_utc": "2026-09-20T02:00:00Z",
    "ufc_url": "https://www.ufc.com/event/ufc-331",
    "local_timezone": "America/Los_Angeles",
}


def at(moment: str) -> datetime:
    return datetime.fromisoformat(moment.replace("Z", "+00:00"))


# --------------------------------------------------------- 1-3: UFC 331 ----
def test_ufc_331_before_event_start_is_upcoming():
    result = resolve_event_status(UFC_331, now=at("2026-09-19T12:00:00Z"))
    assert result.status == EventStatus.UPCOMING.value
    assert result.confidence == StatusConfidence.DERIVED.value
    assert result.reasons


def test_ufc_331_on_the_event_day_before_start_is_not_completed():
    """The exact bug: the event's date is today, so the old code called it over."""
    result = resolve_event_status(UFC_331, now=at("2026-09-19T23:59:00Z"))
    assert result.status == EventStatus.UPCOMING.value
    assert result.status != EventStatus.COMPLETED.value


def test_ufc_331_after_event_start_is_live():
    result = resolve_event_status(UFC_331, now=at("2026-09-20T04:00:00Z"))
    assert result.status == EventStatus.LIVE.value


def test_ufc_331_after_start_is_never_completed_without_evidence():
    """A start time that has passed is not evidence the event finished."""
    result = resolve_event_status(UFC_331, now=at("2026-09-25T00:00:00Z"))
    assert result.status == EventStatus.UNKNOWN.value
    assert "will not assume" in " ".join(result.reasons).lower()


def test_ufc_331_after_reliable_completion_evidence_is_completed():
    evidence = [EventEvidence(EVIDENCE_COMPLETED, source_type="MAJOR_NEWS",
                              source_name="ESPN", reported_at="2026-09-20T08:30:00Z")]
    result = resolve_event_status(UFC_331, evidence, now=at("2026-09-21T00:00:00Z"))
    assert result.status == EventStatus.COMPLETED.value
    assert result.source == "ESPN"


# ------------------------------------------------- 4: same-day behaviour ----
def test_same_day_event_without_a_start_time_stays_unknown():
    """Knowing only the day is not enough to say whether it has run."""
    event = {"name": "UFC 331", "event_date": "2026-09-19"}
    result = resolve_event_status(event, now=at("2026-09-19T18:00:00Z"))
    assert result.status == EventStatus.UNKNOWN.value
    assert result.status != EventStatus.COMPLETED.value


def test_future_dated_event_without_a_start_time_is_upcoming():
    event = {"name": "UFC 999", "event_date": "2026-12-25"}
    result = resolve_event_status(event, now=at("2026-09-19T18:00:00Z"))
    assert result.status == EventStatus.UPCOMING.value


def test_past_dated_event_without_evidence_is_unknown_not_completed():
    event = {"name": "UFC 300", "event_date": "2024-04-13"}
    result = resolve_event_status(event, now=at("2026-09-19T18:00:00Z"))
    assert result.status == EventStatus.UNKNOWN.value


# ------------------------------------------------- 5-6: cancel / postpone ---
def test_officially_cancelled_event():
    evidence = [EventEvidence(EVIDENCE_CANCELLED, source_type="OFFICIAL",
                              source_name="UFC.com", official=True)]
    result = resolve_event_status(UFC_331, evidence, now=at("2026-09-18T00:00:00Z"))
    assert result.status == EventStatus.CANCELLED.value
    assert result.confidence == StatusConfidence.OFFICIAL.value


def test_officially_postponed_event():
    evidence = [EventEvidence(EVIDENCE_POSTPONED, source_type="OFFICIAL",
                              source_name="UFC.com", official=True)]
    result = resolve_event_status(UFC_331, evidence, now=at("2026-09-18T00:00:00Z"))
    assert result.status == EventStatus.POSTPONED.value


def test_weak_cancellation_chatter_never_cancels_an_event():
    """A fan account saying it is off does not take the event off the schedule."""
    evidence = [EventEvidence(EVIDENCE_CANCELLED, source_type="FAN_ACCOUNT",
                              source_name="@somefan")]
    result = resolve_event_status(UFC_331, evidence, now=at("2026-09-18T00:00:00Z"))
    assert result.status == EventStatus.UPCOMING.value
    assert result.conflicts, "the claim must be preserved as a conflict"
    assert "not officially confirmed" in result.conflicts[0]


def test_completion_claim_dated_before_the_event_is_rejected():
    """A preview published last week cannot prove the event already happened."""
    evidence = [EventEvidence(EVIDENCE_COMPLETED, source_type="MAJOR_NEWS",
                              source_name="ESPN", reported_at="2026-09-10T08:00:00Z")]
    result = resolve_event_status(UFC_331, evidence, now=at("2026-09-25T00:00:00Z"))
    assert result.status != EventStatus.COMPLETED.value
    assert any("before the event" in note for note in result.conflicts)


def test_unreliable_completion_source_does_not_complete_an_event():
    evidence = [EventEvidence(EVIDENCE_COMPLETED, source_type="FAN_ACCOUNT",
                              source_name="@somefan", reported_at="2026-09-20T08:00:00Z")]
    result = resolve_event_status(UFC_331, evidence, now=at("2026-09-21T00:00:00Z"))
    assert result.status == EventStatus.UNKNOWN.value


# ------------------------------------------- 7-8: canonical / duplicates ----
@pytest.mark.parametrize("name", [
    "UFC 331",
    "Crypto.com UFC 331",
    "Crypto.com UFC 331: Van vs Pantoja 2",
    "UFC 331: Van vs Pantoja 2",
    "ufc  331",
])
def test_every_spelling_of_ufc_331_is_one_canonical_event(name):
    assert canonical_key(name) == "ufc:331"


def test_canonical_key_prefers_the_official_event_id():
    assert canonical_key("UFC 331", official_event_id="1234") == "ufcid:1234"


def test_event_number_is_not_confused_by_other_digits():
    assert canonical_key("Fighter signs 4-fight UFC deal worth $2026") != "ufc:2026"


def test_fight_night_identity_survives_a_missing_date():
    with_url = identity_keys("UFC Fight Night: Silva vs Costa",
                             "https://www.ufc.com/event/ufc-fight-night-october-31-2026")
    without = identity_keys("UFC Fight Night: Silva vs Costa")
    assert set(with_url) & set(without), "the two spellings must share an identity"


def test_duplicate_event_names_resolve_to_a_single_row():
    first = entities_repo.upsert_event("UFC 331", event_date="2026-09-19")
    second = entities_repo.upsert_event("Crypto.com UFC 331: Van vs Pantoja 2",
                                        data_origin="collected",
                                        ufc_url="https://www.ufc.com/event/ufc-331")
    assert first == second
    events = entities_repo.list_events(limit=50)
    assert len([event for event in events if "331" in event["name"]]) == 1


def test_the_fuller_official_name_wins_as_the_display_name():
    entities_repo.upsert_event("UFC 331")
    entities_repo.upsert_event("UFC 331: Van vs Pantoja 2", data_origin="collected")
    event = entities_repo.get_event_by_name("UFC 331")
    assert event["name"] == "UFC 331: Van vs Pantoja 2"


def test_merged_events_keep_old_links_working():
    """An id that was merged away still resolves, so bookmarks do not break."""
    from database.db import execute, get_connection
    from database.event_migration import backfill

    keep = entities_repo.upsert_event("UFC 331: Van vs Pantoja 2", data_origin="collected")
    # Simulate a pre-migration duplicate row that bypassed canonical identity.
    stale = execute(
        "INSERT INTO events (name, normalized_name, canonical_key, event_status, data_origin, "
        "mention_count, is_demo, created_at, updated_at) VALUES (?,?,?,?,?,?,0,?,?)",
        ("UFC 331", "ufc 331", "legacy:ufc-331", "UNKNOWN", "detected", 3,
         "2026-09-01T00:00:00Z", "2026-09-01T00:00:00Z"))
    backfill(get_connection())
    assert entities_repo.get_event(stale)["id"] == keep


def test_lifecycle_status_is_persisted_with_its_reasons():
    event_id = entities_repo.upsert_event(
        "UFC 331", data_origin="collected",
        scheduled_start_utc="2026-09-20T02:00:00Z", event_date="2026-09-19")
    event = entities_repo.get_event(event_id)
    result = resolve_event_status(event, now=at("2026-09-19T12:00:00Z"))
    entities_repo.set_event_lifecycle(event_id, result)
    stored = entities_repo.get_event(event_id)
    assert stored["event_status"] == EventStatus.UPCOMING.value
    assert stored["status_reasons"], "a status must always ship with its reasons"


def test_upcoming_listing_uses_lifecycle_not_the_calendar():
    """An event whose date passed with no completion evidence is still listed."""
    past = entities_repo.upsert_event("UFC 329", data_origin="collected",
                                      event_date="2026-01-01")
    listed = {event["id"] for event in entities_repo.list_events(limit=50, upcoming_only=True)}
    assert past in listed, "a past date alone must not hide an event as finished"


# ----------------------------------------------------------- 28: countdown --
def test_countdown_is_calculated_not_hard_coded():
    start = datetime(2026, 9, 20, 2, 0, tzinfo=timezone.utc)
    now = start - timedelta(days=1, hours=3)
    assert format_countdown("2026-09-20T02:00:00Z", now) == "1d 3h"
    assert format_countdown("2026-09-20T02:00:00Z", start + timedelta(hours=1)) is None
