"""Feed filters and sorting."""
from __future__ import annotations

import pytest

from database import repo_stories as stories_repo
from models.types import FEED_FILTERS, SORT_OPTIONS
from processors import pipeline
from tests.conftest import hours_ago
from ui import filters as filter_mod


@pytest.fixture
def populated(ingest, make_article):
    ingest([
        make_article(title="Jon Jones vs. Tom Aspinall official for UFC 320 heavyweight title",
                     source_name="UFC.com", source_type="OFFICIAL", is_official=True,
                     reliability_weight=1.0, independence_group="ufc_official",
                     excerpt="The UFC announced the heavyweight title fight is official."),
        make_article(title="Report: Alex Pereira out with an injury", source_name="ESPN",
                     independence_group="espn",
                     excerpt="Pereira is reportedly injured and may be forced out, sources say."),
        make_article(title="Rumour: Conor McGregor could return in 2027", source_name="Fan Site",
                     source_type="FAN_ACCOUNT", reliability_weight=0.15, independence_group="fan",
                     excerpt="Speculation suggests a return could be near."),
        make_article(title="New UFC rankings released after the weekend",
                     source_name="MMA Fighting", independence_group="vox",
                     excerpt="Several fighters moved up the rankings this week."),
        make_article(title="UFC announces new broadcast deal", source_name="Sherdog",
                     independence_group="sherdog",
                     excerpt="The promotion signed a media rights agreement."),
        make_article(title="Fighter defeats opponent by unanimous decision", source_name="MMA Junkie",
                     independence_group="usatoday", excerpt="The main event went to the judges."),
    ])
    pipeline.process_all()
    return stories_repo.list_stories(limit=50)


def test_every_filter_runs_and_returns_matching_stories(populated):
    assert populated
    for feed_filter in FEED_FILTERS:
        stories = filter_mod.fetch_stories(feed_filter=feed_filter, limit=50)
        assert isinstance(stories, list)
        if feed_filter == "ALL":
            assert len(stories) == len(populated)
        if feed_filter == "RUMORS":
            assert all(story["status"] in ("RUMOR", "UNVERIFIED", "FIGHTER_CLAIM")
                       for story in stories)
        if feed_filter == "INJURIES":
            assert all(story["category"] == "injury" for story in stories)
        if feed_filter == "IMPORTANT":
            assert all(story["relevance"] >= 55 for story in stories)


def test_title_filter_catches_title_news_by_content(populated):
    stories = filter_mod.fetch_stories(feed_filter="TITLE", limit=50)
    assert stories, "a heavyweight title fight must appear under TITLE"
    assert any("title" in story["headline"].lower() for story in stories)


def test_category_filters_are_wired(populated):
    assert filter_mod.fetch_stories(feed_filter="RANKINGS")
    assert filter_mod.fetch_stories(feed_filter="UFC BUSINESS")
    assert filter_mod.fetch_stories(feed_filter="FIGHT ANNOUNCEMENTS")


def test_every_sort_order_works(populated):
    for sort in SORT_OPTIONS:
        stories = filter_mod.fetch_stories(sort=sort, limit=50)
        assert len(stories) == len(populated)
    by_relevance = filter_mod.fetch_stories(sort="Most Relevant", limit=50)
    scores = [story["relevance"] for story in by_relevance]
    assert scores == sorted(scores, reverse=True)
    by_sources = filter_mod.fetch_stories(sort="Most Sources", limit=50)
    counts = [story["independent_source_count"] for story in by_sources]
    assert counts == sorted(counts, reverse=True)


def test_minimum_relevance_and_search_filters(populated):
    assert all(story["relevance"] >= 40
               for story in filter_mod.fetch_stories(min_relevance=40, limit=50))
    found = filter_mod.fetch_stories(search="Pereira", limit=50)
    assert found and all("Pereira" in story["headline"] or "Pereira" in str(story["fighters"])
                         for story in found)
    assert filter_mod.fetch_stories(search="zzz-no-such-thing", limit=50) == []


def test_dashboard_sections_do_not_repeat_a_story(populated):
    sections = filter_mod.dashboard_sections(per_section=10)
    seen = []
    for stories in sections.values():
        seen.extend(story["id"] for story in stories)
    assert len(seen) == len(set(seen)), "a story must appear in only one dashboard section"
    assert sum(len(stories) for stories in sections.values()) > 0
