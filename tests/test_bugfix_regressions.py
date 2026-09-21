"""Regression tests for the faults found by the website inspection.

Each test is named after the defect it locks down (D1..D12 plus the smaller
items) so a future change that reintroduces one fails here by name.
"""
from __future__ import annotations

import pytest

from models.types import StoryStatus, source_count_line, source_counts
from ui.cards import card_html


# ----------------------------------------------------------------- D1 ------
ALL_STATUSES = [status.value for status in StoryStatus]


def _story(**overrides):
    story = {
        "id": 1,
        "headline": "Fighter A vs Fighter B booked for UFC 400",
        "summary": "A short summary of the development.",
        "status": StoryStatus.CONFIRMED.value,
        "category": "fight_announcement",
        "source_count": 3,
        "independent_source_count": 2,
        "social_post_count": 0,
        "article_count": 3,
        "last_updated_at": "2026-09-20T10:00:00Z",
        "fighters": ["Fighter A", "Fighter B"],
        "events": ["UFC 400"],
    }
    story.update(overrides)
    return story


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_d1_card_markup_never_contains_a_newline(status):
    """A newline lets CommonMark end the HTML block and escape the footer.

    That is exactly how ``<div class="foot">`` ended up printed on the
    dashboard as a grey code block.
    """
    markup = card_html(_story(status=status))
    assert "\n" not in markup
    assert "  " not in markup.replace("&nbsp;", ""), "no indentation may creep back in"


@pytest.mark.parametrize("status", ALL_STATUSES)
def test_d1_card_footer_is_real_markup_for_every_status(status):
    markup = card_html(_story(status=status))
    assert '<div class="foot">' in markup
    assert "&lt;div" not in markup, "markup must not arrive pre-escaped"
    # The footer must carry its content, not an empty shell.
    assert "news source" in markup


def test_d1_empty_claim_block_does_not_break_the_card():
    """The RUMOR-only claim block used to leave a blank line behind it."""
    confirmed = card_html(_story(status=StoryStatus.CONFIRMED.value))
    rumor = card_html(_story(status=StoryStatus.RUMOR.value))
    assert "claimline" not in confirmed
    assert "claimline" in rumor
    for markup in (confirmed, rumor):
        assert markup.startswith('<div class="ncard')
        assert markup.endswith("</div>")


# ----------------------------------------------------------------- D4 ------
def test_d4_independent_can_never_exceed_total_on_screen():
    """Even a row written by an older build must render a possible claim."""
    counts = source_counts({"source_count": 2, "independent_source_count": 3,
                            "social_post_count": 3})
    assert counts["independent"] <= counts["total"]
    assert counts == {"total": 2, "independent": 2, "social": 3}


def test_d4_social_posts_are_named_separately_from_news_sources():
    line = source_count_line({"source_count": 2, "independent_source_count": 2,
                              "social_post_count": 3})
    assert "2 news sources" in line
    assert "2 independent" in line
    assert "3 X posts" in line


def test_d4_linked_x_post_does_not_become_a_news_source(ingest, make_article):
    """Two outlets plus an X post is two news sources, not three."""
    from database import repo_social as social_repo
    from database import repo_stories as stories_repo
    from processors import pipeline

    articles = ingest([
        make_article(title="Fighter A vs Fighter B is booked for UFC 400",
                     source_name="ESPN", independence_group="espn.com",
                     source_type="MAJOR_NEWS"),
        make_article(title="Fighter A vs Fighter B booked for UFC 400",
                     source_name="MMA Fighting", independence_group="mmafighting.com",
                     source_type="MAJOR_NEWS"),
    ])
    pipeline.process_all()
    story = stories_repo.list_stories(limit=1)[0]

    post_id = social_repo.upsert_post({
        "platform": "x", "post_id": "1", "username": "reporter",
        "account_type": "ESTABLISHED_JOURNALIST", "account_verified": 1,
        "text": "Fighter A vs Fighter B is booked for UFC 400.",
        "created_at_source": articles[0]["collected_at"],
        "collected_at": articles[0]["collected_at"],
        "fighters": ["Fighter A"], "events": ["UFC 400"],
    })
    social_repo.link_post_to_story(int(story["id"]), int(post_id), 0.9, ["test link"])
    pipeline.recompute_story(int(story["id"]))

    refreshed = stories_repo.get_story(int(story["id"]))
    assert refreshed["source_count"] == 2, "an X post is not a news source"
    assert refreshed["independent_source_count"] <= refreshed["source_count"]
    assert refreshed["social_post_count"] == 1


