"""Rule-based classification of an article's *kind of development*, plus the
signals the verification engine needs:

* category           - fight announcement, injury, cancellation, ...
* speculation score  - how hedged the language is ("reportedly", "in talks")
* official language  - does the text describe an official announcement
* attribution        - which other outlet this piece credits (derivative work)
* denial/conflict    - does the text dispute something

Everything is keyword-driven and inspectable: no model decides a story's
status behind the scenes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from models.types import Category
from utils.textutil import normalize_text

# ---------------------------------------------------------------- keywords --
CATEGORY_KEYWORDS: Dict[str, List[str]] = {
    Category.CANCELLATION.value: [
        "cancelled", "canceled", "called off", "scrapped", "off the card", "fight is off",
        "scratched from", "postponed", "axed from",
    ],
    Category.REPLACEMENT.value: [
        "replacement", "steps in", "step in on short notice", "replaces", "will replace",
        "short notice", "new opponent", "steps up",
    ],
    Category.INJURY.value: [
        "injury", "injured", "torn", "broken hand", "broken foot", "surgery", "withdraws",
        "withdrawn", "withdrawal", "pulled out", "forced out", "out of the fight", "out of his fight",
        "out of her fight", "medical",
    ],
    Category.RETIREMENT.value: [
        "retires", "retirement", "retiring", "hangs up the gloves", "last fight of his career",
        "final fight", "calls it a career",
    ],
    Category.SUSPENSION.value: [
        "suspension", "suspended", "failed drug test", "doping", "banned", "flagged by",
        "anti-doping", "usada", "drug test failure", "provisional suspension", "medical suspension",
    ],
    Category.RESULT.value: [
        "defeats", "def.", "beat", "knockout win", "submits", "submission win", "unanimous decision",
        "split decision", "results", "finishes", "wins by", "tko victory", "won the fight",
        "highlights from", "recap",
    ],
    Category.FIGHT_ANNOUNCEMENT.value: [
        "booked", "official for", "set for", "targeted for", "agreed to fight", "signs to face",
        "will face", "to meet", "matchup announced", "added to", "fight announced", "slated to",
        "verbally agreed", "in the works for", "headline", "headliner", "main event set",
    ],
    Category.RANKING.value: [
        "rankings", "ranked", "moves up the rankings", "drops in the rankings", "pound-for-pound",
        "rankings update", "top 15", "ranking system",
    ],
    Category.TITLE.value: [
        "stripped of the title", "vacant title", "new champion", "undisputed champion",
        "interim title", "title picture", "championship status", "belt",
    ],
    Category.FIGHTER_STATEMENT.value: [
        "says", "responds", "reacts", "calls out", "fires back", "slams", "claims", "insists",
        "reveals", "explains", "admits", "wants", "challenges", "callout", "warns", "denies",
        "shuts down", "hits back", "issues statement", "breaks silence", "confirms he",
        "confirms she", "speaks out",
    ],
    Category.CONTROVERSY.value: [
        "controversy", "backlash", "arrested", "lawsuit", "sues", "apologises", "apologizes",
        "criticised", "criticized", "accused", "investigation", "scandal", "outrage", "banned from",
        "altercation", "brawl",
    ],
    Category.BUSINESS.value: [
        "tko group", "revenue", "earnings", "broadcast deal", "media rights", "sponsorship",
        "pay-per-view buys", "fighter pay", "contract dispute", "free agency", "signs with",
        "leaves the ufc", "released by the ufc", "antitrust", "settlement", "paramount", "espn deal",
    ],
    Category.EVENT_CHANGE.value: [
        "card change", "venue change", "moved to", "rescheduled", "new date", "event postponed",
        "card shuffle", "bout order",
    ],
    Category.INTERVIEW.value: [
        "interview", "sat down with", "q&a", "exclusive interview", "spoke to", "one-on-one",
    ],
    Category.PROMO.value: [
        "how to watch", "start time", "preview", "predictions", "betting odds", "odds", "tickets",
        "live stream", "what to watch", "fight week", "embedded", "countdown", "highlights video",
        "best moments", "on this day",
    ],
}

# A headline hit on one of these settles the category outright: a scrapped or
# changed booking is the development a fight-card radar cares about, even when
# the article also talks about the reason (an injury, a failed test...).
DECISIVE_TITLE_CATEGORIES = [
    Category.CANCELLATION.value,
    Category.REPLACEMENT.value,
    Category.RETIREMENT.value,
]

# Categories checked first when several match (a cancellation beats a generic
# "fighter says" match on the same article).
CATEGORY_PRIORITY = [
    Category.CANCELLATION.value,
    Category.REPLACEMENT.value,
    Category.INJURY.value,
    Category.SUSPENSION.value,
    Category.RETIREMENT.value,
    Category.FIGHT_ANNOUNCEMENT.value,
    Category.RESULT.value,
    Category.RANKING.value,
    Category.TITLE.value,
    Category.EVENT_CHANGE.value,
    Category.BUSINESS.value,
    Category.CONTROVERSY.value,
    Category.FIGHTER_STATEMENT.value,
    Category.INTERVIEW.value,
    Category.PROMO.value,
]

SPECULATION_TERMS = [
    "reportedly", "report:", "rumor", "rumour", "rumored", "rumoured", "speculation",
    "sources say", "per sources", "according to sources", "believed to", "expected to",
    "in talks", "targeting", "eyeing", "nearing a deal", "close to agreeing", "verbally agreed",
    "could face", "may face", "might face", "linked to", "linked with", "reportedly set",
    "unconfirmed", "not yet official", "yet to be announced", "pending", "if signed",
    "would face", "in the works",
]

OFFICIAL_TERMS = [
    "officially announced", "ufc announced", "ufc has announced", "officially booked",
    "confirmed by the ufc", "ufc confirmed", "it is official", "it's official", "made official",
    "officially signed", "announced on", "announced friday", "announced thursday",
    "announced wednesday", "announced tuesday", "announced monday", "announced saturday",
    "announced sunday", "dana white announced", "ufc officials confirmed", "officially set",
]

DENIAL_TERMS = [
    "denies", "denied", "disputes", "disputed", "refutes", "shot down", "pushed back on",
    "not true", "no truth", "rejects", "contradicts", "calls report false", "false report",
]

# "according to ESPN", "per Ariel Helwani", "first reported by MMA Fighting"
_ATTRIBUTION_RE = re.compile(
    r"(?:according to|per|first reported by|reported by|via|as reported by|told)\s+"
    r"([A-Z][\w\.\'\-]*(?:\s+[A-Z][\w\.\'\-]*){0,3})"
)

# Outlet names that commonly appear in attributions.
KNOWN_OUTLET_NAMES = [
    "ESPN", "MMA Fighting", "MMA Junkie", "MMAJunkie", "Sherdog", "MMA Mania", "Bloody Elbow",
    "Fightful", "The Athletic", "Yahoo Sports", "Sports Illustrated", "BBC", "TalkSport",
    "Ariel Helwani", "Brett Okamoto", "Marc Raimondi", "Damon Martin", "Mike Heck", "Nolan King",
    "Mike Bohn", "John Morgan", "Alexander K. Lee", "Guilherme Cruz", "Shaheen Al-Shatti",
    "UFC", "Dana White",
]


@dataclass
class ClassificationSignals:
    category: str = Category.GENERAL.value
    category_scores: Dict[str, int] = field(default_factory=dict)
    keywords: List[str] = field(default_factory=list)
    speculation_score: float = 0.0
    speculation_terms: List[str] = field(default_factory=list)
    has_official_language: bool = False
    official_terms: List[str] = field(default_factory=list)
    has_denial: bool = False
    denial_terms: List[str] = field(default_factory=list)
    attribution_outlets: List[str] = field(default_factory=list)
    is_derivative: bool = False


def classify_text(
    title: str,
    body: str = "",
    self_outlet: Optional[str] = None,
) -> ClassificationSignals:
    """Classify one article/post. ``self_outlet`` prevents self-attribution."""
    title_text = normalize_text(title)
    body_text = normalize_text(body)
    combined = f"{title_text} {body_text}".strip()
    signals = ClassificationSignals()

    scores: Dict[str, int] = {}
    matched_keywords: List[str] = []
    for category, terms in CATEGORY_KEYWORDS.items():
        score = 0
        for term in terms:
            normalized_term = normalize_text(term)
            if not normalized_term:
                continue
            if normalized_term in title_text:
                score += 3  # the headline carries the most signal
                matched_keywords.append(term)
            elif normalized_term in body_text:
                score += 1
                matched_keywords.append(term)
        if score:
            scores[category] = score
    signals.category_scores = scores
    signals.keywords = sorted(set(matched_keywords))[:12]
    signals.category = _pick_category(scores, title_text)

    hits = [term for term in SPECULATION_TERMS if normalize_text(term) in combined]
    signals.speculation_terms = hits
    signals.speculation_score = min(1.0, len(hits) / 3.0) if hits else 0.0

    official_hits = [term for term in OFFICIAL_TERMS if normalize_text(term) in combined]
    signals.official_terms = official_hits
    signals.has_official_language = bool(official_hits)

    denial_hits = [term for term in DENIAL_TERMS if normalize_text(term) in combined]
    signals.denial_terms = denial_hits
    signals.has_denial = bool(denial_hits)

    outlets = extract_attributions(f"{title} {body}", self_outlet)
    signals.attribution_outlets = outlets
    signals.is_derivative = bool(outlets)
    return signals


def _pick_category(scores: Dict[str, int], title_text: str = "") -> str:
    if not scores:
        return Category.GENERAL.value
    for category in DECISIVE_TITLE_CATEGORIES:
        if category in scores and any(
            normalize_text(term) in title_text for term in CATEGORY_KEYWORDS[category]
        ):
            return category
    best = max(scores.values())
    tied = [category for category, score in scores.items() if score == best]
    if len(tied) == 1:
        return tied[0]
    for category in CATEGORY_PRIORITY:
        if category in tied:
            return category
    return tied[0]


def extract_attributions(text: str, self_outlet: Optional[str] = None) -> List[str]:
    """Outlets/journalists this piece credits, i.e. evidence it is derivative."""
    if not text:
        return []
    found: List[str] = []
    self_normalized = normalize_text(self_outlet or "")
    for match in _ATTRIBUTION_RE.finditer(text):
        candidate = match.group(1).strip(" .,:;")
        normalized_candidate = normalize_text(candidate)
        for outlet in KNOWN_OUTLET_NAMES:
            normalized_outlet = normalize_text(outlet)
            if normalized_outlet and normalized_outlet in normalized_candidate:
                if self_normalized and normalized_outlet in self_normalized:
                    continue  # an outlet citing itself is not derivative
                if outlet not in found:
                    found.append(outlet)
                break
    return found[:4]


def is_promotional(title: str, body: str = "") -> bool:
    """Low-information promo content (previews, odds, 'how to watch')."""
    signals = classify_text(title, body)
    return signals.category in (Category.PROMO.value, Category.INTERVIEW.value)
