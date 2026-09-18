"""Official UFC event schedule collector (names, dates, locations).

Like the rankings collector this one never guesses: if the page cannot be
parsed it reports a parse error instead of inventing events.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from collectors.base import BaseCollector, CollectorError
from utils.logging_setup import get_logger
from utils.textutil import collapse_whitespace
from utils.timeutil import parse_iso, to_iso, utcnow_iso

logger = get_logger(__name__)

_EVENT_SLUG_RE = re.compile(r"/event/([a-z0-9\-]+)", re.IGNORECASE)
_NUMBERED_RE = re.compile(r"^ufc-(\d{2,4})$", re.IGNORECASE)
_FIGHT_NIGHT_RE = re.compile(r"^ufc-fight-night-([a-z0-9\-]+)$", re.IGNORECASE)


class UFCEventsCollector(BaseCollector):
    adapter = "ufc_events"
    kind = "events"

    def collect(self) -> List[Any]:
        url = self.source.get("feed_url") or "https://www.ufc.com/events"
        response = self.client.get(url)
        if not response.ok:
            raise CollectorError(response.error or f"HTTP {response.status_code}",
                                 kind=response.error_kind or "http")
        self.resolved_url = url
        try:
            soup = BeautifulSoup(response.text, "lxml")
        except Exception:
            soup = BeautifulSoup(response.text, "html.parser")

        events = _parse_event_cards(soup, url)
        if not events:
            events = _parse_event_links(soup, url)
        if not events:
            raise CollectorError(
                "no events found - the UFC events page layout may have changed", kind="parse"
            )
        return events

    def normalize(self, raw: Any) -> Optional[Dict[str, Any]]:
        return raw if isinstance(raw, dict) else None

    def validate(self, item: Dict[str, Any]) -> bool:
        return bool(item.get("name"))


def _parse_event_cards(soup: BeautifulSoup, base_url: str) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    cards = soup.select("div.c-card-event--result, [class*='c-card-event']")
    for card in cards:
        link = card.select_one("h3 a, a[href*='/event/']")
        if link is None:
            continue
        href = link.get("href") or ""
        headline = collapse_whitespace(link.get_text(" ", strip=True))
        slug = _slug_from_href(href)
        name = _event_name(slug, headline)
        if not name:
            continue
        date_el = card.select_one("[data-main-card-timestamp], .c-card-event--result__date, [class*='__date']")
        event_date = None
        if date_el is not None:
            timestamp = date_el.get("data-main-card-timestamp") or date_el.get("data-prelims-card-timestamp")
            if timestamp:
                event_date = _from_timestamp(timestamp)
            if not event_date:
                event_date = _from_text(date_el.get_text(" ", strip=True))
        location_el = card.select_one(".c-card-event--result__location, [class*='__location'], .field--name-taxonomy-term-title")
        location = collapse_whitespace(location_el.get_text(" ", strip=True)) if location_el else None
        events.append({
            "name": name,
            "headline": headline,
            "event_date": event_date,
            "location": location,
            "ufc_url": _absolute(href, base_url),
            "data_origin": "collected",
            "collected_at": utcnow_iso(),
        })
    return _dedupe(events)


def _parse_event_links(soup: BeautifulSoup, base_url: str) -> List[Dict[str, Any]]:
    """Fallback: any link that points at an event page."""
    events: List[Dict[str, Any]] = []
    for link in soup.select("a[href*='/event/']"):
        href = link.get("href") or ""
        slug = _slug_from_href(href)
        headline = collapse_whitespace(link.get_text(" ", strip=True))
        name = _event_name(slug, headline)
        if not name:
            continue
        container_text = ""
        parent = link.find_parent(["div", "li", "article", "section"])
        if parent is not None:
            container_text = collapse_whitespace(parent.get_text(" ", strip=True))[:300]
        events.append({
            "name": name,
            "headline": headline,
            "event_date": _from_text(container_text),
            "location": None,
            "ufc_url": _absolute(href, base_url),
            "data_origin": "collected",
            "collected_at": utcnow_iso(),
        })
    return _dedupe(events)


def _slug_from_href(href: str) -> str:
    match = _EVENT_SLUG_RE.search(href or "")
    return match.group(1).lower() if match else ""


def _event_name(slug: str, headline: str) -> str:
    """Build a stable event name such as 'UFC 320: Jones vs. Aspinall'."""
    label = ""
    numbered = _NUMBERED_RE.match(slug)
    if numbered:
        label = f"UFC {numbered.group(1)}"
    elif slug.startswith("ufc-fight-night"):
        label = "UFC Fight Night"
    elif slug:
        label = collapse_whitespace(slug.replace("-", " ").upper())
    if label and headline and headline.lower() not in label.lower():
        return f"{label}: {headline}"
    return label or headline


def _from_timestamp(value: Any) -> Optional[str]:
    try:
        from datetime import datetime, timezone

        return to_iso(datetime.fromtimestamp(int(str(value).strip()), tz=timezone.utc))
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _from_text(text: str) -> Optional[str]:
    if not text:
        return None
    patterns = [
        r"([A-Z][a-z]{2,8}\s+\d{1,2},\s+\d{4})",
        r"(\d{4}-\d{2}-\d{2})",
        r"(\w{3},\s+\w{3}\s+\d{1,2})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            parsed = parse_iso(match.group(1))
            if parsed:
                return to_iso(parsed)
    return None


def _absolute(href: str, base_url: str) -> str:
    if not href:
        return base_url
    if href.startswith("http"):
        return href
    return "https://www.ufc.com" + (href if href.startswith("/") else f"/{href}")


def _dedupe(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen, output = set(), []
    for event in events:
        key = event["name"].lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(event)
    return output