def test_d4_verification_result_keeps_the_pools_apart():
    from processors.verification import evaluate_story

    verdict = evaluate_story(
        {"category": "fight_announcement"},
        [{"source_name": "ESPN", "independence_group": "espn.com",
          "source_type": "MAJOR_NEWS", "title": "Booked", "published_at": None}],
        [{"username": "reporter", "account_type": "ESTABLISHED_JOURNALIST",
          "text": "Booked.", "created_at_source": None}],
    )
    assert verdict.source_count == 1
    assert verdict.independent_source_count == 1
    assert verdict.social_signal_count == 1
    assert verdict.corroboration_count == 2
    assert verdict.independent_source_count <= verdict.source_count


# ----------------------------------------------------------------- D3 ------
def _failing_run(monkeypatch, attempted=14, ok=0):
    """Drive a real collection run whose sources all fail."""
    from collectors import runner as runner_mod
    from collectors.runner import SourceOutcome

    calls = {"n": 0}

    def fake_run_one(source, client):
        calls["n"] += 1
        succeed = calls["n"] <= ok
        return SourceOutcome(source["key"], source["name"], succeed,
                             error=None if succeed else "connection refused",
                             error_kind=None if succeed else "network"), None

    monkeypatch.setattr(runner_mod, "_run_one_source", fake_run_one)
    return runner_mod.run_collection(trigger="test", collect_social=False, process=False)


def test_d3_a_run_where_every_source_fails_is_not_reported_as_an_update(monkeypatch):
    from database import repo_runs as runs_repo
    from database import repo_settings as settings_repo

    result = _failing_run(monkeypatch)
    assert result.outcome == "TOTAL_FAILURE"
    assert "COLLECTION FAILED" in result.headline
    assert result.collected_anything is False
    # The freshness stamp must not move when nothing was collected.
    assert not settings_repo.get_setting("last_collection_at", "")

    status = runs_repo.collection_status()
    assert status["outcome"] == "TOTAL_FAILURE"
    assert status["needs_attention"] is True
    assert status["is_stale"] is True
    assert status["succeeded_at"] is None
    assert "0 of" in status["headline"]


def test_d3_partial_success_is_labelled_partial(monkeypatch):
    from database import repo_runs as runs_repo

    result = _failing_run(monkeypatch, ok=3)
    assert result.outcome == "PARTIAL"
    assert result.sources_ok == 3
    status = runs_repo.collection_status()
    assert status["outcome"] == "PARTIAL"
    assert status["needs_attention"] is True
    assert f"{result.sources_ok} of {result.sources_attempted}" in status["headline"]


def test_d3_a_fully_successful_run_says_so(monkeypatch):
    from database import repo_runs as runs_repo
    from database import repo_settings as settings_repo

    result = _failing_run(monkeypatch, ok=99)
    assert result.outcome == "SUCCESS"
    assert settings_repo.get_setting("last_collection_at", "")
    status = runs_repo.collection_status()
    assert status["outcome"] == "SUCCESS"
    assert status["needs_attention"] is False
    assert status["succeeded_at"]


def test_d3_status_before_any_run_is_not_run():
    from database import repo_runs as runs_repo

    status = runs_repo.collection_status()
    assert status["outcome"] == "NOT_RUN"
    assert status["needs_attention"] is False
    assert status["succeeded_at"] is None


