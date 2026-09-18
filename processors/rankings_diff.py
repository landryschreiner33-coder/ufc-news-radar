"""Store official ranking snapshots and detect what changed between them.

The ranking *system* is whatever the source page said it was - it is stored
with every snapshot and every change, so a future change of ranking system
shows up in the data instead of being silently mixed together.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from database import repo_entities as entities_repo
from database import repo_rankings as rankings_repo
from utils.logging_setup import get_logger
from utils.textutil import normalize_text

logger = get_logger(__name__)


@dataclass
class SnapshotResult:
    rows_inserted: int = 0
    changes_detected: int = 0
    ranking_date: Optional[str] = None
    previous_date: Optional[str] = None
    system_name: Optional[str] = None
    system_version: Optional[str] = None
    changes: List[Dict[str, Any]] = field(default_factory=list)
    note: str = ""


def store_snapshot(rows: List[Dict[str, Any]], detect_changes: bool = True) -> SnapshotResult:
    """Persist one collected rankings snapshot and diff it against the last one."""
    result = SnapshotResult()
    if not rows:
        result.note = "no ranking rows supplied"
        return result

    first = rows[0]
    result.ranking_date = first.get("ranking_date")
    result.system_name = first.get("system_name")
    result.system_version = first.get("system_version")

    previous_date = rankings_repo.previous_ranking_date(result.ranking_date or "")
    result.previous_date = previous_date
    previous_rows = rankings_repo.rankings_for_date(previous_date) if previous_date else []

    result.rows_inserted = rankings_repo.insert_ranking_rows(rows)

    # Fighter rows learn their current rank/champion status from official data only.
    for row in rows:
        name = row.get("fighter_name")
        if not name:
            continue
        entities_repo.upsert_fighter(name, data_origin="collected")
        entities_repo.update_fighter_ranking(
            name=name,
            position=row.get("position"),
            is_champion=bool(row.get("is_champion")),
            division=row.get("division") or "",
            source_url=row.get("source_url"),
            ranking_date=row.get("ranking_date"),
        )

    if detect_changes and previous_rows:
        changes = diff_snapshots(previous_rows, rows)
        for change in changes:
            rankings_repo.insert_ranking_change(change)
        result.changes = changes
        result.changes_detected = len(changes)
    elif not previous_rows:
        result.note = "first snapshot stored - no previous rankings to compare against"
    return result


def diff_snapshots(
    previous_rows: List[Dict[str, Any]], current_rows: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """Compare two snapshots and describe every movement."""
    previous_index = _index(previous_rows)
    current_index = _index(current_rows)
    changes: List[Dict[str, Any]] = []

    for (division, name_key), current in current_index.items():
        previous = previous_index.get((division, name_key))
        base = {
            "division": division,
            "fighter_name": current.get("fighter_name"),
            "ranking_date": current.get("ranking_date"),
            "previous_ranking_date": (previous or {}).get("ranking_date"),
            "system_name": current.get("system_name"),
            "system_version": current.get("system_version"),
            "source_url": current.get("source_url"),
            "is_champion": bool(current.get("is_champion")),
            "is_demo": bool(current.get("is_demo")),
        }
        if previous is None:
            changes.append({
                **base,
                "previous_position": None,
                "new_position": current.get("position"),
                "change_type": "new_champion" if current.get("is_champion") else "new_entry",
                "positions_moved": None,
            })
            continue
        old_position = previous.get("position")
        new_position = current.get("position")
        if bool(previous.get("is_champion")) != bool(current.get("is_champion")):
            changes.append({
                **base,
                "previous_position": old_position,
                "new_position": new_position,
                "change_type": "new_champion" if current.get("is_champion") else "champion_change",
                "positions_moved": None,
            })
            continue
        if old_position is None or new_position is None or old_position == new_position:
            continue
        changes.append({
            **base,
            "previous_position": old_position,
            "new_position": new_position,
            "change_type": "up" if new_position < old_position else "down",
            "positions_moved": abs(old_position - new_position),
        })

    for (division, name_key), previous in previous_index.items():
        if (division, name_key) in current_index:
            continue
        changes.append({
            "division": division,
            "fighter_name": previous.get("fighter_name"),
            "previous_position": previous.get("position"),
            "new_position": None,
            "change_type": "exit",
            "positions_moved": None,
            "is_champion": bool(previous.get("is_champion")),
            "ranking_date": _latest_date(current_rows) or previous.get("ranking_date"),
            "previous_ranking_date": previous.get("ranking_date"),
            "system_name": _first_value(current_rows, "system_name") or previous.get("system_name"),
            "system_version": _first_value(current_rows, "system_version") or previous.get("system_version"),
            "source_url": _first_value(current_rows, "source_url"),
            "is_demo": bool(previous.get("is_demo")),
        })
    return changes


def describe_change(change: Dict[str, Any]) -> str:
    """One-line description used in the UI and in timelines."""
    name = change.get("fighter_name")
    division = change.get("division")
    kind = change.get("change_type")
    if kind == "new_entry":
        return f"{name} enters the {division} rankings at #{change.get('new_position')}"
    if kind == "exit":
        return f"{name} drops out of the {division} rankings (was #{change.get('previous_position')})"
    if kind == "new_champion":
        return f"{name} is listed as {division} champion"
    if kind == "champion_change":
        return f"{name} is no longer listed as {division} champion"
    if kind == "up":
        return (f"{name} moves up in {division}: #{change.get('previous_position')} "
                f"-> #{change.get('new_position')}")
    if kind == "down":
        return (f"{name} moves down in {division}: #{change.get('previous_position')} "
                f"-> #{change.get('new_position')}")
    return f"{name}: {kind}"


def _index(rows: List[Dict[str, Any]]) -> Dict[Tuple[str, str], Dict[str, Any]]:
    index: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for row in rows:
        division = str(row.get("division") or "")
        name = normalize_text(str(row.get("fighter_name") or ""))
        if not division or not name:
            continue
        index[(division, name)] = row
    return index


def _latest_date(rows: List[Dict[str, Any]]) -> Optional[str]:
    dates = [row.get("ranking_date") for row in rows if row.get("ranking_date")]
    return max(dates) if dates else None


def _first_value(rows: List[Dict[str, Any]], key: str) -> Optional[Any]:
    for row in rows:
        if row.get(key):
            return row[key]
    return None
