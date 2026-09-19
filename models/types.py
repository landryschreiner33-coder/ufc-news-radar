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
    CONTESTED = "CONTESTED"


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
    StoryStatus.CONTESTED: StatusStyle(
        "\U0001F7E3", "CONTESTED", "#a855f7",
        "Reliable sources disagree. Both versions are shown; the app does not pick one.",
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


# -------------------------------------------------------- event lifecycle --
class EventStatus(str, Enum):
    """Where a UFC event is in its life.

    Deliberately *not* derived from the date alone.  An event is only
    COMPLETED once something authoritative says it finished; a date that has
    passed with no such evidence leaves the event UNKNOWN rather than
    inventing a result.
    """

    UPCOMING = "UPCOMING"
    LIVE = "LIVE"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    POSTPONED = "POSTPONED"
    UNKNOWN = "UNKNOWN"


EVENT_STATUS_STYLES: Dict[str, StatusStyle] = {
    EventStatus.UPCOMING: StatusStyle(
        "\U0001F4C5", "UPCOMING", "#3b82f6",
        "Scheduled. The start time collected from the official schedule is still in the future.",
    ),
    EventStatus.LIVE: StatusStyle(
        "\U0001F534", "LIVE NOW", "#ef4444",
        "The scheduled start has passed and the event is inside its expected running window.",
    ),
    EventStatus.COMPLETED: StatusStyle(
        "✅", "COMPLETED", "#19c37d",
        "Reliable evidence was collected that this event finished.",
    ),
    EventStatus.CANCELLED: StatusStyle(
        "\U0001F6AB", "CANCELLED", "#ef4444",
        "Officially cancelled according to the collected sources.",
    ),
    EventStatus.POSTPONED: StatusStyle(
        "⏸", "POSTPONED", "#ff9130",
        "Officially postponed according to the collected sources.",
    ),
    EventStatus.UNKNOWN: StatusStyle(
        "⚪", "STATUS UNKNOWN", "#9aa4b2",
        "Not enough evidence to state this event's status. The app will not guess.",
    ),
}


def event_status_style(status: Optional[str]) -> StatusStyle:
    return EVENT_STATUS_STYLES.get(
        str(status or "").upper(), EVENT_STATUS_STYLES[EventStatus.UNKNOWN]
    )


def event_status_badge(status: Optional[str]) -> str:
    style = event_status_style(status)
    return f"{style.emoji} {style.label}"


#: How sure the app is about an event's status, and where that status came from.
class StatusConfidence(str, Enum):
    OFFICIAL = "OFFICIAL"      # straight from an official UFC page
    REPORTED = "REPORTED"      # credible reporting, not official
    DERIVED = "DERIVED"        # computed from an official schedule + the clock
    LOW = "LOW"                # weak or single-source evidence


# --------------------------------------------------------- article intent --
class ArticleIntent(str, Enum):
    """What an article is *doing* - the guard that stops a prediction piece
    being read as a result.

    Only RESULT and POST_FIGHT may ever contribute evidence that fights or
    events have concluded.  PREVIEW and PREDICTION are explicitly forbidden
    from doing so (see ``processors/result_safety.py``).
    """

    PREVIEW = "PREVIEW"
    PREDICTION = "PREDICTION"
    ANNOUNCEMENT = "ANNOUNCEMENT"
    RESULT = "RESULT"
    POST_FIGHT = "POST_FIGHT"
    INTERVIEW = "INTERVIEW"
    RUMOR = "RUMOR"
    UNKNOWN = "UNKNOWN"


INTENT_LABELS: Dict[str, str] = {
    ArticleIntent.PREVIEW: "Preview",
    ArticleIntent.PREDICTION: "Prediction / pick",
    ArticleIntent.ANNOUNCEMENT: "Announcement",
    ArticleIntent.RESULT: "Result",
    ArticleIntent.POST_FIGHT: "Post-fight",
    ArticleIntent.INTERVIEW: "Interview",
    ArticleIntent.RUMOR: "Rumor",
    ArticleIntent.UNKNOWN: "Unclassified",
}

#: Intents that are allowed to establish that a fight or event has concluded.
RESULT_BEARING_INTENTS = {ArticleIntent.RESULT.value, ArticleIntent.POST_FIGHT.value}

#: Intents that must NEVER establish a result, no matter what else they say.
RESULT_FORBIDDEN_INTENTS = {ArticleIntent.PREVIEW.value, ArticleIntent.PREDICTION.value}