def test_d3_the_outcome_is_derived_from_the_run_numbers_not_trusted():
    """finish_run recomputes the outcome, so it cannot be recorded wrongly."""
    from database import repo_runs as runs_repo

    run_id = runs_repo.start_run("test")
    runs_repo.finish_run(run_id, sources_attempted=14, sources_ok=0, sources_failed=14,
                         outcome="SUCCESS")
    assert runs_repo.last_run()["outcome"] == "TOTAL_FAILURE"


# ------------------------------------------------- source health counting --
def test_source_health_states_are_exhaustive_and_reconcile():
    """SOURCES 16 / HEALTHY 9 / FAILING 5 left two sources unaccounted for."""
    from database import repo_sources as sources_repo

    summary = sources_repo.health_summary()
    assert summary["total"] > 0
    assert sum(summary["counts"].values()) == summary["total"]
    assert summary["working"] + summary["failing"] + summary["inactive"] == summary["total"]
    assert {source["health_state"] for source in summary["sources"]} <= set(
        sources_repo.HEALTH_STATES)


def test_every_source_health_state_is_reachable_and_exclusive():
    from database import repo_sources as sources_repo

    cases = {
        sources_repo.DISABLED: {"enabled": 0, "feed_url": "https://x.test/f"},
        sources_repo.NOT_CONFIGURED: {"enabled": 1, "feed_url": None, "fallback_urls": []},
        sources_repo.ERROR: {"enabled": 1, "feed_url": "https://x.test/f", "status": "error"},
        sources_repo.NOT_RUN: {"enabled": 1, "feed_url": "https://x.test/f", "status": "unknown"},
        sources_repo.STALE: {"enabled": 1, "feed_url": "https://x.test/f", "status": "ok",
                             "last_attempt_at": "2020-01-01T00:00:00Z",
                             "last_success_at": "2020-01-01T00:00:00Z"},
        sources_repo.PARTIAL: {"enabled": 1, "feed_url": "https://x.test/f", "status": "ok",
                               "last_attempt_at": _now(), "last_success_at": _now(),
                               "article_count": 0},
        sources_repo.HEALTHY: {"enabled": 1, "feed_url": "https://x.test/f", "status": "ok",
                               "last_attempt_at": _now(), "last_success_at": _now(),
                               "article_count": 5},
    }
    for expected, source in cases.items():
        assert sources_repo.health_state(source) == expected, expected


def _now():
    from utils.timeutil import utcnow_iso

    return utcnow_iso()


# ----------------------------------------------------------------- D7 ------
def _demo_story():
    """Load the demo data and return the officially-announced booking story."""
    from database import repo_stories as stories_repo
    from database.demo_data import load_demo_data

    load_demo_data()
    for story in stories_repo.list_stories(limit=40):
        if "Alpha" in (story.get("headline") or ""):
            return story
    raise AssertionError("demo booking story not found")


def test_d7_entities_are_detected_when_the_headline_names_them():
    from ai.context import build_story_context

    story = _demo_story()
    context = build_story_context(int(story["id"]), story)
    assert context.fighters, "the headline names two fighters"
    assert context.events, "the headline names the event"
    assert any("DEMO FIGHT NIGHT" in name.upper() for name in context.events)


def test_d7_an_event_is_not_both_named_and_unnamed():
    from ai import service as ai_service
    from ai.context import build_story_context
    from ai.templates import build_reporting_check

    story = _demo_story()
    context = build_story_context(int(story["id"]), story)
    check = build_reporting_check(context)
    flat = " ".join(line for lines in check.values() for line in lines)

    names_event = any("DEMO FIGHT NIGHT" in line.upper()
                      for line in check["confirmed_facts"] + check["reported_claims"])
    denies_event = "No event has been named" in flat
    assert not (names_event and denies_event), (
        "the same screen must not name the event and deny one was named")
    assert not denies_event, "the event is named in the headline and linked to the story"


def test_d7_still_unknown_does_not_repeat_not_safe_to_state():
    from ai.context import build_story_context
    from ai.templates import build_reporting_check

    story = _demo_story()
    context = build_story_context(int(story["id"]), story)
    check = build_reporting_check(context)
    unconfirmed = {line[2:].strip() for line in check["unconfirmed"]}
    missing = {line[2:].strip() for line in check["missing"]}
    assert not (unconfirmed & missing), "the two sections must not list the same lines"


