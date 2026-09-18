"""Developing stories: timelines, updates and story-level flags."""
from __future__ import annotations

from database import repo_articles as articles_repo
from database import repo_social as social_repo
from database import repo_stories as stories_repo
from processors import developing, pipeline
from tests.conftest import hours_ago


def _seed_developing(ingest, make_article):
    ingest([
        make_article(title="Report: Khamzat Chimaev out of UFC 320", source_name="MMA Junkie",
                     independence_group="usatoday", published_at=hours_ago(4),
                     excerpt="Chimaev is reportedly out of the bout, sources say."),
        make_article(title="Khamzat Chimaev withdrawal reported by a second outlet",
                     source_name="ESPN", independence_group="espn", published_at=hours_ago(3),
                     excerpt="Sources say Chimaev is expected to withdraw from UFC 320."),
        make_article(title="Khamzat Chimaev denies he is out of UFC 320", source_name="Sherdog",
                     independence_group="sherdog", published_at=hours_ago(1),
                     excerpt="Chimaev disputes the report and calls it false."),
    ])
    pipeline.cluster_unassigned()
    story = stories_repo.list_stories(limit=1, sort="Most Sources")[0]
    pipeline.recompute_story(int(story["id"]))
    return int(story["id"])


def test_updates_accumulate_on_one_story(ingest, make_article):
    story_id = _seed_developing(ingest, make_article)
    assert len(stories_repo.list_stories(limit=10)) == 1, "updates must not create duplicate stories"
    assert len(articles_repo.articles_for_story(story_id)) == 3
    story = stories_repo.get_story(story_id)
    assert story["update_count"] >= 2
    assert story["is_developing"] == 1


def test_timeline_is_chronological_and_labelled(ingest, make_article):
    story_id = _seed_developing(ingest, make_article)
    entries = developing.timeline_view(story_id)
    assert len(entries) >= 3
    stamps = [entry["occurred_at"] for entry in entries]
    assert stamps == sorted(stamps), "timeline must read oldest to newest"
    assert all(entry["when"] and entry["icon"] for entry in entries)
    assert {entry["kind"] for entry in entries} >= {"article"}


def test_social_posts_join_the_timeline(ingest, make_article):
    story_id = _seed_developing(ingest, make_article)
    post_id = social_repo.upsert_post({
        "post_id": "p1", "username": "ufc", "account_type": "OFFICIAL",
        "text": "Khamzat Chimaev remains on the UFC 320 card.",
        "created_at_source": hours_ago(0.5), "url": "https://x.test/p1",
    })
    social_repo.link_post_to_story(story_id, int(post_id), 0.9, ["manual link"])
    developing.add_social_timeline_entry(story_id, social_repo.get_post(int(post_id)))
    kinds = [entry["kind"] for entry in developing.timeline_view(story_id)]
    assert "social" in kinds


def test_status_changes_are_recorded_once(ingest, make_article):
    story_id = _seed_developing(ingest, make_article)
    story = stories_repo.get_story(story_id)
    # The pipeline already recorded the first transition; repeating the same
    # change must not add another identical timeline entry.
    developing.record_status_change(story, "CONFIRMED", ["official source arrived"])
    developing.record_status_change(story, "CONFIRMED", ["official source arrived"])
    confirmed_entries = [
        entry for entry in stories_repo.story_timeline(story_id)
        if entry["kind"] == "status_change" and "CONFIRMED" in (entry["headline"] or "")
    ]
    assert len(confirmed_entries) == 1
    assert "->" in confirmed_entries[0]["headline"]


def test_no_status_entry_when_nothing_changed(ingest, make_article):
    story_id = _seed_developing(ingest, make_article)
    story = stories_repo.get_story(story_id)
    before = stories_repo.timeline_count(story_id)
    developing.record_status_change(story, story["status"], [])
    assert stories_repo.timeline_count(story_id) == before


def test_settled_story_is_not_marked_developing(ingest, make_article):
    ingest([make_article(title="Jon Jones vs. Tom Aspinall official for UFC 320",
                         source_name="UFC.com", source_type="OFFICIAL", is_official=True,
                         reliability_weight=1.0, independence_group="ufc_official",
                         excerpt="The UFC announced the bout is official.")])
    pipeline.cluster_unassigned()
    story = stories_repo.list_stories(limit=1)[0]
    pipeline.recompute_story(int(story["id"]))
    updated = stories_repo.get_story(int(story["id"]))
    assert updated["status"] == "CONFIRMED"
    assert updated["is_developing"] == 0
