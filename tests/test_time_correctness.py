"""Time handling: UTC storage, local display, DST, and junk feed dates.

Every timestamp is stored as UTC and converted only for display. These tests
pin the behaviours that were wrong or missing before the overhaul: a future
timestamp reading as "just now", no venue-local conversion at all, and feed
dates being trusted for ordering however absurd they were.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from utils.timeutil import (
    EARLIEST_PLAUSIBLE,
    format_local,
    humanize_age,
    is_future,
    is_plausible_timestamp,
    parse_iso,
    sanitize_published_at,
    to_iso,
    to_timezone,
)

NOW = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)


# --------------------------------------------------------- 28: timezones ---
def test_utc_is_converted_to_venue_local_time():
    assert "19:00" in format_local("2026-09-20T02:00:00Z", "America/Los_Angeles")


def test_daylight_saving_is_applied_per_instant():
    """The same zone gives different offsets in winter and summer."""
    winter = format_local("2026-01-20T02:00:00Z", "America/New_York")
    summer = format_local("2026-07-20T02:00:00Z", "America/New_York")
    assert "EST" in winter and "21:00" in winter
    assert "EDT" in summer and "22:00" in summer


def test_an_unknown_timezone_falls_back_to_utc_rather_than_failing():
    assert format_local("2026-09-20T02:00:00Z", "Not/AZone") != "unknown"
    assert to_timezone("2026-09-20T02:00:00Z", "Not/AZone") is not None


def test_missing_timezone_keeps_utc():
    assert "UTC" in format_local("2026-09-20T02:00:00Z", None)


# ------------------------------------------------- 20: future-dated items --
def test_a_future_timestamp_reads_as_ahead_not_as_just_now():
    assert humanize_age("2026-09-19T15:00:00Z", NOW) == "in 3h"
    assert humanize_age("2026-09-19T09:00:00Z", NOW) == "3h ago"


def test_is_future_respects_a_tolerance():
    assert is_future("2026-09-19T12:30:00Z", 0, NOW) is True
    assert is_future("2026-09-19T12:30:00Z", 60, NOW) is False


def test_a_future_publication_date_falls_back_to_collection_time():
    cleaned = sanitize_published_at("2030-01-01T00:00:00Z", "2026-09-19T10:00:00Z", NOW)
    assert cleaned == "2026-09-19T10:00:00Z"


def test_a_prehistoric_publication_date_falls_back_to_collection_time():
    cleaned = sanitize_published_at("1970-01-01T00:00:00Z", "2026-09-19T10:00:00Z", NOW)
    assert cleaned == "2026-09-19T10:00:00Z"


def test_a_small_clock_skew_is_tolerated():
    """Feeds really do stamp items a few minutes ahead; that is not corruption."""
    cleaned = sanitize_published_at("2026-09-19T12:10:00Z", "2026-09-19T12:00:00Z", NOW)
    assert cleaned == "2026-09-19T12:10:00Z"


def test_a_normal_date_is_left_alone():
    assert sanitize_published_at("2026-09-19T09:00:00Z", None, NOW) == "2026-09-19T09:00:00Z"


def test_plausibility_window():
    assert is_plausible_timestamp("2026-09-19T09:00:00Z", NOW) is True
    assert is_plausible_timestamp("1970-01-01T00:00:00Z", NOW) is False
    assert is_plausible_timestamp(to_iso(EARLIEST_PLAUSIBLE), NOW) is True


# ------------------------------------------------- malformed feed dates ----
@pytest.mark.parametrize("value", ["", None, "not a date", "2026-13-45T99:99:99Z", "??"])
def test_malformed_dates_return_none_instead_of_raising(value):
    assert parse_iso(value) is None


def test_rfc822_feed_dates_parse():
    parsed = parse_iso("Tue, 16 Sep 2026 14:03:00 -0400")
    assert parsed is not None and parsed.tzinfo is not None
    assert to_iso(parsed) == "2026-09-16T18:03:00Z"


def test_naive_timestamps_are_treated_as_utc():
    assert to_iso(parse_iso("2026-09-19T09:00:00")) == "2026-09-19T09:00:00Z"


def test_stored_format_sorts_chronologically_as_plain_strings():
    stamps = [to_iso(NOW + timedelta(hours=offset)) for offset in (5, 1, 3)]
    assert sorted(stamps) == [to_iso(NOW + timedelta(hours=offset)) for offset in (1, 3, 5)]


# ------------------------------------------- distinct timestamp meanings ---
def test_article_timestamps_are_stored_separately(ingest, make_article):
    """published_at, collected_at and the publisher's own updated stamp are
    three different facts and must not be collapsed into one column."""
    from database import repo_articles as articles_repo

    ingest([make_article(published_at="2026-09-19T08:00:00Z",
                         collected_at="2026-09-19T11:00:00Z",
                         updated_at_source="2026-09-19T10:30:00Z")])
    article = articles_repo.latest_articles(limit=1)[0]
    assert article["published_at"] == "2026-09-19T08:00:00Z"
    assert article["collected_at"] == "2026-09-19T11:00:00Z"
    assert article["updated_at_source"] == "2026-09-19T10:30:00Z"


def test_rss_collector_sanitises_a_future_feed_date():
    from collectors.rss import RSSCollector

    entry = {
        "title": "Some headline", "link": "https://example.test/x",
        "published": "2031-01-01T00:00:00Z", "summary": "text",
    }
    collector = RSSCollector({"key": "t", "name": "T", "feed_url": "https://example.test/feed"})
    item = collector.normalize(entry)
    assert item is not None
    assert not is_future(item["published_at"], 120), "a 2031 feed date must not survive ingest"
