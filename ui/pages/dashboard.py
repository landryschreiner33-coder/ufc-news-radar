"""Main dashboard: what is happening right now, organised for fast scanning.

Layout: a compact status bar, then the sections in the order a creator needs
them - BREAKING, IMPORTANT, RUMORS, DEVELOPING, TRENDING, UPCOMING EVENTS,
LATEST. Each section is a responsive card grid rather than a wall of text.
"""
from __future__ import annotations

from typing import Any, Dict, List

import streamlit as st

from ai import service as ai_service
from database import repo_entities as entities_repo
from database import repo_settings as settings_repo
from database import repo_stories as stories_repo
from database.demo_data import DEMO_BANNER, demo_data_present
from models.types import EventStatus, event_status_style
from processors.event_lifecycle import format_countdown
from social.x_monitor import x_status_panel
from ui import filters as filter_mod
from ui import nav
from ui.cards import card_grid, esc
from ui.components import notice, page_header, section_header
from utils.timeutil import format_display, humanize_age


def render(run_collection_callback) -> None:
    page_header("UFC NEWS <span>RADAR</span>",
                "What is happening now - and what is actually known.")

    counts = stories_repo.dashboard_counts()
    _status_bar(counts)

    if demo_data_present():
        notice(DEMO_BANNER, kind="demo")

    if counts["total"] == 0:
        _empty_state(run_collection_callback)
        return

    controls = st.columns([1.1, 1.3, 1.3, 1.1, 2.2])
    with controls[0]:
        if st.button("\U0001F504 Refresh now", width="stretch", type="primary"):
            run_collection_callback()
    with controls[1]:
        feed_filter = st.selectbox("Filter", filter_mod.filter_options(), index=0, key="dash_filter")
    with controls[2]:
        default_sort = settings_repo.get_setting("default_sort", "Newest")
        sort_choices = filter_mod.sort_options()
        sort = st.selectbox("Sort", sort_choices,
                            index=sort_choices.index(default_sort) if default_sort in sort_choices else 0,
                            key="dash_sort")
    with controls[3]:
        min_relevance = st.number_input(
            "Min relevance", min_value=0, max_value=100,
            value=int(settings_repo.get_int("min_relevance", 0)), step=5, key="dash_min_rel")
    with controls[4]:
        search = st.text_input("Search this feed", value="", key="dash_search",
                               placeholder="fighter, event, keyword...")

    if feed_filter != "ALL" or search:
        _render_single_feed(feed_filter, sort, min_relevance, search)
    else:
        _render_sections(sort, min_relevance, search)


# ------------------------------------------------------------- status bar --
def _status_bar(counts: Dict[str, Any]) -> None:
    last_collection = settings_repo.get_setting("last_collection_at", "")
    x_status = x_status_panel()
    ai_status = ai_service.ai_status()
    live = entities_repo.list_events(limit=3, statuses=[EventStatus.LIVE.value])

    pieces = [
        f'<span>Last updated <b>{esc(humanize_age(last_collection) if last_collection else "never")}</b></span>',
        f'<span>Stories <b>{counts["total"]}</b></span>',
        f'<span>Breaking <b style="color:#ff6b6b">{counts["breaking"]}</b></span>',
        f'<span>Rumors <b style="color:#ff9130">{counts["rumors"]}</b></span>',
        f'<span>Developing <b style="color:#ffc46b">{counts["developing"]}</b></span>',
        f'<span>X {"🟢 on" if x_status["configured"] else "⚪ not configured"}</span>',
        f'<span>AI {"🟢 active" if ai_status["configured"] else "⚪ template mode"}</span>',
    ]
    if live:
        pieces.insert(0, '<span class="lifepill live"><span class="dot"></span>'
                         f'LIVE NOW: {esc(live[0]["name"])}</span>')
    st.markdown('<div class="statusbar">' + '<span class="sep">|</span>'.join(pieces) + '</div>',
                unsafe_allow_html=True)


def _empty_state(run_collection_callback) -> None:
    """First run. Polished, and never filled with invented news."""
    st.markdown(
        '<div class="emptystate">'
        '<div class="big">YOUR UFC RADAR IS READY</div>'
        '<div class="sub">No news has been collected yet.<br>'
        'Press <b>Refresh now</b> to pull the latest from every enabled source - '
        'it takes 10-60 seconds the first time.<br>'
        'If a source fails, <b>Source health</b> shows exactly why.</div></div>',
        unsafe_allow_html=True,
    )
    columns = st.columns([1, 3])
    with columns[0]:
        if st.button("\U0001F504 REFRESH NOW", width="stretch", type="primary",
                     key="empty_refresh"):
            run_collection_callback()
    st.caption(
        "Want to look around first? Settings → Demo data loads clearly-marked fictional "
        "stories. They are never presented as real news and one button removes them."
    )


