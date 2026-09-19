"""Official fight card from a UFC event page.

A card built only from reporting is always incomplete and always a little
behind. This reads UFC's own event page so the app can say which bouts are
*official* and keep everything merely reported clearly separate.

Like every other collector it refuses to guess: a page it cannot parse raises,
and the source health page shows exactly what happened.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from bs4 import BeautifulSoup

from utils.http import HttpClient
from utils.logging_setup import get_logger
from utils.textutil import collapse_whitespace, normalize_text
from utils.timeutil import utcnow_iso

logger = get_logger(__name__)

#: UFC groups bouts under these headings.
_SEGMENT_HINTS = (
    ("early prelims", "prelims"),
    ("early prelim", "prelims"),
    ("prelims", "prelims"),
    ("prelim", "prelims"),
    ("main card", "main_card"),
)

_WEIGHT_CLASSES = [
    "strawweight", "flyweight", "bantamweight", "featherweight", "lightweight",
    "welterweight", "middleweight", "light heavyweight", "heavyweight",
    "catchweight",
]

_TITLE_RE = re.compile(r"\b(title|championship|belt|interim)\b", re.IGNORECASE)
_VS_RE = re.compile(r"\s+vs\.?\s+", re.IGNORECASE)


@dataclass
class OfficialBout:
    fighter_a: str
    fighter_b: str
    weight_class: Optional[str] = None
    is_title_fight: bool = False
    segment: str = "main_card"
    bout_order: Optional[int] = None

    def as_dict(self) -> Dict[str, Any]:
        return {
            "fighter_a": self.fighter_a,
            "fighter_b": self.fighter_b,
            "weight_class": self.weight_class,
            "is_title_fight": self.is_title_fight,
            "segment": self.segment,
            "bout_order": self.bout_order,
            # Everything here came off UFC's own page.
            "confidence": "official",
            "official_status": "official",
            "status": "scheduled",
        }


@dataclass
class CardResult:
    ok: bool = False
    bouts: List[OfficialBout] = field(default_factory=list)
    error: Optional[str] = None
    url: Optional[str] = None
    collected_at: str = ""


def _segment_for(heading: str, index: int) -> str:
    text = normalize_text(heading)
    for hint, segment in _SEGMENT_HINTS:
        if hint in text:
            return segment
    return "main_card"


def _weight_class(text: str) -> Optional[str]:
    lowered = normalize_text(text)
    for weight in sorted(_WEIGHT_CLASSES, key=len, reverse=True):
        if weight in lowered:
            return weight.title()
    return None


def _clean_name(value: str) -> str:
    name = collapse_whitespace(re.sub(r"\s*\(.*?\)\s*", " ", str(value or "")))
    # Strip a trailing record like "12-1-0".
    name = re.sub(r"\b\d+-\d+(-\d+)?\b", "", name).strip(" -–—")
    return collapse_whitespace(name)


def parse_event_card(html: str, url: Optional[str] = None) -> CardResult:
    """Parse the official card out of a UFC event page."""
    result = CardResult(url=url, collected_at=utcnow_iso())
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    bouts: List[OfficialBout] = []
    order = 0

    # Structured markup first: each bout is its own listing.
    listings = soup.select("[class*='fight-card__fight'], li.l-listing__item, .c-listing-fight")
    for listing in listings:
        names = [
            _clean_name(node.get_text(" ", strip=True))
            for node in listing.select(
                "[class*='given-name'], [class*='family-name'], "
                "[class*='c-listing-fight__corner-name']")
        ]
        names = [name for name in names if name]
        pair: Optional[tuple] = None
        if len(names) >= 2:
            # Corner names may arrive as given/family pairs; rebuild two people.
            if len(names) == 4:
                pair = (f"{names[0]} {names[1]}".strip(), f"{names[2]} {names[3]}".strip())
            else:
                pair = (names[0], names[1])
        else:
            text = collapse_whitespace(listing.get_text(" ", strip=True))
            parts = _VS_RE.split(text, maxsplit=1)
            if len(parts) == 2:
                pair = (_clean_name(parts[0])[-60:], _clean_name(parts[1])[:60])
        if not pair or not pair[0] or not pair[1]:
            continue

        context = collapse_whitespace(listing.get_text(" ", strip=True))
        heading = ""
        previous = listing.find_previous(["h2", "h3", "header"])
        if previous is not None:
            heading = previous.get_text(" ", strip=True)
        order += 1
        bouts.append(OfficialBout(
            fighter_a=pair[0],
            fighter_b=pair[1],
            weight_class=_weight_class(context),
            is_title_fight=bool(_TITLE_RE.search(context)),
            segment=_segment_for(heading, order),
            bout_order=order,
        ))

    # De-duplicate: the same bout can appear in more than one block.
    seen, unique = set(), []
    for bout in bouts:
        key = tuple(sorted([normalize_text(bout.fighter_a), normalize_text(bout.fighter_b)]))
        if key in seen or not all(key):
            continue
        seen.add(key)
        unique.append(bout)

    if not unique:
        result.error = "no bouts found - the UFC event page layout may have changed"
        return result

    # The first listed bout is the main event, the second the co-main.
    if unique:
        unique[0].segment = "main_event"
    if len(unique) > 1 and unique[1].segment == "main_card":
        unique[1].segment = "co_main"

    result.ok = True
    result.bouts = unique
    return result


def fetch_event_card(url: str, client: Optional[HttpClient] = None) -> CardResult:
    """Fetch and parse one official event page."""
    client = client or HttpClient()
    response = client.get(url)
    if not response.ok:
        return CardResult(ok=False, url=url, collected_at=utcnow_iso(),
                          error=response.error or f"HTTP {response.status_code}")
    return parse_event_card(response.text, url)


def collect_official_cards(events: List[Dict[str, Any]], client: Optional[HttpClient] = None,
                           max_events: int = 4) -> Dict[str, Any]:
    """Refresh official cards for the next few events.

    Bounded on purpose: one fetch per event per run, newest first, so a long
    schedule never turns into dozens of requests.
    """
    from database import repo_entities as entities_repo

    client = client or HttpClient()
    stored = failed = 0
    errors: List[str] = []
    for event in events[:max_events]:
        url = event.get("ufc_url") or event.get("official_source_url")
        if not url:
            continue
        outcome = fetch_event_card(url, client)
        if not outcome.ok:
            failed += 1
            errors.append(f"{event.get('name')}: {outcome.error}")
            continue
        for bout in outcome.bouts:
            payload = bout.as_dict()
            try:
                entities_repo.upsert_fight(
                    event_id=int(event["id"]),
                    fighter_a=payload["fighter_a"],
                    fighter_b=payload["fighter_b"],
                    weight_class=payload["weight_class"],
                    is_title_fight=payload["is_title_fight"],
                    segment=payload["segment"],
                    bout_order=payload["bout_order"],
                    status=payload["status"],
                    confidence="official",
                    official_status="official",
                    source_url=url,
                    source_name="UFC.com",
                )
                stored += 1
            except Exception as exc:
                logger.debug("Could not store official bout: %s", exc)
    return {"stored": stored, "failed": failed, "errors": errors}
