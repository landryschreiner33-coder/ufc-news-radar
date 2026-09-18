"""Fight-card detection: new fights, cancellations, replacements, changes."""
from __future__ import annotations

from database import repo_entities as entities_repo
from database import repo_stories as stories_repo
from processors import pipeline
from processors.fight_cards import analyse_article, apply_changes
from tests.conftest import hours_ago


def _article(title, excerpt, category=None, **extra):
    from processors.enrich import enrich_article

    article = enrich_article({
        "url": extra.pop("url", f"https://test.test/{abs(hash(title)) % 100000}"),
        "title": title, "excerpt": excerpt, "published_at": hours_ago(1),
        "source_name": extra.pop("source_name", "Test Source"),
        "source_type": extra.pop("source_type", "MAJOR_NEWS"),
        "domain": "test.test", **extra,
    })
    if category:
        article["category"] = category
    return article


def test_new_fight_is_detected():
    changes = analyse_article(_article(
        "Jon Jones vs. Tom Aspinall booked for UFC 320",
        "The heavyweight title fight will headline UFC 320."))
    assert changes
    change = changes[0]
    assert change.change_type == "new_fight"
    assert change.after_text == "Jon Jones vs. Tom Aspinall"
    assert change.segment == "main_event"
    assert change.title_fight is True


def test_cancellation_is_detected_with_its_reason():
    changes = analyse_article(_article(
        "Jon Jones vs. Tom Aspinall cancelled after failed drug test",
        "The bout has been scrapped from the card."))
    assert changes[0].change_type == "cancellation"
    assert changes[0].reason == "failed drug test"


def test_replacement_is_detected():
    changes = analyse_article(_article(
        "Curtis Blaydes replaces Tom Aspinall at UFC 320",
        "Curtis Blaydes replaces Tom Aspinall on short notice after an injury."))
    replacement = [change for change in changes if change.change_type == "replacement"]
    assert replacement
    assert replacement[0].after_text == "Curtis Blaydes"
    assert replacement[0].before_text == "Tom Aspinall"
    assert replacement[0].reason == "injury"


def test_injury_withdrawal_is_treated_as_a_card_change():
    changes = analyse_article(_article(
        "Alex Pereira out of his bout with an injury",
        "Pereira has withdrawn from the fight with a hand injury."))
    assert changes and changes[0].change_type == "cancellation"
    assert "Alex Pereira" in (changes[0].after_text or "")


def test_card_state_and_change_log_are_written(ingest, make_article):
    ingest([make_article(title="Jon Jones vs. Tom Aspinall booked for UFC 320",
                         excerpt="The heavyweight title fight will headline UFC 320.")])
    pipeline.cluster_unassigned()
    story = stories_repo.list_stories(limit=1)[0]
    pipeline.recompute_story(int(story["id"]))
    pipeline.detect_card_changes([int(story["id"])])

    event = entities_repo.get_event_by_name("UFC 320")
    assert event is not None
    card = entities_repo.fight_card(int(event["id"]))
    assert len(card) == 1
    assert card[0]["segment"] == "main_event"
    assert card[0]["is_title_fight"] == 1
    changes = entities_repo.card_changes(int(event["id"]))
    assert changes and changes[0]["change_type"] == "new_fight"
    assert changes[0]["source_name"]


def test_one_change_reported_by_many_outlets_is_logged_once(ingest, make_article):
    ingest([
        make_article(title="Jon Jones vs. Tom Aspinall booked for UFC 320",
                     excerpt="Title fight set for the UFC 320 main event.",
                     source_name="ESPN", independence_group="espn"),
        make_article(title="Jones vs. Aspinall booked for UFC 320 main event",
                     excerpt="The title fight is set to headline UFC 320.",
                     source_name="MMA Fighting", independence_group="vox"),
    ])
    pipeline.process_all()
    event = entities_repo.get_event_by_name("UFC 320")
    changes = [change for change in entities_repo.card_changes(int(event["id"]))
               if change["change_type"] == "new_fight"]
    assert len(changes) == 1, "the same change must not be logged once per outlet"


def test_official_source_upgrades_a_logged_change(ingest, make_article):
    ingest([make_article(title="Jon Jones vs. Tom Aspinall booked for UFC 320",
                         excerpt="Title fight reportedly set for UFC 320.",
                         source_name="Rumour Site", source_type="FAN_ACCOUNT",
                         reliability_weight=0.15, independence_group="rumour")])
    pipeline.process_all()
    event = entities_repo.get_event_by_name("UFC 320")
    before = entities_repo.card_changes(int(event["id"]))[0]

    pipeline.ingest_articles([{
        "url": "https://www.ufc.com/news/official-320", "domain": "ufc.com",
        "title": "Jon Jones vs. Tom Aspinall booked for UFC 320",
        "excerpt": "The UFC announced the title fight for UFC 320.",
        "source_name": "UFC.com", "source_type": "OFFICIAL", "is_official": True,
        "reliability_weight": 1.0, "independence_group": "ufc_official",
        "published_at": hours_ago(1),
    }])
    pipeline.process_all()
    after = [change for change in entities_repo.card_changes(int(event["id"]))
             if change["id"] == before["id"]][0]
    assert after["status"] == "CONFIRMED"
    assert after["source_name"] == "UFC.com"


def test_no_changes_detected_for_unrelated_article():
    assert analyse_article(_article("UFC announces new broadcast deal",
                                    "The promotion signed a media rights agreement.")) == []
