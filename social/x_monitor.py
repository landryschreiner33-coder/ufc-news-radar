"""Collect X activity within the configured API budget.

Rules enforced here:
  * nothing happens without X_BEARER_TOKEN (the UI shows NOT CONFIGURED);
  * a fixed number of searches/timelines per run (from .env);
  * identical queries are not repeated inside the cache window;
  * a rate limit pauses that query until the reset time the API reported;
  * posts are stored through the normal pipeline so they are classified the
    same way as everything else.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from database import repo_runs as runs_repo
from database import repo_settings as settings_repo
from database import repo_social as social_repo
from social.queries import planned_queries
from social.x_client import XClient, get_x_client
from utils.logging_setup import get_logger
from utils.timeutil import utcnow_iso

logger = get_logger(__name__)


@dataclass
class XCollectionOutcome:
    configured: bool = False
    searches_run: int = 0
    searches_skipped: int = 0
    timelines_run: int = 0
    stored: int = 0
    seen: int = 0
    rate_limited: bool = False
    rate_limit_reset_minutes: Optional[int] = None
    errors: List[str] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def status_line(self) -> str:
        if not self.configured:
            return "X MONITORING - NOT CONFIGURED"
        if self.rate_limited:
            return f"X rate limit reached - retry in ~{self.rate_limit_reset_minutes or '?'} min"
        return (
            f"{self.searches_run} search(es), {self.timelines_run} timeline(s), "
            f"{self.stored} new post(s)"
        )


def collect_x_activity(
    client: Optional[XClient] = None,
    max_searches: Optional[int] = None,
    max_timelines: Optional[int] = None,
) -> XCollectionOutcome:
    """One X collection pass. Safe to call when X is not configured."""
    client = client or get_x_client()
    outcome = XCollectionOutcome(configured=client.is_configured)
    if not client.is_configured:
        outcome.notes.append(
            "Set X_BEARER_TOKEN in your .env file to enable X monitoring."
        )
        return outcome
    if not settings_repo.get_bool("x_enabled", True):
        outcome.notes.append("X monitoring is switched off in Settings.")
        return outcome

    config = client.config
    search_budget = max_searches if max_searches is not None else config.max_searches_per_run
    timeline_budget = max_timelines if max_timelines is not None else config.max_timelines_per_run

    if settings_repo.get_bool("x_search_enabled", True):
        _run_searches(client, outcome, search_budget)
    if settings_repo.get_bool("x_timelines_enabled", True) and not outcome.rate_limited:
        _run_timelines(client, outcome, timeline_budget)
    return outcome


def _run_searches(client: XClient, outcome: XCollectionOutcome, budget: int) -> None:
    from processors.pipeline import ingest_social_posts

    cache_minutes = client.config.query_cache_minutes
    for plan in planned_queries(budget):
        if outcome.rate_limited:
            return
        query = plan["query"]
        skip, reason = runs_repo.should_skip_query("search_recent", query, cache_minutes=cache_minutes)
        if skip:
            outcome.searches_skipped += 1
            outcome.notes.append(f"skipped '{plan['name']}': {reason}")
            continue
        cached = runs_repo.get_cached_query("search_recent", query)
        since_id = cached.get("newest_id") if cached else None
        response = client.search_recent(query, since_id=since_id)
        outcome.searches_run += 1
        if response.rate_limited:
            outcome.rate_limited = True
            outcome.rate_limit_reset_minutes = response.reset_in_minutes
            runs_repo.record_query("search_recent", query, status="rate_limited",
                                   error=response.error,
                                   rate_limit_reset_epoch=response.rate_limit_reset_epoch)
            outcome.errors.append(response.error or "rate limited")
            return
        if not response.ok:
            runs_repo.record_query("search_recent", query, status="error", error=response.error)
            outcome.errors.append(f"{plan['name']}: {response.error}")
            continue
        outcome.seen += len(response.posts)
        posts = [dict(post, query_source=plan["name"]) for post in response.posts]
        outcome.stored += ingest_social_posts(posts)
        runs_repo.record_query("search_recent", query, result_count=len(response.posts),
                               newest_id=response.newest_id or _newest_id(response.posts))


def _run_timelines(client: XClient, outcome: XCollectionOutcome, budget: int) -> None:
    from processors.pipeline import ingest_social_posts

    if budget <= 0:
        return
    accounts = settings_repo.list_monitored_accounts(enabled_only=True)
    accounts = [account for account in accounts if account.get("category") in
                ("official", "fighter", "journalist", "reporter", "insider", "coach_team", "promoter")]
    for account in accounts[:budget]:
        if outcome.rate_limited:
            return
        username = account["username"]
        user_id = account.get("user_id")
        if not user_id:
            lookup = client.get_user(username)
            if lookup.rate_limited:
                outcome.rate_limited = True
                outcome.rate_limit_reset_minutes = lookup.reset_in_minutes
                return
            if not lookup.ok:
                settings_repo.update_monitored_account_state(username, last_error=lookup.error)
                outcome.errors.append(f"@{username}: {lookup.error}")
                continue
            user_id = next(iter(lookup.users.keys()), None)
            settings_repo.update_monitored_account_state(username, user_id=user_id)
        if not user_id:
            continue
        response = client.user_timeline(user_id, since_id=account.get("last_post_id"))
        outcome.timelines_run += 1
        if response.rate_limited:
            outcome.rate_limited = True
            outcome.rate_limit_reset_minutes = response.reset_in_minutes
            settings_repo.update_monitored_account_state(username, last_error="rate limited")
            return
        if not response.ok:
            settings_repo.update_monitored_account_state(username, last_error=response.error)
            outcome.errors.append(f"@{username}: {response.error}")
            continue
        posts = [
            dict(post, query_source=f"timeline:@{username}",
                 account_type=account.get("account_type") or post.get("account_type"),
                 username=post.get("username") or username)
            for post in response.posts
        ]
        outcome.seen += len(posts)
        outcome.stored += ingest_social_posts(posts)
        settings_repo.update_monitored_account_state(
            username, user_id=user_id, last_post_id=_newest_id(posts), last_error=None
        )


def _newest_id(posts: List[Dict[str, Any]]) -> Optional[str]:
    ids = [str(post.get("post_id")) for post in posts if post.get("post_id")]
    return max(ids, key=lambda value: (len(value), value)) if ids else None


def x_status_panel() -> Dict[str, Any]:
    """Data for the X status panel in the UI."""
    client = get_x_client()
    status = client.status()
    status.update({
        "stored_posts": social_repo.post_count(),
        "recent_posts_48h": social_repo.post_count(hours=48),
        "monitored_accounts": len(settings_repo.list_monitored_accounts(enabled_only=True)),
        "recent_queries": runs_repo.recent_queries(limit=10),
        "checked_at": utcnow_iso(),
    })
    return status
