"""Ranking snapshots, change detection and the ranking system record."""
from __future__ import annotations

from database import repo_entities as entities_repo
from database import repo_rankings as rankings_repo
from processors.rankings_diff import describe_change, diff_snapshots, store_snapshot


def _rows(date: str, version: str, entries):
    return [
        {
            "division": division, "fighter_name": name, "position": position,
            "is_champion": position == 0, "ranking_date": date,
            "system_name": "UFC Rankings", "system_version": version,
            "source_url": "https://www.ufc.com/rankings",
        }
        for division, name, position in entries
    ]


def test_snapshot_is_stored_with_its_system_and_date():
    rows = _rows("2026-09-09", "v1", [
        ("Heavyweight", "Tom Aspinall", 0), ("Heavyweight", "Ciryl Gane", 1),
    ])
    result = store_snapshot(rows)
    assert result.rows_inserted == 2
    assert result.changes_detected == 0
    assert "first snapshot" in result.note
    systems = rankings_repo.ranking_systems()
    assert systems[0]["system_name"] == "UFC Rankings"
    assert systems[0]["system_version"] == "v1"


def test_repeated_snapshot_is_not_duplicated():
    rows = _rows("2026-09-09", "v1", [("Heavyweight", "Tom Aspinall", 0)])
    store_snapshot(rows)
    assert store_snapshot(rows).rows_inserted == 0


def test_changes_are_detected_between_snapshots():
    store_snapshot(_rows("2026-09-09", "v1", [
        ("Heavyweight", "Tom Aspinall", 0), ("Heavyweight", "Ciryl Gane", 1),
        ("Heavyweight", "Curtis Blaydes", 2), ("Heavyweight", "Derrick Lewis", 3),
    ]))
    result = store_snapshot(_rows("2026-09-16", "v2", [
        ("Heavyweight", "Tom Aspinall", 0), ("Heavyweight", "Curtis Blaydes", 1),
        ("Heavyweight", "Ciryl Gane", 2), ("Heavyweight", "Jailton Almeida", 3),
    ]))
    kinds = {change["change_type"] for change in result.changes}
    assert kinds == {"up", "down", "new_entry", "exit"}
    stored = rankings_repo.recent_ranking_changes()
    assert len(stored) == 4
    assert all(change["system_version"] in ("v1", "v2") for change in stored)


def test_new_champion_is_detected():
    store_snapshot(_rows("2026-09-09", "v1", [
        ("Lightweight", "Islam Makhachev", 0), ("Lightweight", "Arman Tsarukyan", 1)]))
    result = store_snapshot(_rows("2026-09-16", "v1", [
        ("Lightweight", "Arman Tsarukyan", 0), ("Lightweight", "Islam Makhachev", 1)]))
    kinds = {(change["fighter_name"], change["change_type"]) for change in result.changes}
    assert ("Arman Tsarukyan", "new_champion") in kinds
    assert ("Islam Makhachev", "champion_change") in kinds


def test_fighter_ranking_is_written_only_from_collected_data():
    before = entities_repo.get_fighter_by_name("Tom Aspinall")
    assert before["current_rank"] is None and before["is_champion"] == 0
    store_snapshot(_rows("2026-09-16", "v1", [("Heavyweight", "Tom Aspinall", 0)]))
    after = entities_repo.get_fighter_by_name("Tom Aspinall")
    assert after["is_champion"] == 1
    assert after["rank_division"] == "Heavyweight"
    assert after["rank_source_url"].startswith("https://www.ufc.com")


def test_history_and_descriptions():
    store_snapshot(_rows("2026-09-09", "v1", [("Heavyweight", "Ciryl Gane", 1)]))
    store_snapshot(_rows("2026-09-16", "v1", [("Heavyweight", "Ciryl Gane", 3)]))
    history = rankings_repo.fighter_ranking_history("Ciryl Gane")
    assert [row["position"] for row in history] == [3, 1]
    change = rankings_repo.ranking_changes_for_fighter("Ciryl Gane")[0]
    assert "moves down" in describe_change(change)


def test_diff_handles_empty_snapshots():
    assert diff_snapshots([], []) == []
    assert store_snapshot([]).rows_inserted == 0


def test_malformed_ranking_rows_are_skipped():
    rows = _rows("2026-09-16", "v1", [("Heavyweight", "Good Fighter", 1)])
    rows.append({"division": None, "fighter_name": None, "position": None,
                 "ranking_date": "2026-09-16", "system_version": "v1"})
    result = store_snapshot(rows)
    assert result.rows_inserted == 1
