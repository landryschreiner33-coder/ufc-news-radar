"""Decide a story's verification status from its sources - and explain why.

Principles baked into this module:

* Copies are not confirmation.  An article that credits another outlet
  ("according to ESPN") never counts as an independent source, and two
  outlets from the same publisher family count once.
* Volume is not confirmation.  Ten fan sites repeating a rumour leave the
  status where it was.
* A fighter's own claim is labelled as a claim, not as a report.
* Every status carries reasons the dashboard can show.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

from models.types import (
    CREDIBLE_REPORTING_TYPES,
    FIRST_PERSON_TYPES,
    LOW_RELIABILITY_TYPES,
    Category,
    SourceType,
    StoryStatus,
    normalize_source_type,
)
from processors.categorize import DENIAL_TERMS, classify_text
from utils.textutil import normalize_text
from utils.timeutil import age_hours, parse_iso

# A story is "moving" when this many updates land inside the window.
DEVELOPING_MIN_UPDATES = 3
DEVELOPING_WINDOW_HOURS = 12


@dataclass
class SourceEvidence:
    """One independent voice behind a story.

    ``kind`` is the distinction the whole counting model rests on: a *news
    source* is a publication that ran a report; a *social signal* is an X post.
    A post is worth showing next to the reporting, but it is not a news outlet
    and must never be counted as one - see ``VerificationResult``.
    """

    group: str
    name: str
    source_type: str
    reliability: float
    derivative: bool = False
    credits: List[str] = field(default_factory=list)
    kind: str = "news"          # news | social


@dataclass
class VerificationResult:
    status: str = StoryStatus.UNVERIFIED.value
    reasons: List[str] = field(default_factory=list)
    official_confirmed: bool = False
    official_sources: List[str] = field(default_factory=list)
    #: Distinct NEWS outlets behind the story (publisher families count once).
    #: X posts are never included here - they are social signals, not outlets.
    source_count: int = 0
    #: Those news outlets that report independently (not crediting another
    #: outlet).  By construction this can never exceed ``source_count``.
    independent_source_count: int = 0
    independent_groups: List[str] = field(default_factory=list)
    #: Linked X posts, counted and displayed separately from news sources.
    social_signal_count: int = 0
    #: Distinct X accounts of a kind normally trusted to report accurately.
    independent_social_accounts: int = 0
    #: Official confirmation that came from an official social account rather
    #: than from a publication, so the interface can say which it was.
    official_social_accounts: List[str] = field(default_factory=list)
    derivative_count: int = 0
    has_conflict: bool = False
    conflict_notes: List[str] = field(default_factory=list)
    #: Reliable sources on both sides - the CONTESTED trigger.
    credible_disagreement: bool = False
    is_developing: bool = False
    speculation_score: float = 0.0
    evidence: List[SourceEvidence] = field(default_factory=list)

    @property
    def news_source_count(self) -> int:
        """Readable alias - the stored column is ``source_count``."""
        return self.source_count

    @property
    def independent_news_source_count(self) -> int:
        return self.independent_source_count

    @property
    def corroboration_count(self) -> int:
        """How many independent voices back the claim, news plus social.

        Used only to decide a status.  It is deliberately NOT displayed as a
        source count, because an X post is not a news source: mixing the two
        is exactly what produced "2 sources, 3 independent" on screen.
        """
        return self.independent_source_count + self.independent_social_accounts

    def as_story_fields(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "status_reasons": self.reasons,
            "official_confirmed": self.official_confirmed,
            "source_count": self.source_count,
            "independent_source_count": self.independent_source_count,
            "social_post_count": self.social_signal_count,
            "has_conflict": self.has_conflict,
            "conflict_notes": self.conflict_notes,
            "is_developing": self.is_developing,
        }


def evaluate_story(
    story: Dict[str, Any],
    articles: List[Dict[str, Any]],
    social_posts: Optional[List[Dict[str, Any]]] = None,
) -> VerificationResult:
    """Work out the status of one story from the evidence collected for it."""
    social_posts = social_posts or []
    result = VerificationResult()
    if not articles and not social_posts:
        result.reasons.append("No sources collected for this story yet.")
        return result

    evidence, official_names, official_accounts, derivative_count = _build_evidence(
        articles, social_posts)
    result.evidence = evidence
    result.derivative_count = derivative_count
    result.official_sources = official_names
    result.official_social_accounts = official_accounts
    result.official_confirmed = bool(official_names)

    # News and social are counted separately and never added together. An X
    # post can corroborate a claim, but it is not a publication, so it can
    # never turn "two outlets reported this" into "three sources".
    news = [item for item in evidence if item.kind == "news"]
    social = [item for item in evidence if item.kind == "social"]
    result.source_count = len({item.group for item in news})
    result.social_signal_count = len(social_posts)

    credible_news = [item for item in news
                     if item.source_type in CREDIBLE_REPORTING_TYPES and not item.derivative]
    independent_groups = sorted({item.group for item in credible_news})
    result.independent_groups = independent_groups
    result.independent_source_count = len(independent_groups)
    result.independent_social_accounts = len({
        item.group for item in social if item.source_type in CREDIBLE_REPORTING_TYPES
    })
    # The invariant the interface depends on. Independent outlets are a subset
    # of all outlets, so this can only fail if the two are computed from
    # different pools - which is the bug this model exists to prevent.
    assert result.independent_source_count <= result.source_count, (
        "independent news sources can never exceed total news sources"
    )
    credible = credible_news


    result.speculation_score = _average_speculation(articles)
    conflict, conflict_notes, credible_disagreement = _detect_conflicts(articles, social_posts)
    result.has_conflict = conflict
    result.conflict_notes = conflict_notes
    result.credible_disagreement = credible_disagreement
    result.is_developing = _is_developing(story, articles, social_posts, conflict)
    if result.official_confirmed and not conflict:
        # An officially confirmed story with no dispute is settled, not moving.
        result.is_developing = False

    _explain_sources(result, evidence, credible, derivative_count)
    result.status = _decide_status(story, result, evidence, articles, social_posts)
    return result


# ----------------------------------------------------------------- helpers --
def _build_evidence(
    articles: List[Dict[str, Any]], social_posts: List[Dict[str, Any]]
) -> Tuple[List[SourceEvidence], List[str], List[str], int]:
    """(evidence, official outlet names, official X accounts, derivative count)."""
    evidence: List[SourceEvidence] = []
    official_names: List[str] = []
    official_accounts: List[str] = []
    derivative_count = 0
    seen_groups: Set[str] = set()

    for article in articles:
        source_type = normalize_source_type(article.get("source_type"))
        group = (article.get("independence_group") or article.get("domain")
                 or article.get("source_name") or "unknown")
        is_derivative = bool(article.get("is_derivative"))
        credits = list(article.get("attribution_outlets") or [])
        if is_derivative:
            derivative_count += 1
        if source_type == SourceType.OFFICIAL.value and not is_derivative:
            name = article.get("source_name") or group
            if name not in official_names:
                official_names.append(name)
        key = f"{group}|{is_derivative}"
        if key in seen_groups:
            continue
        seen_groups.add(key)
        evidence.append(SourceEvidence(
            group=str(group),
            name=str(article.get("source_name") or group),
            source_type=source_type,
            reliability=float(article.get("reliability_weight") or 0.3),
            derivative=is_derivative,
            credits=credits,
            kind="news",
        ))

    for post in social_posts:
        account_type = normalize_source_type(post.get("account_type"))
        group = f"x:{post.get('username') or post.get('author_id') or 'unknown'}"
        if account_type == SourceType.OFFICIAL.value:
            name = f"@{post.get('username')}"
            # An official account's own post does confirm what it states, but
            # it is recorded as an official *account*, not as a news outlet.
            if name not in official_names:
                official_names.append(name)
            if name not in official_accounts:
                official_accounts.append(name)
        if group in seen_groups:
            continue
        seen_groups.add(group)
        evidence.append(SourceEvidence(
            group=group,
            name=f"@{post.get('username') or 'unknown'}",
            source_type=account_type,
            reliability=float(post.get("reliability_weight") or 0.0) or _reliability_of(account_type),
            kind="social",
        ))
    return evidence, official_names, official_accounts, derivative_count


def _reliability_of(source_type: str) -> float:
    from models.types import reliability_for

    return reliability_for(source_type)


def _average_speculation(articles: List[Dict[str, Any]]) -> float:
    scores = [float(article.get("speculation_score") or 0.0) for article in articles]
    return round(sum(scores) / len(scores), 3) if scores else 0.0


#: Source types whose disagreement is worth calling CONTESTED. Two fan
#: accounts contradicting each other is noise, not a contested story.
CREDIBLE_FOR_CONFLICT = {
    SourceType.OFFICIAL.value,
    SourceType.MAJOR_NEWS.value,
    SourceType.ESTABLISHED_JOURNALIST.value,
    SourceType.TRUSTED_REPORTER.value,
}


def _detect_conflicts(
    articles: List[Dict[str, Any]], social_posts: List[Dict[str, Any]]
) -> Tuple[bool, List[str], bool]:
    """Look for denials/contradictions. Disagreement is preserved, not resolved.

    Returns (has_conflict, notes, credible_disagreement).  The third value is
    True only when *both* sides of the disagreement include a source trusted to
    report accurately - that is what earns the CONTESTED status, as opposed to
    a story that is merely still moving.
    """
    notes: List[str] = []
    denial_sources: List[str] = []
    confirm_sources: List[str] = []
    credible_denials = credible_confirms = 0

    for article in articles:
        text = f"{article.get('title') or ''} {article.get('excerpt') or ''} {article.get('content_snippet') or ''}"
        haystack = normalize_text(text)
        credible = normalize_source_type(article.get("source_type")) in CREDIBLE_FOR_CONFLICT
        if any(normalize_text(term) in haystack for term in DENIAL_TERMS):
            denial_sources.append(str(article.get("source_name") or "a source"))
            credible_denials += 1 if credible else 0
        else:
            # Anything credible that is not a denial is asserting the claim, so
            # it sits on the other side of the disagreement. Restricting this to
            # official wording would miss the ordinary case of one outlet
            # reporting something and another reporting a denial of it.
            if credible:
                credible_confirms += 1
            if classify_text(article.get("title") or "",
                             article.get("excerpt") or "").has_official_language:
                confirm_sources.append(str(article.get("source_name") or "a source"))
    for post in social_posts:
        haystack = normalize_text(post.get("text") or "")
        if any(normalize_text(term) in haystack for term in DENIAL_TERMS):
            denial_sources.append(f"@{post.get('username') or 'unknown'}")
            if normalize_source_type(post.get("account_type")) in CREDIBLE_FOR_CONFLICT:
                credible_denials += 1

    credible_disagreement = bool(credible_denials and credible_confirms)
    if denial_sources and (confirm_sources or len(denial_sources) < len(articles)):
        notes.append(
            "Sources disagree: "
            + ", ".join(sorted(set(denial_sources))[:3])
            + " dispute or deny what other sources report."
        )
        if credible_disagreement:
            notes.append(
                "Both sides of this disagreement include sources normally considered "
                "reliable, so the app does not pick a version. Check both before reporting."
            )
        return True, notes, credible_disagreement
    if denial_sources:
        notes.append(
            "A denial was published by " + ", ".join(sorted(set(denial_sources))[:3]) + "."
        )
        return True, notes, False
    return False, notes, False


def _is_developing(
    story: Dict[str, Any],
    articles: List[Dict[str, Any]],
    social_posts: List[Dict[str, Any]],
    conflict: bool,
) -> bool:
    recent = 0
    for item in list(articles) + list(social_posts):
        stamp = item.get("published_at") or item.get("created_at_source") or item.get("collected_at")
        hours = age_hours(stamp)
        if hours is not None and hours <= DEVELOPING_WINDOW_HOURS:
            recent += 1
    if recent >= DEVELOPING_MIN_UPDATES:
        return True
    if conflict and recent >= 2:
        return True
    return False


def _explain_sources(
    result: VerificationResult,
    evidence: List[SourceEvidence],
    credible: List[SourceEvidence],
    derivative_count: int,
) -> None:
    if result.official_confirmed:
        result.reasons.append(
            "Official source in the collected set: " + ", ".join(result.official_sources[:3])
        )
    if result.official_social_accounts:
        result.reasons.append(
            "That official confirmation came from an official X account ("
            + ", ".join(result.official_social_accounts[:3])
            + "), not from a publication."
        )
    if credible:
        names = ", ".join(sorted({item.name for item in credible})[:5])
        result.reasons.append(
            f"{result.independent_source_count} independent news source(s) of "
            f"{result.source_count} total: {names}"
        )
    if result.social_signal_count:
        result.reasons.append(
            f"{result.social_signal_count} X post(s) are linked as social signals. "
            "They are shown beside the reporting and are never counted as news sources."
        )
    if derivative_count:
        result.reasons.append(
            f"{derivative_count} report(s) credit another outlet, so they are counted as copies, "
            "not as independent confirmation."
        )
    low_quality = [item for item in evidence if item.source_type in LOW_RELIABILITY_TYPES]
    if low_quality and not credible:
        result.reasons.append(
            f"Only low-reliability sources so far ({', '.join(sorted({i.name for i in low_quality})[:4])})."
        )
    first_person = [item for item in evidence if item.source_type in FIRST_PERSON_TYPES]
    if first_person:
        result.reasons.append(
            "Includes a first-person claim from " + ", ".join(sorted({i.name for i in first_person})[:3])
            + " - that is their word, not confirmation."
        )
    if result.speculation_score >= 0.34:
        result.reasons.append(
            f"Hedged/speculative wording detected in the reporting (score {result.speculation_score:.2f})."
        )
    result.reasons.extend(result.conflict_notes)


def _decide_status(
    story: Dict[str, Any],
    result: VerificationResult,
    evidence: List[SourceEvidence],
    articles: List[Dict[str, Any]],
    social_posts: List[Dict[str, Any]],
) -> str:
    category = story.get("category") or Category.GENERAL.value

    if result.official_confirmed and not result.has_conflict:
        return StoryStatus.CONFIRMED.value
    # Reliable sources contradicting each other is its own state. The app shows
    # both versions and never silently chooses one.
    if result.has_conflict and result.credible_disagreement:
        return StoryStatus.CONTESTED.value
    if result.official_confirmed and result.has_conflict:
        return StoryStatus.DEVELOPING.value
    # DEVELOPING needs a credible source behind it. Low-quality accounts
    # repeating each other - even while contradicting each other - never lift a
    # rumour into a developing story.
    #
    # ``corroboration_count`` is used here rather than the news-source count: a
    # journalist's X post is a real corroborating voice for deciding a status,
    # even though it is never *displayed* as a news source.
    if result.has_conflict and result.corroboration_count >= 1:
        return StoryStatus.DEVELOPING.value
    # DEVELOPING also means "moving AND unresolved": new material is still
    # arriving and the reporting is still hedged. Firm multi-source reporting
    # is REPORTED.
    if (
        result.is_developing
        and result.corroboration_count >= 1
        and not result.official_confirmed
        and result.speculation_score >= 0.34
    ):
        return StoryStatus.DEVELOPING.value

    origin_type = _origin_type(articles, social_posts)
    if result.corroboration_count >= 2:
        return StoryStatus.REPORTED.value
    if result.corroboration_count == 1:

        if result.speculation_score >= 0.67 or category == Category.RUMOR.value:
            return StoryStatus.RUMOR.value
        return StoryStatus.REPORTED.value
    if origin_type in FIRST_PERSON_TYPES:
        return StoryStatus.FIGHTER_CLAIM.value
    if evidence and all(item.source_type in LOW_RELIABILITY_TYPES for item in evidence):
        return StoryStatus.RUMOR.value if result.speculation_score > 0 else StoryStatus.UNVERIFIED.value
    if result.speculation_score >= 0.34:
        return StoryStatus.RUMOR.value
    return StoryStatus.UNVERIFIED.value


def _origin_type(articles: List[Dict[str, Any]], social_posts: List[Dict[str, Any]]) -> str:
    """Source type of the earliest item - who started this."""
    items: List[Tuple[Any, str]] = []
    for article in articles:
        items.append((parse_iso(article.get("published_at") or article.get("collected_at")),
                      normalize_source_type(article.get("source_type"))))
    for post in social_posts:
        items.append((parse_iso(post.get("created_at_source") or post.get("collected_at")),
                      normalize_source_type(post.get("account_type"))))
    items = [item for item in items if item[0] is not None]
    if not items:
        return SourceType.UNKNOWN.value
    items.sort(key=lambda pair: pair[0])
    return items[0][1]
