"""The processing pipeline that turns collected items into the dashboard.

    articles in  ->  enrich  ->  store  ->  cluster into stories
                 ->  verify  ->  score support  ->  score relevance
                 ->  trending / developing / breaking flags
                 ->  fight-card changes

Every step is safe to re-run: the pipeline is idempotent per article and per
story, so a partial failure can simply be run again.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from database import repo_articles as articles_repo
from database import repo_entities as entities_repo
from database import repo_settings as settings_repo
from database import repo_social as social_repo
from database import repo_stories as stories_repo
from models.types import Category, StoryStatus
from processors import developing as developing_mod
from processors import fight_cards, relevance, support, trending, verification
from processors.clustering import StoryMatcher, assign_article
from processors.enrich import enrich_article, enrich_social_post
from processors.entities import find_events, find_fighters, get_fighter_index, reset_indexes
from processors.similarity import set_similarity
from utils.logging_setup import get_logger
from utils.textutil import jaccard, normalize_text, token_set
from utils.timeutil import age_hours, utcnow_iso

logger = get_logger(__name__)

# Categories that can qualify as BREAKING when they are fresh and relevant.
BREAKING_CATEGORIES = {
    Category.FIGHT_ANNOUNCEMENT.value, Category.CANCELLATION.value, Category.REPLACEMENT.value,
    Category.INJURY.value, Category.RETIREMENT.value, Category.SUSPENSION.value,
    Category.TITLE.value, Category.RESULT.value, Category.EVENT_CHANGE.value,
    Category.CONTROVERSY.value, Category.RANKING.value, Category.BUSINESS.value,
}


@dataclass
class PipelineStats:
    articles_seen: int = 0
    articles_new: int = 0
    articles_rejected: int = 0
    stories_new: int = 0
    stories_updated: int = 0
    social_linked: int = 0
    card_changes: int = 0
    events_reconciled: int = 0
    errors: List[str] = field(default_factory=list)

    def merge(self, other: "PipelineStats") -> "PipelineStats":
        self.articles_seen += other.articles_seen
        self.articles_new += other.articles_new
        self.articles_rejected += other.articles_rejected
        self.stories_new += other.stories_new
        self.stories_updated += other.stories_updated
        self.social_linked += other.social_linked
        self.events_reconciled += other.events_reconciled
        self.card_changes += other.card_changes
        self.errors.extend(other.errors)
        return self


# ------------------------------------------------------------- ingestion ----
def ingest_articles(items: List[Dict[str, Any]]) -> PipelineStats:
    """Enrich and store collected items. Duplicates (same URL) are skipped."""
    stats = PipelineStats(articles_seen=len(items))
    index = get_fighter_index()
    for item in items:
        try:
            enriched = enrich_article(item, index)
            article_id = articles_repo.insert_article(enriched)
            if article_id is None:
                stats.articles_rejected += 1
                continue
            stats.articles_new += 1
            _register_entities(enriched)
        except Exception as exc:  # one bad item must not stop ingestion
            logger.warning("Failed to ingest item %s: %s", item.get("url"), exc)
            stats.errors.append(f"ingest: {exc}")
    return stats


def _register_entities(article: Dict[str, Any]) -> None:
    when = article.get("published_at") or article.get("collected_at")
    for name in article.get("fighters") or []:
        entities_repo.upsert_fighter(name, data_origin="detected")
        entities_repo.record_fighter_mention(name, when)
    for event_name in article.get("events") or []:
        entities_repo.upsert_event(event_name, data_origin="detected")
        entities_repo.record_event_mention(event_name, when)


def ingest_social_posts(posts: List[Dict[str, Any]]) -> int:
    """Store X posts (already fetched by the social layer)."""
    stored = 0
    index = get_fighter_index()
    for post in posts:
        try:
            enriched = enrich_social_post(post, index)
            if social_repo.upsert_post(enriched) is not None:
                stored += 1
        except Exception as exc:
            logger.warning("Failed to store social post: %s", exc)
    return stored


# -------------------------------------------------------------- clustering --
def cluster_unassigned(limit: int = 500) -> PipelineStats:
    """Attach every article without a story to one (creating stories as needed)."""
    stats = PipelineStats()
    pending = articles_repo.unassigned_articles(limit=limit)
    if not pending:
        return stats
    matcher = StoryMatcher()
    for article in pending:
        try:
            decision = assign_article(article, matcher)
            if decision.created:
                stats.stories_new += 1
            else:
                stats.stories_updated += 1
        except Exception as exc:
            logger.warning("Clustering failed for article %s: %s", article.get("id"), exc)
            stats.errors.append(f"cluster: {exc}")
    return stats


# ------------------------------------------------------------- story rebuild -
def recompute_story(story_id: int, watchlist_terms: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """Re-derive status, support, relevance and flags for one story."""
    story = stories_repo.get_story(story_id)
    if story is None:
        return None
    articles = articles_repo.articles_for_story(story_id)
    posts = social_repo.posts_for_story(story_id)
    if watchlist_terms is None:
        watchlist_terms = _watchlist_terms()

    verdict = verification.evaluate_story(story, articles, posts)
    developing_mod.record_status_change(story, verdict.status, verdict.reasons)

    assessment = support.assess_support(verdict, articles, posts)
    trend = trending.evaluate_trend(story, articles, posts)
    story_for_scoring = {
        **story,
        **verdict.as_story_fields(),
        "is_trending": trend.is_trending,
    }
    score = relevance.score_story(story_for_scoring, articles, posts, watchlist_terms, verdict)

    fields: Dict[str, Any] = {}
    fields.update(verdict.as_story_fields())
    fields.update(assessment.as_story_fields())
    fields.update(trend.as_story_fields())
    fields.update(score.as_story_fields())
    fields["article_count"] = len(articles)
    # source_count / independent_source_count / social_post_count all come from
    # the verification pass so they are always computed from the same pool.
    # Recomputing any of them here is what previously produced a story with
    # more "independent" sources than it had sources at all.
    fields["social_post_count"] = len(posts)

    fields["category"] = _dominant_category(story, articles)
    settled = verdict.official_confirmed and not verdict.has_conflict
    fields["is_developing"] = False if settled else (
        verdict.is_developing
        or developing_mod.is_developing({**story, "has_conflict": verdict.has_conflict})
    )
    fields["is_breaking"] = _is_breaking(story, fields)
    fields["status_updated_at"] = utcnow_iso()
    if not story.get("summary") and articles:
        fields["summary"] = articles[0].get("excerpt") or ""
    event_id = _resolve_event(story)
    if event_id:
        fields["event_id"] = event_id

    stories_repo.update_story(story_id, **fields)
    return stories_repo.get_story(story_id)


def _dominant_category(story: Dict[str, Any], articles: List[Dict[str, Any]]) -> str:
    """The category most of the story's articles agree on."""
    counts: Dict[str, int] = {}
    for article in articles:
        category = article.get("category") or Category.GENERAL.value
        weight = 2 if article.get("is_official") else 1
        counts[category] = counts.get(category, 0) + weight
    if not counts:
        return story.get("category") or Category.GENERAL.value
    ranked = sorted(counts.items(), key=lambda pair: pair[1], reverse=True)
    top_category, top_count = ranked[0]
    if top_category in (Category.GENERAL.value, Category.PROMO.value) and len(ranked) > 1:
        # Prefer a concrete development over a generic label on ties.
        for category, count in ranked[1:]:
            if count == top_count:
                return category
    return top_category


