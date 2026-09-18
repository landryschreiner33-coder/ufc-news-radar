"""Verification: statuses, independence, conflicts and status transitions."""
from __future__ import annotations

from database import repo_stories as stories_repo
from models.types import StoryStatus
from processors import pipeline
from processors.verification import evaluate_story
from tests.conftest import hours_ago


def _story_after(ingest, items):
    ingest(items)
    pipeline.cluster_unassigned()
    stories = stories_repo.list_stories(limit=10, sort="Most Sources")
    for story in stories:
        pipeline.recompute_story(int(story["id"]))
    return stories_repo.list_stories(limit=10, sort="Most Sources")[0]


def test_official_source_confirms(ingest, make_article):
    story = _story_after(ingest, [
        make_article(title="Jon Jones vs. Tom Aspinall official for UFC 320",
                     source_name="UFC.com", source_type="OFFICIAL", is_official=True,
                     reliability_weight=1.0, independence_group="ufc_official",
                     excerpt="The UFC announced the bout is official."),
    ])
    assert story["status"] == StoryStatus.CONFIRMED.value
    assert story["official_confirmed"] == 1
    assert any("Official source" in reason for reason in story["status_reasons"])


def test_two_independent_credible_sources_are_reported(ingest, make_article):
    story = _story_after(ingest, [
        make_article(title="Jon Jones vs. Tom Aspinall targeted for UFC 320", source_name="ESPN",
                     independence_group="espn", excerpt="The bout is being finalised."),
        make_article(title="Jones vs. Aspinall in the works for UFC 320", source_name="MMA Fighting",
                     independence_group="vox", excerpt="The two will meet at UFC 320."),
    ])
    assert story["status"] == StoryStatus.REPORTED.value
    assert story["independent_source_count"] == 2


def test_copies_do_not_count_as_independent_confirmation(ingest, make_article):
    story = _story_after(ingest, [
        make_article(title="Jon Jones out of UFC 320, per report", source_name="ESPN",
                     independence_group="espn",
                     excerpt="Jones is out of the bout, sources told ESPN."),
        make_article(title="Jon Jones reportedly out of UFC 320", source_name="LowKick MMA",
                     source_type="FAN_ACCOUNT", reliability_weight=0.15, independence_group="lowkick",
                     excerpt="Jones is out, according to ESPN."),
        make_article(title="Report: Jon Jones off UFC 320 card", source_name="Sportskeeda",
                     source_type="FAN_ACCOUNT", reliability_weight=0.2, independence_group="skeeda",
                     excerpt="Jones is off the card, according to ESPN."),
    ])
    assert story["independent_source_count"] == 1, "three copies of one report is still one source"
    assert any("copies" in reason for reason in story["status_reasons"])


def test_same_publisher_family_counts_once(ingest, make_article):
    story = _story_after(ingest, [
        make_article(title="Jon Jones vs. Tom Aspinall booked for UFC 320", source_name="MMA Fighting",
                     independence_group="vox_sbnation", excerpt="The bout is booked."),
        make_article(title="Jones vs. Aspinall booked for UFC 320", source_name="MMA Mania",
                     independence_group="vox_sbnation", excerpt="The bout is booked."),
    ])
    assert story["independent_source_count"] == 1


def test_low_quality_repetition_stays_a_rumor(ingest, make_article):
    story = _story_after(ingest, [
        make_article(title="Rumour: Jon Jones could retire after UFC 320", source_name="Fan Site A",
                     source_type="FAN_ACCOUNT", reliability_weight=0.15, independence_group="fa",
                     excerpt="Speculation suggests he may retire."),
        make_article(title="Jon Jones rumoured to retire", source_name="Fan Site B",
                     source_type="FAN_ACCOUNT", reliability_weight=0.15, independence_group="fb",
                     excerpt="Rumours suggest he could walk away."),
        make_article(title="Is Jon Jones retiring? Rumours grow", source_name="Fan Site C",
                     source_type="FAN_ACCOUNT", reliability_weight=0.15, independence_group="fc",
                     excerpt="More speculation about retirement."),
    ])
    assert story["status"] in (StoryStatus.RUMOR.value, StoryStatus.UNVERIFIED.value)
    assert story["independent_source_count"] == 0


def test_fighter_claim_is_labelled_as_a_claim(ingest, make_article):
    story = _story_after(ingest, [
        make_article(title="Fighter says he has signed a new contract", source_name="@fighterhandle",
                     source_type="FIGHTER", reliability_weight=0.55, independence_group="x:fighter",
                     excerpt="I just signed a new deal."),
    ])
    assert story["status"] == StoryStatus.FIGHTER_CLAIM.value


def test_conflict_is_preserved_not_resolved(ingest, make_article):
    story = _story_after(ingest, [
        make_article(title="Khamzat Chimaev out of UFC 320, per report", source_name="MMA Junkie",
                     independence_group="usatoday", published_at=hours_ago(3),
                     excerpt="He is reportedly out of the bout."),
        make_article(title="Khamzat Chimaev denies he is out of UFC 320", source_name="Sherdog",
                     independence_group="sherdog", published_at=hours_ago(2),
                     excerpt="He disputes the report and calls it false."),
    ])
    assert story["has_conflict"] == 1
    assert story["status"] == StoryStatus.DEVELOPING.value
    assert story["conflict_notes"]
    assert story["support_label"].startswith("CONTESTED")


def test_status_transition_is_recorded_on_the_timeline(ingest, make_article):
    ingest([make_article(title="Report: Jones vs. Aspinall in the works",
                         excerpt="Sources say the bout is being finalised.",
                         independence_group="espn", source_name="ESPN")])
    pipeline.cluster_unassigned()
    story = stories_repo.list_stories(limit=1)[0]
    pipeline.recompute_story(int(story["id"]))
    first_status = stories_repo.get_story(int(story["id"]))["status"]

    pipeline.ingest_articles([{
        "source_name": "UFC.com", "source_key": "ufc_com", "source_type": "OFFICIAL",
        "is_official": True, "reliability_weight": 1.0, "independence_group": "ufc_official",
        "url": "https://www.ufc.com/news/official", "domain": "ufc.com",
        "title": "Jon Jones vs. Tom Aspinall official for UFC 320",
        "excerpt": "The UFC announced the bout is official.",
        "published_at": hours_ago(1), "collected_at": hours_ago(1),
    }])
    pipeline.cluster_unassigned()
    pipeline.recompute_story(int(story["id"]))
    updated = stories_repo.get_story(int(story["id"]))
    assert first_status != updated["status"]
    assert updated["status"] == StoryStatus.CONFIRMED.value
    kinds = [entry["kind"] for entry in stories_repo.story_timeline(int(story["id"]))]
    assert "status_change" in kinds


def test_evaluate_story_with_no_sources_is_unverified():
    story_id = stories_repo.create_story({"headline": "Empty", "first_seen_at": hours_ago(1),
                                          "last_updated_at": hours_ago(1)})
    verdict = evaluate_story(stories_repo.get_story(story_id), [], [])
    assert verdict.status == StoryStatus.UNVERIFIED.value
    assert verdict.reasons
