"""Run every enabled source, then hand the results to the pipeline.

Failure isolation is the whole point of this module: each source runs inside
its own try/except, its health is recorded, and the run continues.  A run
that collects nothing still finishes cleanly and updates the Source Health
panel with the reason.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from collectors.base import CollectorResult
from collectors.registry import build_collector
from database import repo_articles as articles_repo
from database import repo_entities as entities_repo
from database import repo_runs as runs_repo
from database import repo_settings as settings_repo
from database import repo_sources as sources_repo
from models.types import CollectionOutcome, collection_outcome_for, collection_outcome_style
from processors import pipeline
from processors.rankings_diff import SnapshotResult, store_snapshot
from utils.http import HttpClient
from utils.logging_setup import get_logger
from utils.timeutil import utcnow_iso

logger = get_logger(__name__)


@dataclass
class SourceOutcome:
    key: str
    name: str
    ok: bool
    items: int = 0
    error: Optional[str] = None
    error_kind: Optional[str] = None
    duration_ms: int = 0


@dataclass
class RunResult:
    run_id: Optional[int] = None
    started_at: str = ""
    finished_at: str = ""
    duration_ms: int = 0
    sources_attempted: int = 0
    sources_ok: int = 0
    sources_failed: int = 0
    articles_seen: int = 0
    articles_new: int = 0
    stories_new: int = 0
    stories_updated: int = 0
    social_new: int = 0
    social_linked: int = 0
    card_changes: int = 0
    rankings: Optional[SnapshotResult] = None
    events_seen: int = 0
    official_bouts: int = 0
    outcomes: List[SourceOutcome] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def outcome(self) -> str:
        """SUCCESS / PARTIAL / TOTAL_FAILURE / NOT_RUN - never assumed."""
        return collection_outcome_for(self.sources_attempted, self.sources_ok)

    @property
    def collected_anything(self) -> bool:
        return self.sources_ok > 0

    @property
    def headline(self) -> str:
        """The one line the interface leads with. Never claims a false update."""
        style = collection_outcome_style(self.outcome)
        if self.outcome == CollectionOutcome.NOT_RUN.value:
            return f"{style.emoji} {style.label} - no sources were enabled to collect from."
        if self.outcome == CollectionOutcome.TOTAL_FAILURE.value:
            return (f"{style.emoji} {style.label} - 0 of {self.sources_attempted} sources "
                    "returned. Nothing was updated; what is on screen may be stale.")
        if self.outcome == CollectionOutcome.PARTIAL.value:
            return (f"{style.emoji} {style.label} - {self.sources_ok} of "
                    f"{self.sources_attempted} sources succeeded, {self.sources_failed} failed.")
        return (f"{style.emoji} {style.label} - all {self.sources_attempted} sources "
                "returned.")

    @property
    def summary_line(self) -> str:
        return (
            f"{self.sources_ok}/{self.sources_attempted} sources OK - "
            f"{self.articles_new} new articles, {self.stories_new} new stories, "
            f"{self.stories_updated} updated"
        )


def run_collection(
    trigger: str = "manual",
    source_keys: Optional[List[str]] = None,
    client: Optional[HttpClient] = None,
    process: bool = True,
    collect_social: bool = True,
    fetch_full_text: Optional[bool] = None,
) -> RunResult:
    """Collect from every enabled source and run the processing pipeline."""
    started = time.monotonic()
    result = RunResult(started_at=utcnow_iso())
    result.run_id = runs_repo.start_run(trigger)

    sources = sources_repo.list_sources(enabled_only=True)
    if source_keys:
        wanted = {key.lower() for key in source_keys}
        sources = [source for source in sources if source["key"].lower() in wanted]
    result.sources_attempted = len(sources)

    collected_articles: List[Dict[str, Any]] = []
    for source in sources:
        outcome, collector_result = _run_one_source(source, client)
        result.outcomes.append(outcome)
        if outcome.ok:
            result.sources_ok += 1
        else:
            result.sources_failed += 1
            if outcome.error:
                result.errors.append(f"{outcome.name}: {outcome.error}")
            continue
        if collector_result is None:
            continue
        if collector_result.kind == "articles":
            collected_articles.extend(collector_result.items)
        elif collector_result.kind == "rankings":
            result.rankings = _store_rankings(collector_result)
        elif collector_result.kind == "events":
            result.events_seen += _store_events(collector_result)

    result.articles_seen = len(collected_articles)

    # Official fight cards for the next few events. Bounded to a handful of
    # requests per run, and never fatal: a layout change shows up on Source
    # health rather than losing the whole collection.
    if settings_repo.get_bool("collect_official_cards", True):
        try:
            from collectors.ufc_event_card import collect_official_cards

            upcoming = entities_repo.list_events(limit=6, upcoming_only=True)
            outcome = collect_official_cards(
                [event for event in upcoming if event.get("ufc_url")], client=client)
            result.official_bouts = outcome["stored"]
            if outcome["errors"]:
                result.errors.extend(outcome["errors"][:3])
        except Exception as exc:
            logger.warning("Official card collection failed: %s", exc)

    if collect_social:
        try:
            result.social_new = _collect_social()
        except Exception as exc:  # the X layer must never break a news run
            logger.warning("Social collection failed: %s", exc)
            result.errors.append(f"X collection: {exc}")

    if process:
        stats = pipeline.ingest_articles(collected_articles)
        result.articles_new = stats.articles_new
        result.errors.extend(stats.errors)

        if fetch_full_text is None:
            fetch_full_text = settings_repo.get_bool("article_fetch_full_text", True)
        if fetch_full_text:
            try:
                enhanced = enhance_article_texts(client=client)
                if enhanced:
                    result.notes.append(f"fetched full text for {enhanced} article(s)")
            except Exception as exc:
                logger.warning("Full-text fetch failed: %s", exc)

        pipeline_stats = pipeline.process_all()
        result.stories_new = pipeline_stats.stories_new
        result.stories_updated = pipeline_stats.stories_updated
        result.social_linked = pipeline_stats.social_linked
        result.card_changes = pipeline_stats.card_changes
        result.errors.extend(pipeline_stats.errors)

    result.duration_ms = int((time.monotonic() - started) * 1000)
    result.finished_at = utcnow_iso()
    runs_repo.finish_run(
        result.run_id,
        sources_attempted=result.sources_attempted,
        sources_ok=result.sources_ok,
        sources_failed=result.sources_failed,
        articles_seen=result.articles_seen,
        articles_new=result.articles_new,
        stories_new=result.stories_new,
        stories_updated=result.stories_updated,
        social_new=result.social_new,
        duration_ms=result.duration_ms,
        outcome=result.outcome,
        notes="; ".join(result.notes) if result.notes else None,
        error="; ".join(result.errors[:5]) if result.errors else None,
    )
    # Two different facts, stored separately on purpose. "We tried" is not
    # "we got something": a run where every source failed must never make the
    # interface say the data was just updated.
    settings_repo.set_setting("last_collection_attempt_at", result.finished_at, "str")
    settings_repo.set_setting("last_collection_outcome", result.outcome, "str")
    if result.collected_anything:
        settings_repo.set_setting("last_collection_at", result.finished_at, "str")
    logger.info("Collection run finished: %s (%s)", result.summary_line, result.outcome)
    return result


def _run_one_source(source: Dict[str, Any], client: Optional[HttpClient]):
    sources_repo.record_attempt(int(source["id"]))
    collector = build_collector(source, client=client)
    if collector is None:
        message = f"unknown adapter '{source.get('adapter')}'"
        sources_repo.record_failure(int(source["id"]), message, "config")
        return SourceOutcome(source["key"], source["name"], False, error=message,
                             error_kind="config"), None
    collector_result: CollectorResult = collector.run()
    if not collector_result.ok:
        sources_repo.record_failure(
            int(source["id"]), collector_result.error or "unknown error",
            collector_result.error_kind or "unknown",
        )
        return SourceOutcome(
            source["key"], source["name"], False, error=collector_result.error,
            error_kind=collector_result.error_kind, duration_ms=collector_result.duration_ms,
        ), collector_result

    latest = None
    if collector_result.kind == "articles" and collector_result.items:
        stamps = [item.get("published_at") for item in collector_result.items if item.get("published_at")]
        latest = max(stamps) if stamps else None
    sources_repo.record_success(
        int(source["id"]), article_count=collector_result.count,
        resolved_url=collector_result.resolved_url, latest_article_at=latest,
    )
    return SourceOutcome(
        source["key"], source["name"], True, items=collector_result.count,
        duration_ms=collector_result.duration_ms,
    ), collector_result


def _store_rankings(collector_result: CollectorResult) -> SnapshotResult:
    return store_snapshot(collector_result.items)


def _store_events(collector_result: CollectorResult) -> int:
    stored = 0
    for event in collector_result.items:
        try:
            event_id = entities_repo.upsert_event(
                name=event.get("name"),
                event_date=event.get("event_date"),
                location=event.get("location"),
                city=event.get("city"),
                venue=event.get("venue"),
                ufc_url=event.get("ufc_url"),
                official_event_id=event.get("official_event_id"),
                scheduled_start_utc=event.get("scheduled_start_utc"),
                scheduled_end_utc=event.get("scheduled_end_utc"),
                local_timezone=event.get("local_timezone"),
                image_url=event.get("image_url"),
                official_source_url=event.get("official_source_url"),
                data_origin=event.get("data_origin") or "collected",
            )
            # Give the event a lifecycle status immediately, so a freshly
            # collected card is never left looking finished or unknown.
            if event_id:
                _refresh_event_status(event_id)
            stored += 1
        except Exception as exc:
            logger.debug("Could not store event %s: %s", event.get("name"), exc)
    return stored


def _refresh_event_status(event_id: int) -> None:
    """Recompute one event's lifecycle from its schedule (never the calendar)."""
    try:
        from processors.event_reconcile import reconcile_event

        event = entities_repo.get_event(event_id)
        if event:
            reconcile_event(event)
    except Exception as exc:  # pragma: no cover - status must not fail a run
        logger.debug("Could not refresh status for event %s: %s", event_id, exc)