def test_d7_an_event_the_app_already_knows_is_found_by_name():
    """UFC 331 in any wording, once the event exists - no regex special case."""
    from database import repo_entities as entities_repo
    from processors.entities import find_events, reset_event_index

    entities_repo.upsert_event("Crypto.com UFC 331: Van vs Pantoja 2", data_origin="official")
    reset_event_index()
    found = find_events("Tickets for Crypto.com UFC 331: Van vs Pantoja 2 go on sale Friday.")
    assert any("331" in name for name in found)


def test_d7_a_matchup_headline_is_not_learned_from_non_person_words():
    from processors.entities import FighterIndex, find_fighters

    assert find_fighters("UFC Fight Night vs. The Main Card", FighterIndex()) == []


# ---------------------------------------------------------------- D10 ------
D10_STATUSES = ALL_STATUSES
D10_ENTITY_CASES = [
    ([], []),                                   # nothing detected - the failing case
    (["Jon Jones"], []),
    (["Jon Jones", "Tom Aspinall"], ["UFC 331"]),
    ([], ["UFC 331"]),
]


def _context(status, fighters, events, headline="Something happened at the weekend"):
    from ai.context import StoryContext

    return StoryContext(story={
        "id": 1, "headline": headline, "status": status, "category": "general",
        "source_count": 0, "independent_source_count": 0, "social_post_count": 0,
        "first_seen_at": None, "last_updated_at": None,
        "fighters": fighters, "events": events,
    })


@pytest.mark.parametrize("status", D10_STATUSES)
@pytest.mark.parametrize("fighters,events", D10_ENTITY_CASES)
def test_d10_no_placeholder_text_reaches_a_finished_script(status, fighters, events):
    """"The UFC just made it official - this story." must never be spoken."""
    from ai.templates import build_script, contains_placeholder

    context = _context(status, fighters, events)
    for seconds in (30, 60):
        script = build_script(context, seconds)
        assert script.strip()
        assert not contains_placeholder(script), script


@pytest.mark.parametrize("status", D10_STATUSES)
@pytest.mark.parametrize("fighters,events", D10_ENTITY_CASES)
def test_d10_no_placeholder_text_in_hooks_angles_or_questions(status, fighters, events):
    from ai.templates import (
        build_hooks, build_questions, build_story_angle, build_why_it_matters,
        contains_placeholder,
    )

    context = _context(status, fighters, events)
    lines = (build_hooks(context) + build_questions(context)
             + [build_story_angle(context), build_why_it_matters(context)])
    assert lines
    for line in lines:
        assert not contains_placeholder(line), line


def test_d10_subject_is_empty_rather_than_filled_with_a_word():
    from ai.templates import subject_of, subject_or_claim

    context = _context("REPORTED", [], [], headline="Report: a booking is close")
    assert subject_of(context) == "", "an unknown subject must be empty, not a filler word"
    assert subject_or_claim(context), "there is always the claim to fall back on"
    assert "this story" not in subject_or_claim(context).lower()


def test_d10_placeholder_detector_does_not_flag_ordinary_prose():
    from ai.templates import contains_placeholder

    assert not contains_placeholder("No event has been named in the collected sources.")
    assert not contains_placeholder("Three events are on the schedule.")
    assert contains_placeholder("That's locked in for this story.")
    assert contains_placeholder("Booked for {event}.")


# ------------------------------------------------------------- D8 / D9 -----
def test_d8_dashboard_is_at_the_root_and_dashboard_path_is_a_real_page():
    """Streamlit serves the default page at "/" only, whatever url_path says.

    Visiting "/dashboard" therefore used to resolve to nothing and raise a
    "the page you have requested does not seem to exist" dialog.
    """
    import app

    assert app.CANONICAL_DASHBOARD_PATH == "/"
    assert app.PAGE_URL_PATHS["dashboard_alias"] == "dashboard"
    assert app.PAGE_URL_PATHS["dashboard"] != "dashboard", (
        "the default page cannot also own /dashboard - the script hashes collide")
    assert "dashboard_alias" in app.PAGES