def _is_breaking(story: Dict[str, Any], fields: Dict[str, Any]) -> bool:
    window = settings_repo.get_int("breaking_window_hours", 8)
    min_relevance = settings_repo.get_int("breaking_min_relevance", 70)
    age = age_hours(story.get("first_seen_at"))
    if age is None or age > window:
        return False
    if float(fields.get("relevance") or 0) < min_relevance:
        return False
    if fields.get("category") not in BREAKING_CATEGORIES:
        return False
    return fields.get("status") in (
        StoryStatus.CONFIRMED.value, StoryStatus.REPORTED.value, StoryStatus.DEVELOPING.value,
    )


def _resolve_event(story: Dict[str, Any]) -> Optional[int]:
    for event_name in story.get("events") or []:
        event = entities_repo.get_event_by_name(event_name)
        if event:
            return int(event["id"])
        return entities_repo.upsert_event(event_name, data_origin="detected")
    return None


def _watchlist_terms() -> List[str]:
    terms: List[str] = []
    for item in settings_repo.list_watchlist():
        if item.get("kind") in ("fighter", "event", "topic"):
            terms.append(item["value"])
    return terms


def recompute_stories(story_ids: Optional[List[int]] = None, limit: int = 400) -> int:
    ids = story_ids if story_ids is not None else [
        story["id"] for story in stories_repo.list_stories(limit=limit, sort="Recently Updated")
    ]
    watchlist_terms = _watchlist_terms()
    count = 0
    for story_id in ids:
        try:
            recompute_story(story_id, watchlist_terms)
            count += 1
        except Exception as exc:
            logger.warning("Failed to recompute story %s: %s", story_id, exc)
    return count


