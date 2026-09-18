"""Story storage: clusters of articles/social posts about one development."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import execute, json_dump, json_load, query_all, query_one, query_value, transaction
from utils.textutil import sha1, slugify, truncate
from utils.timeutil import hours_ago_iso, utcnow_iso

JSON_FIELDS = (
    "status_reasons", "relevance_breakdown", "support_reasons", "trending_reasons",
    "fighters", "events", "keywords", "conflict_notes",
)


def hydrate(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    data = dict(row)
    for field in JSON_FIELDS:
        if field in data:
            default = {} if field == "relevance_breakdown" else []
            data[field] = json_load(data.get(field), default)
            if data[field] is None:
                data[field] = default
    return data


def hydrate_all(rows: Any) -> List[Dict[str, Any]]:
    return [hydrate(row) for row in rows]  # type: ignore[misc]


def create_story(story: Dict[str, Any]) -> int:
    now = utcnow_iso()
    headline = story.get("headline") or "Untitled story"
    return execute(
        "INSERT INTO stories (slug, headline, summary, status, status_reasons, status_updated_at, "
        "category, relevance, relevance_breakdown, support_score, support_label, support_reasons, "
        "first_seen_at, last_updated_at, article_count, source_count, independent_source_count, "
        "social_post_count, official_confirmed, has_conflict, conflict_notes, is_breaking, "
        "is_developing, is_trending, trending_score, trending_reasons, fighters, events, keywords, "
        "event_id, primary_article_id, image_url, update_count, is_demo, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            story.get("slug") or slugify(headline), headline, story.get("summary"),
            story.get("status", "UNVERIFIED"), json_dump(story.get("status_reasons") or []), now,
            story.get("category", "general"), float(story.get("relevance") or 0),
            json_dump(story.get("relevance_breakdown") or {}), float(story.get("support_score") or 0),
            story.get("support_label"), json_dump(story.get("support_reasons") or []),
            story.get("first_seen_at") or now, story.get("last_updated_at") or now,
            int(story.get("article_count") or 0), int(story.get("source_count") or 0),
            int(story.get("independent_source_count") or 0), int(story.get("social_post_count") or 0),
            1 if story.get("official_confirmed") else 0, 1 if story.get("has_conflict") else 0,
            json_dump(story.get("conflict_notes") or []), 1 if story.get("is_breaking") else 0,
            1 if story.get("is_developing") else 0, 1 if story.get("is_trending") else 0,
            float(story.get("trending_score") or 0), json_dump(story.get("trending_reasons") or []),
            json_dump(story.get("fighters") or []), json_dump(story.get("events") or []),
            json_dump(story.get("keywords") or []), story.get("event_id"),
            story.get("primary_article_id"), story.get("image_url"),
            int(story.get("update_count") or 0), 1 if story.get("is_demo") else 0, now, now,
        ),
    )


def update_story(story_id: int, **fields: Any) -> None:
    allowed = {
        "slug", "headline", "summary", "status", "status_reasons", "status_updated_at", "category",
        "relevance", "relevance_breakdown", "support_score", "support_label", "support_reasons",
        "first_seen_at", "last_updated_at", "article_count", "source_count",
        "independent_source_count", "social_post_count", "official_confirmed", "has_conflict",
        "conflict_notes", "is_breaking", "is_developing", "is_trending", "trending_score",
        "trending_reasons", "fighters", "events", "keywords", "event_id", "primary_article_id",
        "image_url", "update_count",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    for field in JSON_FIELDS:
        if field in updates and not isinstance(updates[field], (str, type(None))):
            updates[field] = json_dump(updates[field])
    for flag in ("official_confirmed", "has_conflict", "is_breaking", "is_developing", "is_trending"):
        if flag in updates:
            updates[flag] = 1 if updates[flag] else 0
    assignments = ", ".join(f"{key} = ?" for key in updates)
    execute(
        f"UPDATE stories SET {assignments}, updated_at = ? WHERE id = ?",
        (*updates.values(), utcnow_iso(), story_id),
    )


def get_story(story_id: int) -> Optional[Dict[str, Any]]:
    return hydrate(query_one("SELECT * FROM stories WHERE id = ?", (story_id,)))


def attach_article(
    story_id: int, article_id: int, role: str = "corroboration",
    similarity: Optional[float] = None, match_reasons: Optional[List[str]] = None,
) -> None:
    with transaction() as connection:
        connection.execute(
            "INSERT OR IGNORE INTO story_sources (story_id, article_id, role, similarity, match_reasons, "
            "added_at) VALUES (?,?,?,?,?,?)",
            (story_id, article_id, role, similarity, json_dump(match_reasons or []), utcnow_iso()),
        )
        connection.execute("UPDATE articles SET story_id = ? WHERE id = ?", (story_id, article_id))


def detach_article(story_id: int, article_id: int) -> None:
    with transaction() as connection:
        connection.execute(
            "DELETE FROM story_sources WHERE story_id = ? AND article_id = ?", (story_id, article_id)
        )
        connection.execute(
            "UPDATE articles SET story_id = NULL WHERE id = ? AND story_id = ?", (article_id, story_id)
        )


def add_timeline_entry(
    story_id: int,
    occurred_at: str,
    kind: str,
    headline: Optional[str] = None,
    detail: Optional[str] = None,
    source_name: Optional[str] = None,
    source_type: Optional[str] = None,
    url: Optional[str] = None,
    article_id: Optional[int] = None,
    social_post_id: Optional[int] = None,
) -> None:
    dedup_key = sha1(f"{kind}|{occurred_at}|{headline or ''}|{url or ''}")
    execute(
        "INSERT OR IGNORE INTO story_updates (story_id, occurred_at, kind, headline, detail, source_name, "
        "source_type, url, article_id, social_post_id, dedup_key, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (story_id, occurred_at, kind, truncate(headline, 240) if headline else None, detail, source_name,
         source_type, url, article_id, social_post_id, dedup_key, utcnow_iso()),
    )


def story_timeline(story_id: int, limit: int = 200) -> List[Dict[str, Any]]:
    rows = query_all(
        "SELECT * FROM story_updates WHERE story_id = ? ORDER BY occurred_at ASC, id ASC LIMIT ?",
        (story_id, limit),
    )
    return [dict(row) for row in rows]


def timeline_count(story_id: int) -> int:
    return int(query_value("SELECT COUNT(*) FROM story_updates WHERE story_id = ?", (story_id,), 0))


def candidate_stories(hours: int = 72, limit: int = 400) -> List[Dict[str, Any]]:
    """Recent stories a new article could belong to."""
    return hydrate_all(query_all(
        "SELECT * FROM stories WHERE last_updated_at >= ? ORDER BY last_updated_at DESC LIMIT ?",
        (hours_ago_iso(hours), limit),
    ))


def list_stories(
    limit: int = 60,
    offset: int = 0,
    status: Optional[str] = None,
    statuses: Optional[List[str]] = None,
    category: Optional[str] = None,
    breaking: Optional[bool] = None,
    developing: Optional[bool] = None,
    trending: Optional[bool] = None,
    min_relevance: float = 0,
    since_hours: Optional[int] = None,
    sort: str = "Newest",
    include_demo: bool = True,
    search: Optional[str] = None,
    fighter: Optional[str] = None,
    event_id: Optional[int] = None,
) -> List[Dict[str, Any]]:
    clauses: List[str] = ["1 = 1"]
    params: List[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if statuses:
        clauses.append(f"status IN ({','.join('?' * len(statuses))})")
        params.extend(statuses)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if breaking is not None:
        clauses.append("is_breaking = ?")
        params.append(1 if breaking else 0)
    if developing is not None:
        clauses.append("is_developing = ?")
        params.append(1 if developing else 0)
    if trending is not None:
        clauses.append("is_trending = ?")
        params.append(1 if trending else 0)
    if min_relevance:
        clauses.append("relevance >= ?")
        params.append(float(min_relevance))
    if since_hours:
        clauses.append("last_updated_at >= ?")
        params.append(hours_ago_iso(since_hours))
    if not include_demo:
        clauses.append("is_demo = 0")
    if search:
        pattern = f"%{search.strip()}%"
        clauses.append("(headline LIKE ? OR summary LIKE ? OR fighters LIKE ? OR events LIKE ? OR keywords LIKE ?)")
        params.extend([pattern] * 5)
    if fighter:
        clauses.append("fighters LIKE ?")
        params.append(f'%"{fighter}"%')
    if event_id:
        clauses.append("event_id = ?")
        params.append(event_id)

    order = {
        "Newest": "COALESCE(first_seen_at, last_updated_at) DESC",
        "Most Relevant": "relevance DESC, last_updated_at DESC",
        "Most Sources": "independent_source_count DESC, source_count DESC, relevance DESC",
        "Most Discussed": "social_post_count DESC, trending_score DESC, relevance DESC",
        "Recently Updated": "last_updated_at DESC",
    }.get(sort, "COALESCE(first_seen_at, last_updated_at) DESC")

    return hydrate_all(query_all(
        f"SELECT * FROM stories WHERE {' AND '.join(clauses)} ORDER BY {order} LIMIT ? OFFSET ?",
        (*params, limit, offset),
    ))


def related_stories(story: Dict[str, Any], limit: int = 6) -> List[Dict[str, Any]]:
    """Other stories sharing a fighter or event with this one."""
    fighters = story.get("fighters") or []
    events = story.get("events") or []
    if not fighters and not events:
        return []
    clauses, params = [], []
    for name in fighters[:4]:
        clauses.append("fighters LIKE ?")
        params.append(f'%"{name}"%')
    for name in events[:2]:
        clauses.append("events LIKE ?")
        params.append(f'%"{name}"%')
    return hydrate_all(query_all(
        f"SELECT * FROM stories WHERE id != ? AND ({' OR '.join(clauses)}) "
        "ORDER BY last_updated_at DESC LIMIT ?",
        (story.get("id"), *params, limit),
    ))


def stories_for_fighter(name: str, limit: int = 40) -> List[Dict[str, Any]]:
    return hydrate_all(query_all(
        'SELECT * FROM stories WHERE fighters LIKE ? ORDER BY last_updated_at DESC LIMIT ?',
        (f'%"{name}"%', limit),
    ))


def stories_for_event(event_id: int, event_name: Optional[str] = None, limit: int = 60) -> List[Dict[str, Any]]:
    if event_name:
        return hydrate_all(query_all(
            "SELECT * FROM stories WHERE event_id = ? OR events LIKE ? ORDER BY last_updated_at DESC LIMIT ?",
            (event_id, f'%"{event_name}"%', limit),
        ))
    return hydrate_all(query_all(
        "SELECT * FROM stories WHERE event_id = ? ORDER BY last_updated_at DESC LIMIT ?", (event_id, limit)
    ))


def dashboard_counts(new_window_hours: int = 24) -> Dict[str, int]:
    """Numbers for the dashboard header."""
    cutoff = hours_ago_iso(new_window_hours)
    def count(where: str, params: tuple = ()) -> int:
        return int(query_value(f"SELECT COUNT(*) FROM stories WHERE {where}", params, 0))

    return {
        "total": count("1=1"),
        "new": count("first_seen_at >= ?", (cutoff,)),
        "breaking": count("is_breaking = 1"),
        "important": count("relevance >= ?", (55,)),
        "rumors": count("status IN ('RUMOR','UNVERIFIED','FIGHTER_CLAIM')"),
        "developing": count("is_developing = 1"),
        "trending": count("is_trending = 1"),
        "confirmed": count("status = 'CONFIRMED'"),
        "updated_recently": count("last_updated_at >= ?", (cutoff,)),
    }


