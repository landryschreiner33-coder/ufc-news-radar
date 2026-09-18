"""Official X (Twitter) API v2 client - read-only.

Scope on purpose
----------------
Only endpoints that a standard App-only Bearer Token can reach are used:

    GET /2/tweets/search/recent          (last 7 days ONLY)
    GET /2/users/by/username/:username
    GET /2/users/:id/tweets

Full-archive search is a separate access level, so this app never assumes it.
Nothing here scrapes x.com or works around API access: without a token the
client reports "not configured" and the rest of the app carries on.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from database import repo_settings as settings_repo
from models.types import normalize_source_type
from utils.config import XConfig, get_config
from utils.http import HttpClient, get_client
from utils.logging_setup import get_logger
from utils.timeutil import parse_iso, to_iso, utcnow_iso

logger = get_logger(__name__)

API_BASE = "https://api.x.com/2"
RECENT_SEARCH_DAYS = 7

TWEET_FIELDS = "created_at,public_metrics,entities,lang,referenced_tweets,author_id,possibly_sensitive"
USER_FIELDS = "username,name,verified,verified_type,public_metrics,description"
MEDIA_FIELDS = "type,url,preview_image_url"
EXPANSIONS = "author_id,attachments.media_keys"


@dataclass
class XResponse:
    """Result of one API call. ``ok`` means the payload is usable."""

    ok: bool = False
    posts: List[Dict[str, Any]] = field(default_factory=list)
    users: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    error: Optional[str] = None
    error_kind: Optional[str] = None          # not_configured|rate_limit|auth|http|network|parse
    rate_limited: bool = False
    rate_limit_reset_epoch: Optional[int] = None
    status_code: Optional[int] = None
    newest_id: Optional[str] = None
    raw_count: int = 0

    @property
    def not_configured(self) -> bool:
        return self.error_kind == "not_configured"

    @property
    def reset_in_minutes(self) -> Optional[int]:
        if not self.rate_limit_reset_epoch:
            return None
        return max(0, int((self.rate_limit_reset_epoch - time.time()) / 60))


class XClient:
    """Thin, defensive wrapper around the X API v2 read endpoints."""

    def __init__(self, config: Optional[XConfig] = None, client: Optional[HttpClient] = None) -> None:
        self.config = config or get_config().x
        self.http = client or get_client()

    # ------------------------------------------------------------- status --
    @property
    def is_configured(self) -> bool:
        return bool(self.config.bearer_token)

    def status(self) -> Dict[str, Any]:
        return {
            "configured": self.is_configured,
            "label": "X MONITORING - ACTIVE" if self.is_configured else "X MONITORING - NOT CONFIGURED",
            "search_window": f"Recent search covers the last {RECENT_SEARCH_DAYS} days only",
            "max_searches_per_run": self.config.max_searches_per_run,
            "max_timelines_per_run": self.config.max_timelines_per_run,
            "max_results_per_query": self.config.max_results_per_query,
            "cache_minutes": self.config.query_cache_minutes,
        }

    def _headers(self) -> Dict[str, str]:
        return {
            "Authorization": f"Bearer {self.config.bearer_token}",
            "User-Agent": "UFCNewsRadar/1.0",
        }

    def _not_configured(self) -> XResponse:
        return XResponse(
            ok=False,
            error="X_BEARER_TOKEN is not set - X monitoring is disabled.",
            error_kind="not_configured",
        )

    # ------------------------------------------------------------ requests --
    def _request(self, path: str, params: Dict[str, Any]) -> XResponse:
        if not self.is_configured:
            return self._not_configured()
        response = self.http.get_json(
            f"{API_BASE}{path}", headers=self._headers(), params=params, max_retries=1
        )
        result = XResponse(status_code=response.status_code)
        if response.rate_limited:
            result.error = "X API rate limit reached"
            result.error_kind = "rate_limit"
            result.rate_limited = True
            result.rate_limit_reset_epoch = response.rate_limit_reset_epoch
            logger.warning("X API rate limited (resets in %s min)", result.reset_in_minutes)
            return result
        if not response.ok:
            if response.status_code in (401, 403):
                result.error = (
                    f"X API rejected the credentials (HTTP {response.status_code}). "
                    "Check X_BEARER_TOKEN and your access level."
                )
                result.error_kind = "auth"
            else:
                result.error = response.error or f"HTTP {response.status_code}"
                result.error_kind = response.error_kind or "http"
            return result
        payload = response.json()
        if payload is None:
            result.error = "X API returned a response that is not valid JSON"
            result.error_kind = "parse"
            return result
        if isinstance(payload, dict) and payload.get("errors") and not payload.get("data"):
            first = payload["errors"][0] if payload["errors"] else {}
            result.error = str(first.get("detail") or first.get("title") or "X API error")
            result.error_kind = "http"
            return result
        result.ok = True
        result.users = _index_users(payload)
        result.posts = [
            normalize_post(item, result.users, _index_media(payload))
            for item in (payload.get("data") or [])
            if isinstance(item, dict)
        ]
        result.raw_count = len(payload.get("data") or [])
        result.newest_id = (payload.get("meta") or {}).get("newest_id")
        return result

    # ------------------------------------------------------------ endpoints --
    def search_recent(
        self, query: str, max_results: Optional[int] = None, since_id: Optional[str] = None
    ) -> XResponse:
        """Recent search - the last 7 days only (API limitation, not a bug)."""
        if not self.is_configured:
            return self._not_configured()
        params: Dict[str, Any] = {
            "query": query,
            "max_results": max(10, min(100, max_results or self.config.max_results_per_query)),
            "tweet.fields": TWEET_FIELDS,
            "user.fields": USER_FIELDS,
            "media.fields": MEDIA_FIELDS,
            "expansions": EXPANSIONS,
        }
        if since_id:
            params["since_id"] = since_id
        return self._request("/tweets/search/recent", params)

    def get_user(self, username: str) -> XResponse:
        if not self.is_configured:
            return self._not_configured()
        handle = str(username).lstrip("@").strip()
        response = self.http.get_json(
            f"{API_BASE}/users/by/username/{handle}",
            headers=self._headers(),
            params={"user.fields": USER_FIELDS},
            max_retries=1,
        )
        result = XResponse(status_code=response.status_code)
        if response.rate_limited:
            result.error_kind, result.rate_limited = "rate_limit", True
            result.rate_limit_reset_epoch = response.rate_limit_reset_epoch
            result.error = "X API rate limit reached"
            return result
        if not response.ok:
            result.error = response.error or f"HTTP {response.status_code}"
            result.error_kind = "auth" if response.status_code in (401, 403) else "http"
            return result
        payload = response.json() or {}
        data = payload.get("data")
        if not data:
            result.error = f"X account @{handle} was not found"
            result.error_kind = "http"
            return result
        result.ok = True
        result.users = {str(data.get("id")): data}
        return result

    def user_timeline(
        self, user_id: str, max_results: Optional[int] = None, since_id: Optional[str] = None
    ) -> XResponse:
        if not self.is_configured:
            return self._not_configured()
        params: Dict[str, Any] = {
            "max_results": max(5, min(100, max_results or self.config.max_results_per_query)),
            "tweet.fields": TWEET_FIELDS,
            "user.fields": USER_FIELDS,
            "media.fields": MEDIA_FIELDS,
            "expansions": EXPANSIONS,
            "exclude": "retweets,replies",
        }
        if since_id:
            params["since_id"] = since_id
        return self._request(f"/users/{user_id}/tweets", params)


# ------------------------------------------------------------ normalising ---
def _index_users(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    users = {}
    for user in ((payload.get("includes") or {}).get("users") or []):
        if isinstance(user, dict) and user.get("id"):
            users[str(user["id"])] = user
    return users


def _index_media(payload: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    media = {}
    for item in ((payload.get("includes") or {}).get("media") or []):
        if isinstance(item, dict) and item.get("media_key"):
            media[str(item["media_key"])] = item
    return media


def normalize_post(
    item: Dict[str, Any],
    users: Optional[Dict[str, Dict[str, Any]]] = None,
    media: Optional[Dict[str, Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Convert one API tweet object into the app's social_posts shape.

    Only the fields the app actually uses are kept.
    """
    users = users or {}
    media = media or {}
    author_id = str(item.get("author_id") or "")
    user = users.get(author_id, {})
    username = str(user.get("username") or "").lower()
    metrics = item.get("public_metrics") or {}
    created = item.get("created_at")
    created_iso = to_iso(parse_iso(created)) if created else None

    media_items = []
    for key in ((item.get("attachments") or {}).get("media_keys") or []):
        entry = media.get(str(key))
        if entry:
            media_items.append({
                "type": entry.get("type"),
                "url": entry.get("url") or entry.get("preview_image_url"),
            })

    entities = item.get("entities") or {}
    trimmed_entities = {
        "hashtags": [tag.get("tag") for tag in (entities.get("hashtags") or []) if tag.get("tag")][:10],
        "mentions": [m.get("username") for m in (entities.get("mentions") or []) if m.get("username")][:10],
        "urls": [
            {"expanded_url": url.get("expanded_url"), "title": url.get("title")}
            for url in (entities.get("urls") or [])
        ][:5],
    }

    return {
        "platform": "x",
        "post_id": str(item.get("id") or ""),
        "author_id": author_id or None,
        "username": username or None,
        "display_name": user.get("name"),
        "account_type": normalize_source_type(settings_repo.classify_x_account(username)),
        "account_verified": bool(user.get("verified")),
        "text": item.get("text") or "",
        "lang": item.get("lang"),
        "created_at_source": created_iso,
        "url": f"https://x.com/{username or 'i'}/status/{item.get('id')}",
        "like_count": metrics.get("like_count"),
        "reply_count": metrics.get("reply_count"),
        "repost_count": metrics.get("retweet_count"),
        "quote_count": metrics.get("quote_count"),
        "impression_count": metrics.get("impression_count"),
        "entities_json": trimmed_entities,
        "media_json": media_items,
        "referenced_json": item.get("referenced_tweets"),
        "collected_at": utcnow_iso(),
    }


def get_x_client(client: Optional[HttpClient] = None) -> XClient:
    return XClient(config=get_config(refresh=True).x, client=client)
