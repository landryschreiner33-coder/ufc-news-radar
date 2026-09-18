"""X API client: responses, errors, rate limits and the not-configured path."""
from __future__ import annotations

import json
import time

import pytest

from database import repo_runs as runs_repo
from database import repo_settings as settings_repo
from database import repo_social as social_repo
from social.queries import build_account_query, planned_queries
from social.x_client import XClient, normalize_post
from social.x_monitor import collect_x_activity
from tests.fake_http import FakeHttpClient
from utils.config import XConfig
from utils.http import HttpResult

SEARCH_PAYLOAD = {
    "data": [
        {
            "id": "1801",
            "text": "Jon Jones vs. Tom Aspinall is official for UFC 320.",
            "author_id": "42",
            "created_at": "2026-09-18T14:05:00.000Z",
            "lang": "en",
            "public_metrics": {"like_count": 1200, "retweet_count": 300, "reply_count": 45,
                               "quote_count": 12, "impression_count": 90000},
            "entities": {"hashtags": [{"tag": "UFC320"}], "mentions": [{"username": "ufc"}]},
        }
    ],
    "includes": {"users": [{"id": "42", "username": "ufc", "name": "UFC", "verified": True}]},
    "meta": {"newest_id": "1801", "result_count": 1},
}


def _client(response: HttpResult, token: str = "test-token") -> XClient:
    config = XConfig(bearer_token=token, max_searches_per_run=2, max_timelines_per_run=2,
                     max_results_per_query=10, query_cache_minutes=30)
    return XClient(config=config, client=FakeHttpClient(default=response))


def _json_result(payload, status: int = 200) -> HttpResult:
    body = json.dumps(payload)
    return HttpResult(ok=status < 400, status_code=status, url="https://api.x.com/2/x",
                      text=body, content=body.encode())


def test_not_configured_returns_a_clear_status():
    client = XClient(config=XConfig(bearer_token=""), client=FakeHttpClient())
    assert client.is_configured is False
    response = client.search_recent("UFC")
    assert response.not_configured
    assert "X_BEARER_TOKEN" in response.error
    assert client.status()["label"] == "X MONITORING - NOT CONFIGURED"


def test_search_parses_posts_and_author():
    response = _client(_json_result(SEARCH_PAYLOAD)).search_recent("UFC")
    assert response.ok
    post = response.posts[0]
    assert post["post_id"] == "1801"
    assert post["username"] == "ufc"
    assert post["like_count"] == 1200
    assert post["repost_count"] == 300
    assert post["created_at_source"] == "2026-09-18T14:05:00Z"
    assert post["url"] == "https://x.com/ufc/status/1801"
    assert post["entities_json"]["hashtags"] == ["UFC320"]


def test_rate_limit_is_reported_with_reset():
    reset = int(time.time()) + 900
    limited = HttpResult(status_code=429, url="u", error_kind="rate_limit",
                         error="rate limited", rate_limit_reset_epoch=reset)
    response = _client(limited).search_recent("UFC")
    assert response.rate_limited
    assert response.ok is False
    assert response.reset_in_minutes and response.reset_in_minutes <= 15


def test_auth_failure_is_explained():
    response = _client(HttpResult(status_code=401, url="u", error_kind="http",
                                  error="HTTP 401")).search_recent("UFC")
    assert response.error_kind == "auth"
    assert "access level" in response.error


def test_malformed_json_is_handled():
    broken = HttpResult(ok=True, status_code=200, url="u", text="{not json", content=b"{not json")
    response = _client(broken).search_recent("UFC")
    assert response.ok is False
    assert response.error_kind == "parse"


def test_api_error_payload_is_surfaced():
    payload = {"errors": [{"title": "Invalid Request", "detail": "query is malformed"}]}
    response = _client(_json_result(payload)).search_recent("bad(query")
    assert response.ok is False
    assert "malformed" in response.error


def test_empty_result_set_is_fine():
    response = _client(_json_result({"meta": {"result_count": 0}})).search_recent("UFC")
    assert response.ok is True
    assert response.posts == []


def test_normalize_post_tolerates_missing_fields():
    post = normalize_post({"id": "9", "text": "hi"})
    assert post["post_id"] == "9"
    assert post["username"] is None
    assert post["like_count"] is None
    assert post["account_type"] == "UNKNOWN"


def test_collect_without_token_is_a_no_op():
    outcome = collect_x_activity(client=XClient(config=XConfig(bearer_token=""),
                                                client=FakeHttpClient()))
    assert outcome.configured is False
    assert outcome.stored == 0
    assert outcome.status_line == "X MONITORING - NOT CONFIGURED"


def test_collect_stores_posts_and_caches_queries():
    client = _client(_json_result(SEARCH_PAYLOAD))
    outcome = collect_x_activity(client=client, max_searches=1, max_timelines=0)
    assert outcome.configured is True
    assert outcome.stored == 1
    assert social_repo.post_count() == 1
    stored = social_repo.latest_posts(1)[0]
    assert stored["account_type"] == "OFFICIAL", "monitored @ufc should classify as official"
    assert len(runs_repo.recent_queries()) == 1

    # A second run inside the cache window must not repeat the same query.
    second = collect_x_activity(client=client, max_searches=1, max_timelines=0)
    assert second.searches_run == 0
    assert second.searches_skipped == 1


def test_rate_limited_query_is_paused_until_reset():
    reset = int(time.time()) + 600
    limited = HttpResult(status_code=429, url="u", error_kind="rate_limit",
                         error="rate limited", rate_limit_reset_epoch=reset)
    client = _client(limited)
    outcome = collect_x_activity(client=client, max_searches=1, max_timelines=0)
    assert outcome.rate_limited is True
    assert "rate limit" in outcome.status_line
    query = runs_repo.recent_queries()[0]
    assert query["status"] == "rate_limited"
    skip, reason = runs_repo.should_skip_query("search_recent", query["query"])
    assert skip and "rate limited" in reason


def test_queries_are_narrow_and_within_length_limits():
    settings_repo.add_watchlist_item("fighter", "Jon Jones")
    settings_repo.add_watchlist_item("event", "UFC 320")
    queries = planned_queries(6)
    assert queries
    for query in queries:
        assert len(query["query"]) <= 512
        assert "-is:retweet" in query["query"]
    account_query = build_account_query(["ufc", "@danawhite"])
    assert "from:ufc" in account_query["query"] and "from:danawhite" in account_query["query"]


def test_x_disabled_in_settings_stops_collection():
    settings_repo.set_setting("x_enabled", False, "bool")
    outcome = collect_x_activity(client=_client(_json_result(SEARCH_PAYLOAD)))
    assert outcome.stored == 0
    assert any("switched off" in note for note in outcome.notes)
