"""Reconcile what the sources say about an event with its official schedule.

The rule: **weaker evidence never silently overwrites stronger evidence.**

When UFC.com still lists an event as scheduled and a journalist reports a
cancellation, the app shows both:

    STATUS:   SCHEDULED
    CONFLICT: Cancellation has been reported but is not officially confirmed.

Nothing here decides who is right.  It gathers evidence, applies the lifecycle
rules in ``event_lifecycle`` (which only trusts reliable sources for
completion, and never the calendar), and preserves every disagreement.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from database import repo_articles as articles_repo
from database import repo_entities as entities_repo
from models.types import Category, SourceType
from processors.event_lifecycle import (
    EVIDENCE_CANCELLED,
    EVIDENCE_COMPLETED,
    EVIDENCE_LIVE,
    EVIDENCE_POSTPONED,
    EventEvidence,
    LifecycleResult,
    resolve_event_status,
)
from processors.result_safety import may_establish_result
from utils.logging_setup import get_logger
from utils.textutil import normalize_text

logger = get_logger(__name__)

_POSTPONE_TERMS = ("postponed", "pushed back", "rescheduled", "moved to", "delayed")
_CANCEL_TERMS = ("cancelled", "canceled", "called off", "scrapped", "axed", "off the card")
_LIVE_TERMS = ("live results", "live blog", "round by round", "live updates")


def _mentions_event(article: Dict[str, Any], event: Dict[str, Any]) -> bool:
    names = {normalize_text(name) for name in (article.get("events") or []) if name}
    return normalize_text(event.get("name")) in names or any(
        normalize_text(event.get("short_name") or "") == name for name in names if name
    )


def evidence_from_articles(event: Dict[str, Any],
                           articles: List[Dict[str, Any]]) -> List[EventEvidence]:
    """Turn collected articles into typed evidence about this event."""
    evidence: List[EventEvidence] = []
    for article in articles:
        if not _mentions_event(article, event):
            continue
        text = normalize_text(
            f"{article.get('title') or ''} {article.get('excerpt') or ''}")
        source_type = str(article.get("source_type") or SourceType.UNKNOWN.value).upper()
        official = bool(article.get("is_official"))
        common = {
            "source_type": source_type,
            "source_name": article.get("source_name"),
            "source_url": article.get("url"),
            "reported_at": article.get("published_at") or article.get("collected_at"),
            "official": official,
        }

        # A denial ("UFC denies the event is off") is not evidence *for* the claim.
        if article.get("has_denial"):
            continue

        category = str(article.get("category") or "")
        if any(term in text for term in _CANCEL_TERMS) and category in (
                Category.CANCELLATION.value, Category.EVENT_CHANGE.value, Category.GENERAL.value):
            evidence.append(EventEvidence(EVIDENCE_CANCELLED, detail=article.get("title"), **common))
        elif any(term in text for term in _POSTPONE_TERMS):
            evidence.append(EventEvidence(EVIDENCE_POSTPONED, detail=article.get("title"), **common))

        # Completion is gated twice: by intent (never a preview/prediction) and
        # by source reliability.
        if may_establish_result(article.get("intent"), source_type):
            evidence.append(EventEvidence(EVIDENCE_COMPLETED, detail=article.get("title"), **common))
        elif any(term in text for term in _LIVE_TERMS):
            evidence.append(EventEvidence(EVIDENCE_LIVE, detail=article.get("title"), **common))
    return evidence


def reconcile_event(event: Dict[str, Any],
                    articles: Optional[List[Dict[str, Any]]] = None,
                    now: Optional[Any] = None) -> LifecycleResult:
    """Resolve one event's lifecycle status and persist it."""
    if articles is None:
        articles = articles_repo.recent_articles(hours=24 * 60, limit=800)
    evidence = evidence_from_articles(event, articles)
    result = resolve_event_status(event, evidence, now=now)

    # An official schedule that still lists the event outranks reporting that
    # says otherwise: the disagreement is surfaced, not applied.
    if (event.get("data_origin") in ("official", "collected")
            and event.get("official_source_url")
            and result.status in ("CANCELLED", "POSTPONED")
            and not any(item.official for item in evidence
                        if item.kind in (EVIDENCE_CANCELLED, EVIDENCE_POSTPONED))):
        scheduled = resolve_event_status(event, [], now=now)
        scheduled.conflicts = list(result.conflicts) + [
            f"{result.status.title()} has been reported by {result.source}, but the official "
            "UFC schedule still lists this event. Not applied."
        ]
        scheduled.reasons = list(scheduled.reasons) + [
            "Official schedule data outranks unofficial reporting."
        ]
        result = scheduled

    entities_repo.set_event_lifecycle(int(event["id"]), result)
    return result


def reconcile_all_events(now: Optional[Any] = None) -> Dict[str, int]:
    """Refresh every event's lifecycle status. Cheap enough to run per collection."""
    events = entities_repo.all_events()
    if not events:
        return {"events": 0, "changed": 0}
    articles = articles_repo.recent_articles(hours=24 * 60, limit=800)
    changed = 0
    for event in events:
        before = event.get("event_status")
        result = reconcile_event(event, articles, now=now)
        if result.status != before:
            changed += 1
            logger.info("Event %s (%s): %s -> %s", event["id"], event.get("name"),
                        before, result.status)
    return {"events": len(events), "changed": changed}
