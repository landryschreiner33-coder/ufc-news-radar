"""Result safety: preview and prediction coverage must never become results.

Ahead of a big card, prediction/preview pieces outnumber everything else.  If
any of them can create a winner, a loser or a completed event, the dashboard
starts publishing fiction - so these are the guards that matter most.
"""
from __future__ import annotations

import pytest

from models.types import ArticleIntent, Category
from processors.enrich import enrich_article
from processors.event_lifecycle import EVIDENCE_COMPLETED, EventEvidence, resolve_event_status
from processors.result_safety import (
    classify_intent,
    completion_evidence_from_article,
    may_establish_result,
)

PREDICTIONS = [
    "UFC 331 predictions: Who wins Van vs Pantoja 2?",
    "UFC 331 picks and best bets",
    "Expert picks for Van vs Pantoja 2",
    "UFC 331 betting odds and prop bets",
    "Staff picks: our writers predict UFC 331",
]

PREVIEWS = [
    "UFC 331 preview: everything you need to know",
    "How to watch UFC 331: start time and full card",
    "UFC 331 weigh-in results and ceremonial staredowns",
    "Van vs Pantoja 2 set for UFC 331 in September",
    "Tale of the tape: Van vs Pantoja 2",
]

RESULTS = [
    "UFC 331 results: Van defeats Pantoja by unanimous decision",
    "Aspinall knocks out Jones to claim the title",
    "Pantoja submits Van in round three",
]


@pytest.mark.parametrize("headline", PREDICTIONS)
def test_prediction_articles_are_classified_as_predictions(headline):
    assert classify_intent(headline).intent == ArticleIntent.PREDICTION.value


@pytest.mark.parametrize("headline", PREVIEWS)
def test_preview_articles_are_classified_as_previews(headline):
    assert classify_intent(headline).intent == ArticleIntent.PREVIEW.value


@pytest.mark.parametrize("headline", RESULTS)
def test_genuine_results_are_classified_as_results(headline):
    assert classify_intent(headline).intent == ArticleIntent.RESULT.value


@pytest.mark.parametrize("headline", PREDICTIONS + PREVIEWS)
def test_predictions_and_previews_can_never_establish_a_result(headline):
    """The structural guard, independent of any scoring."""
    result = classify_intent(headline)
    assert result.may_establish_result is False
    assert may_establish_result(result.intent, "OFFICIAL") is False
    assert may_establish_result(result.intent, "MAJOR_NEWS") is False


@pytest.mark.parametrize("headline", RESULTS)
def test_results_need_a_reliable_source(headline):
    intent = classify_intent(headline).intent
    assert may_establish_result(intent, "MAJOR_NEWS") is True
    assert may_establish_result(intent, "FAN_ACCOUNT") is False
    assert may_establish_result(intent, "UNKNOWN") is False


def test_a_future_tense_headline_is_never_a_result():
    for headline in ("Jones will defeat Aspinall, says coach",
                     "Pantoja could finish Van early this weekend",
                     "Van should beat Pantoja tonight"):
        assert classify_intent(headline).may_establish_result is False


def test_a_question_headline_is_never_a_result():
    assert classify_intent("Did Van really beat Pantoja?").may_establish_result is False


def test_enrichment_downgrades_a_result_category_on_a_prediction_piece():
    article = enrich_article({
        "title": "UFC 331 predictions: who wins Van vs Pantoja 2?",
        "excerpt": "Our staff make their picks for the main card.",
        "source_name": "ESPN", "source_type": "MAJOR_NEWS",
    })
    assert article["intent"] == ArticleIntent.PREDICTION.value
    assert article["category"] != Category.RESULT.value


def test_enrichment_keeps_a_real_result_as_a_result():
    article = enrich_article({
        "title": "UFC 331 results: Van defeats Pantoja by unanimous decision",
        "excerpt": "Van took the decision after five rounds.",
        "source_name": "ESPN", "source_type": "MAJOR_NEWS",
    })
    assert article["intent"] == ArticleIntent.RESULT.value
    assert completion_evidence_from_article(article) is True


def test_a_prediction_article_cannot_complete_an_event():
    """End to end: the prediction never produces completion evidence, so the
    event stays UPCOMING rather than being marked finished."""
    article = enrich_article({
        "title": "UFC 331 predictions: who wins Van vs Pantoja 2?",
        "excerpt": "Our staff make their picks.",
        "source_name": "ESPN", "source_type": "MAJOR_NEWS",
    })
    assert completion_evidence_from_article(article) is False

    evidence = []
    if completion_evidence_from_article(article):
        evidence.append(EventEvidence(EVIDENCE_COMPLETED, source_type="MAJOR_NEWS"))
    event = {"name": "UFC 331", "scheduled_start_utc": "2026-09-20T02:00:00Z"}
    from datetime import datetime, timezone
    now = datetime(2026, 9, 19, 12, 0, tzinfo=timezone.utc)
    assert resolve_event_status(event, evidence, now=now).status == "UPCOMING"


def test_intent_always_explains_itself():
    for headline in PREDICTIONS + PREVIEWS + RESULTS:
        assert classify_intent(headline).reasons, f"no reason given for {headline!r}"
