"""Collectors: parsing, normalisation, validation and failure isolation."""
from __future__ import annotations

import pytest

from collectors.registry import available_adapters, build_collector
from collectors.rss import RSSCollector
from collectors.runner import run_collection
from collectors.ufc_events import UFCEventsCollector
from collectors.ufc_rankings import UFCRankingsCollector, detect_ranking_date, detect_ranking_system
from database import repo_sources as sources_repo
from tests.fake_http import FakeHttpClient, failure, http_error
from utils.http import HttpResult


@pytest.fixture
def source():
    return sources_repo.get_source_by_key("mma_fighting")


def test_rss_collector_parses_and_normalises(source):
    result = RSSCollector(source, client=FakeHttpClient({"": "sample_rss.xml"})).run()
    assert result.ok
    assert result.count == 3           # 4 entries, one has no link
    assert result.rejected_count == 1
    first = result.items[0]
    assert first["title"].startswith("Jon Jones vs. Tom Aspinall")
    assert first["published_at"] == "2026-09-18T14:05:00Z"
    assert first["author"] == "Test Reporter"
    assert first["image_url"].endswith("1.jpg")
    assert first["source_type"] == "MAJOR_NEWS"
    assert first["url"].startswith("https://")


def test_rss_collector_isolates_network_failure(source):
    result = RSSCollector(source, client=FakeHttpClient(default=failure("timeout", "timed out"))).run()
    assert result.ok is False
    assert result.error_kind == "timeout"
    assert result.items == []


def test_rss_collector_reports_http_error(source):
    result = RSSCollector(source, client=FakeHttpClient(default=http_error(503))).run()
    assert result.ok is False
    assert result.error_kind == "http"
    assert "503" in result.error


def test_rss_collector_handles_malformed_feed(source):
    garbage = HttpResult(ok=True, status_code=200, url="x",
                         content=b"<<<not xml at all>>>", text="<<<not xml at all>>>")
    result = RSSCollector(source, client=FakeHttpClient(default=garbage)).run()
    assert result.ok is False
    assert result.error_kind == "parse"


def test_rss_collector_handles_empty_feed(source):
    empty = "<?xml version='1.0'?><rss version='2.0'><channel><title>x</title></channel></rss>"
    result = RSSCollector(source, client=FakeHttpClient(
        default=HttpResult(ok=True, status_code=200, url="x", content=empty.encode(), text=empty))).run()
    assert result.ok is False
    assert "no items" in (result.error or "")


def test_rss_collector_uses_fallback_url(source):
    """The first candidate fails, the fallback works, and the winner is recorded."""
    def route(url: str) -> HttpResult:
        if url == source["feed_url"]:
            return failure("connection", "primary down")
        data = open("tests/fixtures/sample_rss.xml", "rb").read()
        return HttpResult(ok=True, status_code=200, url=url, content=data,
                          text=data.decode("utf-8"))

    result = RSSCollector(source, client=FakeHttpClient(default=route)).run()
    assert result.ok
    assert result.resolved_url == source["fallback_urls"][0]


def test_not_modified_is_success_with_no_items(source):
    not_modified = HttpResult(status_code=304, url="x", error_kind="not_modified", error="not modified")
    result = RSSCollector(source, client=FakeHttpClient(default=not_modified)).run()
    assert result.ok is True
    assert result.count == 0


def test_aggregator_items_are_reclassified_to_publisher():
    google = sources_repo.get_source_by_key("google_news_ufc")
    result = RSSCollector(google, client=FakeHttpClient({"": "sample_google_news.xml"})).run()
    assert result.ok
    assert result.count == 1, "the non-MMA item must be filtered out"
    item = result.items[0]
    assert item["domain"] == "espn.com"
    assert item["source_type"] == "MAJOR_NEWS"
    assert item["independence_group"] == "espn"
    assert "ESPN" in item["source_name"]


