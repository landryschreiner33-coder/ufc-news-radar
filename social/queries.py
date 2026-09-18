"""X search-query construction.

Queries are built to be *narrow and few*: X access tiers have small request
quotas, so the app combines many terms into a handful of well-formed queries
instead of one query per keyword.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from database import repo_settings as settings_repo

# Term groups from the spec, folded into a few queries.
CHANGE_TERMS = [
    "injury", "injured", "withdrawal", "withdraws", "replacement", "replaces",
    "canceled", "cancelled", "\"out of\"", "\"short notice\"",
]
BOOKING_TERMS = [
    "\"title fight\"", "championship", "\"main event\"", "\"co-main event\"",
    "booked", "official", "\"fight announcement\"", "signed",
]
CAREER_TERMS = [
    "retirement", "retires", "contract", "ranking", "rankings", "\"weigh-in\"",
    "\"press conference\"", "suspended",
]

BASE_FILTER = "-is:retweet lang:en"
MAX_QUERY_LENGTH = 500  # standard access allows 512 characters


def _or_group(terms: List[str]) -> str:
    return "(" + " OR ".join(terms) + ")"


def _quote(value: str) -> str:
    cleaned = str(value).strip().replace('"', "")
    return f'"{cleaned}"' if " " in cleaned else cleaned


def _clip(query: str) -> str:
    if len(query) <= MAX_QUERY_LENGTH:
        return query
    return query[:MAX_QUERY_LENGTH].rsplit(" OR ", 1)[0] + ")" + " " + BASE_FILTER


def build_topic_queries() -> List[Dict[str, str]]:
    """The standing UFC-news queries, highest value first."""
    return [
        {
            "name": "card changes",
            "query": _clip(f"UFC {_or_group(CHANGE_TERMS)} {BASE_FILTER}"),
            "purpose": "injuries, withdrawals, replacements, cancellations",
        },
        {
            "name": "bookings & titles",
            "query": _clip(f"UFC {_or_group(BOOKING_TERMS)} {BASE_FILTER}"),
            "purpose": "fight announcements, title fights, main events",
        },
        {
            "name": "career & event news",
            "query": _clip(f"UFC {_or_group(CAREER_TERMS)} {BASE_FILTER}"),
            "purpose": "retirements, contracts, rankings, fight week",
        },
    ]


def build_watchlist_queries() -> List[Dict[str, str]]:
    """Queries for the fighters/events/topics you are watching."""
    queries: List[Dict[str, str]] = []
    fighters = [item["value"] for item in settings_repo.list_watchlist("fighter")]
    events = [item["value"] for item in settings_repo.list_watchlist("event")]
    topics = [item["value"] for item in settings_repo.list_watchlist("topic")]

    for chunk in _chunks(fighters, 6):
        queries.append({
            "name": f"watched fighters ({len(chunk)})",
            "query": _clip(f"{_or_group([_quote(name) for name in chunk])} (UFC OR MMA) {BASE_FILTER}"),
            "purpose": "watchlist: fighters",
        })
    for chunk in _chunks(events, 5):
        queries.append({
            "name": f"watched events ({len(chunk)})",
            "query": _clip(f"{_or_group([_quote(name) for name in chunk])} {BASE_FILTER}"),
            "purpose": "watchlist: events",
        })
    for chunk in _chunks(topics, 5):
        queries.append({
            "name": f"watched topics ({len(chunk)})",
            "query": _clip(f"UFC {_or_group([_quote(topic) for topic in chunk])} {BASE_FILTER}"),
            "purpose": "watchlist: topics",
        })
    return queries


def build_account_query(usernames: List[str], limit: int = 12) -> Optional[Dict[str, str]]:
    """One query covering several monitored accounts (cheaper than one call each)."""
    handles = [str(name).lstrip("@").strip() for name in usernames if name][:limit]
    if not handles:
        return None
    return {
        "name": f"monitored accounts ({len(handles)})",
        "query": _clip(_or_group([f"from:{handle}" for handle in handles]) + " -is:retweet"),
        "purpose": "posts from monitored official/press accounts",
    }


def planned_queries(max_queries: int) -> List[Dict[str, str]]:
    """The query plan for one run, ordered by value and trimmed to budget."""
    plan: List[Dict[str, str]] = []
    plan.extend(build_watchlist_queries())
    plan.extend(build_topic_queries())
    accounts = [
        account["username"] for account in settings_repo.list_monitored_accounts(enabled_only=True)
        if account.get("category") in ("official", "journalist", "reporter")
    ]
    account_query = build_account_query(accounts)
    if account_query:
        plan.insert(1, account_query)
    return plan[:max_queries] if max_queries > 0 else []


def _chunks(values: List[Any], size: int) -> List[List[Any]]:
    return [values[index:index + size] for index in range(0, len(values), size)]
