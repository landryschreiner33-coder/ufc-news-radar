"""Entity matching: fighters, events, matchups, weight classes."""
from __future__ import annotations

from database.repo_entities import get_fighter_by_name, upsert_fighter
from processors.entities import (
    build_fighter_index, find_events, find_fighters, find_matchups, find_weight_classes,
    get_fighter_index, is_title_fight, reset_fighter_index,
)


def test_finds_full_names():
    found = find_fighters("Jon Jones vs. Tom Aspinall is official for UFC 320")
    assert found == ["Jon Jones", "Tom Aspinall"]


def test_finds_nicknames_and_aliases():
    assert "Alex Pereira" in find_fighters("Poatan responds to the callout")
    assert "Sean O'Malley" in find_fighters("Suga Sean reacts to the news")


def test_matches_unique_surname_only():
    assert "Tom Aspinall" in find_fighters("Aspinall out of the main event")


def test_ignores_ambiguous_surnames():
    reset_fighter_index()
    index = get_fighter_index()
    assert "jones" not in index.surnames, "common surnames must not match on their own"
    assert "silva" not in index.surnames


def test_accent_insensitive_matching():
    upsert_fighter("Jose Aldo", aliases=["José Aldo"])
    reset_fighter_index()
    assert "Jose Aldo" in find_fighters("José Aldo is back in the gym")


def test_no_false_positives_on_unrelated_text():
    assert find_fighters("Premier League transfer window roundup") == []


def test_event_detection_patterns():
    assert find_events("Booked for UFC 320 in October") == ["UFC 320"]
    assert "UFC Fight Night" in find_events("Set for UFC Fight Night this weekend")
    assert find_events("No event mentioned here") == []


def test_matchup_extraction_resolves_registry_names():
    matchups = find_matchups("Report: Jones vs. Aspinall targeted for the main event")
    assert ("Jon Jones", "Tom Aspinall") in matchups


def test_weight_class_and_title_detection():
    assert "heavyweight" in find_weight_classes("A heavyweight title fight")
    assert is_title_fight("The heavyweight title fight is set") is True
    assert is_title_fight("A regular prelim bout") is False


def test_index_rebuild_picks_up_new_fighters():
    reset_fighter_index()
    before = get_fighter_index().size
    upsert_fighter("Brand New Prospect", data_origin="detected")
    reset_fighter_index()
    assert get_fighter_index().size == before + 1
    assert get_fighter_by_name("Brand New Prospect") is not None