# --------------------------------------------------------------- sections --
def _render_single_feed(feed_filter: str, sort: str, min_relevance: float, search: str) -> None:
    stories = filter_mod.fetch_stories(
        feed_filter=feed_filter, sort=sort, min_relevance=min_relevance, search=search,
        limit=settings_repo.get_int("feed_max_stories", 60),
    )
    label = feed_filter if feed_filter != "ALL" else "ALL STORIES"
    section_header(label, len(stories),
                   f"Sorted by {sort}" + (f' · search: "{search}"' if search else ""))
    card_grid(stories, "No stories match this filter yet. Try Refresh, or widen the filter.",
              key=f"feed_{feed_filter}")


def _render_sections(sort: str, min_relevance: float, search: str) -> None:
    sections: Dict[str, Any] = filter_mod.dashboard_sections(
        sort=sort, min_relevance=min_relevance, search=search or None,
        per_section=settings_repo.get_int("feed_max_stories", 60) // 8 or 6,
    )

    section_header("\U0001F525 BREAKING", len(sections["breaking"]),
                   "High-relevance developments from the last few hours.")
    card_grid(sections["breaking"], "Nothing is breaking right now.", key="breaking")

    section_header("⚡ IMPORTANT", len(sections["important"]),
                   "Ranked by the automated relevance score - a feed-ordering tool, not a truth claim.")
    card_grid(sections["important"], "No high-relevance stories yet.", key="important")

    section_header("\U0001F534 RUMORS & REPORTS", len(sections["rumors"]),
                   "Unconfirmed claims. Each card shows who claimed it and whether UFC has confirmed.")
    card_grid(sections["rumors"], "No unconfirmed claims collected.", key="rumors")

    section_header("⚡ DEVELOPING", len(sections["developing"]),
                   "Stories that are still moving - open one to see the timeline of updates.")
    card_grid(sections["developing"], "Nothing is actively developing.", key="developing")

    section_header("\U0001F4C8 TRENDING", len(sections["trending"]),
                   "Unusual measured activity: independent outlets, updates and X posts in the window.")
    card_grid(sections["trending"], "No unusual activity measured.", key="trending")

    render_upcoming_events()

    section_header("\U0001F4F0 LATEST", len(sections["latest"]), "Everything else, newest first.")
    card_grid(sections["latest"], "No stories collected yet - press Refresh now.", key="latest")


def render_upcoming_events(limit: int = 4) -> None:
    """Upcoming and live events with a calculated countdown."""
    events = entities_repo.list_events(limit=limit, upcoming_only=True)
    section_header("\U0001F4C5 UPCOMING EVENTS", len(events),
                   "Status comes from the official schedule plus collected evidence - never "
                   "from the date alone.")
    if not events:
        st.markdown('<div class="muted">No scheduled events collected yet.</div>',
                    unsafe_allow_html=True)
        return

    from ui.pages.events import event_card_grid

    event_card_grid(events, key="dash_upcoming")

    # Any disagreement between reporting and the official schedule is surfaced
    # next to the event, never silently applied.
    for event in events:
        for note in (event.get("status_conflicts") or [])[:1]:
            st.markdown(
                f'<div class="warn-box">⚠ <b>{esc(event["name"])}:</b> {esc(note)}</div>',
                unsafe_allow_html=True)


def render_feed(feed_filter: str, title: str, note: str = "") -> None:
    """A single filtered feed, kept for direct links to a filtered view."""
    page_header(title, note)
    controls = st.columns([1.3, 1.2, 3.5])
    sort_choices = filter_mod.sort_options()
    with controls[0]:
        sort = st.selectbox("Sort", sort_choices, key=f"feed_sort_{feed_filter}")
    with controls[1]:
        min_relevance = st.number_input("Min relevance", 0, 100, 0, 5, key=f"feed_rel_{feed_filter}")
    with controls[2]:
        search = st.text_input("Search", "", key=f"feed_search_{feed_filter}",
                               placeholder="fighter, event, keyword...")
    _render_single_feed(feed_filter, sort, min_relevance, search)
