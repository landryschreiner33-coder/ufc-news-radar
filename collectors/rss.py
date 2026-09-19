"""RSS/Atom collector.

Works for every feed-based source in the registry.  Tries the configured feed
URL first, then any fallback URLs, and remembers which one worked so the next
run starts with it.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import feedparser

from collectors.base import BaseCollector, CollectorError
from database import repo_settings as settings_repo
from database.repo_sources import candidate_urls
from models.types import SourceType, normalize_source_type, reliability_for
from utils.logging_setup import get_logger
from utils.textutil import (
    canonical_url,
    clean_title,
    collapse_whitespace,
    domain_of,
    excerpt_from,
    normalize_text,
    strip_html,
)
from utils.timeutil import parse_iso, sanitize_published_at, struct_time_to_iso, to_iso, utcnow_iso

logger = get_logger(__name__)

# Aggregator feeds carry other publishers' work; their items are re-classified
# using the publisher the item actually came from.
AGGREGATOR_GROUPS = {"aggregator"}

MMA_KEYWORDS = (
    "ufc", "mma", "mixed martial arts", "octagon", "dana white", "bellator",
    "pfl", "one championship", "fight night", "welterweight", "heavyweight",
    "lightweight", "featherweight", "bantamweight", "flyweight", "middleweight",
    "strawweight", "dfw", "contender series",
)


class RSSCollector(BaseCollector):
    adapter = "rss"
    kind = "articles"

    def collect(self) -> List[Any]:
        urls = candidate_urls(self.source)
        if not urls:
            raise CollectorError("no feed URL configured", kind="config")
        last_error = "no attempt made"
        last_kind = "unknown"
        for url in urls:
            response = self.client.get_conditional(url, cache_key=f"feed::{self.source.get('key')}")
            if response.not_modified:
                # Nothing new since the last run - that is a success with 0 items.
                self.resolved_url = url
                logger.debug("%s: feed unchanged (HTTP 304)", self.source.get("key"))
                return []
            if not response.ok:
                last_error = response.error or f"HTTP {response.status_code}"
                last_kind = response.error_kind or "http"
                continue
            parsed = feedparser.parse(response.content or response.text)
            entries = list(getattr(parsed, "entries", []) or [])
            if not entries:
                # bozo means malformed XML; without entries we treat it as a failure
                bozo_error = getattr(parsed, "bozo_exception", None)
                last_error = f"feed had no items ({bozo_error})" if bozo_error else "feed had no items"
                last_kind = "parse"
                continue
            self.resolved_url = url
            self._feed_title = collapse_whitespace(
                getattr(getattr(parsed, "feed", None), "get", lambda *_: "")("title") or ""
            )
            return entries
        raise CollectorError(last_error, kind=last_kind)

    # ------------------------------------------------------------ normalize --
    def normalize(self, raw: Any) -> Optional[Dict[str, Any]]:
        entry = raw
        link = _first_link(entry)
        title = clean_title(entry.get("title") if hasattr(entry, "get") else None)
        if not link or not title:
            return None

        published = (
            struct_time_to_iso(entry.get("published_parsed"))
            or struct_time_to_iso(entry.get("updated_parsed"))
            or struct_time_to_iso(entry.get("created_parsed"))
        )
        summary_text = strip_html(
            entry.get("summary")
            or (entry.get("content")[0].get("value") if entry.get("content") else "")
            or entry.get("description")
            or ""
        )
        author = _author_of(entry)
        image_url = _image_of(entry)
        keywords = [
            collapse_whitespace(tag.get("term", ""))
            for tag in (entry.get("tags") or [])
            if isinstance(tag, dict) and tag.get("term")
        ]

        collected = utcnow_iso()
        item: Dict[str, Any] = {
            "source_id": self.source.get("id"),
            "source_key": self.source.get("key"),
            "source_name": self.source.get("name"),
            "external_id": entry.get("id") or entry.get("guid") or None,
            "url": link,
            "domain": domain_of(canonical_url(link)),
            "title": title,
            "author": author,
            # A feed date in the future would pin the item to the top of the
            # feed forever; one from 1970 would make it look ancient. Both
            # fall back to when we actually collected it.
            "published_at": sanitize_published_at(published, collected) or collected,
            "updated_at_source": _updated_at(entry),
            "excerpt": excerpt_from(summary_text, 320),
            "content_snippet": summary_text[:1200] if summary_text else "",
            "content_chars": len(summary_text or ""),
            "image_url": image_url,
            "keywords": keywords[:12],
            "language": (entry.get("language") or None),
            "source_type": self.source.get("source_type", SourceType.UNKNOWN.value),
            "reliability_weight": self.source.get("reliability_weight"),
            "independence_group": self.source.get("independence_group"),
            "is_official": normalize_source_type(self.source.get("source_type")) == SourceType.OFFICIAL.value,
            "collected_at": collected,
        }
        if self.source.get("independence_group") in AGGREGATOR_GROUPS:
            _apply_publisher(item, entry)
        return item

    def validate(self, item: Dict[str, Any]) -> bool:
        if not super().validate(item):
            return False
        if self.source.get("independence_group") in AGGREGATOR_GROUPS or self.source.get("key") == "reddit_mma":
            # Broad feeds can return unrelated sport news - keep MMA items only.
            haystack = normalize_text(f"{item.get('title','')} {item.get('excerpt','')}")
            if not any(keyword in haystack for keyword in MMA_KEYWORDS):
                return False
        return True


def _first_link(entry: Any) -> str:
    link = entry.get("link") if hasattr(entry, "get") else None
    if link:
        return str(link).strip()
    for candidate in entry.get("links") or []:
        if isinstance(candidate, dict) and candidate.get("href"):
            return str(candidate["href"]).strip()
    return ""


def _author_of(entry: Any) -> Optional[str]:
    author = entry.get("author")
    if not author and entry.get("authors"):
        first = entry["authors"][0]
        author = first.get("name") if isinstance(first, dict) else str(first)
    if not author:
        author = entry.get("dc_creator") or entry.get("creator")
    author = collapse_whitespace(strip_html(author)) if author else None
    if author and author.lower().startswith("by "):
        author = author[3:].strip()
    return author or None


def _image_of(entry: Any) -> Optional[str]:
    for media in entry.get("media_content") or []:
        if isinstance(media, dict) and media.get("url"):
            return media["url"]
    for media in entry.get("media_thumbnail") or []:
        if isinstance(media, dict) and media.get("url"):
            return media["url"]
    for enclosure in entry.get("enclosures") or []:
        if isinstance(enclosure, dict) and str(enclosure.get("type", "")).startswith("image"):
            return enclosure.get("href") or enclosure.get("url")
    return None


def _apply_publisher(item: Dict[str, Any], entry: Any) -> None:
    """For aggregator feeds, re-classify the item using its real publisher.

    Google News items carry a <source url="..."> element naming the outlet that
    actually published the story.  Using it means a syndicated copy is scored
    (and counted for independence) as the original publisher, not as 'Google'.
    """
    source_info = entry.get("source") if hasattr(entry, "get") else None
    publisher_name, publisher_href = None, None
    if isinstance(source_info, dict):
        publisher_name = collapse_whitespace(source_info.get("title") or "")
        publisher_href = source_info.get("href") or source_info.get("url")
    publisher_domain = domain_of(publisher_href) or domain_of(item.get("url"))
    if publisher_domain and publisher_domain != "news.google.com":
        item["domain"] = publisher_domain
    if publisher_name:
        item["source_name"] = f"{publisher_name} (via Google News)"
        item["publisher_name"] = publisher_name
    classification = settings_repo.classify_domain(item.get("domain"))
    if classification:
        item["source_type"] = classification["classification"]
        item["reliability_weight"] = classification["reliability_weight"]
        item["independence_group"] = classification["independence_group"] or item.get("domain")
        item["is_official"] = classification["classification"] == SourceType.OFFICIAL.value
    else:
        item["source_type"] = SourceType.UNKNOWN.value
        item["reliability_weight"] = reliability_for(SourceType.UNKNOWN.value)
        # Unknown publishers are independent of each other, so group by domain.
        item["independence_group"] = item.get("domain") or "aggregator"
        item["is_official"] = False


def _updated_at(entry: Any) -> Optional[str]:
    """The publisher's own 'last updated' stamp, kept separate from published_at.

    An outlet quietly rewriting a story is itself a signal, so the two dates are
    never collapsed into one.
    """
    for key in ("updated_parsed", "modified_parsed"):
        value = entry.get(key)
        if value:
            return struct_time_to_iso(value)
    for key in ("updated", "modified"):
        value = entry.get(key)
        if value:
            parsed = parse_iso(value)
            if parsed:
                return to_iso(parsed)
    return None