def _collect_social() -> int:
    """Collect from X when it is configured; a no-op otherwise."""
    from social.x_monitor import collect_x_activity

    outcome = collect_x_activity()
    return outcome.stored


def enhance_article_texts(limit: Optional[int] = None, client: Optional[HttpClient] = None) -> int:
    """Fetch main-content extracts for a few recent, thin articles.

    Only a short extract is stored (see collectors/article_text.py) and the
    number of fetches per run is capped so this never hammers publishers.
    """
    from collectors.article_text import extract_article_text

    limit = limit or settings_repo.get_int("article_fetch_limit_per_run", 12)
    if limit <= 0:
        return 0
    candidates = [
        article for article in articles_repo.recent_articles(hours=24, limit=120)
        if int(article.get("content_chars") or 0) < 400
        and float(article.get("reliability_weight") or 0) >= 0.5
        and article.get("domain") not in ("news.google.com", "reddit.com")
        and not article.get("is_demo")
    ]
    enhanced = 0
    for article in candidates[:limit]:
        snippet, excerpt, error = extract_article_text(article["url"], client=client)
        if error or not snippet:
            continue
        articles_repo.update_article(
            int(article["id"]),
            content_snippet=snippet,
            content_chars=len(snippet),
            excerpt=article.get("excerpt") or excerpt,
        )
        enhanced += 1
    return enhanced
