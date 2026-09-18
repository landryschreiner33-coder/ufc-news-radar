"""Automated source-support assessment.

This is NOT a probability that a claim is true.  It is a transparent score of
*how much source support the collected material provides*, with the evidence
spelled out.  The UI must always label it as such.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from models.types import (
    CREDIBLE_REPORTING_TYPES,
    FIRST_PERSON_TYPES,
    LOW_RELIABILITY_TYPES,
    SourceType,
    normalize_source_type,
)
from processors.verification import VerificationResult

DISCLAIMER = (
    "Automated source-support assessment - how much the collected sources back this up. "
    "It is NOT a probability that the claim is true."
)

LABELS = [
    (85, "STRONG SOURCE SUPPORT"),
    (65, "GOOD SOURCE SUPPORT"),
    (45, "MODERATE SOURCE SUPPORT"),
    (25, "LIMITED SOURCE SUPPORT"),
    (0, "MINIMAL SOURCE SUPPORT"),
]


@dataclass
class SupportAssessment:
    score: float = 0.0
    label: str = "MINIMAL SOURCE SUPPORT"
    reasons: List[str] = field(default_factory=list)
    disclaimer: str = DISCLAIMER

    def as_story_fields(self) -> Dict[str, Any]:
        return {
            "support_score": round(self.score, 1),
            "support_label": self.label,
            "support_reasons": self.reasons,
        }


def assess_support(
    verification: VerificationResult,
    articles: List[Dict[str, Any]],
    social_posts: Optional[List[Dict[str, Any]]] = None,
) -> SupportAssessment:
    """Score 0-100 built from explicit, inspectable components."""
    social_posts = social_posts or []
    assessment = SupportAssessment()
    score = 0.0
    reasons: List[str] = []

    if verification.official_confirmed:
        score += 55
        reasons.append(
            f"+55 official source published it ({', '.join(verification.official_sources[:2])})"
        )

    independent = verification.independent_source_count
    if independent >= 3:
        score += 30
        reasons.append(f"+30 {independent} independent credible sources")
    elif independent == 2:
        score += 22
        reasons.append("+22 two independent credible sources")
    elif independent == 1:
        score += 12
        reasons.append("+12 one credible source, not yet corroborated")
    else:
        reasons.append("+0 no independent credible source yet")

    best_reliability = _best_reliability(articles, social_posts)
    reliability_points = round(best_reliability * 12, 1)
    if reliability_points:
        score += reliability_points
        reasons.append(f"+{reliability_points} strongest source reliability {best_reliability:.2f}")

    official_social = [
        post for post in social_posts
        if normalize_source_type(post.get("account_type")) == SourceType.OFFICIAL.value
    ]
    if official_social and not verification.official_confirmed:
        score += 10
        reasons.append(f"+10 official account posted about it (@{official_social[0].get('username')})")

    journalist_social = [
        post for post in social_posts
        if normalize_source_type(post.get("account_type")) in CREDIBLE_REPORTING_TYPES
    ]
    if journalist_social:
        score += 5
        reasons.append(f"+5 {len(journalist_social)} post(s) from credible reporting accounts")

    first_person = [
        post for post in social_posts
        if normalize_source_type(post.get("account_type")) in FIRST_PERSON_TYPES
    ]
    if first_person and independent == 0 and not verification.official_confirmed:
        reasons.append("+0 a fighter/coach claim on its own is not corroboration")

    if verification.derivative_count:
        penalty = min(12.0, verification.derivative_count * 4.0)
        score -= penalty
        reasons.append(
            f"-{penalty:.0f} {verification.derivative_count} report(s) only repeat another outlet"
        )

    if verification.speculation_score:
        penalty = round(verification.speculation_score * 15, 1)
        score -= penalty
        reasons.append(f"-{penalty} hedged wording ('reportedly', 'sources say', ...)")

    low_quality = [
        article for article in articles
        if normalize_source_type(article.get("source_type")) in LOW_RELIABILITY_TYPES
    ]
    if low_quality and independent == 0:
        reasons.append(
            f"+0 {len(low_quality)} low-reliability source(s) - repetition does not add support"
        )

    if verification.has_conflict:
        score -= 15
        reasons.append("-15 sources contradict each other (disagreement preserved below)")

    assessment.score = round(max(0.0, min(100.0, score)), 1)
    assessment.label = _label_for(assessment.score)
    if verification.has_conflict:
        assessment.label = "CONTESTED - SOURCES DISAGREE"
    assessment.reasons = reasons
    return assessment


def _best_reliability(articles: List[Dict[str, Any]], social_posts: List[Dict[str, Any]]) -> float:
    from models.types import reliability_for

    values = [float(article.get("reliability_weight") or 0.0) for article in articles]
    values += [reliability_for(normalize_source_type(post.get("account_type"))) for post in social_posts]
    return max(values) if values else 0.0


def _label_for(score: float) -> str:
    for threshold, label in LABELS:
        if score >= threshold:
            return label
    return LABELS[-1][1]
