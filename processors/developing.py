"""Developing-story handling: keep one story updated instead of creating
near-duplicates, and maintain the chronological timeline shown in the UI.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from database import repo_settings as settings_repo
from database import repo_social as social_repo
from database import repo_stories as stories_repo
from models.types import status_badge
from utils.textutil import truncate
from utils.timeutil import age_hours, format_display, utcnow_iso


def record_status_change(story: Dict[str, Any], new_status: str, reasons: List[str]) -> None:
    """Add a timeline entry when a story's status actually changes."""
    old_status = story.get("status")
    if not old_status or old_status == new_status:
        return
    stories_repo.add_timeline_entry(
        story_id=int(story["id"]),
        occurred_at=utcnow_iso(),
        kind="status_change",
        headline=f"Status changed: {status_badge(old_status)} -> {status_badge(new_status)}",
        detail="; ".join(reasons[:3]) if reasons else None,
        source_name="UFC News Radar",
        source_type="SYSTEM",
    )


def add_social_timeline_entry(story_id: int, post: Dict[str, Any]) -> None:
    stories_repo.add_timeline_entry(
        story_id=story_id,
        occurred_at=post.get("created_at_source") or post.get("collected_at") or utcnow_iso(),
        kind="social",
        headline=f"@{post.get('username') or 'unknown'} posted on X",
        detail=truncate(post.get("text") or "", 200),
        source_name=f"@{post.get('username')}" if post.get("username") else "X",
        source_type=post.get("account_type"),
        url=post.get("url"),
        social_post_id=post.get("id"),
    )


def add_note(story_id: int, headline: str, detail: str = "", occurred_at: Optional[str] = None) -> None:
    stories_repo.add_timeline_entry(
        story_id=story_id,
        occurred_at=occurred_at or utcnow_iso(),
        kind="note",
        headline=headline,
        detail=detail or None,
        source_name="UFC News Radar",
        source_type="SYSTEM",
    )


def is_developing(story: Dict[str, Any], timeline: Optional[List[Dict[str, Any]]] = None) -> bool:
    """True when several distinct updates landed inside the developing window."""
    window = settings_repo.get_int("developing_window_hours", 48)
    entries = timeline if timeline is not None else stories_repo.story_timeline(int(story["id"]))
    recent = [
        entry for entry in entries
        if entry.get("kind") in ("article", "social", "card_change", "status_change")
        and (age_hours(entry.get("occurred_at")) or 1e9) <= window
    ]
    if len(recent) >= 3:
        return True
    if story.get("has_conflict") and len(recent) >= 2:
        return True
    return False


def timeline_view(story_id: int) -> List[Dict[str, Any]]:
    """Timeline entries with display-ready labels, oldest first."""
    entries = stories_repo.story_timeline(story_id)
    view: List[Dict[str, Any]] = []
    for entry in entries:
        icon = {
            "article": "\U0001F4F0",
            "social": "\U0001F4AC",
            "status_change": "\U0001F504",
            "card_change": "\U0001F94A",
            "ranking": "\U0001F4CA",
            "note": "\U0001F4CC",
        }.get(entry.get("kind", ""), "•")
        view.append({
            **entry,
            "icon": icon,
            "when": format_display(entry.get("occurred_at"), "%b %d, %H:%M UTC"),
        })
    return view


def latest_update_summary(story_id: int) -> Optional[str]:
    entries = stories_repo.story_timeline(story_id)
    if not entries:
        return None
    last = entries[-1]
    return f"{format_display(last.get('occurred_at'), '%b %d %H:%M UTC')} - {last.get('headline') or ''}"


def refresh_social_links(story_id: int) -> int:
    """Attach story social posts to the timeline (idempotent)."""
    count = 0
    for post in social_repo.posts_for_story(story_id):
        add_social_timeline_entry(story_id, post)
        count += 1
    return count
