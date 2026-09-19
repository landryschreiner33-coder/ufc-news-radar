"""Official fight cards and event reconciliation.

Two things must stay visibly separate: what UFC itself lists, and what has only
been reported. A rumoured matchup appearing as an official bout is exactly the
kind of error that gets a creator called out.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from collectors.ufc_event_card import parse_event_card
from database import repo_entities as entities_repo
from models.types import EventStatus
from processors.event_lifecycle import EVIDENCE_CANCELLED, EVIDENCE_COMPLETED
from processors.event_reconcile import evidence_from_articles, reconcile_event

CARD_HTML = """
<html><body>
<h2>Main Card</h2>
<ul>
 <li class="l-listing__item"><div class="c-listing-fight">
   <div class="c-listing-fight__class-text">Flyweight Title Bout</div>
   <span class="c-listing-fight__corner-name">Alexandre Pantoja</span>
   <span class="c-listing-fight__corner-name">Joshua Van</span>
 </div></li>
 <li class="l-listing__item"><div class="c-listing-fight">
   <div class="c-listing-fight__class-text">Lightweight Bout</div>
   <span class="c-listing-fight__corner-name">Fighter C</span>
   <span class="c-listing-fight__corner-name">Fighter D</span>
 </div></li>
</ul>
<h2>Prelims</h2>
<ul>
 <li class="l-listing__item"><div class="c-listing-fight">
   <div class="c-listing-fight__class-text">Bantamweight Bout</div>
   <span class="c-listing-fight__corner-name">Fighter E</span>
   <span class="c-listing-fight__corner-name">Fighter F</span>
 </div></li>
</ul>
</body></html>
"""


def at(moment: str) -> datetime:
    return datetime.fromisoformat(moment.replace("Z", "+00:00"))


def test_official_card_parses_segments_and_title_bout():
    result = parse_event_card(CARD_HTML, "https://www.ufc.com/event/ufc-331")
    assert result.ok
    assert [bout.segment for bout in result.bouts] == ["main_event", "co_main", "prelims"]
    assert result.bouts[0].is_title_fight is True
    assert result.bouts[0].weight_class == "Flyweight"
    assert result.bouts[2].is_title_fight is False


def test_unparseable_card_fails_loudly_instead_of_guessing():
    result = parse_event_card("<html><body><p>nothing here</p></body></html>")
    assert result.ok is False
    assert result.bouts == []
    assert "layout may have changed" in (result.error or "")


def test_official_bouts_are_marked_official_and_reported_ones_are_not():
    event_id = entities_repo.upsert_event("UFC 331", data_origin="official")
    entities_repo.upsert_fight(event_id, "Fighter A", "Fighter B",
                               confidence="reported", source_name="MMA Junkie")
    entities_repo.upsert_fight(event_id, "Alexandre Pantoja", "Joshua Van",
                               confidence="official", official_status="official",
                               source_name="UFC.com")
    card = entities_repo.fight_card(event_id)
    by_name = {bout["fighter_a"]: bout for bout in card}
    assert by_name["Alexandre Pantoja"]["official_status"] == "official"
    assert by_name["Fighter A"]["official_status"] != "official"


def test_a_reported_bout_upgrading_to_official_is_logged_as_a_change():
    event_id = entities_repo.upsert_event("UFC 331", data_origin="official")
    entities_repo.upsert_fight(event_id, "Fighter A", "Fighter B", confidence="reported")
    _, _, changes = entities_repo.upsert_fight(
        event_id, "Fighter A", "Fighter B", confidence="official", official_status="official")
    assert any("official" in (change.get("after_text") or "") for change in changes)


# ------------------------------------------------------------ reconcile ----
def _event(**overrides):
    base = {
        "id": 1, "name": "UFC 331", "event_date": "2026-09-19",
        "scheduled_start_utc": "2026-09-20T02:00:00Z",
        "data_origin": "official",
        "official_source_url": "https://www.ufc.com/event/ufc-331",
    }
    base.update(overrides)
    return base


def test_reporting_never_overwrites_the_official_schedule():
    """The worked example from the specification: official says scheduled,
    a journalist reports a cancellation. Status stays, conflict is shown."""
    event_id = entities_repo.upsert_event(
        "UFC 331", data_origin="official",
        scheduled_start_utc="2026-09-20T02:00:00Z",
        ufc_url="https://www.ufc.com/event/ufc-331",
        official_source_url="https://www.ufc.com/event/ufc-331")
    event = entities_repo.get_event(event_id)
    articles = [{
        "title": "UFC 331 main event cancelled, per sources",
        "excerpt": "The bout has been called off according to people familiar.",
        "events": ["UFC 331"], "category": "cancellation",
        "source_type": "ESTABLISHED_JOURNALIST", "source_name": "Reporter",
        "url": "https://example.test/a", "published_at": "2026-09-18T10:00:00Z",
        "is_official": False, "intent": "ANNOUNCEMENT",
    }]
    result = reconcile_event(event, articles, now=at("2026-09-18T12:00:00Z"))
    assert result.status == EventStatus.UPCOMING.value
    assert result.conflicts, "the cancellation report must be preserved"
    assert any("still lists this event" in note for note in result.conflicts)


def test_a_denial_is_not_evidence_for_the_claim_it_denies():
    event = _event()
    articles = [{
        "title": "UFC denies UFC 331 has been cancelled",
        "excerpt": "The promotion denies the report.", "events": ["UFC 331"],
        "category": "cancellation", "source_type": "OFFICIAL", "source_name": "UFC.com",
        "url": "https://example.test/b", "has_denial": True, "is_official": True,
        "published_at": "2026-09-18T10:00:00Z", "intent": "ANNOUNCEMENT",
    }]
    assert evidence_from_articles(event, articles) == []


def test_a_prediction_article_produces_no_completion_evidence():
    event = _event()
    articles = [{
        "title": "UFC 331 predictions: who wins?", "excerpt": "Our picks.",
        "events": ["UFC 331"], "category": "general", "source_type": "MAJOR_NEWS",
        "source_name": "ESPN", "url": "https://example.test/c",
        "published_at": "2026-09-18T10:00:00Z", "intent": "PREDICTION",
    }]
    kinds = {item.kind for item in evidence_from_articles(event, articles)}
    assert EVIDENCE_COMPLETED not in kinds


def test_a_real_result_article_produces_completion_evidence():
    event = _event()
    articles = [{
        "title": "UFC 331 results: Van defeats Pantoja", "excerpt": "Van took the decision.",
        "events": ["UFC 331"], "category": "result", "source_type": "MAJOR_NEWS",
        "source_name": "ESPN", "url": "https://example.test/d",
        "published_at": "2026-09-20T08:00:00Z", "intent": "RESULT",
    }]
    kinds = {item.kind for item in evidence_from_articles(event, articles)}
    assert EVIDENCE_COMPLETED in kinds