def test_d8_every_page_url_path_is_unique():
    import app

    paths = list(app.PAGE_URL_PATHS.values())
    assert len(paths) == len(set(paths)), paths
    assert set(app.PAGE_URL_PATHS) == set(app.PAGES)


def test_d8_the_alias_is_hidden_from_the_menu_but_still_registered():
    import app

    listed = [page for group in app.NAVIGATION.values() for page in group]
    assert app.PAGES["dashboard_alias"] in listed, "st.navigation must know the alias"
    assert app.PAGES["dashboard"] in listed


def test_d9_item_owners_cover_every_item_parameter():
    from ui import nav

    assert {parameter for parameter, _, _ in nav.ITEM_OWNERS} == set(nav.ITEM_KEYS)


def test_d9_nav_helpers_move_selections_between_url_and_session(monkeypatch):
    """remember() and sync_url() are the two halves of a switch_page hop."""
    import streamlit as st
    from ui import nav

    class FakeQueryParams(dict):
        def get(self, key, default=None):  # noqa: D102
            return dict.get(self, key, default)

    params = FakeQueryParams({"story": "12"})
    state: dict = {}
    monkeypatch.setattr(st, "query_params", params, raising=False)
    monkeypatch.setattr(st, "session_state", state, raising=False)

    nav.remember("story")
    assert state[nav.ITEM_KEYS["story"]] == "12", "the id survives the page switch"

    params.clear()  # st.switch_page drops the query string
    nav.sync_url("story")
    assert params["story"] == "12", "the URL describes what is on screen again"


# ------------------------------------------------------- storage backend ---
def test_postgres_translation_keeps_sqlite_sql_working():
    """One dialect is written; the backend translates it."""
    from database.backends import PostgresBackend

    backend = PostgresBackend("postgresql://x/y")
    assert backend.translate("SELECT * FROM t WHERE a = ?") == "SELECT * FROM t WHERE a = %s"
    assert backend.translate("INSERT OR IGNORE INTO t (a) VALUES (?)") == (
        "INSERT INTO t (a) VALUES (%s) ON CONFLICT DO NOTHING")
    # SQLite's LIKE is case-insensitive; keeping that behaviour is the point.
    assert "ILIKE" in backend.translate("SELECT * FROM s WHERE headline LIKE ?")
    # A '?' inside a string literal is data, not a placeholder.
    assert backend.translate("SELECT * FROM t WHERE a = 'x?y'") == (
        "SELECT * FROM t WHERE a = 'x?y'")
    assert "BIGSERIAL PRIMARY KEY" in backend.translate_schema(
        "id INTEGER PRIMARY KEY AUTOINCREMENT")
    # ON CONFLICT has to come before RETURNING or PostgreSQL rejects it.
    returning = backend.with_returning_id("INSERT OR IGNORE INTO t (a) VALUES (?)")
    assert returning.index("ON CONFLICT") < returning.index("RETURNING")


def test_a_configured_postgres_url_never_falls_back_to_sqlite(monkeypatch):
    """Silently writing somewhere else is the failure mode this prevents."""
    import database.backends as backends

    monkeypatch.setattr(backends.PostgresBackend, "driver",
                        staticmethod(lambda: (_ for _ in ()).throw(
                            backends.DriverMissingError("no driver"))))
    with pytest.raises(backends.DriverMissingError):
        backends.build_backend("data/test.db", "postgresql://user:pw@host/db")


def test_a_connection_string_is_never_logged_with_its_password():
    from database.backends import PostgresBackend

    backend = PostgresBackend("postgresql://user:hunter2@host:5432/db")
    assert "hunter2" not in backend.describe()
    assert "***" in backend.describe()


def test_settings_page_does_not_print_the_database_file_path():
    from database.persistence import storage_report

    report = storage_report()
    assert report.location
    assert "/" not in report.location or "PostgreSQL" in report.location


