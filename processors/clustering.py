"""Group articles into stories.

An ARTICLE is one piece published by one outlet.  A STORY is the underlying
development that several outlets (and social posts) are covering.

The matcher combines five signals - headline similarity, content similarity,
fighter overlap, event overlap and time proximity - behind three hard gates
that exist to prevent *false* grouping:

  1. the two items must be close in time (configurable window);
  2. if both name fighters, they must share at least one;
  3. two different kinds of development (an announcement vs. a cancellation)
     stay separate even when they involve the same fighters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from database import repo_settings as settings_repo
from database import repo_stories as stories_repo
from models.types import DISTINCT_DEVELOPMENT_CATEGORIES, Category
from processors.similarity import SimilarityIndex, set_similarity, title_similarity
from utils.logging_setup import get_logger
from utils.textutil import clean_title, normalize_text, slugify, truncate
from utils.timeutil import age_hours, parse_iso, utcnow_iso

logger = get_logger(__name__)

# Categories that describe commentary rather than a distinct development, so
# they may join a story of any category.
FLEXIBLE_CATEGORIES = {
    Category.GENERAL.value,
    Category.FIGHTER_STATEMENT.value,
    Category.INTERVIEW.value,
    Category.PROMO.value,
    Category.CONTROVERSY.value,
    Category.RUMOR.value,
}


@dataclass
class MatchDecision:
    story_id: Optional[int] = None
    created: bool = False
    score: float = 0.0
    reasons: List[str] = field(default_factory=list)
    rejections: List[str] = field(default_factory=list)


@dataclass
class ClusterConfig:
    window_hours: int = 72
    threshold: float = 0.42
    title_threshold: float = 0.62
    min_text_similarity: float = 0.3

    @classmethod
    def from_settings(cls) -> "ClusterConfig":
        return cls(
            window_hours=settings_repo.get_int("story_match_window_hours", 72),
            threshold=settings_repo.get_float("story_similarity_threshold", 0.42),
            title_threshold=settings_repo.get_float("story_title_similarity_threshold", 0.62),
        )


def story_document(story: Dict[str, Any]) -> str:
    parts = [story.get("headline") or "", story.get("summary") or ""]
    parts.extend(story.get("fighters") or [])
    parts.extend(story.get("events") or [])
    return " ".join(str(part) for part in parts if part)


def article_document(article: Dict[str, Any]) -> str:
    parts = [article.get("title") or "", article.get("excerpt") or ""]
    parts.extend(article.get("fighters") or [])
    parts.extend(article.get("events") or [])
    return " ".join(str(part) for part in parts if part)


class StoryMatcher:
    """Matches articles to recent stories. Build once per pipeline run."""

    def __init__(self, candidates: Optional[List[Dict[str, Any]]] = None,
                 config: Optional[ClusterConfig] = None) -> None:
        self.config = config or ClusterConfig.from_settings()
        self.candidates: List[Dict[str, Any]] = candidates if candidates is not None else \
            stories_repo.candidate_stories(hours=self.config.window_hours * 2)
        self._rebuild_index()

    def _rebuild_index(self) -> None:
        self.index = SimilarityIndex([story_document(story) for story in self.candidates])

    def add_story(self, story: Dict[str, Any]) -> None:
        """Make a newly created story available to later articles in the run."""
        self.candidates.insert(0, story)
        self._rebuild_index()

    def refresh_story(self, story: Dict[str, Any]) -> None:
        for position, candidate in enumerate(self.candidates):
            if candidate.get("id") == story.get("id"):
                self.candidates[position] = story
                self._rebuild_index()
                return
        self.add_story(story)

    # ------------------------------------------------------------- matching --
    def score_candidates(self, article: Dict[str, Any]) -> List[Tuple[Dict[str, Any], float, List[str]]]:
        if not self.candidates:
            return []
        content_scores = self.index.query(article_document(article))
        scored: List[Tuple[Dict[str, Any], float, List[str]]] = []
        for position, story in enumerate(self.candidates):
            content_similarity = content_scores[position] if position < len(content_scores) else 0.0
            accepted, score, reasons = self._score_pair(article, story, content_similarity)
            if accepted:
                scored.append((story, score, reasons))
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored

    def _score_pair(
        self, article: Dict[str, Any], story: Dict[str, Any], content_similarity: float
    ) -> Tuple[bool, float, List[str]]:
        reasons: List[str] = []

        gap = _hours_between(article, story)
        if gap is None or gap > self.config.window_hours:
            return False, 0.0, [f"outside the {self.config.window_hours}h matching window"]

        article_fighters = [name for name in (article.get("fighters") or []) if name]
        story_fighters = [name for name in (story.get("fighters") or []) if name]
        fighter_similarity = set_similarity(article_fighters, story_fighters)
        if article_fighters and story_fighters and fighter_similarity == 0.0:
            return False, 0.0, ["no fighter in common"]

        article_category = article.get("category") or Category.GENERAL.value
        story_category = story.get("category") or Category.GENERAL.value
        event_overlap_early = set_similarity(article.get("events") or [], story.get("events") or [])
        # A denial belongs *with* the report it disputes, so the "different
        # development" gate is skipped when an article disputes something this
        # story already says, about the same people, within a day.
        is_response = (
            bool(article.get("has_denial"))
            and gap <= 24
            and (fighter_similarity > 0 or event_overlap_early > 0)
        )
        if (
            not is_response
            and article_category != story_category
            and article_category in DISTINCT_DEVELOPMENT_CATEGORIES
            and story_category in DISTINCT_DEVELOPMENT_CATEGORIES
        ):
            return False, 0.0, [
                f"different development ({article_category} vs {story_category})"
            ]
        if is_response:
            reasons.append("disputes/denies what this story reports")

        headline_similarity = title_similarity(article.get("title") or "", story.get("headline") or "")
        event_similarity = event_overlap_early
        recency = max(0.0, 1.0 - (gap / max(1, self.config.window_hours)))

        has_entities = bool(article_fighters or story_fighters or article.get("events") or story.get("events"))
        entity_similarity = max(fighter_similarity, event_similarity)
        if has_entities:
            score = (
                0.34 * content_similarity
                + 0.28 * headline_similarity
                + 0.24 * entity_similarity
                + 0.14 * recency
            )
        else:  # no entities to compare - lean on the text
            score = 0.48 * content_similarity + 0.38 * headline_similarity + 0.14 * recency

        strong_text = max(content_similarity, headline_similarity) >= self.config.min_text_similarity
        title_match = headline_similarity >= self.config.title_threshold
        accepted = (score >= self.config.threshold and strong_text) or (
            title_match and (entity_similarity > 0 or not has_entities)
        ) or (is_response and entity_similarity >= 0.5 and max(content_similarity, headline_similarity) >= 0.2)
        if not accepted:
            return False, score, [f"similarity {score:.2f} below threshold {self.config.threshold:.2f}"]

        if fighter_similarity:
            shared = sorted(
                {normalize_text(n) for n in article_fighters} & {normalize_text(n) for n in story_fighters}
            )
            reasons.append(f"shares {len(shared)} fighter(s) with this story")
        if event_similarity:
            reasons.append("same event")
        reasons.append(f"headline similarity {headline_similarity:.2f}")
        reasons.append(f"content similarity {content_similarity:.2f}")
        reasons.append(f"published {gap:.1f}h apart")
        return True, score, reasons


# ------------------------------------------------------------- assignment ---
def assign_article(
    article: Dict[str, Any], matcher: Optional[StoryMatcher] = None
) -> MatchDecision:
    """Attach one article to the best matching story, or start a new one."""
    matcher = matcher or StoryMatcher()
    decision = MatchDecision()
    scored = matcher.score_candidates(article)
    if scored:
        story, score, reasons = scored[0]
        decision.story_id = int(story["id"])
        decision.score = score
        decision.reasons = reasons
        role = "duplicate" if _looks_like_duplicate(article, story, score) else "corroboration"
        stories_repo.attach_article(story["id"], article["id"], role=role,
                                    similarity=score, match_reasons=reasons)
        updated = _merge_article_into_story(story, article)
        matcher.refresh_story(updated)
        _add_article_timeline_entry(story["id"], article)
        return decision

    story_id = _create_story_from_article(article)
    decision.story_id = story_id
    decision.created = True
    decision.reasons = ["first report of this development"]
    stories_repo.attach_article(story_id, article["id"], role="origin", similarity=1.0,
                                match_reasons=decision.reasons)
    new_story = stories_repo.get_story(story_id)
    if new_story:
        matcher.add_story(new_story)
    _add_article_timeline_entry(story_id, article)
    return decision


def _looks_like_duplicate(article: Dict[str, Any], story: Dict[str, Any], score: float) -> bool:
    """A near-identical headline from the same publisher family is a duplicate."""
    same_family = (
        article.get("independence_group")
        and article.get("independence_group") == story.get("_origin_group")
    )
    return score > 0.92 or bool(same_family and score > 0.8)


def _create_story_from_article(article: Dict[str, Any]) -> int:
    headline = clean_title(article.get("title") or "Untitled")
    published = article.get("published_at") or article.get("collected_at") or utcnow_iso()
    return stories_repo.create_story({
        "slug": slugify(headline),
        "headline": headline,
        "summary": truncate(article.get("excerpt") or "", 400),
        "category": article.get("category") or Category.GENERAL.value,
        "fighters": article.get("fighters") or [],
        "events": article.get("events") or [],
        "keywords": article.get("keywords") or [],
        "first_seen_at": published,
        "last_updated_at": published,
        "primary_article_id": article.get("id"),
        "image_url": article.get("image_url"),
        "article_count": 1,
        "source_count": 1,
        "is_demo": bool(article.get("is_demo")),
    })


def _merge_article_into_story(story: Dict[str, Any], article: Dict[str, Any]) -> Dict[str, Any]:
    """Fold a newly attached article's facts into the story row."""
    published = article.get("published_at") or article.get("collected_at") or utcnow_iso()
    updates: Dict[str, Any] = {}

    fighters = _merge_lists(story.get("fighters"), article.get("fighters"))
    if fighters != (story.get("fighters") or []):
        updates["fighters"] = fighters
    events = _merge_lists(story.get("events"), article.get("events"))
    if events != (story.get("events") or []):
        updates["events"] = events
    keywords = _merge_lists(story.get("keywords"), article.get("keywords"), limit=20)
    if keywords != (story.get("keywords") or []):
        updates["keywords"] = keywords

    if parse_iso(published) and parse_iso(story.get("last_updated_at")):
        if parse_iso(published) > parse_iso(story.get("last_updated_at")):  # type: ignore[operator]
            updates["last_updated_at"] = published
    if parse_iso(published) and parse_iso(story.get("first_seen_at")):
        if parse_iso(published) < parse_iso(story.get("first_seen_at")):  # type: ignore[operator]
            updates["first_seen_at"] = published

    # Prefer an official headline/summary once one arrives.
    if article.get("is_official") and not story.get("official_confirmed"):
        updates["headline"] = clean_title(article.get("title") or story.get("headline"))
        if article.get("excerpt"):
            updates["summary"] = truncate(article["excerpt"], 400)
    if not story.get("image_url") and article.get("image_url"):
        updates["image_url"] = article["image_url"]

    updates["update_count"] = int(story.get("update_count") or 0) + 1
    stories_repo.update_story(story["id"], **updates)
    return stories_repo.get_story(story["id"]) or story


