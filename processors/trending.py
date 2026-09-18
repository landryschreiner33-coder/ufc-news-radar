"""Trending detection based on measurable activity, not vibes.

A story is only marked as trending when the collected data shows it: several
independent outlets inside a short window, a burst of updates, official
activity, or social engagement that is actually recorded from the X API.  The
evidence is stored with the flag so the dashboard can show why.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from database import repo_settings as settings_repo
from models.types import CREDIBLE_REPORTING_TYPES, SourceType, normalize_source_type
from utils.timeutil import age_hours

TREND_THRESHOLD = 10.0


@dataclass
class TrendResult:
    score: float = 0.0
    is_trending: bool = False
    reasons: List[str] = field(default_factory=list)

    def as_story_fields(self) -> Dict[str, Any]:
        return {
            "is_trending": self.is_trending,
            "trending_score": round(self.score, 1),
            "trending_reasons": self.reasons,
        }


def evaluate_trend(
    story: Dict[str, Any],
    articles: List[Dict[str, Any]],
    social_posts: Optional[List[Dict[str, Any]]] = None,
    window_hours: Optional[int] = None,
) -> TrendResult:
    social_posts = social_posts or []
    window = window_hours or settings_repo.get_int("trending_window_hours", 12)
    result = TrendResult()
    score = 0.0

    recent_articles = [a for a in articles if _within(a.get("published_at") or a.get("collected_at"), window)]
    recent_posts = [p for p in social_posts
                    if _within(p.get("created_at_source") or p.get("collected_at"), window)]

    groups = {
        (a.get("independence_group") or a.get("domain") or a.get("source_name"))
        for a in recent_articles if not a.get("is_derivative")
    }
    groups.discard(None)
    if len(groups) >= 3:
        score += 6
        result.reasons.append(f"{len(groups)} independent outlets published inside {window}h")
    elif len(groups) == 2:
        score += 3
        result.reasons.append(f"2 independent outlets published inside {window}h")

    if len(recent_articles) >= 4:
        score += 4
        result.reasons.append(f"{len(recent_articles)} articles collected inside {window}h")

    official_recent = [
        a for a in recent_articles
        if normalize_source_type(a.get("source_type")) == SourceType.OFFICIAL.value
    ]
    if official_recent:
        score += 3
        result.reasons.append("official UFC activity inside the window")

    if recent_posts:
        score += min(4.0, len(recent_posts))
        result.reasons.append(f"{len(recent_posts)} monitored X post(s) inside {window}h")
        press_posts = [
            p for p in recent_posts
            if normalize_source_type(p.get("account_type")) in CREDIBLE_REPORTING_TYPES
            or normalize_source_type(p.get("account_type")) == SourceType.OFFICIAL.value
        ]
        if press_posts:
            score += 2
            result.reasons.append(f"{len(press_posts)} of those are official/press accounts")
        engagement = _engagement(recent_posts)
        if engagement["measured_posts"]:
            if engagement["total"] >= 50000:
                score += 4
                result.reasons.append(
                    f"measured X engagement: {engagement['total']:,} likes+reposts across "
                    f"{engagement['measured_posts']} post(s)"
                )
            elif engagement["total"] >= 5000:
                score += 2
                result.reasons.append(
                    f"measured X engagement: {engagement['total']:,} likes+reposts"
                )

    update_count = int(story.get("update_count") or 0)
    if update_count >= 4:
        score += 2
        result.reasons.append(f"{update_count} updates attached to this story")

    result.score = score
    result.is_trending = score >= TREND_THRESHOLD
    if not result.is_trending and result.reasons:
        result.reasons.append(
            f"Activity score {score:.0f} is below the trending threshold ({TREND_THRESHOLD:.0f})."
        )
    if not result.reasons:
        result.reasons.append("No unusual activity measured for this story.")
    return result


def _within(timestamp: Any, hours: int) -> bool:
    age = age_hours(timestamp)
    return age is not None and age <= hours


def _engagement(posts: List[Dict[str, Any]]) -> Dict[str, int]:
    """Only counts posts where the API actually returned metrics."""
    total = 0
    measured = 0
    for post in posts:
        likes = post.get("like_count")
        reposts = post.get("repost_count")
        if likes is None and reposts is None:
            continue
        measured += 1
        total += int(likes or 0) + int(reposts or 0)
    return {"total": total, "measured_posts": measured}
