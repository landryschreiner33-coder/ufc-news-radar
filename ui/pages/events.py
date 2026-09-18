"""Event pages: the card as the app has collected it, plus every change."""
from __future__ import annotations

from typing import Any, Dict, List

import streamlit as st

from database import repo_entities as entities_repo
from database import repo_settings as settings_repo
from database import repo_social as social_repo
from database import repo_stories as stories_repo
from models.types import Category
from ui.components import bullet_list, metric_row, navigate, page_header, section_header, story_grid
from utils.textutil import normalize_text, truncate
from utils.timeutil import format_display, humanize_age

SEGMENT_LABELS = {
    "main_event": "Main event",
    "co_main": "Co-main event",
    "main_card": "Main card",
    "prelims": "Prelims",
    None: "Unplaced",
    "": "Unplaced",
}


def render_list() -> None:
    page_header("EVENTS", "Collected from UFC.com and from names detected in reporting.")
    columns = st.columns([2, 1])
    term = columns[0].text_input("Search events", key="event_search", placeholder="UFC 320...")
    upcoming_only = columns[1].toggle("Upcoming only", value=True, key="event_upcoming")

    events = (entities_repo.search_events(term, limit=100) if term
              else entities_repo.list_events(limit=120, upcoming_only=upcoming_only))
    section_header("EVENTS", len(events))
    if not events:
        st.markdown('<div class="muted">No events collected yet - press Refresh on the dashboard.</div>',
                    unsafe_allow_html=True)
    for start in range(0, len(events), 3):
        cols = st.columns(3)
        for offset, event in enumerate(events[start:start + 3]):
            with cols[offset]:
                if st.button(event["name"], key=f"event_btn_{event['id']}", use_container_width=True):
                    navigate("event", event_id=event["id"])
                meta = []
                if event.get("event_date"):
                    meta.append(format_display(event["event_date"], "%b %d, %Y"))
                if event.get("location"):
                    meta.append(event["location"])
                meta.append(f"source: {event.get('data_origin')}")
                st.markdown(f'<div class="muted" style="margin:-6px 0 10px 2px">{" · ".join(meta)}</div>',
                            unsafe_allow_html=True)


def render_detail(event_id: int) -> None:
    event = entities_repo.get_event(event_id)
    if event is None:
        st.error("Event not found.")
        return
    if st.button("← Back to events"):
        navigate("events")

    page_header(event["name"].upper(),
                " · ".join(filter(None, [
                    format_display(event.get("event_date"), "%A %d %B %Y") if event.get("event_date") else "",
                    event.get("location") or "",
                ])) or "No date or location collected yet")

    card = entities_repo.fight_card(event_id)
    changes = entities_repo.card_changes(event_id, limit=40)
    stories = stories_repo.stories_for_event(event_id, event["name"], limit=60)

    metric_row([
        ("Bouts tracked", len(card)),
        ("Card changes", len(changes)),
        ("Stories", len(stories)),
        ("Date", format_display(event.get("event_date"), "%b %d, %Y") if event.get("event_date") else "unknown"),
        ("Data origin", event.get("data_origin") or "detected"),
    ])

    watch_columns = st.columns([1, 4])
    watched = any(normalize_text(item["value"]) == normalize_text(event["name"])
                  for item in settings_repo.list_watchlist("event"))
    if not watched and watch_columns[0].button("☆ Watch this event", key="event_watch"):
        settings_repo.add_watchlist_item("event", event["name"])
        st.rerun()

    section_header("CURRENT FIGHT CARD", len(card),
                   note="Built from official pages and from reporting. Each bout shows how solid it is.")
    if not card:
        st.markdown(
            '<div class="muted">No bouts collected for this event yet. Bouts appear when the app '
            'collects an official card or reporting that names a matchup.</div>',
            unsafe_allow_html=True)
    else:
        for segment in ("main_event", "co_main", "main_card", "prelims", None, ""):
            bouts = [bout for bout in card if (bout.get("segment") or "") == (segment or "")]
            if not bouts:
                continue
            st.markdown(f"**{SEGMENT_LABELS.get(segment, 'Other')}**")
            for bout in bouts:
                status_color = {"scheduled": "#19c37d", "cancelled": "#ef4444",
                                "changed": "#ff9130"}.get(bout.get("status"), "#9aa4b2")
                title_flag = " · TITLE FIGHT" if bout.get("is_title_fight") else ""
                st.markdown(
                    f'<div class="panel" style="border-left:3px solid {status_color}">'
                    f'<div class="kv"><b>{bout["fighter_a"]} vs. {bout["fighter_b"]}</b>'
                    f'{title_flag}</div>'
                    f'<div class="muted">{bout.get("weight_class") or "weight class not collected"} · '
                    f'status: {bout.get("status")} · confidence: {bout.get("confidence")} · '
                    f'source: {bout.get("source_name") or "n/a"}</div></div>',
                    unsafe_allow_html=True,
                )

    _render_changes(changes)

    section_header("INJURIES, REPLACEMENTS & CANCELLATIONS")
    relevant = [story for story in stories if story.get("category") in (
        Category.INJURY.value, Category.REPLACEMENT.value, Category.CANCELLATION.value)]
    story_grid(relevant[:6], f"evt_change_{event_id}", empty_message="None collected for this event.")

    section_header("RUMORS & DEVELOPING")
    watchers = [story for story in stories
                if story.get("is_developing") or story.get("status") in ("RUMOR", "UNVERIFIED")]
    story_grid(watchers[:6], f"evt_rumor_{event_id}", empty_message="None collected for this event.")

    section_header("RELATED ARTICLES & STORIES", len(stories))
    story_grid(stories[:8], f"evt_story_{event_id}", empty_message="No stories mention this event yet.")

    _render_social(event["name"])


def _render_changes(changes: List[Dict[str, Any]]) -> None:
    section_header("FIGHT CARD CHANGES", len(changes),
                   note="Before / after / reason / status / source / timestamp for every detected change.")
    if not changes:
        st.markdown('<div class="muted">No card changes detected for this event.</div>',
                    unsafe_allow_html=True)
        return
    import pandas as pd

    frame = pd.DataFrame([
        {
            "Detected": format_display(change.get("detected_at"), "%b %d %H:%M"),
            "Change": (change.get("change_type") or "").replace("_", " ").title(),
            "Before": change.get("before_text") or "-",
            "After": change.get("after_text") or "-",
            "Reason": change.get("reason") or "not stated",
            "Status": change.get("status"),
            "Source": change.get("source_name") or "-",
        }
        for change in changes
    ])
    st.dataframe(frame, use_container_width=True, hide_index=True)


def _render_social(event_name: str) -> None:
    section_header("X ACTIVITY")
    posts = [
        post for post in social_repo.recent_posts(hours=24 * 14, limit=200)
        if any(normalize_text(event_name) == normalize_text(value) for value in (post.get("events") or []))
    ][:8]
    if not posts:
        st.markdown('<div class="muted">No collected X posts mention this event.</div>',
                    unsafe_allow_html=True)
        return
    for post in posts:
        st.markdown(
            f'<div class="panel"><div class="muted">@{post.get("username")} · '
            f'{post.get("account_type")} · {format_display(post.get("created_at_source"))}</div>'
            f'<div class="kv">{truncate(post.get("text"), 240)}</div></div>',
            unsafe_allow_html=True,
        )
