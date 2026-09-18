"""Relevance scoring - a feed-organisation tool, not a claim about importance.

The score is a transparent sum of components; the UI shows the breakdown so
you can see exactly why a story is near the top.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from models.types import CREDIBLE_REPORTING_TYPES, Category, SourceType, normalize_source_type
from utils.textutil import normalize_text
from utils.timeutil import age_hours

DISCLAIMER = (
    "Relevance is an automated feed-ranking score used to order the dashboard. "
    "It is not a measure of truth or of objective importance."
)

# Points by development type.
CATEGORY_POINTS: Dict[str, int] = {
    Category.CANCELLATION.value: 30,
    Category.REPLACEMENT.value: 28,
    Category.INJURY.value: 26,
    Category.FIGHT_ANNOUNCEMENT.value: 25,
    Category.RETIREMENT.value: 24,
    Category.SUSPENSION.value: 22,
    Category.TITLE.value: 22,
    Category.RANKING.value: 16,
    Category.RESULT.value: 16,
    Category.EVENT_CHANGE.value: 18,
    Category.BUSINESS.value: 14,
    Category.CONTROVERSY.value: 16,
    Category.FIGHTER_STATEMENT.value: 8,
    Category.INTERVIEW.value: 4,
    Category.PROMO.value: 1,
    Category.GENERAL.value: 6,
    Category.RUMOR.value: 10,
}

TITLE_TERMS = ("title", "champion", "championship", "belt", "undisputed", "interim")
MAIN_EVENT_TERMS = ("main event", "headliner", "co-main", "main card")

# Fighters whose news reliably matters to a UFC audience. Editable: this list
# only nudges feed order, it never changes a story's status.
MAJOR_FIGHTERS = {
    "jon jones", "conor mcgregor", "islam makhachev", "alex pereira", "tom aspinall",
    "ilia topuria", "khamzat chimaev", "sean o'malley", "dricus du plessis",
    "israel adesanya", "alexander volkanovski", "max holloway", "charles oliveira",
    "justin gaethje", "dustin poirier", "merab dvalishvili", "belal muhammad",
    "leon edwards", "kamaru usman", "zhang weili", "valentina shevchenko",
    "amanda nunes", "kayla harrison", "alexandre pantoja", "magomed ankalaev",
    "jack della maddalena", "shavkat rakhmonov", "paddy pimblett", "arman tsarukyan",
    "michael chandler", "sean strickland", "robert whittaker", "dana white",
}


@dataclass
class RelevanceResult:
    score: float = 0.0
    breakdown: Dict[str, float] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def as_story_fields(self) -> Dict[str, Any]:
        return {"relevance": round(self.score, 1), "relevance_breakdown": self.breakdown}


def score_story(
    story: Dict[str, Any],
    articles: Optional[List[Dict[str, Any]]] = None,
    social_posts: Optional[List[Dict[str, Any]]] = None,
    watchlist_terms: Optional[List[str]] = None,
    verification: Optional[Any] = None,
) -> RelevanceResult:
    articles = articles or []
    social_posts = social_posts or []
    watchlist_terms = watchlist_terms or []
    result = RelevanceResult()
    breakdown: Dict[str, float] = {}

    category = story.get("category") or Category.GENERAL.value
    breakdown["development type"] = float(CATEGORY_POINTS.get(category, 5))

    haystack = normalize_text(
        f"{story.get('headline','')} {story.get('summary','')} {' '.join(story.get('keywords') or [])}"
    )
    if any(term in haystack for term in TITLE_TERMS):
        breakdown["championship/title"] = 14.0
    if any(term in haystack for term in MAIN_EVENT_TERMS):
        breakdown["main/co-main event"] = 8.0

    fighters = [normalize_text(name) for name in (story.get("fighters") or [])]
    major_hits = [name for name in fighters if name in MAJOR_FIGHTERS]
    if major_hits:
        breakdown["major fighter"] = float(min(14, 7 * len(major_hits)))

    independent = int(story.get("independent_source_count") or 0)
    if independent:
        breakdown["independent sources"] = float(min(16, independent * 6))
    credible_articles = [
        article for article in articles
        if normalize_source_type(article.get("source_type")) in CREDIBLE_REPORTING_TYPES
    ]
    if credible_articles:
        breakdown["source quality"] = float(min(8, 2 * len(credible_articles)))
    if story.get("official_confirmed"):
        breakdown["official confirmation"] = 8.0

    if social_posts:
        official_or_press = [
            post for post in social_posts
            if normalize_source_type(post.get("account_type")) in CREDIBLE_REPORTING_TYPES
            or normalize_source_type(post.get("account_type")) == SourceType.OFFICIAL.value
        ]
        breakdown["social activity"] = float(min(10, 2 * len(social_posts) + 2 * len(official_or_press)))

    update_count = int(story.get("update_count") or 0)
    if update_count >= 2:
        breakdown["story is moving"] = float(min(10, update_count * 2))
    if story.get("is_trending"):
        breakdown["trending"] = 6.0

    hours = age_hours(story.get("last_updated_at"))
    if hours is not None:
        if hours <= 3:
            breakdown["fresh (<3h)"] = 12.0
        elif hours <= 12:
            breakdown["recent (<12h)"] = 8.0
        elif hours <= 36:
            breakdown["recent (<36h)"] = 4.0
        elif hours > 96:
            breakdown["older than 4 days"] = -6.0

    watch_hits = [
        term for term in watchlist_terms
        if term and (normalize_text(term) in haystack or normalize_text(term) in " ".join(fighters))
    ]
    if watch_hits:
        breakdown["on your watchlist"] = float(min(18, 9 * len(watch_hits)))
        result.notes.append("Matches your watchlist: " + ", ".join(watch_hits[:3]))

    if category in (Category.PROMO.value, Category.INTERVIEW.value):
        breakdown["low-information content"] = -8.0
    if verification is not None and getattr(verification, "speculation_score", 0) >= 0.67:
        breakdown["heavily hedged"] = -4.0

    total = sum(breakdown.values())
    result.breakdown = {key: round(value, 1) for key, value in breakdown.items()}
    result.score = max(0.0, min(100.0, total))
    return result


def relevance_band(score: float) -> str:
    if score >= 70:
        return "HIGH"
    if score >= 55:
        return "ELEVATED"
    if score >= 35:
        return "MEDIUM"
    return "LOW"
