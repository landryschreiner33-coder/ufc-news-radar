"""Relevance, source support and trending."""
from __future__ import annotations

from database import repo_settings as settings_repo
from processors import relevance, support, trending
from processors.verification import VerificationResult
from tests.conftest import hours_ago


def _story(**overrides):
    story = {
        "id": 1, "headline": "Jon Jones vs. Tom Aspinall official for UFC 320",
        "summary": "Heavyweight title fight is official.", "category": "fight_announcement",
        "fighters": ["Jon Jones", "Tom Aspinall"], "events": ["UFC 320"], "keywords": [],
        "first_seen_at": hours_ago(2), "last_updated_at": hours_ago(1),
        "independent_source_count": 3, "official_confirmed": 1, "update_count": 2,
    }
    story.update(overrides)
    return story


def test_relevance_rewards_title_fights_and_major_fighters():
    result = relevance.score_story(_story())
    assert result.score > 60
    assert "championship/title" in result.breakdown
    assert "major fighter" in result.breakdown


def test_relevance_penalises_promotional_content():
    promo = relevance.score_story(_story(category="promo", headline="How to watch UFC 320: odds"))
    news = relevance.score_story(_story())
    assert promo.score < news.score
    assert promo.breakdown.get("low-information content", 0) < 0


def test_relevance_boosts_watchlist_matches():
    without = relevance.score_story(_story())
    with_watch = relevance.score_story(_story(), watchlist_terms=["Tom Aspinall"])
    assert with_watch.score > without.score
    assert "on your watchlist" in with_watch.breakdown


def test_relevance_decays_with_age():
    fresh = relevance.score_story(_story(last_updated_at=hours_ago(1)))
    stale = relevance.score_story(_story(last_updated_at=hours_ago(24 * 6)))
    assert fresh.score > stale.score


def test_relevance_is_bounded():
    assert 0 <= relevance.score_story(_story()).score <= 100
    assert relevance.relevance_band(80) == "HIGH"
    assert relevance.relevance_band(10) == "LOW"


def test_support_rewards_official_confirmation():
    verdict = VerificationResult(official_confirmed=True, official_sources=["UFC.com"],
                                 independent_source_count=3)
    assessment = support.assess_support(verdict, [{"reliability_weight": 1.0}], [])
    assert assessment.score > 80
    assert assessment.label == "STRONG SOURCE SUPPORT"
    assert "NOT a probability" in assessment.disclaimer


def test_support_penalises_copies_and_hedging():
    strong = support.assess_support(
        VerificationResult(independent_source_count=2), [{"reliability_weight": 0.85}], [])
    weak = support.assess_support(
        VerificationResult(independent_source_count=2, derivative_count=3, speculation_score=1.0),
        [{"reliability_weight": 0.85}], [])
    assert weak.score < strong.score
    assert any(reason.startswith("-") for reason in weak.reasons)


def test_support_marks_contested_when_sources_disagree():
    verdict = VerificationResult(independent_source_count=2, has_conflict=True,
                                 conflict_notes=["they disagree"])
    assessment = support.assess_support(verdict, [{"reliability_weight": 0.85}], [])
    assert assessment.label.startswith("CONTESTED")


def test_support_never_leaves_the_0_100_range():
    verdict = VerificationResult(independent_source_count=0, derivative_count=9,
                                 speculation_score=1.0, has_conflict=True)
    assessment = support.assess_support(verdict, [], [])
    assert 0 <= assessment.score <= 100


def test_trending_requires_measurable_activity():
    quiet = trending.evaluate_trend(_story(), [{"published_at": hours_ago(2),
                                                "independence_group": "a"}], [])
    assert quiet.is_trending is False
    assert quiet.reasons

    busy_articles = [
        {"published_at": hours_ago(1), "independence_group": group, "source_type": "MAJOR_NEWS"}
        for group in ("espn", "vox", "junkie", "sherdog")
    ]
    busy_posts = [
        {"created_at_source": hours_ago(1), "account_type": "OFFICIAL",
         "like_count": 30000, "repost_count": 30000},
        {"created_at_source": hours_ago(1), "account_type": "ESTABLISHED_JOURNALIST",
         "like_count": 100, "repost_count": 50},
    ]
    busy = trending.evaluate_trend(_story(), busy_articles, busy_posts)
    assert busy.is_trending is True
    assert any("independent outlets" in reason for reason in busy.reasons)
    assert any("engagement" in reason for reason in busy.reasons)


def test_trending_engagement_ignores_posts_without_metrics():
    posts = [{"created_at_source": hours_ago(1), "account_type": "FAN_ACCOUNT"}]
    result = trending.evaluate_trend(_story(), [], posts)
    assert not any("engagement" in reason for reason in result.reasons)


def test_trending_window_comes_from_settings():
    settings_repo.set_setting("trending_window_hours", 1, "int")
    old_articles = [{"published_at": hours_ago(5), "independence_group": group}
                    for group in ("a", "b", "c", "d")]
    result = trending.evaluate_trend(_story(), old_articles, [])
    assert result.is_trending is False
