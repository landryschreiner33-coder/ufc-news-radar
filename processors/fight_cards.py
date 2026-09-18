"""Fight-card tracking: build event cards from reporting and record changes.

Detected change types match the spec: new fight, cancellation, replacement,
opponent change, main-event change, co-main change, title-fight change and
weight-class change.  Every recorded change keeps BEFORE / AFTER / REASON /
STATUS / SOURCE / TIMESTAMP so nothing is asserted without provenance.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from database import repo_entities as entities_repo
from models.types import Category, SourceType, normalize_source_type
from processors.entities import find_matchups, find_weight_classes, get_fighter_index, is_title_fight
from utils.logging_setup import get_logger
from utils.textutil import collapse_whitespace, normalize_text
from utils.timeutil import utcnow_iso

logger = get_logger(__name__)

_REPLACEMENT_PATTERNS = [
    re.compile(r"([A-Z][\w'\-\.]+(?:\s+[A-Z][\w'\-\.]+){0,2})\s+(?:will\s+)?replaces?\s+"
               r"([A-Z][\w'\-\.]+(?:\s+[A-Z][\w'\-\.]+){0,2})"),
    re.compile(r"([A-Z][\w'\-\.]+(?:\s+[A-Z][\w'\-\.]+){0,2})\s+steps\s+in\s+for\s+"
               r"([A-Z][\w'\-\.]+(?:\s+[A-Z][\w'\-\.]+){0,2})"),
    re.compile(r"([A-Z][\w'\-\.]+(?:\s+[A-Z][\w'\-\.]+){0,2})\s+takes\s+the\s+place\s+of\s+"
               r"([A-Z][\w'\-\.]+(?:\s+[A-Z][\w'\-\.]+){0,2})"),
]

_REASON_TERMS = [
    ("injury", ["injury", "injured", "torn", "surgery", "broken"]),
    ("failed drug test", ["failed drug test", "doping", "anti-doping", "flagged"]),
    ("weight miss", ["missed weight", "weight miss"]),
    ("illness", ["illness", "ill", "sick"]),
    ("visa issue", ["visa"]),
    ("undisclosed reason", ["undisclosed"]),
]

SEGMENT_TERMS = [
    ("main_event", ["main event", "headliner", "headline the card", "will headline"]),
    ("co_main", ["co-main", "co main event"]),
    ("prelims", ["prelim", "preliminary card"]),
    ("main_card", ["main card"]),
]


@dataclass
class DetectedChange:
    change_type: str
    before_text: Optional[str] = None
    after_text: Optional[str] = None
    reason: Optional[str] = None
    fighters: List[str] = field(default_factory=list)
    segment: Optional[str] = None
    weight_class: Optional[str] = None
    title_fight: bool = False


def analyse_article(article: Dict[str, Any]) -> List[DetectedChange]:
    """Read one article and describe the card changes it reports."""
    text = collapse_whitespace(
        f"{article.get('title') or ''}. {article.get('excerpt') or ''} {article.get('content_snippet') or ''}"
    )
    if not text:
        return []
    category = article.get("category") or Category.GENERAL.value
    index = get_fighter_index()
    matchups = find_matchups(text, index)
    weight_classes = find_weight_classes(text)
    weight_class = weight_classes[0] if weight_classes else None
    segment = _detect_segment(text)
    reason = _detect_reason(text)
    title_flag = is_title_fight(text)
    changes: List[DetectedChange] = []

    replacement = _detect_replacement(text, index)
    if replacement:
        incoming, outgoing = replacement
        changes.append(DetectedChange(
            change_type="replacement",
            before_text=outgoing,
            after_text=incoming,
            reason=reason,
            fighters=[incoming, outgoing],
            segment=segment,
            weight_class=weight_class,
            title_fight=title_flag,
        ))

    if category == Category.CANCELLATION.value:
        target = _fight_label(matchups[0]) if matchups else (article.get("fighters") or [None])[0]
        changes.append(DetectedChange(
            change_type="cancellation",
            before_text=target,
            after_text="Bout reported off the card",
            reason=reason,
            fighters=list(matchups[0]) if matchups else list(article.get("fighters") or [])[:2],
            segment=segment,
            weight_class=weight_class,
            title_fight=title_flag,
        ))
    elif category == Category.FIGHT_ANNOUNCEMENT.value and matchups:
        for pair in matchups[:2]:
            changes.append(DetectedChange(
                change_type="new_fight",
                after_text=_fight_label(pair),
                reason=None,
                fighters=list(pair),
                segment=segment,
                weight_class=weight_class,
                title_fight=title_flag,
            ))
    elif category == Category.INJURY.value and _mentions_withdrawal(text):
        who = (article.get("fighters") or [None])[0]
        changes.append(DetectedChange(
            change_type="cancellation",
            before_text=_fight_label(matchups[0]) if matchups else who,
            after_text=f"{who} reported out" if who else "Fighter reported out",
            reason=reason or "injury",
            fighters=list(matchups[0]) if matchups else ([who] if who else []),
            segment=segment,
            weight_class=weight_class,
            title_fight=title_flag,
        ))
    return changes


def apply_changes(
    story: Dict[str, Any], articles: List[Dict[str, Any]], event_id: Optional[int] = None
) -> List[int]:
    """Persist fight-card state and change history for one story."""
    event_name = (story.get("events") or [None])[0]
    if event_id is None and event_name:
        event_id = entities_repo.upsert_event(event_name, data_origin="detected")
    recorded: List[int] = []

    for article in articles:
        for change in analyse_article(article):
            status = _status_for(article, story)
            fight_id = _apply_to_card(event_id, change, article, story, status)
            existing = entities_repo.find_card_change(event_id, change.change_type, change.after_text)
            if existing:
                # Same change, another outlet: keep one log entry, but let a
                # stronger source upgrade its status and provenance. An official
                # source always takes over the attribution.
                official = normalize_source_type(article.get("source_type")) == SourceType.OFFICIAL.value
                if official or (status == "CONFIRMED" and existing.get("status") != "CONFIRMED"):
                    entities_repo.upgrade_card_change(
                        int(existing["id"]), status, article.get("source_name"),
                        article.get("url"), article.get("id"), change.reason,
                    )
                continue
            change_id = entities_repo.record_card_change(
                event_id=event_id,
                event_name=event_name,
                change_type=change.change_type,
                before_text=change.before_text,
                after_text=change.after_text,
                reason=change.reason,
                status=status,
                fight_card_item_id=fight_id,
                story_id=story.get("id"),
                source_article_id=article.get("id"),
                source_url=article.get("url"),
                source_name=article.get("source_name"),
                occurred_at=article.get("published_at") or article.get("collected_at"),
                is_demo=bool(article.get("is_demo")),
            )
            recorded.append(change_id)
    return recorded


def _apply_to_card(
    event_id: Optional[int],
    change: DetectedChange,
    article: Dict[str, Any],
    story: Dict[str, Any],
    status: str,
) -> Optional[int]:
    """Update the stored card when a change names two fighters and an event."""
    if not event_id or len(change.fighters) < 2:
        return None
    fighter_a, fighter_b = change.fighters[0], change.fighters[1]
    official = normalize_source_type(article.get("source_type")) == SourceType.OFFICIAL.value
    if change.change_type == "new_fight":
        fight_id, created, item_changes = entities_repo.upsert_fight(
            event_id=event_id,
            fighter_a=fighter_a,
            fighter_b=fighter_b,
            weight_class=change.weight_class,
            is_title_fight=change.title_fight,
            segment=change.segment,
            status="scheduled",
            confidence="official" if official else "reported",
            source_article_id=article.get("id"),
            source_story_id=story.get("id"),
            source_url=article.get("url"),
            source_name=article.get("source_name"),
            is_demo=bool(article.get("is_demo")),
        )
        for item_change in item_changes:
            if entities_repo.card_change_exists(event_id, item_change["change_type"],
                                                item_change.get("after_text")):
                continue
            entities_repo.record_card_change(
                event_id=event_id,
                event_name=(story.get("events") or [None])[0],
                change_type=item_change["change_type"],
                before_text=item_change.get("before_text"),
                after_text=item_change.get("after_text"),
                reason=change.reason,
                status=status,
                fight_card_item_id=fight_id,
                story_id=story.get("id"),
                source_article_id=article.get("id"),
                source_url=article.get("url"),
                source_name=article.get("source_name"),
            )
        return fight_id
    if change.change_type == "cancellation":
        for fight in entities_repo.find_fights_for_fighter(event_id, fighter_a):
            entities_repo.set_fight_status(int(fight["id"]), "cancelled")
            return int(fight["id"])
    if change.change_type == "replacement":
        # change.fighters = [incoming, outgoing]
        incoming, outgoing = change.fighters[0], change.fighters[1]
        for fight in entities_repo.find_fights_for_fighter(event_id, outgoing):
            remaining = (
                fight["fighter_b"] if normalize_text(fight["fighter_a"]) == normalize_text(outgoing)
                else fight["fighter_a"]
            )
            entities_repo.set_fight_status(int(fight["id"]), "changed")
            new_id, _, _ = entities_repo.upsert_fight(
                event_id=event_id,
                fighter_a=remaining,
                fighter_b=incoming,
                weight_class=fight.get("weight_class") or change.weight_class,
                is_title_fight=bool(fight.get("is_title_fight")) or change.title_fight,
                segment=fight.get("segment") or change.segment,
                status="scheduled",
                confidence="official" if official else "reported",
                source_article_id=article.get("id"),
                source_story_id=story.get("id"),
                source_url=article.get("url"),
                source_name=article.get("source_name"),
                is_demo=bool(article.get("is_demo")),
            )
            if not entities_repo.card_change_exists(event_id, "opponent_change",
                                                    f"{remaining} vs. {incoming}"):
                entities_repo.record_card_change(
                    event_id=event_id,
                    event_name=(story.get("events") or [None])[0],
                    change_type="opponent_change",
                    before_text=f"{remaining} vs. {outgoing}",
                    after_text=f"{remaining} vs. {incoming}",
                    reason=change.reason,
                    status=status,
                    fight_card_item_id=new_id,
                    story_id=story.get("id"),
                    source_article_id=article.get("id"),
                    source_url=article.get("url"),
                    source_name=article.get("source_name"),
                )
            return new_id
    return None


def _status_for(article: Dict[str, Any], story: Dict[str, Any]) -> str:
    if normalize_source_type(article.get("source_type")) == SourceType.OFFICIAL.value:
        return "CONFIRMED"
    return str(story.get("status") or "REPORTED")


def _detect_replacement(text: str, index: Any) -> Optional[Tuple[str, str]]:
    from processors.entities import _resolve_name  # local import keeps the API small

    for pattern in _REPLACEMENT_PATTERNS:
        match = pattern.search(text)
        if match:
            incoming = _resolve_name(match.group(1), index)
            outgoing = _resolve_name(match.group(2), index)
            if incoming and outgoing and normalize_text(incoming) != normalize_text(outgoing):
                return incoming, outgoing
    return None


def _detect_segment(text: str) -> Optional[str]:
    haystack = normalize_text(text)
    for segment, terms in SEGMENT_TERMS:
        if any(normalize_text(term) in haystack for term in terms):
            return segment
    return None


def _detect_reason(text: str) -> Optional[str]:
    haystack = normalize_text(text)
    for reason, terms in _REASON_TERMS:
        if any(normalize_text(term) in haystack for term in terms):
            return reason
    return None


def _mentions_withdrawal(text: str) -> bool:
    haystack = normalize_text(text)
    return any(term in haystack for term in (
        "out of", "withdraws", "withdrawn", "withdrawal", "pulled out", "forced out",
        "off the card", "will not compete",
    ))


def _fight_label(pair: Tuple[str, str]) -> str:
    return f"{pair[0]} vs. {pair[1]}"
