"""Safe manual corrections, with a history of what was changed and why.

Automated rules get things wrong sometimes: two spellings of one event slip
past the identity resolver, a headline is mis-categorised, an outlet is
classified too generously. These helpers let a person fix that from the UI.

Two rules apply to everything here:

* **Nothing is fabricated.** A correction can merge, relabel or reassign data
  that was collected. It cannot invent a fact, a source or a quote.
* **Everything is recorded.** Each change is written to ``data_corrections``
  with its before/after and a reason, so a surprising row can always be traced.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import execute, json_dump, query_all, query_one, rows_to_dicts
from database.event_migration import _merge_pair
from utils.logging_setup import get_logger
from utils.timeutil import utcnow_iso

logger = get_logger(__name__)


def _record(kind: str, table: str, target_id: Optional[int], before: Any, after: Any,
            reason: str = "") -> None:
    execute(
        "INSERT INTO data_corrections (kind, target_table, target_id, before_value, after_value, "
        "reason, applied_by, applied_at) VALUES (?,?,?,?,?,?,?,?)",
        (kind, table, target_id, json_dump(before), json_dump(after), reason, "user", utcnow_iso()),
    )


def history(limit: int = 100) -> List[Dict[str, Any]]:
    return rows_to_dicts(query_all(
        "SELECT * FROM data_corrections ORDER BY applied_at DESC LIMIT ?", (limit,)))


# ----------------------------------------------------------------- events --
def merge_events(keep_id: int, merge_id: int, reason: str = "") -> Dict[str, Any]:
    """Fold one event into another. The losing id becomes a redirect."""
    if keep_id == merge_id:
        return {"ok": False, "error": "An event cannot be merged into itself."}
    from database.db import get_connection

    keeper = query_one("SELECT * FROM events WHERE id = ?", (keep_id,))
    loser = query_one("SELECT * FROM events WHERE id = ?", (merge_id,))
    if keeper is None or loser is None:
        return {"ok": False, "error": "One of those events no longer exists."}
    if loser["merged_into_id"]:
        return {"ok": False, "error": "That event has already been merged."}

    connection = get_connection()
    _merge_pair(connection, dict(keeper), dict(loser))
    connection.commit()
    _record("merge_events", "events", merge_id,
            {"id": merge_id, "name": loser["name"]},
            {"id": keep_id, "name": keeper["name"]}, reason)
    return {"ok": True, "message": f"Merged '{loser['name']}' into '{keeper['name']}'."}


# --------------------------------------------------------------- fighters --
def merge_fighters(keep_id: int, merge_id: int, reason: str = "") -> Dict[str, Any]:
    """Fold a duplicate fighter row into the canonical one.

    The losing name is kept as an alias so future text still resolves, and the
    row is deleted only after everything pointing at it has been moved.
    """
    if keep_id == merge_id:
        return {"ok": False, "error": "A fighter cannot be merged into themselves."}
    keeper = query_one("SELECT * FROM fighters WHERE id = ?", (keep_id,))
    loser = query_one("SELECT * FROM fighters WHERE id = ?", (merge_id,))
    if keeper is None or loser is None:
        return {"ok": False, "error": "One of those fighters no longer exists."}

    from database.db import json_load

    aliases = set(json_load(keeper["aliases"], []) or [])
    aliases.add(loser["name"])
    aliases.update(json_load(loser["aliases"], []) or [])
    aliases.discard(keeper["name"])

    execute("UPDATE fight_card_items SET fighter_a_id = ? WHERE fighter_a_id = ?", (keep_id, merge_id))
    execute("UPDATE fight_card_items SET fighter_b_id = ? WHERE fighter_b_id = ?", (keep_id, merge_id))
    execute("UPDATE rankings SET fighter_id = ? WHERE fighter_id = ?", (keep_id, merge_id))
    execute(
        "UPDATE fighters SET aliases = ?, mention_count = mention_count + ?, updated_at = ? "
        "WHERE id = ?",
        (json_dump(sorted(aliases)), int(loser["mention_count"] or 0), utcnow_iso(), keep_id))
    execute("DELETE FROM fighters WHERE id = ?", (merge_id,))
    _record("merge_fighters", "fighters", merge_id,
            {"id": merge_id, "name": loser["name"]},
            {"id": keep_id, "name": keeper["name"]}, reason)
    return {"ok": True, "message": f"Merged '{loser['name']}' into '{keeper['name']}'."}


# ---------------------------------------------------------------- stories --
def reassign_story_event(story_id: int, event_id: Optional[int],
                         reason: str = "") -> Dict[str, Any]:
    story = query_one("SELECT id, headline, event_id FROM stories WHERE id = ?", (story_id,))
    if story is None:
        return {"ok": False, "error": "That story no longer exists."}
    if event_id is not None and query_one("SELECT 1 FROM events WHERE id = ?", (event_id,)) is None:
        return {"ok": False, "error": "That event does not exist."}
    execute("UPDATE stories SET event_id = ?, updated_at = ? WHERE id = ?",
            (event_id, utcnow_iso(), story_id))
    _record("reassign_story", "stories", story_id, story["event_id"], event_id, reason)
    return {"ok": True, "message": "Story reassigned."}


def recategorize_story(story_id: int, category: str, reason: str = "") -> Dict[str, Any]:
    from models.types import Category

    valid = {member.value for member in Category}
    if category not in valid:
        return {"ok": False, "error": f"'{category}' is not a known category."}
    story = query_one("SELECT id, category FROM stories WHERE id = ?", (story_id,))
    if story is None:
        return {"ok": False, "error": "That story no longer exists."}
    execute("UPDATE stories SET category = ?, updated_at = ? WHERE id = ?",
            (category, utcnow_iso(), story_id))
    _record("recategorize", "stories", story_id, story["category"], category, reason)
    return {"ok": True, "message": f"Category set to {category}."}


# ---------------------------------------------------------------- sources --
def reclassify_source(source_id: int, source_type: str, reliability: Optional[float] = None,
                      independence_group: Optional[str] = None,
                      reason: str = "") -> Dict[str, Any]:
    """Change how much weight a source carries. Affects future scoring only."""
    from models.types import normalize_source_type

    source = query_one("SELECT * FROM sources WHERE id = ?", (source_id,))
    if source is None:
        return {"ok": False, "error": "That source no longer exists."}
    normalized = normalize_source_type(source_type)
    before = {"source_type": source["source_type"],
              "reliability_weight": source["reliability_weight"],
              "independence_group": source["independence_group"]}
    updates: Dict[str, Any] = {"source_type": normalized}
    if reliability is not None:
        updates["reliability_weight"] = max(0.0, min(1.0, float(reliability)))
    if independence_group:
        updates["independence_group"] = independence_group
    assignments = ", ".join(f"{column} = ?" for column in updates)
    execute(f"UPDATE sources SET {assignments}, updated_at = ? WHERE id = ?",
            (*updates.values(), utcnow_iso(), source_id))
    _record("reclassify_source", "sources", source_id, before, updates, reason)
    return {"ok": True, "message": f"{source['name']} reclassified as {normalized}."}
