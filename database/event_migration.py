"""Backfill canonical event identity and merge duplicate event rows.

Runs once after the schema-3 migration and is idempotent, so calling it on
every start is safe.

Merging is deliberately conservative: nothing is deleted.  The losing row keeps
its id and gains ``merged_into_id``, so any bookmark, story or fight card that
still points at it resolves to the surviving event instead of 404-ing.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Dict, List, Optional

from processors.event_identity import canonical_key, display_name_rank, identity_keys
from utils.logging_setup import get_logger
from utils.timeutil import utcnow_iso

logger = get_logger(__name__)


def _columns(connection, table: str) -> set:
    from database.db import table_columns

    return set(table_columns(connection, table))


def register_aliases(connection: sqlite3.Connection, event_id: int,
                     keys: List[str], name: Optional[str] = None) -> None:
    """Point every identity an event answers to at that event."""
    now = utcnow_iso()
    for key in keys:
        connection.execute(
            "INSERT OR IGNORE INTO event_aliases (event_id, alias_key, alias_name, created_at) "
            "VALUES (?,?,?,?)",
            (event_id, key, name, now),
        )


def _merge_pair(connection: sqlite3.Connection, keeper: Dict[str, Any],
                loser: Dict[str, Any]) -> None:
    """Move everything owned by ``loser`` onto ``keeper``, then tombstone it."""
    keep_id, lose_id = int(keeper["id"]), int(loser["id"])
    if keep_id == lose_id:
        return

    # Child rows move across. fight_card_items has UNIQUE(event_id, pair_key),
    # so a bout already on the keeper wins and the duplicate is dropped. The
    # already-present pair keys are read first and excluded by hand: SQLite's
    # "UPDATE OR IGNORE" would do this in one statement but is SQLite-only,
    # and this layer has to work on PostgreSQL too.
    existing = {
        row[0] for row in connection.execute(
            "SELECT pair_key FROM fight_card_items WHERE event_id = ?", (keep_id,)).fetchall()
    }
    movable = [
        row[0] for row in connection.execute(
            "SELECT id, pair_key FROM fight_card_items WHERE event_id = ?", (lose_id,)).fetchall()
        if row[1] not in existing
    ]
    for fight_id in movable:
        connection.execute(
            "UPDATE fight_card_items SET event_id = ? WHERE id = ?", (keep_id, fight_id))
    connection.execute("DELETE FROM fight_card_items WHERE event_id = ?", (lose_id,))

    for table, column in (("fight_card_changes", "event_id"), ("stories", "event_id")):
        connection.execute(f"UPDATE {table} SET {column} = ? WHERE {column} = ?", (keep_id, lose_id))

    # Fill any gap on the keeper from the loser's data - never overwrite.
    fillable = ["event_date", "location", "venue", "city", "ufc_url", "source_url",
                "scheduled_start_utc", "scheduled_end_utc", "local_timezone",
                "official_event_id", "official_source_url", "image_url"]
    available = _columns(connection, "events")
    updates = {
        column: loser[column]
        for column in fillable
        if column in available and not keeper.get(column) and loser.get(column)
    }
    # Mentions are additive: both rows counted real mentions of one event.
    updates["mention_count"] = int(keeper.get("mention_count") or 0) + int(loser.get("mention_count") or 0)
    last_seen = max(filter(None, [keeper.get("last_mentioned_at"), loser.get("last_mentioned_at")]),
                    default=None)
    if last_seen:
        updates["last_mentioned_at"] = last_seen
    updates["updated_at"] = utcnow_iso()
    assignments = ", ".join(f"{column} = ?" for column in updates)
    connection.execute(f"UPDATE events SET {assignments} WHERE id = ?",
                       (*updates.values(), keep_id))

    # The loser becomes a redirect, keeping old links alive.
    connection.execute(
        "UPDATE events SET merged_into_id = ?, updated_at = ? WHERE id = ?",
        (keep_id, utcnow_iso(), lose_id))
    connection.execute("UPDATE event_aliases SET event_id = ? WHERE event_id = ?",
                       (keep_id, lose_id))
    register_aliases(connection, keep_id, identity_keys(
        loser.get("name"), loser.get("ufc_url"),
        loser.get("official_event_id"), loser.get("event_date")), loser.get("name"))
    logger.info("Merged event %s (%r) into %s (%r)",
                lose_id, loser.get("name"), keep_id, keeper.get("name"))


def backfill(connection: sqlite3.Connection) -> Dict[str, int]:
    """Give every event a canonical key and collapse duplicates. Idempotent."""
    available = _columns(connection, "events")
    if "canonical_key" not in available:
        return {"keyed": 0, "merged": 0}

    connection.row_factory = sqlite3.Row
    rows = [dict(row) for row in connection.execute(
        "SELECT * FROM events WHERE merged_into_id IS NULL ORDER BY id")]

    groups: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        key = canonical_key(row.get("name"), row.get("ufc_url"),
                            row.get("official_event_id"), row.get("event_date"))
        groups.setdefault(key, []).append(row)

    keyed = merged = 0
    for key, members in groups.items():
        # The best display name survives; ties go to the oldest row so ids stay stable.
        members.sort(key=lambda row: (
            display_name_rank(row.get("name"), row.get("data_origin") or "detected"),
            -int(row["id"]),
        ), reverse=True)
        keeper, losers = members[0], members[1:]
        for loser in losers:
            _merge_pair(connection, keeper, loser)
            merged += 1
        if keeper.get("canonical_key") != key:
            connection.execute("UPDATE events SET canonical_key = ?, updated_at = ? WHERE id = ?",
                               (key, utcnow_iso(), keeper["id"]))
            keyed += 1
        register_aliases(connection, int(keeper["id"]), identity_keys(
            keeper.get("name"), keeper.get("ufc_url"),
            keeper.get("official_event_id"), keeper.get("event_date")), keeper.get("name"))

    # Tombstones need a key too (the UNIQUE index forbids sharing the keeper's).
    for row in connection.execute(
            "SELECT id, canonical_key FROM events WHERE merged_into_id IS NOT NULL").fetchall():
        if not row["canonical_key"]:
            connection.execute("UPDATE events SET canonical_key = ? WHERE id = ?",
                               (f"merged:{row['id']}", row["id"]))
    connection.commit()
    if merged or keyed:
        logger.info("Event identity backfill: %s keyed, %s merged", keyed, merged)
    return {"keyed": keyed, "merged": merged}
