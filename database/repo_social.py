"""Social post storage (currently X/Twitter) and story links."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import execute, json_dump, json_load, query_all, query_one, query_value, transaction
from utils.timeutil import hours_ago_iso, utcnow_iso

JSON_FIELDS = ("entities_json", "media_json", "referenced_json", "fighters", "events")


def hydrate(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    data = dict(row)
    for field in JSON_FIELDS:
        if field in data:
            data[field] = json_load(data.get(field), [] if field in ("fighters", "events") else {})
    return data


def hydrate_all(rows: Any) -> List[Dict[str, Any]]:
    return [hydrate(row) for row in rows]  # type: ignore[misc]


def upsert_post(post: Dict[str, Any]) -> Optional[int]:
    """Store one post. Returns the row id; existing posts get metric updates."""
    post_id = str(post.get("post_id") or "").strip()
    if not post_id or not post.get("text"):
        return None
    platform = post.get("platform", "x")
    existing = query_one(
        "SELECT id FROM social_posts WHERE platform = ? AND post_id = ?", (platform, post_id)
    )
    now = utcnow_iso()
    if existing:
        execute(
            "UPDATE social_posts SET like_count=?, reply_count=?, repost_count=?, quote_count=?, "
            "impression_count=?, account_type=COALESCE(?, account_type), collected_at=? WHERE id=?",
            (post.get("like_count"), post.get("reply_count"), post.get("repost_count"),
             post.get("quote_count"), post.get("impression_count"), post.get("account_type"),
             now, existing["id"]),
        )
        return int(existing["id"])
    return execute(
        "INSERT INTO social_posts (platform, post_id, author_id, username, display_name, account_type, "
        "account_verified, text, lang, created_at_source, url, like_count, reply_count, repost_count, "
        "quote_count, impression_count, entities_json, media_json, referenced_json, query_source, "
        "fighters, events, category, collected_at, is_demo) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            platform, post_id, post.get("author_id"), (post.get("username") or "").lower() or None,
            post.get("display_name"), post.get("account_type", "UNKNOWN"),
            1 if post.get("account_verified") else 0, post.get("text"), post.get("lang"),
            post.get("created_at_source"), post.get("url"), post.get("like_count"),
            post.get("reply_count"), post.get("repost_count"), post.get("quote_count"),
            post.get("impression_count"), json_dump(post.get("entities_json")),
            json_dump(post.get("media_json")), json_dump(post.get("referenced_json")),
            post.get("query_source"), json_dump(post.get("fighters") or []),
            json_dump(post.get("events") or []), post.get("category"), now,
            1 if post.get("is_demo") else 0,
        ),
    )


def get_post(post_row_id: int) -> Optional[Dict[str, Any]]:
    return hydrate(query_one("SELECT * FROM social_posts WHERE id = ?", (post_row_id,)))


def post_exists(post_id: str, platform: str = "x") -> bool:
    return query_one(
        "SELECT 1 FROM social_posts WHERE platform = ? AND post_id = ?", (platform, str(post_id))
    ) is not None


def recent_posts(hours: int = 48, limit: int = 200, include_demo: bool = True) -> List[Dict[str, Any]]:
    demo_clause = "" if include_demo else "AND is_demo = 0"
    return hydrate_all(query_all(
        f"SELECT * FROM social_posts WHERE COALESCE(created_at_source, collected_at) >= ? {demo_clause} "
        "ORDER BY COALESCE(created_at_source, collected_at) DESC LIMIT ?",
        (hours_ago_iso(hours), limit),
    ))


def latest_posts(limit: int = 50) -> List[Dict[str, Any]]:
    return hydrate_all(query_all(
        "SELECT * FROM social_posts ORDER BY COALESCE(created_at_source, collected_at) DESC LIMIT ?",
        (limit,),
    ))


def posts_by_username(username: str, limit: int = 25) -> List[Dict[str, Any]]:
    return hydrate_all(query_all(
        "SELECT * FROM social_posts WHERE username = ? ORDER BY COALESCE(created_at_source, collected_at) "
        "DESC LIMIT ?",
        (str(username).lstrip("@").lower(), limit),
    ))


def search_posts(term: str, limit: int = 50) -> List[Dict[str, Any]]:
    pattern = f"%{term.strip()}%"
    return hydrate_all(query_all(
        "SELECT * FROM social_posts WHERE text LIKE ? OR username LIKE ? OR fighters LIKE ? "
        "ORDER BY COALESCE(created_at_source, collected_at) DESC LIMIT ?",
        (pattern, pattern, pattern, limit),
    ))


def link_post_to_story(
    story_id: int, social_post_id: int, similarity: Optional[float] = None,
    match_reasons: Optional[List[str]] = None,
) -> None:
    execute(
        "INSERT OR IGNORE INTO story_social_posts (story_id, social_post_id, similarity, match_reasons, "
        "added_at) VALUES (?,?,?,?,?)",
        (story_id, social_post_id, similarity, json_dump(match_reasons or []), utcnow_iso()),
    )


def posts_for_story(story_id: int, limit: int = 50) -> List[Dict[str, Any]]:
    return hydrate_all(query_all(
        "SELECT sp.*, ssp.similarity, ssp.match_reasons FROM story_social_posts ssp "
        "JOIN social_posts sp ON sp.id = ssp.social_post_id WHERE ssp.story_id = ? "
        "ORDER BY COALESCE(sp.created_at_source, sp.collected_at) ASC LIMIT ?",
        (story_id, limit),
    ))


def unlinked_posts(hours: int = 48, limit: int = 200) -> List[Dict[str, Any]]:
    return hydrate_all(query_all(
        "SELECT * FROM social_posts WHERE id NOT IN (SELECT social_post_id FROM story_social_posts) "
        "AND COALESCE(created_at_source, collected_at) >= ? "
        "ORDER BY COALESCE(created_at_source, collected_at) DESC LIMIT ?",
        (hours_ago_iso(hours), limit),
    ))


def post_count(hours: Optional[int] = None) -> int:
    if hours:
        return int(query_value(
            "SELECT COUNT(*) FROM social_posts WHERE COALESCE(created_at_source, collected_at) >= ?",
            (hours_ago_iso(hours),), 0,
        ))
    return int(query_value("SELECT COUNT(*) FROM social_posts", (), 0))


def engagement_for_story(story_id: int) -> Dict[str, int]:
    row = query_one(
        "SELECT COALESCE(SUM(sp.like_count),0) AS likes, COALESCE(SUM(sp.repost_count),0) AS reposts, "
        "COALESCE(SUM(sp.reply_count),0) AS replies, COALESCE(SUM(sp.quote_count),0) AS quotes, "
        "COUNT(*) AS posts FROM story_social_posts ssp JOIN social_posts sp ON sp.id = ssp.social_post_id "
        "WHERE ssp.story_id = ?",
        (story_id,),
    )
    return dict(row) if row else {"likes": 0, "reposts": 0, "replies": 0, "quotes": 0, "posts": 0}