# ---------------------------------------------------- background collection -
def test_background_collection_is_off_until_switched_on():
    from collectors import scheduler

    assert scheduler.is_enabled() is False
    assert scheduler.start() is False, "nothing starts collecting on its own"


def test_background_collection_interval_has_a_floor():
    from collectors import scheduler
    from database import repo_settings as settings_repo

    settings_repo.set_setting(scheduler.INTERVAL_SETTING, 1, "int")
    assert scheduler.interval_minutes() == scheduler.MIN_INTERVAL_MINUTES


def test_only_one_collection_runs_at_a_time():
    """The lock is in the database, so it holds across processes."""
    from collectors import scheduler

    assert scheduler.acquire_lock() is True
    assert scheduler.acquire_lock() is False, "a second collector must stand down"
    scheduler.release_lock()
    assert scheduler.acquire_lock() is True
    scheduler.release_lock()


def test_the_dead_auto_collect_setting_is_gone():
    from database import repo_settings as settings_repo

    assert settings_repo.get_setting("auto_collect_on_start", None) is None


def test_a_bool_setting_seeded_as_zero_is_stored_as_off():
    """`if value` on the string "0" is True, which turned every such default on."""
    from database import repo_settings as settings_repo

    settings_repo.set_setting("test_flag", "0", "bool")
    assert settings_repo.get_bool("test_flag", True) is False
    settings_repo.set_setting("test_flag", "1", "bool")
    assert settings_repo.get_bool("test_flag", False) is True
    settings_repo.set_setting("test_flag", False, "bool")
    assert settings_repo.get_bool("test_flag", True) is False
    # Every seeded boolean default must round-trip to what it says.
    assert settings_repo.get_bool("demo_data_loaded", True) is False


# ------------------------------------------------------- UFC 331 lifecycle -
# The bug that started all of this: UFC 331 shown as COMPLETED while it was
# still weeks away. Nothing here special-cases UFC 331 - the event is a
# stand-in for every event, and the same assertions hold for any of them.
UFC_331_START = "2026-09-19T22:00:00Z"


def _ufc_331(**overrides):
    event = {
        "id": 331, "name": "UFC 331: Van vs Pantoja 2",
        "event_date": "2026-09-19",
        "scheduled_start_utc": UFC_331_START,
        "official_source_url": "https://www.ufc.com/event/ufc-331",
        "data_origin": "official",
    }
    event.update(overrides)
    return event


def _at(iso: str):
    from utils.timeutil import parse_iso

    return parse_iso(iso)


def test_ufc331_is_upcoming_before_its_start():
    from models.types import EventStatus
    from processors.event_lifecycle import resolve_event_status

    result = resolve_event_status(_ufc_331(), now=_at("2026-09-01T12:00:00Z"))
    assert result.status == EventStatus.UPCOMING.value
    assert result.reasons, "a status is never shown without its reasoning"


def test_ufc331_is_live_inside_its_scheduled_window():
    from models.types import EventStatus
    from processors.event_lifecycle import EVIDENCE_LIVE, EventEvidence, resolve_event_status

    result = resolve_event_status(
        _ufc_331(),
        evidence=[EventEvidence(kind=EVIDENCE_LIVE, source_type="MAJOR_NEWS",
                                source_name="ESPN", reported_at="2026-09-19T23:30:00Z")],
        now=_at("2026-09-19T23:30:00Z"))
    assert result.status == EventStatus.LIVE.value


def test_ufc331_is_completed_only_with_reliable_completion_evidence():
    from models.types import EventStatus
    from processors.event_lifecycle import EVIDENCE_COMPLETED, EventEvidence, resolve_event_status

    result = resolve_event_status(
        _ufc_331(),
        evidence=[EventEvidence(kind=EVIDENCE_COMPLETED, source_type="OFFICIAL",
                                source_name="UFC.com", official=True,
                                reported_at="2026-09-20T06:00:00Z")],
        now=_at("2026-09-20T08:00:00Z"))
    assert result.status == EventStatus.COMPLETED.value


