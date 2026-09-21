"""Upgrading an existing database, not just creating a fresh one.

A fresh database is built straight from ``schema.sql``, so every unit test
sees the final shape and never exercises the upgrade path. That is exactly how
an upgraded database ended up with a legacy ``events.status`` column a fresh
one does not have, and with every pre-migration article stuck on
``intent='UNKNOWN'`` - permanently exempt from the result-safety gate.

These tests take a database at the v4 shape and upgrade it for real.
"""
from __future__ import annotations

import sqlite3

import pytest

from database.db import SCHEMA_VERSION, apply_migrations, get_connection, table_columns
from processors import bout_status as bout_model
from tests.conftest import running_on_postgres

pytestmark = pytest.mark.skipif(
    running_on_postgres,
    reason="the upgrade path is exercised against SQLite: there is no older PostgreSQL "
           "database in the wild to upgrade, and a fresh one is created at the current "
           "schema version",
)


def _columns(connection, table):
    return table_columns(connection, table)


@pytest.fixture
def v4_database():
    """Reshape the (fresh, v5) test database back to how v4 stored things."""
    connection = get_connection()
    connection.execute("ALTER TABLE fight_card_items ADD COLUMN confidence TEXT "
                       "NOT NULL DEFAULT 'reported'")
    connection.execute("ALTER TABLE events ADD COLUMN status TEXT NOT NULL DEFAULT 'scheduled'")
    connection.execute("ALTER TABLE fight_card_items DROP COLUMN evidence_level")
    connection.execute("ALTER TABLE fight_card_items DROP COLUMN source_type")
    connection.execute("ALTER TABLE collection_runs DROP COLUMN outcome")
    connection.execute("PRAGMA user_version = 4")
    connection.commit()
    return connection


def test_upgrade_adds_the_canonical_bout_columns_and_drops_the_legacy_one(v4_database):
    connection = v4_database
    event_id = connection.execute(
        "INSERT INTO events (name, normalized_name, canonical_key, event_status, data_origin, "
        "created_at, updated_at) VALUES ('UFC 331','ufc 331','ufc:331','UPCOMING','official',"
        "'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')").lastrowid
    # The exact contradiction the inspection found: official_status left at the
    # migration default while confidence still said 'official'.
    connection.execute(
        "INSERT INTO fight_card_items (event_id, fighter_a, fighter_b, pair_key, status, "
        "confidence, official_status, first_seen_at, last_updated_at) "
        "VALUES (?,'Jon Jones','Tom Aspinall','aspinall|jones','scheduled','official','reported',"
        "'2026-01-01T00:00:00Z','2026-01-01T00:00:00Z')", (event_id,))
    connection.commit()

    applied = apply_migrations(connection)

    assert 5 in applied
    columns = _columns(connection, "fight_card_items")
    assert "evidence_level" in columns
    assert "source_type" in columns
    assert "confidence" not in columns, "the legacy column must not survive the upgrade"
    assert "status" not in _columns(connection, "events"), "legacy events.status must be dropped"

    row = connection.execute(
        "SELECT official_status, evidence_level FROM fight_card_items").fetchone()
    assert row[0] == bout_model.REPORTED
    assert row[1] == bout_model.EVIDENCE_OFFICIAL_SOURCE
    assert bout_model.is_consistent(row[0], row[1])


def test_upgrade_backfills_article_intent(v4_database):
    """Old articles must obey the same preview/prediction gate as new ones."""
    connection = v4_database
    for title in (
        "UFC 331 predictions: who wins the main event",
        "Jon Jones vs Tom Aspinall officially booked for UFC 331",
    ):
        connection.execute(
            "INSERT INTO articles (url, canonical_url, url_hash, title, collected_at, intent, "
            "created_at) VALUES (?,?,?,?,?,?,?)",
            (f"https://example.test/{title[:12]}", f"https://example.test/{title[:12]}",
             title[:12], title, "2026-01-01T00:00:00Z", "UNKNOWN", "2026-01-01T00:00:00Z"))
    connection.commit()

    apply_migrations(connection)

    intents = dict(connection.execute("SELECT title, intent FROM articles").fetchall())
    assert intents["UFC 331 predictions: who wins the main event"] == "PREDICTION"
    assert all(intent != "UNKNOWN" for intent in intents.values())


def test_upgrade_backfills_run_outcomes(v4_database):
    connection = v4_database
    for attempted, ok, failed in ((14, 0, 14), (14, 8, 6), (14, 14, 0), (0, 0, 0)):
        connection.execute(
            "INSERT INTO collection_runs (started_at, trigger, sources_attempted, sources_ok, "
            "sources_failed) VALUES ('2026-01-01T00:00:00Z','manual',?,?,?)",
            (attempted, ok, failed))
    connection.commit()

    apply_migrations(connection)

    outcomes = [row[0] for row in connection.execute(
        "SELECT outcome FROM collection_runs ORDER BY id")]
    assert outcomes == ["TOTAL_FAILURE", "PARTIAL", "SUCCESS", "NOT_RUN"]


def test_upgrade_is_idempotent(v4_database):
    connection = v4_database
    apply_migrations(connection)
    assert apply_migrations(connection) == []
    assert connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_fresh_and_upgraded_databases_have_the_same_shape(v4_database, tmp_path):
    """The bug class this whole migration exists to close."""
    from database.db import apply_schema

    upgraded = v4_database
    apply_migrations(upgraded)

    fresh = sqlite3.connect(str(tmp_path / "fresh.db"))
    from database.db import SCHEMA_FILE

    fresh.executescript(SCHEMA_FILE.read_text(encoding="utf-8"))
    fresh.commit()

    for table in ("events", "fight_card_items", "articles", "collection_runs", "stories"):
        assert sorted(_columns(upgraded, table)) == sorted(_columns(fresh, table)), table
    fresh.close()
