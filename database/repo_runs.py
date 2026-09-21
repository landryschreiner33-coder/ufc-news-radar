"""Collection run bookkeeping and the X API query cache."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import execute, json_dump, query_all, query_one, rows_to_dicts
from models.types import (
    CollectionOutcome,
    collection_outcome_for,
    collection_outcome_style,
)
from utils.textutil import sha1
from utils.timeutil import humanize_age, minutes_between, utcnow_iso


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
    # The outcome is derived from the run's own numbers rather than trusted
    # from the caller, so a run can never be recorded as a success it was not.
    outcome = collection_outcome_for(int(fields["sources_attempted"]), int(fields["sources_ok"]))
    assignments = ", ".join(f"{key} = ?" for key in fields)
    execute(
        f"UPDATE collection_runs SET finished_at = ?, {assignments}, outcome = ?, "
        "notes = ?, error = ? WHERE id = ?",
        (utcnow_iso(), *fields.values(), outcome, stats.get("notes"), stats.get("error"), run_id),
    )


def last_run() -> Optional[Dict[str, Any]]:
    row = query_one("SELECT * FROM collection_runs ORDER BY id DESC LIMIT 1")
    return dict(row) if row else None


def last_successful_run() -> Optional[Dict[str, Any]]:
    """The newest run in which at least one source returned."""
    row = query_one(
        "SELECT * FROM collection_runs WHERE sources_ok > 0 ORDER BY id DESC LIMIT 1")
    return dict(row) if row else None


def collection_status() -> Dict[str, Any]:
    """What the interface must say about the state of the data.

    This is the single answer to "is what I am looking at current?", and it is
    built from the recorded runs rather than from the fact that a button was
    pressed. A run where every source failed reports TOTAL_FAILURE and the
    freshness stamp stays at the last run that actually returned something.
    """
    run = last_run()
    successful = last_successful_run()
    if run is None:
        style = collection_outcome_style(CollectionOutcome.NOT_RUN.value)
        return {
            "outcome": CollectionOutcome.NOT_RUN.value,
            "emoji": style.emoji, "label": style.label, "color": style.color,
            "headline": "No collection has run yet.",
            "detail": "Press Refresh now to pull from every enabled source.",
            "sources_ok": 0, "sources_attempted": 0, "sources_failed": 0,
            "attempted_at": None, "succeeded_at": None,
            "is_stale": False, "needs_attention": False,
        }

    outcome = run.get("outcome") or collection_outcome_for(
        int(run.get("sources_attempted") or 0), int(run.get("sources_ok") or 0))
    style = collection_outcome_style(outcome)
    attempted = int(run.get("sources_attempted") or 0)
    ok = int(run.get("sources_ok") or 0)
    failed = int(run.get("sources_failed") or 0)
    succeeded_at = (successful or {}).get("finished_at") or (successful or {}).get("started_at")

    if outcome == CollectionOutcome.TOTAL_FAILURE.value:
        headline = f"{style.emoji} COLLECTION FAILED - 0 of {attempted} sources returned."
        detail = (
            "Nothing was updated on the last run. "
            + (f"The newest data is from {humanize_age(succeeded_at)}."
               if succeeded_at else "No source has ever returned successfully.")
            + " Open Source health to see exactly which source failed and why."
        )
    elif outcome == CollectionOutcome.PARTIAL.value:
        headline = f"{style.emoji} PARTIAL UPDATE - {ok} of {attempted} sources succeeded."
        detail = (f"{failed} source(s) failed, so the feed may be missing stories. "
                  "Source health lists each failure.")
    elif outcome == CollectionOutcome.NOT_RUN.value:
        headline = f"{style.emoji} NOT RUN - no enabled sources to collect from."
        detail = "Enable at least one source on the Source health page."
    else:
        headline = f"{style.emoji} Collection OK - all {attempted} sources returned."
        detail = f"Last updated {humanize_age(succeeded_at)}."

    return {
        "outcome": outcome,
        "emoji": style.emoji, "label": style.label, "color": style.color,
        "headline": headline, "detail": detail,
        "sources_ok": ok, "sources_attempted": attempted, "sources_failed": failed,
        "attempted_at": run.get("finished_at") or run.get("started_at"),
        "succeeded_at": succeeded_at,
        "error": run.get("error"),
        "is_stale": outcome == CollectionOutcome.TOTAL_FAILURE.value,
        "needs_attention": outcome in (CollectionOutcome.TOTAL_FAILURE.value,
                                       CollectionOutcome.PARTIAL.value),
    }


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
