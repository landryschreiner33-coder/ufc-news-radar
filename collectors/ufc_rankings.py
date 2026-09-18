"""Official UFC rankings collector.

Two things matter here:

1. **No assumption about the ranking method.**  UFC has published rankings
   under more than one system, so this collector *reads* whatever system label
   and date the page shows and stores it with the snapshot
   (``system_name`` / ``system_version``).  If no label can be found the
   snapshot records that fact instead of inventing one.
2. **No invented rows.**  If the page layout changes and nothing can be
   parsed, the collector fails loudly (the Source Health panel shows the
   error) rather than producing partial or guessed rankings.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

from bs4 import BeautifulSoup

from collectors.base import BaseCollector, CollectorError
from utils.logging_setup import get_logger
from utils.textutil import collapse_whitespace, normalize_text
from utils.timeutil import parse_iso, utcnow_iso

logger = get_logger(__name__)

# Division names UFC publishes. Used to recognise headings in a layout-agnostic
# way; unknown headings are still captured, they just are not normalised.
KNOWN_DIVISIONS = [
    "Men's Pound-for-Pound", "Women's Pound-for-Pound", "Pound-for-Pound",
    "Flyweight", "Bantamweight", "Featherweight", "Lightweight", "Welterweight",
    "Middleweight", "Light Heavyweight", "Heavyweight",
    "Women's Strawweight", "Women's Flyweight", "Women's Bantamweight",
    "Women's Featherweight", "Strawweight",
]

_SYSTEM_PATTERNS: List[Tuple[str, str]] = [
    (r"meta\s+ufc\s+rankings?", "Meta UFC Rankings"),
    (r"ufc\s+rankings?\s+presented\s+by\s+([\w\s]+)", "UFC Rankings (presented by)"),
    (r"official\s+ufc\s+rankings?", "Official UFC Rankings"),
    (r"ufc\s+rankings?", "UFC Rankings"),
]

_DATE_PATTERNS = [
    r"(?:as of|updated|last updated)[:\s]+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
    r"(?:as of|updated|last updated)[:\s]+(\d{4}-\d{2}-\d{2})",
    r"rankings?\s+(?:as of|for)\s+([A-Z][a-z]+\s+\d{1,2},\s+\d{4})",
]

_RANK_ROW_RE = re.compile(r"^\s*(\d{1,2})\s*[.\)]?\s+(.{3,60})$")


class UFCRankingsCollector(BaseCollector):
    adapter = "ufc_rankings"
    kind = "rankings"

    def collect(self) -> List[Any]:
        url = self.source.get("feed_url") or "https://www.ufc.com/rankings"
        response = self.client.get(url)
        if not response.ok:
            raise CollectorError(response.error or f"HTTP {response.status_code}",
                                 kind=response.error_kind or "http")
        self.resolved_url = url
        try:
            soup = BeautifulSoup(response.text, "lxml")
        except Exception:
            soup = BeautifulSoup(response.text, "html.parser")

        page_text = soup.get_text(" ", strip=True)
        system_name, system_version = detect_ranking_system(page_text)
        ranking_date = detect_ranking_date(page_text) or utcnow_iso()[:10]

        rows = parse_ranking_groups(soup, url)
        if not rows:
            raise CollectorError(
                "no ranking rows found - the UFC rankings page layout may have changed",
                kind="parse",
            )
        for row in rows:
            row.update({
                "system_name": system_name,
                "system_version": system_version,
                "ranking_date": ranking_date,
                "source_url": url,
            })
        self.payload = {
            "system_name": system_name,
            "system_version": system_version,
            "ranking_date": ranking_date,
        }
        return rows

    def normalize(self, raw: Any) -> Optional[Dict[str, Any]]:
        return raw if isinstance(raw, dict) else None

    def validate(self, item: Dict[str, Any]) -> bool:
        return bool(item.get("fighter_name")) and bool(item.get("division"))

    def run(self):  # type: ignore[override]
        result = super().run()
        result.payload = getattr(self, "payload", {})
        return result


# ------------------------------------------------------------- parsing ------
def detect_ranking_system(page_text: str) -> Tuple[str, str]:
    """Read the ranking system label off the page instead of assuming one.

    Returns (system_name, system_version).  ``system_version`` keeps the exact
    phrase found so a later change of system is visible in the database.
    """
    haystack = collapse_whitespace(page_text or "")
    lowered = haystack.lower()
    for pattern, label in _SYSTEM_PATTERNS:
        match = re.search(pattern, lowered)
        if match:
            start = max(0, match.start() - 40)
            context = collapse_whitespace(haystack[start:match.end() + 60])
            return label, context[:160]
    return "UFC Rankings (system label not found on page)", "unlabelled"


def detect_ranking_date(page_text: str) -> Optional[str]:
    haystack = collapse_whitespace(page_text or "")
    for pattern in _DATE_PATTERNS:
        match = re.search(pattern, haystack, re.IGNORECASE)
        if match:
            parsed = parse_iso(match.group(1))
            if parsed:
                return parsed.strftime("%Y-%m-%d")
    return None


def parse_ranking_groups(soup: BeautifulSoup, source_url: str) -> List[Dict[str, Any]]:
    """Extract (division, fighter, position, champion) rows.

    Tries the structured UFC.com markup first and falls back to a generic
    heading + list scan so a layout tweak does not silently break collection.
    """
    rows = _parse_structured(soup)
    if rows:
        return rows
    return _parse_generic(soup)


def _parse_structured(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    groups = soup.select("div.view-grouping") or soup.select("[class*='view-grouping']")
    for group in groups:
        header = group.select_one("div.view-grouping-header, .view-grouping-header, h3, h4")
        division = collapse_whitespace(header.get_text(" ", strip=True)) if header else ""
        division = clean_division(division)
        if not division:
            continue
        champion_el = group.select_one(
            "div.rankings--athlete--champion h5, .rankings--athlete--champion h5, "
            ".rankings--athlete--champion .views-field-title"
        )
        if champion_el:
            champion_name = collapse_whitespace(champion_el.get_text(" ", strip=True))
            if champion_name:
                rows.append({
                    "division": division, "fighter_name": champion_name,
                    "position": 0, "is_champion": True,
                })
        for row_el in group.select("tbody tr, .views-row"):
            rank_el = row_el.select_one(
                "td.views-field-weight-class-rank, .views-field-weight-class-rank, .rank"
            )
            name_el = row_el.select_one("td.views-field-title a, .views-field-title a, td a, a")
            name = collapse_whitespace(name_el.get_text(" ", strip=True)) if name_el else ""
            rank_text = collapse_whitespace(rank_el.get_text(" ", strip=True)) if rank_el else ""
            if not name:
                continue
            position = _to_int(rank_text)
            if position is None:
                continue
            rows.append({
                "division": division, "fighter_name": name,
                "position": position, "is_champion": False,
            })
    return _dedupe(rows)


def _parse_generic(soup: BeautifulSoup) -> List[Dict[str, Any]]:
    """Fallback: walk headings that name a division and read the list under them."""
    rows: List[Dict[str, Any]] = []
    headings = soup.find_all(["h1", "h2", "h3", "h4", "h5"])
    for heading in headings:
        division = clean_division(collapse_whitespace(heading.get_text(" ", strip=True)))
        if not division:
            continue
        collected: List[Dict[str, Any]] = []
        for sibling in heading.find_all_next():
            if sibling.name in ("h1", "h2", "h3", "h4", "h5") and sibling is not heading:
                break
            if sibling.name in ("li", "tr", "p", "div") and not sibling.find(["li", "tr"]):
                text = collapse_whitespace(sibling.get_text(" ", strip=True))
                if not text or len(text) > 80:
                    continue
                if re.match(r"^(champion|c)\b", text, re.IGNORECASE):
                    name = collapse_whitespace(re.sub(r"^(champion|c)\b[:\-\s]*", "", text,
                                                      flags=re.IGNORECASE))
                    if name:
                        collected.append({"division": division, "fighter_name": name,
                                          "position": 0, "is_champion": True})
                    continue
                match = _RANK_ROW_RE.match(text)
                if match:
                    collected.append({
                        "division": division,
                        "fighter_name": collapse_whitespace(match.group(2)),
                        "position": int(match.group(1)),
                        "is_champion": False,
                    })
            if len(collected) >= 16:
                break
        if len(collected) >= 3:  # a real division listing, not a stray heading
            rows.extend(collected)
    return _dedupe(rows)


def clean_division(text: str) -> str:
    """Normalise a heading to a division name, or '' if it is not one."""
    if not text:
        return ""
    cleaned = collapse_whitespace(re.sub(r"(top\s+rank(ings)?|rankings?|division)", "", text,
                                         flags=re.IGNORECASE))
    cleaned = cleaned.strip(" -–—:")
    normalized = normalize_text(cleaned)
    for division in KNOWN_DIVISIONS:
        if normalize_text(division) == normalized:
            return division
    for division in KNOWN_DIVISIONS:
        if normalize_text(division) in normalized and len(normalized) < 40:
            return division
    return ""


def _to_int(value: str) -> Optional[int]:
    match = re.search(r"\d{1,2}", value or "")
    return int(match.group(0)) if match else None


def _dedupe(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    seen = set()
    output = []
    for row in rows:
        key = (row["division"], normalize_text(row["fighter_name"]))
        if key in seen:
            continue
        seen.add(key)
        output.append(row)
    return output
