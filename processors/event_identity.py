"""Canonical event identity.

The same UFC event turns up under many display names:

    "UFC 331"
    "Crypto.com UFC 331"
    "Crypto.com UFC 331: Van vs Pantoja 2"

All three are one event.  Storing them under their display name (which is what
the first version of this app did) created a separate row for each spelling,
which then split that event's card, stories and changes three ways.

This module produces a stable ``canonical_key`` instead, preferring the
strongest identifier available:

    1. the official UFC numeric event id        -> ``ufcid:1234``
    2. the official UFC URL slug                -> ``slug:ufc-331``
    3. a numbered UFC event parsed from a name  -> ``ufc:331``
    4. a dated Fight Night                      -> ``ufcfn:2026-10-31``
    5. a named Fight Night matchup              -> ``ufcfn:silva-vs-costa``
    6. the normalised display name (last resort)-> ``name:...``

Nothing here guesses beyond what the text actually contains: an unrecognisable
name falls through to (6) and simply stays distinct.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from utils.textutil import collapse_whitespace, normalize_text, slugify

# Sponsor prefixes/suffixes UFC attaches to event branding. They change between
# seasons and must never split an event's identity.
_SPONSOR_RE = re.compile(
    r"\b(?:crypto\s*\.?\s*com|noche|riyadh\s+season|abu\s+dhabi\s+showdown\s+week|"
    r"toyo\s+tires|monster\s+energy|dana\s+white\s*'?s?\s+contender\s+series)\b",
    re.IGNORECASE,
)

#: "UFC 331", "UFC331", "UFC  331" - the number is the identity.
_NUMBERED_RE = re.compile(r"\bufc\s*#?\s*(\d{2,4})\b", re.IGNORECASE)

#: URL slugs: /event/ufc-331, /event/ufc-fight-night-october-31-2026
_SLUG_RE = re.compile(r"/event/([a-z0-9\-]+)", re.IGNORECASE)
_SLUG_NUMBERED_RE = re.compile(r"^ufc-(\d{2,4})$", re.IGNORECASE)

_FIGHT_NIGHT_RE = re.compile(r"\bufc\s+fight\s+night\b", re.IGNORECASE)
_ON_ESPN_RE = re.compile(r"\bufc\s+on\s+(espn|abc|fox|fx)\s*\+?\s*(\d+)?\b", re.IGNORECASE)

#: "Van vs Pantoja 2", "Jones vs. Aspinall"
_MATCHUP_RE = re.compile(
    r"([a-zÀ-ɏ'\-\.]+)\s+vs\.?\s+([a-zÀ-ɏ'\-\.]+)(?:\s+(\d))?",
    re.IGNORECASE,
)

_DATE_IN_SLUG_RE = re.compile(
    r"(january|february|march|april|may|june|july|august|september|october|november|december)"
    r"-(\d{1,2})-(\d{4})",
    re.IGNORECASE,
)

_MONTHS = {
    "january": 1, "february": 2, "march": 3, "april": 4, "may": 5, "june": 6,
    "july": 7, "august": 8, "september": 9, "october": 10, "november": 11, "december": 12,
}


def strip_sponsors(name: Optional[str]) -> str:
    """Remove sponsor branding so 'Crypto.com UFC 331' == 'UFC 331'."""
    if not name:
        return ""
    cleaned = _SPONSOR_RE.sub(" ", str(name))
    # A leading "presented by ..." tail carries no identity either.
    cleaned = re.sub(r"\bpresented\s+by\b.*$", " ", cleaned, flags=re.IGNORECASE)
    return collapse_whitespace(cleaned).strip(" -:–—")


def event_number(name: Optional[str]) -> Optional[int]:
    """The numbered-event identity (331 for any spelling of UFC 331)."""
    match = _NUMBERED_RE.search(strip_sponsors(name))
    if not match:
        return None
    number = int(match.group(1))
    # UFC numbered events are 1..999 in practice; anything else is a false hit
    # (a year, a fighter's record, a viewing figure).
    return number if 1 <= number <= 999 else None


def slug_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    match = _SLUG_RE.search(str(url))
    return match.group(1).lower() if match else None


def _fight_night_date_key(slug: Optional[str], event_date: Optional[str]) -> Optional[str]:
    """Fight Nights have no number, so the date is their most stable identity."""
    if slug:
        date_match = _DATE_IN_SLUG_RE.search(slug)
        if date_match:
            month = _MONTHS[date_match.group(1).lower()]
            return f"ufcfn:{int(date_match.group(3)):04d}-{month:02d}-{int(date_match.group(2)):02d}"
    if event_date:
        day = str(event_date)[:10]
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
            return f"ufcfn:{day}"
    return None


def _fight_night_matchup_key(name: str) -> Optional[str]:
    """The headline matchup, which is how articles usually name a Fight Night."""
    match = _MATCHUP_RE.search(name)
    if not match:
        return None
    left, right = sorted([normalize_text(match.group(1)), normalize_text(match.group(2))])
    suffix = f"-{match.group(3)}" if match.group(3) else ""
    return f"ufcfn:{slugify(left + '-vs-' + right)}{suffix}"


def identity_keys(
    name: Optional[str],
    ufc_url: Optional[str] = None,
    official_event_id: Optional[str] = None,
    event_date: Optional[str] = None,
) -> List[str]:
    """Every identifier this event is known by, strongest first.

    One event legitimately answers to several keys - an article says
    "UFC Fight Night: Silva vs Costa" while the official page says
    "october-31-2026".  Returning all of them lets the repository match an
    incoming mention against any key an existing event was already stored
    under, instead of creating a second row (see ``event_aliases``).
    """
    keys: List[str] = []

    def add(key: Optional[str]) -> None:
        if key and key not in keys:
            keys.append(key)

    if official_event_id:
        add(f"ufcid:{str(official_event_id).strip().lower()}")

    cleaned = strip_sponsors(name)
    slug = slug_from_url(ufc_url)

    number = event_number(cleaned)
    if number is None and slug:
        numbered = _SLUG_NUMBERED_RE.match(slug)
        if numbered:
            number = int(numbered.group(1))
    if number is not None:
        add(f"ufc:{number}")

    is_fight_night = bool(_FIGHT_NIGHT_RE.search(cleaned) or _ON_ESPN_RE.search(cleaned)
                          or (slug or "").startswith("ufc-fight-night"))
    if number is None and is_fight_night:
        add(_fight_night_date_key(slug, event_date))
        add(_fight_night_matchup_key(cleaned))

    if number is None and slug:
        add(f"slug:{slug}")

    normalized = normalize_text(cleaned)
    add(f"name:{normalized}" if normalized else "name:unknown")
    return keys


def canonical_key(
    name: Optional[str],
    ufc_url: Optional[str] = None,
    official_event_id: Optional[str] = None,
    event_date: Optional[str] = None,
) -> str:
    """The single strongest identity for an event."""
    return identity_keys(name, ufc_url, official_event_id, event_date)[0]


def display_name_rank(name: Optional[str], data_origin: str = "detected") -> tuple:
    """Sort key picking the *best* display name among duplicates.

    Official/collected names win; among equals the fuller name (the one that
    names the main event) wins, because it is the more useful label.
    """
    text = str(name or "")
    origin_rank = {"collected": 3, "official": 3, "user": 2}.get(data_origin, 1)
    has_matchup = 1 if _MATCHUP_RE.search(strip_sponsors(text)) else 0
    return (origin_rank, has_matchup, len(text))
