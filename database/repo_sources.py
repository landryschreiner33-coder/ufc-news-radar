"""News source registry + health tracking."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import execute, json_dump, json_load, query_all, query_one, rows_to_dicts
from utils.timeutil import utcnow_iso


def upsert_source(source: Dict[str, Any], overwrite: bool = False) -> int:
    """Insert a source, or refresh the built-in fields of an existing one.

    ``overwrite=False`` (the default on startup) protects user edits: only
    fields that describe *where* to fetch are refreshed, never enablement or
    reliability, which the user may have changed in Settings.
    """
    now = utcnow_iso()
    existing = query_one("SELECT * FROM sources WHERE key = ?", (source["key"],))
    fallbacks = json_dump(source.get("fallback_urls") or [])
    if existing is None:
        return execute(
            "INSERT INTO sources (key, name, adapter, feed_url, fallback_urls, homepage, domain, "
            "source_type, reliability_weight, independence_group, priority, enabled, status, notes, "
            "is_builtin, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                source["key"], source["name"], source.get("adapter", "rss"), source.get("feed_url"),
                fallbacks, source.get("homepage"), source.get("domain"),
                source.get("source_type", "UNKNOWN"), float(source.get("reliability_weight", 0.5)),
                source.get("independence_group"), int(source.get("priority", 100)),
                int(source.get("enabled", 1)), "unknown", source.get("notes"),
                int(source.get("is_builtin", 1)), now, now,
            ),
        )
    if overwrite:
        execute(
            "UPDATE sources SET name=?, adapter=?, feed_url=?, fallback_urls=?, homepage=?, domain=?, "
            "source_type=?, reliability_weight=?, independence_group=?, priority=?, enabled=?, notes=?, "
            "updated_at=? WHERE id=?",
            (
                source["name"], source.get("adapter", "rss"), source.get("feed_url"), fallbacks,
                source.get("homepage"), source.get("domain"), source.get("source_type", "UNKNOWN"),
                float(source.get("reliability_weight", 0.5)), source.get("independence_group"),
                int(source.get("priority", 100)), int(source.get("enabled", 1)), source.get("notes"),
                now, existing["id"],
            ),
        )
    else:
        execute(
            "UPDATE sources SET name=?, adapter=?, homepage=?, domain=?, fallback_urls=?, notes=?, "
            "updated_at=? WHERE id=?",
            (source["name"], source.get("adapter", "rss"), source.get("homepage"), source.get("domain"),
             fallbacks, source.get("notes"), now, existing["id"]),
        )
    return int(existing["id"])


def get_source(source_id: int) -> Optional[Dict[str, Any]]:
    row = query_one("SELECT * FROM sources WHERE id = ?", (source_id,))
    return _hydrate(row)


def get_source_by_key(key: str) -> Optional[Dict[str, Any]]:
    row = query_one("SELECT * FROM sources WHERE key = ?", (key,))
    return _hydrate(row)


def list_sources(enabled_only: bool = False, adapter: Optional[str] = None) -> List[Dict[str, Any]]:
    clauses, params = [], []
    if enabled_only:
        clauses.append("enabled = 1")
    if adapter:
        clauses.append("adapter = ?")
        params.append(adapter)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = query_all(f"SELECT * FROM sources {where} ORDER BY priority, name", params)
    return [_hydrate(row) for row in rows]  # type: ignore[misc]


def _hydrate(row: Any) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    data = dict(row)
    data["fallback_urls"] = json_load(data.get("fallback_urls"), []) or []
    return data


def candidate_urls(source: Dict[str, Any]) -> List[str]:
    """Feed URLs to try, best first (the last known-good URL comes first)."""
    urls: List[str] = []
    for url in [source.get("resolved_feed_url"), source.get("feed_url"), *(source.get("fallback_urls") or [])]:
        if url and url not in urls:
            urls.append(url)
    return urls


def set_source_enabled(source_id: int, enabled: bool) -> None:
    execute(
        "UPDATE sources SET enabled = ?, status = CASE WHEN ? THEN status ELSE 'disabled' END, updated_at = ? "
        "WHERE id = ?",
        (1 if enabled else 0, 1 if enabled else 0, utcnow_iso(), source_id),
    )


def update_source_fields(source_id: int, **fields: Any) -> None:
    allowed = {
        "name", "feed_url", "homepage", "domain", "source_type", "reliability_weight",
        "independence_group", "priority", "enabled", "notes", "adapter",
    }
    updates = {key: value for key, value in fields.items() if key in allowed}
    if not updates:
        return
    assignments = ", ".join(f"{key} = ?" for key in updates)
    execute(
        f"UPDATE sources SET {assignments}, updated_at = ? WHERE id = ?",
        (*updates.values(), utcnow_iso(), source_id),
    )


def record_attempt(source_id: int) -> None:
    execute("UPDATE sources SET last_attempt_at = ?, updated_at = ? WHERE id = ?",
            (utcnow_iso(), utcnow_iso(), source_id))


def record_success(
    source_id: int, article_count: int = 0, resolved_url: Optional[str] = None,
    latest_article_at: Optional[str] = None,
) -> None:
    now = utcnow_iso()
    execute(
        "UPDATE sources SET status='ok', last_success_at=?, consecutive_failures=0, last_error=NULL, "
        "last_error_kind=NULL, article_count = article_count + ?, "
        "resolved_feed_url = COALESCE(?, resolved_feed_url), "
        "last_article_at = CASE WHEN ? IS NOT NULL AND (last_article_at IS NULL OR ? > last_article_at) "
        "  THEN ? ELSE last_article_at END, "
        "updated_at=? WHERE id=?",
        (now, int(article_count), resolved_url, latest_article_at, latest_article_at, latest_article_at,
         now, source_id),
    )


def record_failure(source_id: int, error: str, error_kind: str = "unknown") -> None:
    now = utcnow_iso()
    execute(
        "UPDATE sources SET status='error', last_error=?, last_error_kind=?, last_error_at=?, "
        "consecutive_failures = consecutive_failures + 1, updated_at=? WHERE id=?",
        (str(error)[:500], error_kind, now, now, source_id),
    )


def source_health() -> List[Dict[str, Any]]:
    """Rows for the Source Health panel."""
    rows = query_all(
        "SELECT id, key, name, adapter, enabled, status, feed_url, resolved_feed_url, homepage, "
        "source_type, reliability_weight, last_success_at, last_attempt_at, last_error, last_error_kind, "
        "last_error_at, consecutive_failures, article_count, last_article_at "
        "FROM sources ORDER BY enabled DESC, priority, name"
    )
    return rows_to_dicts(rows)


def delete_source(source_id: int) -> None:
    execute("DELETE FROM sources WHERE id = ?", (source_id,))
