"""Find fighters, events and weight classes in article text.

Matching is deliberately conservative: a wrong fighter match would merge two
unrelated stories, which is worse than missing one.  Full names, registered
aliases and nicknames always match; a bare surname only matches when it is
unique in the fighter registry and not a common ambiguous surname.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple

from database.repo_entities import all_events, list_fighters
from utils.textutil import collapse_whitespace, normalize_text
from utils.timeutil import utcnow_iso

# Surnames that belong to several well-known fighters (or ordinary words).
AMBIGUOUS_SURNAMES = {
    "jones", "silva", "santos", "smith", "costa", "pereira", "rodriguez", "lee",
    "hill", "green", "young", "martin", "johnson", "williams", "moreno", "garcia",
    "dos", "de", "da", "van", "page", "brown", "thompson", "white", "king",
    "nunes", "barber", "allen", "dern", "ferguson", "holland", "craig", "burns",
    "yan", "zhang", "song", "silva", "almeida", "oliveira", "andrade", "vera",
}

WEIGHT_CLASSES = [
    "strawweight", "flyweight", "bantamweight", "featherweight", "lightweight",
    "welterweight", "middleweight", "light heavyweight", "heavyweight",
    "catchweight", "pound-for-pound",
]

# Event name patterns, most specific first.
_EVENT_PATTERNS: List[Tuple[re.Pattern, str]] = [
    (re.compile(r"\bufc\s*(\d{2,4})\b", re.IGNORECASE), "UFC {0}"),
    (re.compile(r"\bnoche\s+ufc\b", re.IGNORECASE), "Noche UFC"),
    (re.compile(r"\bufc\s+fight\s+night\s*[:\-]?\s*(\d{1,3})?\b", re.IGNORECASE), "UFC Fight Night"),
    (re.compile(r"\bufc\s+on\s+(espn\+?|abc|fox|fx)\s*(\d{1,3})?\b", re.IGNORECASE), "UFC on {0}"),
    (re.compile(r"\bufc\s+(vegas|apex)\s+(\d{1,3})\b", re.IGNORECASE), "UFC {0} {1}"),
    (re.compile(r"\bdana\s+white'?s?\s+contender\s+series\b", re.IGNORECASE), "Dana White's Contender Series"),
    (re.compile(r"\bthe\s+ultimate\s+fighter\b", re.IGNORECASE), "The Ultimate Fighter"),
    (re.compile(r"\bpower\s+slap\b", re.IGNORECASE), "Power Slap"),
]

_VS_PATTERN = re.compile(
    r"([A-Z][\w'\-\.]+(?:\s+[A-Z][\w'\-\.]+){0,3})\s+(?:vs\.?|versus)\s+([A-Z][\w'\-\.]+(?:\s+[A-Z][\w'\-\.]+){0,3})"
)


@dataclass
class FighterIndex:
    """Lookup table built once per pipeline run (not per article)."""

    by_phrase: Dict[str, str] = field(default_factory=dict)   # normalized phrase -> display name
    surnames: Dict[str, str] = field(default_factory=dict)    # unique, unambiguous surname -> name
    all_surnames: Dict[str, str] = field(default_factory=dict)  # every unique surname, incl. ambiguous
    built_at: str = ""

    @property
    def size(self) -> int:
        return len(set(self.by_phrase.values()))


_INDEX: Optional[FighterIndex] = None


@dataclass
class EventIndex:
    """Every event name the app already knows, for matching in free text.

    The regex patterns above only recognise the shapes UFC currently uses. An
    event the app has already stored - from the official schedule, from a
    merge alias, or from demo data - must be recognised by its own name too,
    otherwise a story whose headline plainly names the event is reported as
    "no event has been named in the collected sources".
    """

    by_phrase: Dict[str, str] = field(default_factory=dict)   # normalized -> display
    built_at: str = ""

    @property
    def size(self) -> int:
        return len(set(self.by_phrase.values()))


_EVENT_INDEX: Optional[EventIndex] = None

#: Shorter than this and a name is too generic to match safely ("UFC").
MIN_EVENT_PHRASE_CHARS = 5


def build_event_index(events: Optional[List[Dict[str, Any]]] = None) -> EventIndex:
    rows = events if events is not None else all_events()
    index = EventIndex(built_at=utcnow_iso())
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        for phrase in (name, row.get("short_name")):
            normalized = normalize_text(str(phrase or ""))
            if len(normalized) >= MIN_EVENT_PHRASE_CHARS:
                index.by_phrase.setdefault(normalized, name)
    return index


def get_event_index(refresh: bool = False) -> EventIndex:
    global _EVENT_INDEX
    if _EVENT_INDEX is None or refresh:
        _EVENT_INDEX = build_event_index()
    return _EVENT_INDEX


def reset_event_index() -> None:
    global _EVENT_INDEX
    _EVENT_INDEX = None


def build_fighter_index(fighters: Optional[List[Dict[str, Any]]] = None) -> FighterIndex:
    """Build the phrase -> fighter lookup from the fighters table."""
    rows = fighters if fighters is not None else list_fighters(limit=5000)
    index = FighterIndex(built_at=utcnow_iso())
    surname_counts: Dict[str, Set[str]] = {}
    for row in rows:
        name = str(row.get("name") or "").strip()
        if not name:
            continue
        phrases = [name, *(row.get("aliases") or [])]
        nickname = row.get("nickname")
        if nickname and len(str(nickname)) >= 4:
            phrases.append(str(nickname))
        for phrase in phrases:
            normalized = normalize_text(phrase)
            if len(normalized) >= 4:
                index.by_phrase.setdefault(normalized, name)
        parts = normalize_text(name).split()
        if len(parts) >= 2:
            surname = parts[-1]
            if len(surname) >= 4:
                surname_counts.setdefault(surname, set()).add(name)
    for surname, owners in surname_counts.items():
        if len(owners) != 1:
            continue
        owner = next(iter(owners))
        index.all_surnames[surname] = owner
        # Surnames shared by several well-known fighters only match when there
        # is other evidence (see _resolve_name's allow_ambiguous path).
        if len(surname) >= 5 and surname not in AMBIGUOUS_SURNAMES:
            index.surnames[surname] = owner
    return index


def get_fighter_index(refresh: bool = False) -> FighterIndex:
    global _INDEX
    if _INDEX is None or refresh:
        _INDEX = build_fighter_index()
    return _INDEX


def reset_indexes() -> None:
    """Forget both cached indexes - called once per pipeline run."""
    reset_fighter_index()
    reset_event_index()


def reset_fighter_index() -> None:

    global _INDEX
    _INDEX = None


#: Words that appear either side of "vs." without naming a person. A matchup
#: pattern alone is not proof of a fighter name, so anything containing one of
#: these is not learned as a fighter.
NON_PERSON_TERMS = {
    "ufc", "fight", "night", "card", "event", "main", "co", "prelims", "title",
    "belt", "champion", "division", "the", "a", "an", "and", "vs", "versus",
    "report", "rumor", "rumour", "breaking", "exclusive", "update", "espn",
    "week", "weekend", "everyone", "everybody", "field", "world",
}


def looks_like_person_name(name: Optional[str]) -> bool:
    """Conservative check before learning a name from a 'A vs. B' headline."""
    parts = [part for part in normalize_text(name or "").split() if part]
    if not (2 <= len(parts) <= 4):
        return False
    return not any(part in NON_PERSON_TERMS for part in parts)


def find_fighters(text: str, index: Optional[FighterIndex] = None, limit: int = 8,
                  include_matchups: bool = True) -> List[str]:
    """Return the fighters mentioned in ``text``, most prominent first.

    ``include_matchups`` also learns both sides of an "A vs. B" headline when
    neither name is in the registry yet. Without it, a story whose headline
    names two fighters reports "no fighter detected in the collected text" for
    as long as those fighters stay unknown - which is how the app ended up
    stating a matchup as fact and denying it had detected the fighters in it,
    on the same screen.
    """
    if not text:
        return []
    index = index or get_fighter_index()
    haystack = f" {normalize_text(text)} "
    found: List[Tuple[int, str]] = []
    seen: Set[str] = set()
    for phrase, name in index.by_phrase.items():
        position = haystack.find(f" {phrase} ")
        if position >= 0 and name not in seen:
            seen.add(name)
            found.append((position, name))
    for surname, name in index.surnames.items():
        if name in seen:
            continue
        position = haystack.find(f" {surname} ")
        if position >= 0:
            seen.add(name)
            found.append((position, name))
    if include_matchups:
        for left, right in find_matchups(text, index):
            for name in (left, right):
                if name in seen or not looks_like_person_name(name):
                    continue
                seen.add(name)
                found.append((max(0, haystack.find(f" {normalize_text(name)} ")), name))
    found.sort(key=lambda pair: pair[0])
    return [name for _, name in found[:limit]]


def find_events(
    text: str,
    known_events: Optional[Iterable[str]] = None,
    index: Optional[EventIndex] = None,
    use_index: bool = True,
) -> List[str]:
    """Return event names mentioned in ``text`` (e.g. ['UFC 320']).

    Three sources, in order: the name patterns, any event names the caller
    already knows about, and every event already stored in the database. The
    last of these is what lets an event the regexes do not recognise still be
    found in a headline that names it.
    """
    if not text:
        return []
    results: List[str] = []
    for pattern, template in _EVENT_PATTERNS:
        for match in pattern.finditer(text):
            groups = [g for g in match.groups() if g]
            try:
                name = template.format(*[g.upper() if len(g) <= 4 else g.title() for g in groups])
            except (IndexError, KeyError):
                name = template
            name = collapse_whitespace(name.replace("{0}", "").replace("{1}", ""))
            if name and name not in results:
                results.append(name)
    haystack = f" {normalize_text(text)} "
    for known in known_events or []:
        if known and normalize_text(known) in haystack and known not in results:
            results.append(known)
    if use_index:
        try:
            index = index or get_event_index()
        except Exception:  # no database available (unit tests on raw text)
            index = EventIndex()
        for phrase, name in index.by_phrase.items():
            if phrase in haystack and name not in results:
                results.append(name)
    return results[:4]


def find_weight_classes(text: str) -> List[str]:
    haystack = normalize_text(text)
    return [wc for wc in WEIGHT_CLASSES if normalize_text(wc) in haystack]


def find_matchups(text: str, index: Optional[FighterIndex] = None) -> List[Tuple[str, str]]:
    """Find 'A vs. B' pairs, resolved to registry names when possible."""
    if not text:
        return []
    index = index or get_fighter_index()
    matchups: List[Tuple[str, str]] = []
    for match in _VS_PATTERN.finditer(text):
        left = _resolve_name(match.group(1), index)
        right = _resolve_name(match.group(2), index)
        # "Jones vs. Aspinall": once one side is a known fighter, a bare
        # surname on the other side is safe enough to resolve.
        if left and not right:
            right = _resolve_name(match.group(2), index, allow_ambiguous_surname=True)
        elif right and not left:
            left = _resolve_name(match.group(1), index, allow_ambiguous_surname=True)
        if left and right and normalize_text(left) != normalize_text(right):
            pair = (left, right)
            if pair not in matchups and (right, left) not in matchups:
                matchups.append(pair)
    return matchups[:4]


def _resolve_name(
    raw: str, index: FighterIndex, allow_ambiguous_surname: bool = False
) -> Optional[str]:
    """Map a captured name fragment onto a registry fighter when we can."""
    cleaned = collapse_whitespace(raw or "").strip(" .,:;-")
    if not cleaned:
        return None
    normalized = normalize_text(cleaned)
    if normalized in index.by_phrase:
        return index.by_phrase[normalized]
    parts = normalized.split()
    if parts and parts[-1] in index.surnames:
        return index.surnames[parts[-1]]
    if allow_ambiguous_surname and parts and parts[-1] in index.all_surnames:
        return index.all_surnames[parts[-1]]
    # Trim leading words that are not part of a name ("Report: Jones vs ...").
    for start in range(1, len(parts)):
        tail = " ".join(parts[start:])
        if tail in index.by_phrase:
            return index.by_phrase[tail]
    if 2 <= len(parts) <= 4 and all(len(p) > 1 for p in parts):
        return " ".join(word.capitalize() for word in cleaned.split())
    return None


def is_title_fight(text: str) -> bool:
    haystack = normalize_text(text)
    return any(
        phrase in haystack
        for phrase in ("title fight", "championship bout", "for the title", "title bout",
                       "undisputed title", "interim title", "championship fight", "belt on the line")
    )
