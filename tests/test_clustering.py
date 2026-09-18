"""Duplicate detection and story grouping."""
from __future__ import annotations

from ai import service as ai_service
from database import repo_articles as articles_repo
from database import repo_stories as stories_repo
from processors import pipeline
from processors.clustering import ClusterConfig, StoryMatcher, assign_article
from tests.conftest import hours_ago


def _articles(ingest, items):
    return {article["title"]: article for article in ingest(items)}


def test_same_story_from_three_outlets_groups_into_one(ingest, make_article):
    ingest([
        make_article(title="Jon Jones vs. Tom Aspinall official for UFC 320",
                     source_name="UFC.com", independence_group="ufc_official",
                     source_type="OFFICIAL", is_official=True,
                     excerpt="The UFC announced the heavyweight title fight for UFC 320."),
        make_article(title="Jones vs. Aspinall set for UFC 320 main event",
                     source_name="ESPN", independence_group="espn",
                     excerpt="The heavyweight title fight between Jon Jones and Tom Aspinall is official."),
        make_article(title="Jon Jones and Tom Aspinall booked as UFC 320 headliner",
                     source_name="MMA Fighting", independence_group="vox",
                     excerpt="Jones and Aspinall will meet in the UFC 320 main event."),
    ])
    pipeline.cluster_unassigned()
    stories = stories_repo.list_stories(limit=10)
    assert len(stories) == 1, [story["headline"] for story in stories]
    assert len(articles_repo.articles_for_story(stories[0]["id"])) == 3


def test_different_developments_about_the_same_fighters_stay_separate(ingest, make_article):
    ingest([
        make_article(title="Jon Jones vs. Tom Aspinall official for UFC 320",
                     excerpt="The bout is official for the main event.",
                     independence_group="a"),
        make_article(title="Jon Jones out of UFC 320 with an injury, per report",
                     excerpt="Jon Jones is reportedly injured and out of the bout.",
                     independence_group="b"),
    ])
    pipeline.cluster_unassigned()
    stories = stories_repo.list_stories(limit=10)
    assert len(stories) == 2
    categories = {story["category"] for story in stories}
    assert "fight_announcement" in categories and "injury" in categories


def test_unrelated_stories_are_not_merged(ingest, make_article):
    ingest([
        make_article(title="Jon Jones vs. Tom Aspinall official for UFC 320",
                     excerpt="Heavyweight title fight is set."),
        make_article(title="Zhang Weili defends her title in Shanghai",
                     excerpt="The strawweight champion won a decision."),
    ])
    pipeline.cluster_unassigned()
    assert len(stories_repo.list_stories(limit=10)) == 2


def test_denial_joins_the_story_it_disputes(ingest, make_article):
    ingest([
        make_article(title="Khamzat Chimaev out of UFC 320 bout, per report",
                     excerpt="Chimaev is reportedly off the card with a replacement being sought.",
                     independence_group="junkie", published_at=hours_ago(3)),
        make_article(title="Khamzat Chimaev denies report he has withdrawn from UFC 320",
                     excerpt="Chimaev disputes the report and calls it false.",
                     independence_group="sherdog", published_at=hours_ago(2)),
    ])
    pipeline.cluster_unassigned()
    stories = stories_repo.list_stories(limit=10)
    assert len(stories) == 1, "a denial belongs with the report it disputes"
    assert len(articles_repo.articles_for_story(stories[0]["id"])) == 2


def test_time_window_keeps_old_and_new_apart(ingest, make_article):
    ingest([
        make_article(title="Jon Jones vs. Tom Aspinall official for UFC 320",
                     excerpt="Title fight set.", published_at=hours_ago(24 * 20)),
        make_article(title="Jon Jones vs. Tom Aspinall official for UFC 320",
                     url="https://other.test/x", excerpt="Title fight set.",
                     published_at=hours_ago(1)),
    ])
    pipeline.cluster_unassigned()
    assert len(stories_repo.list_stories(limit=10)) == 2


def test_duplicate_detection_flags_near_identical_articles(ingest, make_article):
    stored = ingest([
        make_article(title="Jon Jones vs. Tom Aspinall official for UFC 320",
                     excerpt="The heavyweight title fight is official."),
        make_article(title="Jon Jones vs. Tom Aspinall official for UFC 320",
                     url="https://mirror.test/copy", excerpt="The heavyweight title fight is official."),
    ])
    duplicates = ai_service.detect_duplicates(stored[0], stored[1:])
    assert duplicates and duplicates[0]["reason"] in ("same URL", "near-identical text")


def test_matcher_explains_its_decision(ingest, make_article):
    ingest([make_article(title="Jon Jones vs. Tom Aspinall official for UFC 320",
                         excerpt="Title fight set for the main event.")])
    pipeline.cluster_unassigned()
    second = make_article(title="Jones vs. Aspinall confirmed for UFC 320",
                          url="https://second.test/x", excerpt="The title fight is set.")
    pipeline.ingest_articles([second])
    article = articles_repo.get_article_by_url(second["url"])
    decision = assign_article(article, StoryMatcher())
    assert decision.created is False
    assert any("similarity" in reason for reason in decision.reasons)


def test_cluster_config_reads_settings():
    from database import repo_settings as settings_repo

    settings_repo.set_setting("story_similarity_threshold", 0.77, "float")
    assert ClusterConfig.from_settings().threshold == 0.77