def test_ufc331_without_completion_evidence_is_unknown_not_completed():
    """A date that has passed is not evidence that the event happened."""
    from models.types import EventStatus
    from processors.event_lifecycle import resolve_event_status

    result = resolve_event_status(_ufc_331(), now=_at("2026-10-01T00:00:00Z"))
    assert result.status == EventStatus.UNKNOWN.value
    assert result.status != EventStatus.COMPLETED.value
    assert any("will not assume" in reason for reason in result.reasons)


def test_ufc331_completion_claimed_before_the_event_is_rejected():
    from models.types import EventStatus
    from processors.event_lifecycle import EVIDENCE_COMPLETED, EventEvidence, resolve_event_status

    result = resolve_event_status(
        _ufc_331(),
        evidence=[EventEvidence(kind=EVIDENCE_COMPLETED, source_type="MAJOR_NEWS",
                                source_name="A preview piece",
                                reported_at="2026-09-10T10:00:00Z")],
        now=_at("2026-09-11T10:00:00Z"))
    assert result.status != EventStatus.COMPLETED.value
    assert any("before the event was due to start" in note for note in result.conflicts)


def test_ufc331_with_only_a_date_never_becomes_completed():
    from models.types import EventStatus
    from processors.event_lifecycle import resolve_event_status

    event = _ufc_331(scheduled_start_utc=None)
    upcoming = resolve_event_status(event, now=_at("2026-09-01T00:00:00Z"))
    passed = resolve_event_status(event, now=_at("2026-10-01T00:00:00Z"))
    assert upcoming.status == EventStatus.UPCOMING.value
    assert passed.status == EventStatus.UNKNOWN.value


def test_ufc331_explanation_matches_the_data_it_has():
    """D11 for this event: no sentence about a start time it does not have."""
    from processors.event_lifecycle import status_explanation

    with_time = status_explanation({**_ufc_331(), "event_status": "UPCOMING"})
    without_time = status_explanation(
        {**_ufc_331(scheduled_start_utc=None), "event_status": "UPCOMING"})
    assert UFC_331_START in with_time
    assert "No start time has been collected" not in with_time
    assert "No start time has been collected" in without_time
    assert UFC_331_START not in without_time


def test_the_lifecycle_has_no_special_case_for_any_event_name():
    """Guard against ever hard-coding an event. The rules are generic."""
    from pathlib import Path

    source = Path("processors/event_lifecycle.py").read_text(encoding="utf-8")
    code = "\n".join(
        line for line in source.splitlines()
        if not line.strip().startswith("#")
    )
    assert "331" not in code, "no event number may appear in lifecycle logic"


def test_a_search_term_survives_the_page_switch_and_reaches_the_url():
    """The sidebar box switches page, and switch_page drops the query string."""
    from ui import nav

    assert "q" in nav.ITEM_KEYS
    assert ("q", ("search",), "search") in nav.ITEM_OWNERS


def test_clearing_a_selection_clears_the_url_parameter(monkeypatch):
    import streamlit as st
    from ui import nav

    params = dict(q="Aspinall")
    state = {nav.ITEM_KEYS["q"]: "Aspinall"}
    monkeypatch.setattr(st, "query_params", params, raising=False)
    monkeypatch.setattr(st, "session_state", state, raising=False)

    nav.set_selection("q", "")
    nav.sync_url("q")
    assert "q" not in params, "an empty search must not leave ?q= in the URL"


def test_d1_a_headline_containing_a_newline_cannot_break_the_card():
    """A feed title can contain a newline; the card must survive it."""
    markup = card_html(_story(headline="Broken\nheadline\r\nfrom a feed"))
    assert "\n" not in markup
    assert "Broken headline from a feed" in markup


def test_d10_a_headline_that_mentions_a_story_is_quoted_not_rejected():
    """The ban is on filler the app writes, not on words the source used."""
    from ai.templates import build_script

    context = _context("CONFIRMED", [], [], headline="Why this story matters for the division")
    script = build_script(context, 30)
    assert "Why this story matters" in script, "the claim is quoted as written"
