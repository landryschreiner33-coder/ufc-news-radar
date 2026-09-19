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
        schedule = _parse_schedule(card)
        location_el = card.select_one(".c-card-event--result__location, [class*='__location'], .field--name-taxonomy-term-title")
        location = collapse_whitespace(location_el.get_text(" ", strip=True)) if location_el else None
        events.append({
            "name": name,
            "headline": headline,
            "event_date": schedule["event_date"],
            "scheduled_start_utc": schedule["scheduled_start_utc"],
            "scheduled_end_utc": schedule["scheduled_end_utc"],
            "local_timezone": schedule["local_timezone"],
            "location": location,
            "city": _city_from_location(location),
            "official_event_id": _official_id(card),
            "ufc_url": _absolute(href, base_url),
            "official_source_url": _absolute(href, base_url),
            "image_url": _card_image(card),
            "data_origin": "official",
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
            "scheduled_start_utc": None,
            "scheduled_end_utc": None,
            "local_timezone": None,
            "location": None,
            "city": None,
            "official_event_id": None,
            "ufc_url": _absolute(href, base_url),
            "official_source_url": _absolute(href, base_url),
            "image_url": None,
            "data_origin": "official",
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


# --------------------------------------------------------------- schedule --
#: UFC publishes each segment's start as a unix timestamp. The earliest one is
#: when the event actually begins; the main card start is the headline time.
_TIMESTAMP_ATTRS = (
    "data-early-prelims-card-timestamp",
    "data-prelims-card-timestamp",
    "data-main-card-timestamp",
)

#: Broadcast abbreviations -> IANA zones, so a local start time can be shown
#: in the venue's own timezone rather than silently in UTC.
_TZ_ABBREVIATIONS = {
    "ET": "America/New_York", "EDT": "America/New_York", "EST": "America/New_York",
    "CT": "America/Chicago", "CDT": "America/Chicago", "CST": "America/Chicago",
    "MT": "America/Denver", "MDT": "America/Denver", "MST": "America/Denver",
    "PT": "America/Los_Angeles", "PDT": "America/Los_Angeles", "PST": "America/Los_Angeles",
    "BST": "Europe/London", "GMT": "Europe/London", "CET": "Europe/Paris",
    "CEST": "Europe/Paris", "AEST": "Australia/Sydney", "AEDT": "Australia/Sydney",
    "GST": "Asia/Dubai", "AST": "Asia/Riyadh", "SGT": "Asia/Singapore",
    "JST": "Asia/Tokyo", "BRT": "America/Sao_Paulo",
}

_TZ_RE = re.compile(r"\b(" + "|".join(sorted(_TZ_ABBREVIATIONS, key=len, reverse=True)) + r")\b")

#: A UFC card runs roughly seven hours from the first prelim.
_EVENT_DURATION_HOURS = 7


def _parse_schedule(card: Any) -> Dict[str, Any]:
    """Start/end/timezone/date from the event card's own timestamps.

    Falls back to the printed date when no timestamp is published; in that case
    ``scheduled_start_utc`` stays None and the lifecycle refuses to guess
    whether the event has started.
    """
    from datetime import timedelta

    starts: List[str] = []
    for element in card.select("[" + "], [".join(_TIMESTAMP_ATTRS) + "]"):
        for attribute in _TIMESTAMP_ATTRS:
            value = element.get(attribute)
            if value:
                parsed = _from_timestamp(value)
                if parsed:
                    starts.append(parsed)
    date_el = card.select_one(
        "[data-main-card-timestamp], .c-card-event--result__date, [class*='__date']")
    text = date_el.get_text(" ", strip=True) if date_el is not None else ""

    start = min(starts) if starts else None
    end = None
    if start:
        parsed = parse_iso(start)
        if parsed:
            end = to_iso(parsed + timedelta(hours=_EVENT_DURATION_HOURS))

    event_date = None
    if start:
        event_date = start[:10]
    elif text:
        printed = _from_text(text)
        event_date = printed[:10] if printed else None

    zone = None
    match = _TZ_RE.search(text or "")
    if match:
        zone = _TZ_ABBREVIATIONS.get(match.group(1))

    return {
        "event_date": event_date,
        "scheduled_start_utc": start,
        "scheduled_end_utc": end,
        "local_timezone": zone,
    }


def _official_id(card: Any) -> Optional[str]:
    """UFC's own numeric id when the markup exposes it."""
    for attribute in ("data-event-id", "data-nid", "data-node-id"):
        value = card.get(attribute)
        if value:
            return str(value).strip()
    node = card.select_one("[data-event-id], [data-nid]")
    if node is not None:
        return str(node.get("data-event-id") or node.get("data-nid") or "").strip() or None
    return None


def _card_image(card: Any) -> Optional[str]:
    image = card.select_one("img[src], img[data-src]")
    if image is None:
        return None
    source = image.get("src") or image.get("data-src") or ""
    if not source:
        return None
    return source if source.startswith("http") else "https://www.ufc.com" + source


def _city_from_location(location: Optional[str]) -> Optional[str]:
    """'T-Mobile Arena, Las Vegas, Nevada' -> 'Las Vegas, Nevada'."""
    if not location:
        return None
    parts = [part.strip() for part in str(location).split(",") if part.strip()]
    if len(parts) >= 3:
        return ", ".join(parts[1:])
    if len(parts) == 2:
        return parts[1]
    return parts[0] if parts else None