def test_rankings_collector_structured_layout():
    collector = UFCRankingsCollector({"key": "ufc_rankings", "feed_url": "https://www.ufc.com/rankings"},
                                     client=FakeHttpClient({"": "ufc_rankings.html"}))
    result = collector.run()
    assert result.ok
    assert result.payload["system_name"] == "Meta UFC Rankings"
    assert result.payload["ranking_date"] == "2026-09-16"
    champions = [row for row in result.items if row["is_champion"]]
    assert champions and champions[0]["fighter_name"] == "Tom Aspinall"
    heavyweights = [row for row in result.items if row["division"] == "Heavyweight"]
    assert len(heavyweights) == 5


def test_rankings_collector_generic_layout():
    collector = UFCRankingsCollector({"key": "ufc_rankings", "feed_url": "u"},
                                     client=FakeHttpClient({"": "ufc_rankings_alt_layout.html"}))
    result = collector.run()
    assert result.ok
    assert result.payload["system_name"] == "Official UFC Rankings"
    assert {row["division"] for row in result.items} == {"Lightweight", "Middleweight"}


def test_rankings_collector_fails_loudly_on_unknown_layout():
    collector = UFCRankingsCollector({"key": "ufc_rankings", "feed_url": "u"},
                                     client=FakeHttpClient({"": "ufc_rankings_broken.html"}))
    result = collector.run()
    assert result.ok is False
    assert result.error_kind == "parse"
    assert result.items == []


def test_ranking_system_detection_makes_no_assumption():
    name, version = detect_ranking_system("Some page with no ranking wording at all")
    assert "not found" in name
    assert version == "unlabelled"
    name, _ = detect_ranking_system("The Meta UFC Rankings are updated weekly")
    assert name == "Meta UFC Rankings"
    assert detect_ranking_date("Rankings as of September 16, 2026") == "2026-09-16"


def test_events_collector_parses_cards():
    collector = UFCEventsCollector({"key": "ufc_events", "feed_url": "https://www.ufc.com/events"},
                                   client=FakeHttpClient({"": "ufc_events.html"}))
    result = collector.run()
    assert result.ok
    names = [item["name"] for item in result.items]
    assert "UFC 320: Jones vs Aspinall" in names
    assert result.items[0]["location"].startswith("T-Mobile Arena")


def test_registry_knows_every_seeded_adapter():
    adapters = set(available_adapters())
    for source in sources_repo.list_sources():
        assert source["adapter"] in adapters
        assert build_collector(source) is not None


def test_unknown_adapter_is_reported_not_raised():
    sources_repo.upsert_source({
        "key": "broken_adapter", "name": "Broken", "adapter": "does_not_exist",
        "feed_url": "https://x.test/feed", "source_type": "UNKNOWN", "enabled": 1,
    })
    result = run_collection(trigger="test", source_keys=["broken_adapter"],
                            client=FakeHttpClient(), process=False, collect_social=False)
    assert result.sources_failed == 1
    assert "unknown adapter" in (result.errors[0] if result.errors else "")
    stored = sources_repo.get_source_by_key("broken_adapter")
    assert stored["status"] == "error"


def test_one_broken_source_does_not_stop_the_run():
    client = FakeHttpClient(routes={
        "ufc.com/rss": "feeds/ufc_com.xml",
        "espn.com": "feeds/espn.xml",
        "mmafighting.com": failure("connection", "down"),
        "ufc.com/rankings": "ufc_rankings.html",
        "ufc.com/events": "ufc_events.html",
    }, default=failure("connection", "no route"))
    result = run_collection(trigger="test", client=client, collect_social=False)
    assert result.sources_ok >= 3
    assert result.sources_failed >= 1
    assert result.articles_new > 0, "healthy sources must still produce articles"
    health = {row["key"]: row["status"] for row in sources_repo.source_health()}
    assert health["ufc_com"] == "ok"
    assert health["mma_fighting"] == "error"
