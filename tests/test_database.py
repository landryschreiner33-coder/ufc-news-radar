"""Database schema, repositories and settings."""
from __future__ import annotations

import pytest

from database import repo_articles as articles_repo
from database import repo_settings as settings_repo
from database import repo_sources as sources_repo
from database import repo_stories as stories_repo
from database.db import database_stats, init_db, json_load, query_one
from tests.conftest import hours_ago


def test_schema_and_seed_created():
    stats = database_stats()
    assert stats["sources"] > 0, "built-in sources should be seeded"
    assert stats["settings"] > 0
    assert stats["fighters"] > 0
    assert stats["source_classifications"] > 0
    assert stats["monitored_social_accounts"] > 0


def test_init_db_is_idempotent():
    before = database_stats()
    init_db()
    init_db()
    assert database_stats() == before


def test_article_insert_and_duplicate_url(make_article):
    article = make_article(url="https://example.test/a?utm_source=x")
    first = articles_repo.insert_article(article)
    assert first is not None
    # Same article, different tracking parameters -> recognised as a duplicate.
    duplicate = make_article(url="https://example.test/a?utm_source=newsletter")
    assert articles_repo.insert_article(duplicate) is None
    assert articles_repo.article_count() == 1


def test_article_requires_url_and_title(make_article):
    assert articles_repo.insert_article(make_article(url="")) is None
    assert articles_repo.insert_article(make_article(title="")) is None


def test_json_columns_round_trip(make_article):
    article_id = articles_repo.insert_article(
        make_article(fighters=["Jon Jones", "Tom Aspinall"], events=["UFC 320"])
    )
    stored = articles_repo.get_article(article_id)
    assert stored["fighters"] == ["Jon Jones", "Tom Aspinall"]
    assert stored["events"] == ["UFC 320"]


def test_json_load_survives_corrupt_values():
    assert json_load("not json", []) == []
    assert json_load(None, {}) == {}
    assert json_load('["ok"]') == ["ok"]


def test_story_create_attach_and_counts(make_article):
    article_id = articles_repo.insert_article(make_article())
    story_id = stories_repo.create_story({
        "headline": "Test story", "category": "fight_announcement",
        "first_seen_at": hours_ago(2), "last_updated_at": hours_ago(1),
    })
    stories_repo.attach_article(story_id, article_id, role="origin", similarity=1.0)
    assert len(articles_repo.articles_for_story(story_id)) == 1
    assert articles_repo.get_article(article_id)["story_id"] == story_id
    stories_repo.detach_article(story_id, article_id)
    assert articles_repo.get_article(article_id)["story_id"] is None


def test_story_timeline_is_deduplicated():
    story_id = stories_repo.create_story({"headline": "T", "first_seen_at": hours_ago(1),
                                          "last_updated_at": hours_ago(1)})
    for _ in range(3):
        stories_repo.add_timeline_entry(story_id, hours_ago(1), "article",
                                        headline="Same entry", url="https://x.test/1")
    assert stories_repo.timeline_count(story_id) == 1


def test_settings_types_round_trip():
    settings_repo.set_setting("an_int", 42, "int")
    settings_repo.set_setting("a_float", 0.55, "float")
    settings_repo.set_setting("a_bool", True, "bool")
    settings_repo.set_setting("a_list", ["a", "b"], "json")
    assert settings_repo.get_int("an_int", 0) == 42
    assert settings_repo.get_float("a_float", 0) == 0.55
    assert settings_repo.get_bool("a_bool") is True
    assert settings_repo.get_setting("a_list") == ["a", "b"]
    assert settings_repo.get_int("missing_key", 7) == 7


def test_source_health_tracking():
    source = sources_repo.get_source_by_key("espn_mma")
    sources_repo.record_failure(int(source["id"]), "boom", "timeout")
    failed = sources_repo.get_source_by_key("espn_mma")
    assert failed["status"] == "error"
    assert failed["consecutive_failures"] == 1
    sources_repo.record_success(int(source["id"]), article_count=3,
                                resolved_url="https://feed.test/rss",
                                latest_article_at=hours_ago(1))
    healed = sources_repo.get_source_by_key("espn_mma")
    assert healed["status"] == "ok"
    assert healed["consecutive_failures"] == 0
    assert healed["article_count"] == 3
    assert healed["resolved_feed_url"] == "https://feed.test/rss"


def test_candidate_urls_prefers_last_known_good():
    source = sources_repo.get_source_by_key("mma_junkie")
    urls = sources_repo.candidate_urls(source)
    assert urls[0] == source["feed_url"]
    assert len(urls) > 1, "fallback URLs should be included"
    source["resolved_feed_url"] = "https://working.test/feed"
    assert sources_repo.candidate_urls(source)[0] == "https://working.test/feed"


def test_dashboard_counts_shape():
    counts = stories_repo.dashboard_counts()
    for key in ("total", "new", "breaking", "important", "rumors", "developing", "trending"):
        assert key in counts