def _add_article_timeline_entry(story_id: int, article: Dict[str, Any]) -> None:
    stories_repo.add_timeline_entry(
        story_id=story_id,
        occurred_at=article.get("published_at") or article.get("collected_at") or utcnow_iso(),
        kind="article",
        headline=article.get("title"),
        detail=truncate(article.get("excerpt") or "", 220),
        source_name=article.get("source_name"),
        source_type=article.get("source_type"),
        url=article.get("url"),
        article_id=article.get("id"),
    )


def _merge_lists(existing: Any, incoming: Any, limit: int = 12) -> List[str]:
    output: List[str] = []
    seen = set()
    for value in list(existing or []) + list(incoming or []):
        key = normalize_text(str(value))
        if not key or key in seen:
            continue
        seen.add(key)
        output.append(str(value))
        if len(output) >= limit:
            break
    return output


def _hours_between(article: Dict[str, Any], story: Dict[str, Any]) -> Optional[float]:
    published = parse_iso(article.get("published_at") or article.get("collected_at"))
    if published is None:
        return None
    gaps = []
    for field_name in ("last_updated_at", "first_seen_at"):
        value = parse_iso(story.get(field_name))
        if value is not None:
            gaps.append(abs((published - value).total_seconds()) / 3600.0)
    return min(gaps) if gaps else None
