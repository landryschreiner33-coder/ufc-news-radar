"""Article storage and retrieval."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import execute, json_dump, json_load, query_all, query_one, query_value, transaction
from utils.textutil import canonical_url, domain_of, normalize_title, sha1, url_hash
from utils.timeutil import hours_ago_iso, utcnow_iso

JSON_FIELDS = ("fighters", "events", "keywords", "attribution_outlets")


def hydrate(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    data = dict(row)
    for field in JSON_FIELDS:
        if field in data:
            data[field] = json_load(data.get(field), []) or []
    return data


def hydrate_all(rows: Any) -> List[Dict[str, Any]]:
    return [hydrate(row) for row in rows]  # type: ignore[misc]


def get_article(article_id: int) -> Optional[Dict[str, Any]]:
    return hydrate(query_one("SELECT * FROM articles WHERE id = ?", (article_id,)))


def get_article_by_url(url: str) -> Optional[Dict[str, Any]]:
    return hydrate(query_one("SELECT * FROM articles WHERE url_hash = ?", (url_hash(url),)))


def insert_article(article: Dict[str, Any]) -> Optional[int]:
    """Insert one normalised article. Returns None if it is already stored."""
    url = article.get("url") or ""
    if not url or not article.get("title"):
        return None
    canonical = canonical_url(url)
    hashed = url_hash(url)
    if query_one("SELECT 1 FROM articles WHERE url_hash = ?", (hashed,)):
        return None
    now = utcnow_iso()
    title = article["title"]
    normalized = normalize_title(title)
    with transaction() as connection:
        cursor = connection.execute(
            "INSERT INTO articles (source_id, source_key, source_name, external_id, url, canonical_url, "
            "url_hash, domain, title, normalized_title, title_hash, author, published_at, collected_at, "
            "excerpt, content_snippet, content_chars, image_url, category, fighters, events, keywords, "
            "language, source_type, reliability_weight, independence_group, attribution_outlets, "
            "is_derivative, speculation_score, has_denial, is_official, story_id, is_demo, raw_json, "
            "created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                article.get("source_id"), article.get("source_key"), article.get("source_name"),
                article.get("external_id"), url, canonical, hashed,
                article.get("domain") or domain_of(canonical), title, normalized, sha1(normalized),
                article.get("author"), article.get("published_at"), article.get("collected_at") or now,
                article.get("excerpt"), article.get("content_snippet"),
                int(article.get("content_chars") or 0), article.get("image_url"),
                article.get("category") or "general", json_dump(article.get("fighters") or []),
                json_dump(article.get("events") or []), json_dump(article.get("keywords") or []),
                article.get("language"), article.get("source_type"),
                article.get("reliability_weight"), article.get("independence_group"),
                json_dump(article.get("attribution_outlets") or []),
                1 if article.get("is_derivative") else 0, float(article.get("speculation_score") or 0.0),
                1 if article.get("has_denial") else 0,
                1 if article.get("is_official") else 0, article.get("story_id"),
                1 if article.get("is_demo") else 0, json_dump(article.get("raw_json")), now,
            ),
        )
        return cursor.lastrowid


def update_article(article_id: int, **fields: Any) -> None:
    allowed = {
        "title", "normalized_title", "author", "published_at", "excerpt", "content_snippet",
        "content_chars", "image_url", "category", "fighters", "events", "keywords", "source_type",
        "reliability_weight", "independence_group", "attribution_outlets", "is_derivative",
        "speculation_score", "is_official", "story_id", "domain", "source_name",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    for field in JSON_FIELDS:
        if field in updates and not isinstance(updates[field], str):
            updates[field] = json_dump(updates[field])
    assignments = ", ".join(f"{key} = ?" for key in updates)
    execute(f"UPDATE articles SET {assignments} WHERE id = ?", (*updates.values(), article_id))


def recent_articles(hours: int = 72, limit: int = 500, include_demo: bool = True) -> List[Dict[str, Any]]:
    cutoff = hours_ago_iso(hours)
    demo_clause = "" if include_demo else "AND is_demo = 0"
    return hydrate_all(query_all(
        f"SELECT * FROM articles WHERE COALESCE(published_at, collected_at) >= ? {demo_clause} "
        "ORDER BY COALESCE(published_at, collected_at) DESC LIMIT ?",
        (cutoff, limit),
    ))


def latest_articles(limit: int = 50, include_demo: bool = True) -> List[Dict[str, Any]]:
    demo_clause = "" if include_demo else "WHERE is_demo = 0"
    return hydrate_all(query_all(
        f"SELECT * FROM articles {demo_clause} ORDER BY COALESCE(published_at, collected_at) DESC LIMIT ?",
        (limit,),
    ))


def articles_for_story(story_id: int) -> List[Dict[str, Any]]:
    return hydrate_all(query_all(
        "SELECT a.*, ss.role, ss.similarity, ss.match_reasons FROM story_sources ss "
        "JOIN articles a ON a.id = ss.article_id WHERE ss.story_id = ? "
        "ORDER BY COALESCE(a.published_at, a.collected_at) ASC",
        (story_id,),
    ))


def unassigned_articles(limit: int = 500) -> List[Dict[str, Any]]:
    return hydrate_all(query_all(
        "SELECT * FROM articles WHERE story_id IS NULL "
        "ORDER BY COALESCE(published_at, collected_at) ASC LIMIT ?",
        (limit,),
    ))


def search_articles(term: str, limit: int = 50) -> List[Dict[str, Any]]:
    pattern = f"%{term.strip()}%"
    return hydrate_all(query_all(
        "SELECT * FROM articles WHERE title LIKE ? OR excerpt LIKE ? OR fighters LIKE ? OR events LIKE ? "
        "ORDER BY COALESCE(published_at, collected_at) DESC LIMIT ?",
        (pattern, pattern, pattern, pattern, limit),
    ))


def article_count(since: Optional[str] = None) -> int:
    if since:
        return int(query_value("SELECT COUNT(*) FROM articles WHERE collected_at >= ?", (since,), 0))
    return int(query_value("SELECT COUNT(*) FROM articles", (), 0))


