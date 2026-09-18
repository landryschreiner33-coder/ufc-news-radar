"""Shared vocabulary: story statuses, source types, categories.

Everything in the app imports its labels from here so the dashboard, the
processors and the tests can never drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Dict, List, Optional


# ------------------------------------------------------------- statuses ----
class StoryStatus(str, Enum):
    CONFIRMED = "CONFIRMED"
    REPORTED = "REPORTED"
    DEVELOPING = "DEVELOPING"
    RUMOR = "RUMOR"
    UNVERIFIED = "UNVERIFIED"
    FIGHTER_CLAIM = "FIGHTER_CLAIM"


@dataclass(frozen=True)
class StatusStyle:
    emoji: str
    label: str
    color: str
    meaning: str


STATUS_STYLES: Dict[str, StatusStyle] = {
    StoryStatus.CONFIRMED: StatusStyle(
        "\U0001F7E2", "CONFIRMED", "#19c37d",
        "Confirmed by UFC or another official party in the collected sources.",
    ),
    StoryStatus.REPORTED: StatusStyle(
        "\U0001F7E1", "REPORTED", "#e8c547",
        "Reported by credible outlets/journalists, but not officially confirmed.",
    ),
    StoryStatus.DEVELOPING: StatusStyle(
        "\U0001F7E0", "DEVELOPING", "#ff9130",
        "Actively changing - new reports are still arriving and details may change.",
    ),
    StoryStatus.RUMOR: StatusStyle(
        "\U0001F534", "RUMOR", "#ef4444",
        "Speculative or single low-reliability origin. Treat as unconfirmed.",
    ),
    StoryStatus.UNVERIFIED: StatusStyle(
        "⚪", "UNVERIFIED", "#9aa4b2",
        "Not enough source evidence collected yet to say more.",
    ),
    StoryStatus.FIGHTER_CLAIM: StatusStyle(
        "\U0001F535", "FIGHTER CLAIM", "#3b82f6",
        "A fighter, coach or team is making the claim. It is their word, not confirmation.",
    ),
}


def status_style(status: Optional[str]) -> StatusStyle:
    return STATUS_STYLES.get(str(status or "").upper(), STATUS_STYLES[StoryStatus.UNVERIFIED])


def status_badge(status: Optional[str]) -> str:
    style = status_style(status)
    return f"{style.emoji} {style.label}"


# ---------------------------------------------------------- source types ---
class SourceType(str, Enum):
    OFFICIAL = "OFFICIAL"
    MAJOR_NEWS = "MAJOR_NEWS"
    ESTABLISHED_JOURNALIST = "ESTABLISHED_JOURNALIST"
    TRUSTED_REPORTER = "TRUSTED_REPORTER"
    INSIDER = "INSIDER"
    FIGHTER = "FIGHTER"
    COACH_TEAM = "COACH_TEAM"
    PROMOTER = "PROMOTER"
    UNKNOWN = "UNKNOWN"
    FAN_ACCOUNT = "FAN_ACCOUNT"


SOURCE_TYPE_LABELS: Dict[str, str] = {
    SourceType.OFFICIAL: "Official",
    SourceType.MAJOR_NEWS: "Major news",
    SourceType.ESTABLISHED_JOURNALIST: "Established journalist",
    SourceType.TRUSTED_REPORTER: "Trusted reporter",
    SourceType.INSIDER: "Insider",
    SourceType.FIGHTER: "Fighter",
    SourceType.COACH_TEAM: "Coach / team",
    SourceType.PROMOTER: "Promoter",
    SourceType.UNKNOWN: "Unknown",
    SourceType.FAN_ACCOUNT: "Fan account",
}

# Default reliability weights (0-1). Editable per source in Settings; these are
# only the starting values, never an AI-generated reputation score.
DEFAULT_RELIABILITY: Dict[str, float] = {
    SourceType.OFFICIAL: 1.0,
    SourceType.MAJOR_NEWS: 0.85,
    SourceType.ESTABLISHED_JOURNALIST: 0.8,
    SourceType.TRUSTED_REPORTER: 0.7,
    SourceType.INSIDER: 0.5,
    SourceType.FIGHTER: 0.55,
    SourceType.COACH_TEAM: 0.5,
    SourceType.PROMOTER: 0.5,
    SourceType.UNKNOWN: 0.3,
    SourceType.FAN_ACCOUNT: 0.15,
}

# Source types whose word alone can move a story to CONFIRMED.
OFFICIAL_TYPES = {SourceType.OFFICIAL}
# Source types that count towards "independent credible reporting".
CREDIBLE_REPORTING_TYPES = {
    SourceType.OFFICIAL,
    SourceType.MAJOR_NEWS,
    SourceType.ESTABLISHED_JOURNALIST,
    SourceType.TRUSTED_REPORTER,
}
FIRST_PERSON_TYPES = {SourceType.FIGHTER, SourceType.COACH_TEAM, SourceType.PROMOTER}
LOW_RELIABILITY_TYPES = {SourceType.UNKNOWN, SourceType.FAN_ACCOUNT}


def reliability_for(source_type: Optional[str]) -> float:
    return DEFAULT_RELIABILITY.get(str(source_type or "").upper(), 0.3)


# ------------------------------------------------------------ categories ---
class Category(str, Enum):
    FIGHT_ANNOUNCEMENT = "fight_announcement"
    INJURY = "injury"
    CANCELLATION = "cancellation"
    REPLACEMENT = "replacement"
    RANKING = "ranking"
    RESULT = "result"
    RETIREMENT = "retirement"
    SUSPENSION = "suspension"
    CONTROVERSY = "controversy"
    FIGHTER_STATEMENT = "fighter_statement"
    TITLE = "title"
    EVENT_CHANGE = "event_change"
    BUSINESS = "business"
    INTERVIEW = "interview"
    PROMO = "promo"
    RUMOR = "rumor"
    GENERAL = "general"


CATEGORY_LABELS: Dict[str, str] = {
    Category.FIGHT_ANNOUNCEMENT: "Fight announcement",
    Category.INJURY: "Injury",
    Category.CANCELLATION: "Cancellation",
    Category.REPLACEMENT: "Replacement",
    Category.RANKING: "Rankings",
    Category.RESULT: "Fight result",
    Category.RETIREMENT: "Retirement",
    Category.SUSPENSION: "Suspension",
    Category.CONTROVERSY: "Controversy",
    Category.FIGHTER_STATEMENT: "Fighter statement",
    Category.TITLE: "Title / championship",
    Category.EVENT_CHANGE: "Event change",
    Category.BUSINESS: "UFC business",
    Category.INTERVIEW: "Interview",
    Category.PROMO: "Promotional",
    Category.RUMOR: "Rumor",
    Category.GENERAL: "General",
}

# Categories that describe *different developments*. Two articles about the
# same fighters but in two of these buckets stay in separate stories.
DISTINCT_DEVELOPMENT_CATEGORIES = {
    Category.FIGHT_ANNOUNCEMENT.value,
    Category.INJURY.value,
    Category.CANCELLATION.value,
    Category.REPLACEMENT.value,
    Category.RESULT.value,
    Category.RETIREMENT.value,
    Category.SUSPENSION.value,
    Category.RANKING.value,
}


def category_label(category: Optional[str]) -> str:
    return CATEGORY_LABELS.get(str(category or "").lower(), "General")


# --------------------------------------------------------- X account types -
X_ACCOUNT_TYPES: List[str] = [
    SourceType.OFFICIAL.value,
    "TRUSTED_JOURNALIST",
    "ESTABLISHED_REPORTER",
    SourceType.FIGHTER.value,
    SourceType.COACH_TEAM.value,
    SourceType.PROMOTER.value,
    SourceType.INSIDER.value,
    SourceType.UNKNOWN.value,
    SourceType.FAN_ACCOUNT.value,
]

# The X list uses two labels that map onto the news-source vocabulary.
X_TYPE_ALIASES: Dict[str, str] = {
    "TRUSTED_JOURNALIST": SourceType.ESTABLISHED_JOURNALIST.value,
    "ESTABLISHED_REPORTER": SourceType.TRUSTED_REPORTER.value,
}


def normalize_source_type(value: Optional[str]) -> str:
    """Map any label (including the X-specific ones) to a SourceType value."""
    raw = str(value or "").strip().upper().replace(" ", "_").replace("/", "_")
    raw = raw.replace("COACH___TEAM", "COACH_TEAM").replace("COACH__TEAM", "COACH_TEAM")
    if raw in X_TYPE_ALIASES:
        return X_TYPE_ALIASES[raw]
    valid = {member.value for member in SourceType}
    return raw if raw in valid else SourceType.UNKNOWN.value


# -------------------------------------------------------------- filtering --
FEED_FILTERS: List[str] = [
    "ALL", "BREAKING", "IMPORTANT", "RUMORS", "DEVELOPING", "TRENDING",
    "FIGHT ANNOUNCEMENTS", "INJURIES", "RANKINGS", "TITLE", "RESULTS",
    "FIGHTERS", "UFC BUSINESS", "CONTROVERSY",
]

FILTER_TO_CATEGORY: Dict[str, str] = {
    "FIGHT ANNOUNCEMENTS": Category.FIGHT_ANNOUNCEMENT.value,
    "INJURIES": Category.INJURY.value,
    "RANKINGS": Category.RANKING.value,
    "TITLE": Category.TITLE.value,
    "RESULTS": Category.RESULT.value,
    "FIGHTERS": Category.FIGHTER_STATEMENT.value,
    "UFC BUSINESS": Category.BUSINESS.value,
    "CONTROVERSY": Category.CONTROVERSY.value,
}

SORT_OPTIONS: List[str] = [
    "Newest", "Most Relevant", "Most Sources", "Most Discussed", "Recently Updated",
]
