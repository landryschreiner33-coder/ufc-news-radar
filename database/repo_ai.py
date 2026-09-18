"""Cached generated text (AI provider output and template-mode output)."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import execute, json_dump, json_load, query_all, query_one
from utils.timeutil import utcnow_iso


def save_summary(
    story_id: Optional[int],
    kind: str,
    content: str,
    provider: str,
    prompt_hash: str,
    model: Optional[str] = None,
    content_json: Any = None,
    sources: Optional[List[Dict[str, Any]]] = None,
    grounding: Optional[Dict[str, Any]] = None,
) -> int:
    return execute(
        "INSERT INTO summaries (story_id, kind, provider, model, content, content_json, prompt_hash, "
        "sources_json, grounding_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(story_id, kind, prompt_hash, provider) DO UPDATE SET content=excluded.content, "
        "content_json=excluded.content_json, sources_json=excluded.sources_json, "
        "grounding_json=excluded.grounding_json, created_at=excluded.created_at",
        (story_id, kind, provider, model, content, json_dump(content_json), prompt_hash,
         json_dump(sources or []), json_dump(grounding or {}), utcnow_iso()),
    )


def get_summary(story_id: Optional[int], kind: str, prompt_hash: str, provider: str) -> Optional[Dict[str, Any]]:
    row = query_one(
        "SELECT * FROM summaries WHERE COALESCE(story_id,-1) = COALESCE(?,-1) AND kind = ? "
        "AND prompt_hash = ? AND provider = ?",
        (story_id, kind, prompt_hash, provider),
    )
    if row is None:
        return None
    data = dict(row)
    data["content_json"] = json_load(data.get("content_json"))
    data["sources_json"] = json_load(data.get("sources_json"), [])
    data["grounding_json"] = json_load(data.get("grounding_json"), {})
    return data


def latest_summary(story_id: int, kind: str) -> Optional[Dict[str, Any]]:
    row = query_one(
        "SELECT * FROM summaries WHERE story_id = ? AND kind = ? ORDER BY created_at DESC LIMIT 1",
        (story_id, kind),
    )
    if row is None:
        return None
    data = dict(row)
    data["content_json"] = json_load(data.get("content_json"))
    data["sources_json"] = json_load(data.get("sources_json"), [])
    data["grounding_json"] = json_load(data.get("grounding_json"), {})
    return data


def summaries_for_story(story_id: int) -> List[Dict[str, Any]]:
    rows = query_all("SELECT * FROM summaries WHERE story_id = ? ORDER BY created_at DESC", (story_id,))
    return [dict(row) for row in rows]


def cache_get(cache_key: str) -> Optional[str]:
    row = query_one("SELECT response FROM ai_cache WHERE cache_key = ?", (cache_key,))
    return row["response"] if row else None


def cache_put(cache_key: str, kind: str, provider: str, response: str, model: Optional[str] = None) -> None:
    execute(
        "INSERT INTO ai_cache (cache_key, kind, provider, model, response, created_at) VALUES (?,?,?,?,?,?) "
        "ON CONFLICT(cache_key) DO UPDATE SET response=excluded.response, created_at=excluded.created_at",
        (cache_key, kind, provider, model, response, utcnow_iso()),
    )


def cache_size() -> int:
    row = query_one("SELECT COUNT(*) AS total FROM ai_cache")
    return int(row["total"]) if row else 0


def clear_cache() -> None:
    execute("DELETE FROM ai_cache")
