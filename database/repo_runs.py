"""Collection run bookkeeping and the X API query cache."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import execute, json_dump, query_all, query_one, rows_to_dicts
from utils.textutil import sha1
from utils.timeutil import minutes_between, utcnow_iso


def start_run(trigger: str = "manual") -> int:
    return execute(
        "INSERT INTO collection_runs (started_at, trigger) VALUES (?,?)", (utcnow_iso(), trigger)
    )


def finish_run(run_id: int, **stats: Any) -> None:
    fields = {
        key: stats.get(key, 0)
        for key in (
            "sources_attempted", "sources_ok", "sources_failed", "articles_seen", "articles_new",
            "stories_new", "stories_updated", "social_new", "duration_ms",
        )
    }
    assignments = ", ".join(f"{key} = ?" for key in fields)
    execute(
        f"UPDATE collection_runs SET finished_at = ?, {assignments}, notes = ?, error = ? WHERE id = ?",
        (utcnow_iso(), *fields.values(), stats.get("notes"), stats.get("error"), run_id),
    )


def last_run() -> Optional[Dict[str, Any]]:
    row = query_one("SELECT * FROM collection_runs ORDER BY id DESC LIMIT 1")
    return dict(row) if row else None


def last_successful_run() -> Optional[Dict[str, Any]]:
    row = query_one(
        "SELECT * FROM collection_runs WHERE finished_at IS NOT NULL AND error IS NULL "
        "ORDER BY id DESC LIMIT 1"
    )
    return dict(row) if row else None


def recent_runs(limit: int = 20) -> List[Dict[str, Any]]:
    return rows_to_dicts(query_all("SELECT * FROM collection_runs ORDER BY id DESC LIMIT ?", (limit,)))


# ---------------------------------------------------------- X query cache --
def query_key(endpoint: str, query: str, params: Optional[Dict[str, Any]] = None) -> str:
    return sha1(f"{endpoint}||{query}||{json_dump(params or {})}")


def get_cached_query(endpoint: str, query: str, params: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    row = query_one("SELECT * FROM x_query_cache WHERE query_hash = ?", (query_key(endpoint, query, params),))
    return dict(row) if row else None


def should_skip_query(
    endpoint: str, query: str, params: Optional[Dict[str, Any]] = None, cache_minutes: int = 30
) -> tuple:
    """(skip?, reason) - keeps the app from spending X quota on repeat queries."""
    cached = get_cached_query(endpoint, query, params)
    if not cached:
        return False, ""
    if cached.get("status") == "rate_limited" and cached.get("rate_limit_reset_epoch"):
        import time

        remaining = int(cached["rate_limit_reset_epoch"]) - int(time.time())
        if remaining > 0:
            return True, f"rate limited - retry in {max(1, remaining // 60)} min"
    elapsed = minutes_between(cached.get("last_run_at"), utcnow_iso())
    if elapsed is not None and elapsed < cache_minutes:
        return True, f"cached {int(elapsed)} min ago (cache window {cache_minutes} min)"
    return False, ""


def record_query(
    endpoint: str,
    query: str,
    params: Optional[Dict[str, Any]] = None,
    result_count: int = 0,
    newest_id: Optional[str] = None,
    status: str = "ok",
    error: Optional[str] = None,
    rate_limit_reset_epoch: Optional[int] = None,
) -> None:
    execute(
        "INSERT INTO x_query_cache (query_hash, endpoint, query, params_json, last_run_at, newest_id, "
        "result_count, status, error, rate_limit_reset_epoch) VALUES (?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(query_hash) DO UPDATE SET last_run_at=excluded.last_run_at, "
        "newest_id=COALESCE(excluded.newest_id, x_query_cache.newest_id), "
        "result_count=excluded.result_count, status=excluded.status, error=excluded.error, "
        "rate_limit_reset_epoch=excluded.rate_limit_reset_epoch",
        (query_key(endpoint, query, params), endpoint, query, json_dump(params or {}), utcnow_iso(),
         newest_id, int(result_count), status, error, rate_limit_reset_epoch),
    )


def recent_queries(limit: int = 25) -> List[Dict[str, Any]]:
    return rows_to_dicts(query_all("SELECT * FROM x_query_cache ORDER BY last_run_at DESC LIMIT ?", (limit,)))
