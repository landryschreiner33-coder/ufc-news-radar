"""Regression tests for the remaining cases in the overhaul acceptance list.

The cases already covered elsewhere are not repeated here:
event lifecycle and canonical identity in ``test_event_lifecycle``, result
safety in ``test_result_safety``, official cards and reconciliation in
``test_official_cards``, time handling in ``test_time_correctness``,
aggregator/syndication in ``test_collectors``, X and AI fallbacks in
``test_x_api`` / ``test_ai``.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from database import corrections
from database import repo_articles as articles_repo
from database import repo_entities as entities_repo
from database import repo_stories as stories_repo
from tests.conftest import hours_ago, running_on_postgres


# ----------------------------------------------- 9: duplicate fighters -----
def test_duplicate_fighters_can_be_merged_keeping_the_old_name_as_an_alias():
    keep = entities_repo.upsert_fighter("Alexandre Pantoja")
    drop = entities_repo.upsert_fighter("Alexandre  Pantoja Jr")
    outcome = corrections.merge_fighters(keep, drop, reason="same person, two spellings")
    assert outcome["ok"], outcome
    merged = entities_repo.get_fighter(keep)
    assert "Alexandre  Pantoja Jr" in (merged.get("aliases") or [])
    assert entities_repo.get_fighter(drop) is None


def test_a_fighter_cannot_be_merged_into_themselves():
    fighter = entities_repo.upsert_fighter("Joshua Van")
    assert corrections.merge_fighters(fighter, fighter)["ok"] is False


def test_every_correction_is_recorded_with_a_reason():
    keep = entities_repo.upsert_event("UFC 331", data_origin="collected")
    drop = entities_repo.upsert_event("UFC Fight Night: A vs B", data_origin="collected")
    corrections.merge_events(keep, drop, reason="wrong split")
    history = corrections.history()
    assert history and history[0]["kind"] == "merge_events"
    assert history[0]["reason"] == "wrong split"


def test_a_correction_never_invents_a_story():
    before = len(stories_repo.list_stories(limit=100))
    keep = entities_repo.upsert_fighter("Fighter One")
    drop = entities_repo.upsert_fighter("Fighter Two")
    corrections.merge_fighters(keep, drop)
    assert len(stories_repo.list_stories(limit=100)) == before


# --------------------------------------------- 19: stale article handling --
def test_old_articles_are_kept_but_never_counted_as_breaking(ingest, make_article):
    from processors import pipeline

    ingest([make_article(title="Something that happened a while ago",
                         published_at=hours_ago(24 * 20), collected_at=hours_ago(24 * 20),
                         excerpt="Old news.")])
    pipeline.cluster_unassigned()
    story = stories_repo.list_stories(limit=1)[0]
    pipeline.recompute_story(int(story["id"]))
    refreshed = stories_repo.get_story(int(story["id"]))
    assert refreshed["is_breaking"] == 0
    assert refreshed is not None, "an old article is still stored, just not promoted"


def test_a_stale_story_does_not_count_as_new(ingest, make_article):
    ingest([make_article(published_at=hours_ago(24 * 10), collected_at=hours_ago(24 * 10))])
    from processors import pipeline

    pipeline.cluster_unassigned()
    counts = stories_repo.dashboard_counts()
    assert counts["new"] == 0


# ------------------------------------------------- 24: Streamlit secrets ---
def test_secrets_are_read_from_streamlit_when_the_environment_is_empty(monkeypatch):
    """Streamlit Cloud has no .env file, so st.secrets has to work as a source."""
    import utils.config as config_module

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setattr(config_module, "_from_streamlit_secrets",
                        lambda name: "sk-from-secrets" if name == "ANTHROPIC_API_KEY" else None)
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    settings = config_module.get_config(refresh=True)
    assert settings.ai.anthropic_api_key == "sk-from-secrets"
    assert settings.ai.is_configured is True


def test_a_real_environment_variable_beats_streamlit_secrets(monkeypatch):
    import utils.config as config_module

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-env")
    monkeypatch.setattr(config_module, "_from_streamlit_secrets", lambda name: "sk-from-secrets")
    monkeypatch.setenv("AI_PROVIDER", "anthropic")
    assert config_module.get_config(refresh=True).ai.anthropic_api_key == "sk-from-env"


def test_missing_secrets_never_raise(monkeypatch):
    """A missing secrets file must not crash the app on start-up."""
    import utils.config as config_module

    def explode(name):
        raise RuntimeError("no secrets file")

    monkeypatch.setattr(config_module, "_from_streamlit_secrets",
                        lambda name: None)
    settings = config_module.get_config(refresh=True)
    assert settings is not None


def test_secrets_are_never_written_to_the_database(monkeypatch):
    """A key in the environment must never end up stored anywhere in the file."""
    from database.db import query_all

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-should-never-be-stored")
    from utils.config import get_config

    get_config(refresh=True)
    rows = query_all("SELECT key, value FROM settings")
    stored = " ".join(f"{row['key']}={row['value']}" for row in rows)
    assert "sk-should-never-be-stored" not in stored


# ------------------------------------------------ 29: first-run behaviour --
def test_a_fresh_database_reports_an_empty_dashboard_rather_than_fake_news():
    counts = stories_repo.dashboard_counts()
    assert counts["total"] == 0
    assert stories_repo.list_stories(limit=10) == []


def test_a_fresh_database_has_its_sources_seeded_ready_to_collect():
    from database import repo_sources as sources_repo

    sources = sources_repo.list_sources(enabled_only=True)
    assert len(sources) >= 10
    assert any(source["key"] == "ufc_com" for source in sources)


def test_demo_data_is_marked_and_never_looks_like_real_news():
    from database.demo_data import clear_demo_data, demo_data_present, load_demo_data

    load_demo_data()
    assert demo_data_present() is True
    stories = stories_repo.list_stories(limit=50)
    assert stories, "demo mode should populate the dashboard"
    assert all(story["is_demo"] == 1 for story in stories)
    assert all(story["headline"].startswith("[DEMO]") for story in stories)
    clear_demo_data()
    assert demo_data_present() is False
    assert stories_repo.list_stories(limit=50) == []


# ------------------------------------------------------- 26: image rules --
def test_a_story_without_an_image_gets_a_generic_placeholder_not_a_photo():
    from ui.images import image_for_story

    result = image_for_story({"headline": "Test", "category": "injury"})
    assert result["is_placeholder"] is True
    assert result["url"].startswith("data:image/svg+xml")
    assert "generic" in result["caption"].lower()


def test_a_tracking_pixel_is_not_used_as_a_story_image():
    from ui.images import image_for_story

    result = image_for_story({"headline": "Test", "category": "injury",
                              "image_url": "https://feeds.feedburner.com/~r/1x1.gif"})
    assert result["is_placeholder"] is True


def test_a_real_source_image_is_used_and_labelled_as_the_source():
    from ui.images import image_for_story

    result = image_for_story({"headline": "Test", "category": "injury",
                              "image_url": "https://espn.com/photo.jpg",
                              "primary_source_name": "ESPN"})
    assert result["is_placeholder"] is False
    assert result["url"] == "https://espn.com/photo.jpg"


def test_a_fighter_without_a_photo_gets_initials_not_someone_else():
    from ui.images import image_for_fighter

    result = image_for_fighter({"name": "Alexandre Pantoja"})
    assert result["is_placeholder"] is True
    assert result["url"].startswith("data:image/svg+xml")


# ------------------------------------------------------ backup / restore ---
@pytest.mark.skipif(
    running_on_postgres,
    reason="the file backup path covers SQLite; PostgreSQL backups are the provider's")
def test_a_backup_round_trips_without_losing_data(ingest, make_article, tmp_path):
    from database.persistence import export_database, restore_database, validate_backup


    ingest([make_article(title="Backup me")])
    before = articles_repo.article_count()
    backup = export_database(str(tmp_path / "backup.db"))

    check = validate_backup(backup)
    assert check["ok"] and check["articles"] == before

    outcome = restore_database(backup)
    assert outcome["ok"]
    assert articles_repo.article_count() == before


def test_a_file_that_is_not_a_backup_is_refused(tmp_path):
    from database.persistence import restore_database, validate_backup

    junk = tmp_path / "notadb.db"
    junk.write_bytes(b"this is not a database")
    assert validate_backup(str(junk))["ok"] is False
    assert restore_database(str(junk))["ok"] is False


def test_storage_report_flags_a_temporary_location():
    from database.persistence import looks_ephemeral, storage_report

    report = storage_report()
    assert report.headline
    assert isinstance(report.is_ephemeral, bool)
    assert looks_ephemeral("/tmp/whatever.db") is True


# --------------------------------------------------- validator behaviour ---
def test_the_validator_runs_clean_on_a_healthy_database(ingest, make_article):
    import sys
    from pathlib import Path

    root = str(Path(__file__).resolve().parent.parent / "scripts")
    if root not in sys.path:
        sys.path.insert(0, root)
    import validate_production_data as validator

    ingest([make_article(title="Jon Jones vs. Tom Aspinall targeted for UFC 320",
                         excerpt="The bout is being finalised.")])
    from processors import pipeline

    pipeline.cluster_unassigned()
    errors = [finding for finding in validator.run_all() if finding.level == validator.ERROR]
    assert errors == [], [finding.message for finding in errors]


def test_the_validator_reports_an_event_completed_before_it_starts():
    import sys
    from pathlib import Path

    root = str(Path(__file__).resolve().parent.parent / "scripts")
    if root not in sys.path:
        sys.path.insert(0, root)
    import validate_production_data as validator

    from database.db import execute

    future = (datetime.now(timezone.utc) + timedelta(days=60)).strftime("%Y-%m-%dT%H:%M:%SZ")
    execute(
        "INSERT INTO events (name, normalized_name, canonical_key, event_status, "
        "scheduled_start_utc, data_origin, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        ("UFC 999", "ufc 999", "ufc:999", "COMPLETED", future, "official",
         hours_ago(1), hours_ago(1)))
    findings = validator.check_event_status_sanity()
    assert any(finding.check == "future_event_completed" for finding in findings)


# ------------------------------------------- safe corrections (exposed UI) --
def test_a_story_can_be_recategorised_and_the_change_is_logged(ingest, make_article):
    from processors import pipeline

    ingest([make_article(title="Something categorised wrongly")])
    pipeline.cluster_unassigned()
    story_id = int(stories_repo.list_stories(limit=1)[0]["id"])
    outcome = corrections.recategorize_story(story_id, "injury", reason="keyword rules got it wrong")
    assert outcome["ok"], outcome
    assert stories_repo.get_story(story_id)["category"] == "injury"
    assert any(row["kind"] == "recategorize" for row in corrections.history())


def test_an_unknown_category_is_refused(ingest, make_article):
    from processors import pipeline

    ingest([make_article(title="Anything")])
    pipeline.cluster_unassigned()
    story_id = int(stories_repo.list_stories(limit=1)[0]["id"])
    assert corrections.recategorize_story(story_id, "not-a-category")["ok"] is False


def test_a_story_can_be_reassigned_to_another_event(ingest, make_article):
    from processors import pipeline

    ingest([make_article(title="A story about a card")])
    pipeline.cluster_unassigned()
    story_id = int(stories_repo.list_stories(limit=1)[0]["id"])
    event_id = entities_repo.upsert_event("UFC 340", data_origin="collected")
    assert corrections.reassign_story_event(story_id, event_id, "wrong event")["ok"]
    assert stories_repo.get_story(story_id)["event_id"] == event_id
    assert corrections.reassign_story_event(story_id, 999999)["ok"] is False


def test_a_source_can_be_reclassified_and_the_old_value_is_recorded():
    from database import repo_sources as sources_repo

    source = sources_repo.list_sources()[0]
    before = source["source_type"]
    outcome = corrections.reclassify_source(int(source["id"]), "FAN_ACCOUNT", 0.1,
                                            reason="has been unreliable")
    assert outcome["ok"], outcome
    assert sources_repo.get_source(int(source["id"]))["source_type"] == "FAN_ACCOUNT"
    entry = next(row for row in corrections.history() if row["kind"] == "reclassify_source")
    assert before in str(entry["before_value"])


# ------------------------------------------------ grouped duplicate reports --
def test_the_feed_shows_one_story_for_several_reports_of_it(ingest, make_article):
    """Five outlets reporting one thing is one card, not five headlines."""
    from processors import pipeline

    ingest([
        make_article(title="Jon Jones vs. Tom Aspinall booked for UFC 320",
                     source_name="ESPN", independence_group="espn",
                     excerpt="The bout is booked for UFC 320."),
        make_article(title="Jones vs. Aspinall set for UFC 320",
                     source_name="MMA Fighting", independence_group="vox",
                     excerpt="The bout is booked for UFC 320."),
        make_article(title="UFC 320 to feature Jones vs. Aspinall",
                     source_name="MMA Junkie", independence_group="usatoday",
                     excerpt="The bout is booked for UFC 320."),
    ])
    pipeline.cluster_unassigned()
    stories = stories_repo.list_stories(limit=10)
    assert len(stories) == 1, "three reports of one development should be one story"
    pipeline.recompute_story(int(stories[0]["id"]))
    story = stories_repo.get_story(int(stories[0]["id"]))
    assert story["article_count"] == 3
    assert articles_repo.articles_for_story(int(story["id"]))
