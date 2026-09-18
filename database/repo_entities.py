"""Fighters, events, fight cards and fight-card change history."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import execute, json_dump, json_load, query_all, query_one, rows_to_dicts, transaction
from utils.textutil import normalize_text
from utils.timeutil import utcnow_iso


# --------------------------------------------------------------- fighters --
def hydrate_fighter(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    data = dict(row)
    data["aliases"] = json_load(data.get("aliases"), []) or []
    return data


def upsert_fighter(
    name: str,
    nickname: Optional[str] = None,
    aliases: Optional[List[str]] = None,
    data_origin: str = "detected",
    **fields: Any,
) -> int:
    name = str(name).strip()
    if not name:
        return 0
    normalized = normalize_text(name)
    now = utcnow_iso()
    existing = query_one("SELECT * FROM fighters WHERE normalized_name = ?", (normalized,))
    if existing is None:
        return execute(
            "INSERT INTO fighters (name, normalized_name, aliases, nickname, division, country, "
            "x_username, ufc_profile_url, data_origin, mention_count, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,0,?,?)",
            (name, normalized, json_dump(aliases or []), nickname, fields.get("division"),
             fields.get("country"), fields.get("x_username"), fields.get("ufc_profile_url"),
             data_origin, now, now),
        )
    updates: Dict[str, Any] = {}
    if nickname and not existing["nickname"]:
        updates["nickname"] = nickname
    if aliases:
        merged = sorted(set((json_load(existing["aliases"], []) or []) + list(aliases)))
        updates["aliases"] = json_dump(merged)
    for key in ("division", "country", "x_username", "ufc_profile_url"):
        if fields.get(key) and not existing[key]:
            updates[key] = fields[key]
    if updates:
        assignments = ", ".join(f"{key} = ?" for key in updates)
        execute(f"UPDATE fighters SET {assignments}, updated_at = ? WHERE id = ?",
                (*updates.values(), now, existing["id"]))
    return int(existing["id"])


def record_fighter_mention(name: str, when: Optional[str] = None) -> None:
    normalized = normalize_text(name)
    execute(
        "UPDATE fighters SET mention_count = mention_count + 1, last_mentioned_at = ?, updated_at = ? "
        "WHERE normalized_name = ?",
        (when or utcnow_iso(), utcnow_iso(), normalized),
    )


def get_fighter(fighter_id: int) -> Optional[Dict[str, Any]]:
    return hydrate_fighter(query_one("SELECT * FROM fighters WHERE id = ?", (fighter_id,)))


def get_fighter_by_name(name: str) -> Optional[Dict[str, Any]]:
    return hydrate_fighter(
        query_one("SELECT * FROM fighters WHERE normalized_name = ?", (normalize_text(name),))
    )


def list_fighters(limit: int = 500, order: str = "name") -> List[Dict[str, Any]]:
    order_sql = {
        "name": "name ASC",
        "mentions": "mention_count DESC, name ASC",
        "recent": "last_mentioned_at DESC NULLS LAST, name ASC",
    }.get(order, "name ASC")
    if order == "recent":  # SQLite lacks NULLS LAST before 3.30; emulate it
        order_sql = "last_mentioned_at IS NULL, last_mentioned_at DESC, name ASC"
    rows = query_all(f"SELECT * FROM fighters ORDER BY {order_sql} LIMIT ?", (limit,))
    return [hydrate_fighter(row) for row in rows]  # type: ignore[misc]


def search_fighters(term: str, limit: int = 25) -> List[Dict[str, Any]]:
    pattern = f"%{normalize_text(term)}%"
    rows = query_all(
        "SELECT * FROM fighters WHERE normalized_name LIKE ? OR LOWER(COALESCE(nickname,'')) LIKE ? "
        "OR LOWER(COALESCE(aliases,'')) LIKE ? ORDER BY mention_count DESC, name LIMIT ?",
        (pattern, f"%{term.lower()}%", f"%{term.lower()}%", limit),
    )
    return [hydrate_fighter(row) for row in rows]  # type: ignore[misc]


def update_fighter_ranking(
    name: str, position: Optional[int], is_champion: bool, division: str,
    source_url: Optional[str], ranking_date: Optional[str],
) -> None:
    """Only ever called with data collected from an official rankings page."""
    execute(
        "UPDATE fighters SET current_rank = ?, is_champion = ?, rank_division = ?, division = "
        "COALESCE(division, ?), rank_source_url = ?, rank_updated_at = ?, updated_at = ? "
        "WHERE normalized_name = ?",
        (position, 1 if is_champion else 0, division, division, source_url, ranking_date,
         utcnow_iso(), normalize_text(name)),
    )


# ----------------------------------------------------------------- events --
def upsert_event(
    name: str,
    event_date: Optional[str] = None,
    location: Optional[str] = None,
    venue: Optional[str] = None,
    ufc_url: Optional[str] = None,
    data_origin: str = "detected",
    status: Optional[str] = None,
    is_demo: bool = False,
) -> int:
    name = str(name).strip()
    if not name:
        return 0
    normalized = normalize_text(name)
    now = utcnow_iso()
    existing = query_one("SELECT * FROM events WHERE normalized_name = ?", (normalized,))
    if existing is None:
        return execute(
            "INSERT INTO events (name, normalized_name, short_name, event_date, location, venue, status, "
            "ufc_url, data_origin, mention_count, is_demo, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,0,?,?,?)",
            (name, normalized, name, event_date, location, venue, status or "scheduled", ufc_url,
             data_origin, 1 if is_demo else 0, now, now),
        )
    updates: Dict[str, Any] = {}
    # Collected data always wins over data merely detected in article text.
    prefer = data_origin in ("collected", "user")
    for key, value in (("event_date", event_date), ("location", location), ("venue", venue),
                       ("ufc_url", ufc_url), ("status", status)):
        if value and (prefer or not existing[key]):
            updates[key] = value
    if prefer and existing["data_origin"] != "collected":
        updates["data_origin"] = data_origin
    if updates:
        assignments = ", ".join(f"{key} = ?" for key in updates)
        execute(f"UPDATE events SET {assignments}, updated_at = ? WHERE id = ?",
                (*updates.values(), now, existing["id"]))
    return int(existing["id"])


def record_event_mention(name: str, when: Optional[str] = None) -> None:
    execute(
        "UPDATE events SET mention_count = mention_count + 1, last_mentioned_at = ?, updated_at = ? "
        "WHERE normalized_name = ?",
        (when or utcnow_iso(), utcnow_iso(), normalize_text(name)),
    )


def get_event(event_id: int) -> Optional[Dict[str, Any]]:
    row = query_one("SELECT * FROM events WHERE id = ?", (event_id,))
    return dict(row) if row else None


def get_event_by_name(name: str) -> Optional[Dict[str, Any]]:
    row = query_one("SELECT * FROM events WHERE normalized_name = ?", (normalize_text(name),))
    return dict(row) if row else None


def list_events(limit: int = 100, upcoming_only: bool = False) -> List[Dict[str, Any]]:
    if upcoming_only:
        rows = query_all(
            "SELECT * FROM events WHERE event_date IS NULL OR event_date >= ? "
            "ORDER BY event_date IS NULL, event_date ASC LIMIT ?",
            (utcnow_iso()[:10], limit),
        )
    else:
        rows = query_all(
            "SELECT * FROM events ORDER BY event_date IS NULL, event_date DESC LIMIT ?", (limit,)
        )
    return rows_to_dicts(rows)


def search_events(term: str, limit: int = 25) -> List[Dict[str, Any]]:
    pattern = f"%{normalize_text(term)}%"
    return rows_to_dicts(query_all(
        "SELECT * FROM events WHERE normalized_name LIKE ? ORDER BY event_date DESC LIMIT ?",
        (pattern, limit),
    ))


# ------------------------------------------------------- fight card items --
def pair_key_for(fighter_a: str, fighter_b: str) -> str:
    return "|".join(sorted([normalize_text(fighter_a), normalize_text(fighter_b)]))


def upsert_fight(
    event_id: int,
    fighter_a: str,
    fighter_b: str,
    weight_class: Optional[str] = None,
    is_title_fight: bool = False,
    segment: Optional[str] = None,
    bout_order: Optional[int] = None,
    status: str = "scheduled",
    confidence: str = "reported",
    source_article_id: Optional[int] = None,
    source_story_id: Optional[int] = None,
    source_url: Optional[str] = None,
    source_name: Optional[str] = None,
    is_demo: bool = False,
) -> tuple:
    """Insert or update one bout. Returns (fight_id, created?, changes[])."""
    now = utcnow_iso()
    key = pair_key_for(fighter_a, fighter_b)
    existing = query_one(
        "SELECT * FROM fight_card_items WHERE event_id = ? AND pair_key = ?", (event_id, key)
    )
    if existing is None:
        fight_id = execute(
            "INSERT INTO fight_card_items (event_id, fighter_a, fighter_b, pair_key, weight_class, "
            "is_title_fight, segment, bout_order, status, confidence, source_article_id, source_story_id, "
            "source_url, source_name, first_seen_at, last_updated_at, is_demo) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (event_id, fighter_a, fighter_b, key, weight_class, 1 if is_title_fight else 0, segment,
             bout_order, status, confidence, source_article_id, source_story_id, source_url, source_name,
             now, now, 1 if is_demo else 0),
        )
        return fight_id, True, []

    changes: List[Dict[str, Any]] = []
    updates: Dict[str, Any] = {}
    if status and status != existing["status"]:
        updates["status"] = status
        changes.append({"change_type": "cancellation" if status == "cancelled" else "status_change",
                        "before_text": existing["status"], "after_text": status})
    if weight_class and weight_class != existing["weight_class"]:
        updates["weight_class"] = weight_class
        if existing["weight_class"]:
            changes.append({"change_type": "weight_class_change",
                            "before_text": existing["weight_class"], "after_text": weight_class})
    if is_title_fight and not existing["is_title_fight"]:
        updates["is_title_fight"] = 1
        changes.append({"change_type": "title_fight_change", "before_text": "non-title",
                        "after_text": "title fight"})
    if segment and segment != existing["segment"]:
        updates["segment"] = segment
        if existing["segment"] and segment in ("main_event", "co_main"):
            changes.append({
                "change_type": "main_event_change" if segment == "main_event" else "co_main_change",
                "before_text": existing["segment"], "after_text": segment,
            })
    if confidence == "official" and existing["confidence"] != "official":
        updates["confidence"] = "official"
    if bout_order is not None and bout_order != existing["bout_order"]:
        updates["bout_order"] = bout_order
    if updates:
        assignments = ", ".join(f"{key_} = ?" for key_ in updates)
        execute(
            f"UPDATE fight_card_items SET {assignments}, last_updated_at = ?, "
            "source_article_id = COALESCE(?, source_article_id), source_url = COALESCE(?, source_url), "
            "source_name = COALESCE(?, source_name) WHERE id = ?",
            (*updates.values(), now, source_article_id, source_url, source_name, existing["id"]),
        )
    return int(existing["id"]), False, changes


def set_fight_status(fight_id: int, status: str) -> None:
    execute("UPDATE fight_card_items SET status = ?, last_updated_at = ? WHERE id = ?",
            (status, utcnow_iso(), fight_id))


def get_fight(fight_id: int) -> Optional[Dict[str, Any]]:
    row = query_one("SELECT * FROM fight_card_items WHERE id = ?", (fight_id,))
    return dict(row) if row else None


def find_fights_for_fighter(event_id: int, fighter: str) -> List[Dict[str, Any]]:
    normalized = normalize_text(fighter)
    rows = query_all("SELECT * FROM fight_card_items WHERE event_id = ?", (event_id,))
    return [dict(row) for row in rows if normalized in str(row["pair_key"]).split("|")]


def fight_card(event_id: int) -> List[Dict[str, Any]]:
    rows = query_all(
        "SELECT * FROM fight_card_items WHERE event_id = ? ORDER BY "
        "CASE segment WHEN 'main_event' THEN 0 WHEN 'co_main' THEN 1 WHEN 'main_card' THEN 2 "
        "WHEN 'prelims' THEN 3 ELSE 4 END, bout_order IS NULL, bout_order, id",
        (event_id,),
    )
    return rows_to_dicts(rows)


def record_card_change(
    event_id: Optional[int],
    change_type: str,
    before_text: Optional[str] = None,
    after_text: Optional[str] = None,
    reason: Optional[str] = None,
    status: str = "REPORTED",
    event_name: Optional[str] = None,
    fight_card_item_id: Optional[int] = None,
    story_id: Optional[int] = None,
    source_article_id: Optional[int] = None,
    source_url: Optional[str] = None,
    source_name: Optional[str] = None,
    occurred_at: Optional[str] = None,
    is_demo: bool = False,
) -> int:
    return execute(
        "INSERT INTO fight_card_changes (event_id, event_name, fight_card_item_id, change_type, "
        "before_text, after_text, reason, status, story_id, source_article_id, source_url, source_name, "
        "detected_at, occurred_at, is_demo) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (event_id, event_name, fight_card_item_id, change_type, before_text, after_text, reason, status,
         story_id, source_article_id, source_url, source_name, utcnow_iso(),
         occurred_at or utcnow_iso(), 1 if is_demo else 0),
    )


def card_changes(event_id: Optional[int] = None, limit: int = 50) -> List[Dict[str, Any]]:
    if event_id:
        rows = query_all(
            "SELECT * FROM fight_card_changes WHERE event_id = ? ORDER BY detected_at DESC LIMIT ?",
            (event_id, limit),
        )
    else:
        rows = query_all("SELECT * FROM fight_card_changes ORDER BY detected_at DESC LIMIT ?", (limit,))
    return rows_to_dicts(rows)


def find_card_change(
    event_id: Optional[int], change_type: str, after_text: Optional[str]
) -> Optional[Dict[str, Any]]:
    """The same change reported by several outlets is ONE entry in the log."""
    row = query_one(
        "SELECT * FROM fight_card_changes WHERE COALESCE(event_id,-1) = COALESCE(?,-1) "
        "AND change_type = ? AND COALESCE(after_text,'') = COALESCE(?,'') "
        "ORDER BY detected_at ASC LIMIT 1",
        (event_id, change_type, after_text),
    )
    return dict(row) if row else None


def card_change_exists(event_id: Optional[int], change_type: str, after_text: Optional[str],
                       source_article_id: Optional[int] = None) -> bool:
    return find_card_change(event_id, change_type, after_text) is not None


def upgrade_card_change(
    change_id: int, status: str, source_name: Optional[str] = None,
    source_url: Optional[str] = None, source_article_id: Optional[int] = None,
    reason: Optional[str] = None,
) -> None:
    """Promote a logged change when a stronger source confirms it."""
    execute(
        "UPDATE fight_card_changes SET status = ?, source_name = COALESCE(?, source_name), "
        "source_url = COALESCE(?, source_url), source_article_id = COALESCE(?, source_article_id), "
        "reason = COALESCE(?, reason) WHERE id = ?",
        (status, source_name, source_url, source_article_id, reason, change_id),
    )
