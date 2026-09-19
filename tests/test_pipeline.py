"""End-to-end pipeline: collection -> stories -> flags -> social links -> cards."""
from __future__ import annotations

from collectors.runner import run_collection
from database import repo_articles as articles_repo
from database import repo_entities as entities_repo
from database import repo_rankings as rankings_repo
from database import repo_runs as runs_repo
from database import repo_settings as settings_repo
from database import repo_social as social_repo
from database import repo_stories as stories_repo
from processors import pipeline
from tests.conftest import hours_ago
from tests.fake_http import FakeHttpClient, failure, http_error

ROUTES = {
    "ufc.com/rss": "feeds/ufc_com.xml",
    "ufc.com/rankings": "ufc_rankings.html",
    "ufc.com/events": "ufc_events.html",
    "espn.com": "feeds/espn.xml",
    "mmafighting.com": "feeds/mmafighting.xml",
    "mmajunkie": "feeds/mmajunkie.xml",
    "sherdog.com": "feeds/sherdog.xml",
    "lowkickmma.com": "feeds/lowkick.xml",
    "news.google.com": "sample_google_news.xml",
    "bloodyelbow.com": failure("timeout", "simulated timeout"),
    "fightful.com": http_error(503),
}


def _run(**kwargs):
    client = FakeHttpClient(routes=ROUTES, default=failure("connection", "no route in test"))
    return run_collection(trigger="test", client=client, fetch_full_text=False, **kwargs)


def test_full_run_produces_stories_and_records_health():
    result = _run()
    assert result.sources_ok >= 8
    assert result.sources_failed >= 2, "unreachable sources must be recorded, not fatal"
    assert result.articles_new > 0
    assert result.stories_new > 0

    stories = stories_repo.list_stories(limit=20)
    assert stories
    assert all(story["status"] for story in stories)
    assert any(story["status"] == "CONFIRMED" for story in stories)
    assert any(story["status"] in ("RUMOR", "UNVERIFIED", "DEVELOPING") for story in stories)

    run = runs_repo.last_run()
    assert run["finished_at"]
    assert run["sources_ok"] == result.sources_ok
    assert settings_repo.get_setting("last_collection_at")


def test_rerunning_collection_does_not_duplicate_articles():
    first = _run()
    before = articles_repo.article_count()
    second = _run()
    assert second.articles_new == 0, "the same feed items must not be stored twice"
    assert articles_repo.article_count() == before
    assert first.articles_new > 0


def test_pipeline_is_idempotent_for_stories():
    _run()
    before = [(story["id"], story["status"]) for story in stories_repo.list_stories(limit=50)]
    pipeline.process_all()
    after = [(story["id"], story["status"]) for story in stories_repo.list_stories(limit=50)]
    assert before == after


def test_run_collects_rankings_and_events():
    result = _run()
    assert result.rankings is not None
    assert result.rankings.rows_inserted > 0
    assert result.rankings.system_name == "Meta UFC Rankings"
    assert rankings_repo.divisions()
    events = entities_repo.list_events(limit=20)
    assert any(event["name"].startswith("UFC 320") for event in events)
    # UFC.com is the official schedule, so its rows carry the strongest origin.
    collected = [event for event in events if event["data_origin"] == "official"]
    assert collected and collected[0]["location"]
    # The official page publishes segment start times, which the lifecycle needs.
    dated = [event for event in collected if event.get("scheduled_start_utc")]
    assert dated, "a collected event should carry its scheduled start"
    assert dated[0]["event_status"] in ("UPCOMING", "LIVE", "COMPLETED", "UNKNOWN")


def test_card_changes_are_detected_from_reporting():
    _run()
    changes = entities_repo.card_changes(limit=20)
    assert changes
    types = {change["change_type"] for change in changes}
    assert "new_fight" in types
    for change in changes:
        assert change["status"]
        assert change["detected_at"]
        assert change["source_name"]


def test_breaking_and_relevance_flags_are_set():
    _run()
    stories = stories_repo.list_stories(limit=20, sort="Most Relevant")
    top = stories[0]
    assert top["relevance"] > 0
    assert top["relevance_breakdown"]
    assert stories_repo.dashboard_counts()["total"] == len(stories_repo.list_stories(limit=100))


def test_social_posts_link_only_on_real_overlap():
    _run()
    story = stories_repo.list_stories(limit=1, sort="Most Relevant")[0]
    fighters = story["fighters"] or ["Jon Jones"]
    social_repo.upsert_post({
        "post_id": "specific-1", "username": "ufc", "account_type": "OFFICIAL",
        "text": f"Official: {story['headline']}", "created_at_source": hours_ago(1),
        "url": "https://x.test/1", "fighters": fighters, "events": story["events"],
    })
    social_repo.upsert_post({
        "post_id": "vague-1", "username": "somefighter", "account_type": "FIGHTER",
        "text": "Can't believe this.", "created_at_source": hours_ago(1),
        "url": "https://x.test/2",
    })
    linked = pipeline.link_social_posts()
    assert linked == 1, "a vague post must not be attached to a story"
    attached = [post["post_id"] for post in social_repo.posts_for_story(int(story["id"]))]
    assert attached == ["specific-1"]
    assert "vague-1" in [post["post_id"] for post in social_repo.unlinked_posts()]


def test_malformed_items_do_not_break_ingestion():
    stats = pipeline.ingest_articles([
        {"url": "https://ok.test/1", "title": "A perfectly fine headline about UFC 320",
         "published_at": hours_ago(1), "source_name": "Test"},
        {"url": "", "title": "no url"},
        {"title": "no url key at all"},
        {"url": "https://ok.test/2"},
        {"url": "https://ok.test/3", "title": "Another fine headline", "published_at": "garbage-date"},
    ])
    assert stats.articles_new == 2
    assert stats.articles_rejected == 3
    assert stats.errors == []


def test_collection_with_every_source_down_still_finishes():
    client = FakeHttpClient(default=failure("connection", "everything is down"))
    result = run_collection(trigger="test", client=client, fetch_full_text=False)
    assert result.sources_ok == 0
    assert result.sources_failed == result.sources_attempted
    assert result.articles_new == 0
    assert runs_repo.last_run()["finished_at"], "the run must still close cleanly"
