"""UFC ranking snapshots and detected ranking changes.

The ranking *system* (and the version/label UFC publishes) is stored with
every snapshot, because UFC has more than one ranking system in play - the
app records whatever the page says rather than assuming a fixed method.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import execute, query_all, query_one, query_value, rows_to_dicts
from utils.textutil import normalize_text
from utils.timeutil import utcnow_iso


def insert_ranking_rows(rows: List[Dict[str, Any]]) -> int:
    """Insert a ranking snapshot. Existing (division, fighter, date, version) rows are ignored."""
    inserted = 0
    for row in rows:
        name = str(row.get("fighter_name") or "").strip()
        if not name:
            continue
        try:
            result = execute(
                "INSERT OR IGNORE INTO rankings (division, fighter_name, normalized_name, fighter_id, "
                "position, is_champion, ranking_date, system_name, system_version, source_url, "
                "collected_at, is_demo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row.get("division"), name, normalize_text(name), row.get("fighter_id"),
                    row.get("position"), 1 if row.get("is_champion") else 0, row.get("ranking_date"),
                    row.get("system_name") or "UFC Rankings", row.get("system_version"),
                    row.get("source_url"), utcnow_iso(), 1 if row.get("is_demo") else 0,
                ),
            )
            if result:
                inserted += 1
        except Exception:  # one malformed row must not lose the whole snapshot
            continue
    return inserted


def latest_ranking_date(division: Optional[str] = None, system_version: Optional[str] = None) -> Optional[str]:
    clauses, params = [], []
    if division:
        clauses.append("division = ?")
        params.append(division)
    if system_version:
        clauses.append("system_version = ?")
        params.append(system_version)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return query_value(f"SELECT MAX(ranking_date) FROM rankings {where}", params)


def previous_ranking_date(before: str, division: Optional[str] = None) -> Optional[str]:
    clauses, params = ["ranking_date < ?"], [before]
    if division:
        clauses.append("division = ?")
        params.append(division)
    return query_value(
        f"SELECT MAX(ranking_date) FROM rankings WHERE {' AND '.join(clauses)}", params
    )


def rankings_for_date(ranking_date: str, division: Optional[str] = None) -> List[Dict[str, Any]]:
    clauses, params = ["ranking_date = ?"], [ranking_date]
    if division:
        clauses.append("division = ?")
        params.append(division)
    return rows_to_dicts(query_all(
        f"SELECT * FROM rankings WHERE {' AND '.join(clauses)} ORDER BY division, is_champion DESC, position",
        params,
    ))


def current_rankings(division: Optional[str] = None) -> List[Dict[str, Any]]:
    latest = latest_ranking_date(division)
    if not latest:
        return []
    return rankings_for_date(latest, division)


def divisions() -> List[str]:
    rows = query_all("SELECT DISTINCT division FROM rankings ORDER BY division")
    return [row["division"] for row in rows if row["division"]]


def ranking_systems() -> List[Dict[str, Any]]:
    """Which ranking systems/versions this database has seen, and when."""
    return rows_to_dicts(query_all(
        "SELECT system_name, system_version, COUNT(*) AS rows_stored, MIN(ranking_date) AS first_seen, "
        "MAX(ranking_date) AS last_seen FROM rankings GROUP BY system_name, system_version "
        "ORDER BY last_seen DESC"
    ))


def fighter_ranking_history(name: str, limit: int = 40) -> List[Dict[str, Any]]:
    return rows_to_dicts(query_all(
        "SELECT * FROM rankings WHERE normalized_name = ? ORDER BY ranking_date DESC LIMIT ?",
        (normalize_text(name), limit),
    ))


def insert_ranking_change(change: Dict[str, Any]) -> Optional[int]:
    return execute(
        "INSERT OR IGNORE INTO ranking_changes (division, fighter_name, normalized_name, "
        "previous_position, new_position, change_type, positions_moved, is_champion, ranking_date, "
        "previous_ranking_date, system_name, system_version, source_url, detected_at, is_demo) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            change.get("division"), change.get("fighter_name"),
            normalize_text(change.get("fighter_name") or ""), change.get("previous_position"),
            change.get("new_position"), change.get("change_type"), change.get("positions_moved"),
            1 if change.get("is_champion") else 0, change.get("ranking_date"),
            change.get("previous_ranking_date"), change.get("system_name"),
            change.get("system_version"), change.get("source_url"), utcnow_iso(),
            1 if change.get("is_demo") else 0,
        ),
    )


def recent_ranking_changes(limit: int = 50, division: Optional[str] = None) -> List[Dict[str, Any]]:
    if division:
        return rows_to_dicts(query_all(
            "SELECT * FROM ranking_changes WHERE division = ? ORDER BY ranking_date DESC, id DESC LIMIT ?",
            (division, limit),
        ))
    return rows_to_dicts(query_all(
        "SELECT * FROM ranking_changes ORDER BY ranking_date DESC, id DESC LIMIT ?", (limit,)
    ))


def ranking_changes_for_fighter(name: str, limit: int = 20) -> List[Dict[str, Any]]:
    return rows_to_dicts(query_all(
        "SELECT * FROM ranking_changes WHERE normalized_name = ? ORDER BY ranking_date DESC LIMIT ?",
        (normalize_text(name), limit),
    ))


def ranking_snapshot_count() -> int:
    return int(query_value("SELECT COUNT(DISTINCT ranking_date) FROM rankings", (), 0))