# --------------------------------------------------------- social linking ---
def link_social_posts(hours: int = 48, min_score: float = 0.34) -> int:
    """Attach recent X posts to the stories they are about.

    A post is a *signal*: linking it never confirms anything on its own, it
    just puts the post next to the reporting so you can judge it.
    """
    posts = social_repo.unlinked_posts(hours=hours, limit=200)
    if not posts:
        return 0
    candidates = stories_repo.candidate_stories(hours=max(hours, 72), limit=300)
    if not candidates:
        return 0
    linked = 0
    for post in posts:
        best_story, best_score, reasons = None, 0.0, []
        post_fighters = post.get("fighters") or []
        post_events = post.get("events") or []
        for story in candidates:
            fighter_overlap = set_similarity(post_fighters, story.get("fighters") or [])
            event_overlap = set_similarity(post_events, story.get("events") or [])
            post_tokens = token_set(post.get("text") or "")
            story_tokens = token_set(
                f"{story.get('headline') or ''} {' '.join(story.get('fighters') or [])}"
            )
            shared_tokens = len(post_tokens & story_tokens)
            # Jaccard (not overlap ratio): a short, vague post must not match
            # every long headline that happens to share a word or two.
            text_score = jaccard(post_tokens, story_tokens)
            has_entities = bool(
                post_fighters or post_events or story.get("fighters") or story.get("events")
            )
            if has_entities:
                score = 0.45 * fighter_overlap + 0.25 * event_overlap + 0.30 * text_score
            else:  # nothing to match on but the wording - lean on it
                score = 0.85 * text_score
            if post_fighters and (story.get("fighters") or []) and fighter_overlap == 0:
                continue
            if fighter_overlap == 0 and event_overlap == 0 and shared_tokens < 3:
                continue  # too little in common to attribute the post to a story
            if score > best_score:
                best_story, best_score = story, score
                reasons = [
                    f"fighter overlap {fighter_overlap:.2f}",
                    f"event overlap {event_overlap:.2f}",
                    f"text similarity {text_score:.2f} ({shared_tokens} shared terms)",
                ]
        if best_story and best_score >= min_score:
            social_repo.link_post_to_story(int(best_story["id"]), int(post["id"]), best_score, reasons)
            developing_mod.add_social_timeline_entry(int(best_story["id"]), post)
            linked += 1
    return linked


# ------------------------------------------------------------ card changes --
def detect_card_changes(story_ids: Optional[List[int]] = None, limit: int = 120) -> int:
    ids = story_ids if story_ids is not None else [
        story["id"] for story in stories_repo.list_stories(limit=limit, sort="Recently Updated")
    ]
    recorded = 0
    for story_id in ids:
        story = stories_repo.get_story(story_id)
        if story is None:
            continue
        if story.get("category") not in (
            Category.FIGHT_ANNOUNCEMENT.value, Category.CANCELLATION.value,
            Category.REPLACEMENT.value, Category.INJURY.value, Category.EVENT_CHANGE.value,
        ):
            continue
        articles = articles_repo.articles_for_story(story_id)
        try:
            changes = fight_cards.apply_changes(story, articles, story.get("event_id"))
        except Exception as exc:
            logger.warning("Card-change detection failed for story %s: %s", story_id, exc)
            continue
        for change_id in changes:
            recorded += 1
        if changes:
            developing_mod.add_note(
                story_id,
                f"{len(changes)} fight-card change(s) recorded from this story",
            )
    return recorded


# ------------------------------------------------------------------- run ----
def process_all(items: Optional[List[Dict[str, Any]]] = None) -> PipelineStats:
    """Full pass: ingest (optional) -> cluster -> rescore -> link -> cards."""
    reset_indexes()
    stats = PipelineStats()
    if items:
        stats.merge(ingest_articles(items))
    stats.merge(cluster_unassigned())
    stats.social_linked += link_social_posts()
    touched = [
        story["id"] for story in stories_repo.list_stories(limit=400, sort="Recently Updated")
    ]
    recompute_stories(touched)
    stats.card_changes += detect_card_changes(touched[:120])
    # Refresh every event's lifecycle status from the schedule + new evidence.
    # Never derived from the calendar alone - see processors/event_lifecycle.py.
    try:
        from processors.event_reconcile import reconcile_all_events

        stats.events_reconciled = reconcile_all_events().get("changed", 0)
    except Exception:  # pragma: no cover - a status pass must not lose a run
        logger.exception("Event reconciliation failed")
    # Reprocessing is not collecting. ``last_collection_at`` is written by
    # collectors/runner.py, and only when a source actually returned, so a
    # failed run can never make the dashboard claim fresh data.
    settings_repo.set_setting("last_processed_at", utcnow_iso(), "str")
    return stats

