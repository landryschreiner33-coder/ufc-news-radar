"""Main dashboard: what is happening right now, organised for fast scanning."""
from __future__ import annotations

from typing import Any, Dict

import streamlit as st

from database import repo_settings as settings_repo
from database import repo_stories as stories_repo
from database.demo_data import DEMO_BANNER, demo_data_present
from ui import filters as filter_mod
from ui.components import metric_row, notice, page_header, section_header, story_grid
from utils.timeutil import humanize_age


def render(run_collection_callback) -> None:
    page_header("UFC NEWS <span>RADAR</span>",
                "What is happening now - and what is actually known.")

    last_collection = settings_repo.get_setting("last_collection_at", "")
    counts = stories_repo.dashboard_counts()
    metric_row([
        ("Last Updated", humanize_age(last_collection) if last_collection else "never"),
        ("New Stories", counts["new"]),
        ("Breaking", counts["breaking"], counts["breaking"] > 0),
        ("Important", counts["important"]),
        ("Rumors", counts["rumors"]),
        ("Developing", counts["developing"]),
        ("Trending", counts["trending"]),
        ("Confirmed", counts["confirmed"]),
    ])

    if demo_data_present():
        notice(DEMO_BANNER, kind="demo")

    if counts["total"] == 0:
        notice(
            "<b>Nothing collected yet.</b> Press <b>Refresh now</b> to pull the latest UFC news "
            "from every enabled source - it takes a few seconds. If a source fails, "
            "<b>Source health</b> shows exactly why. To try the interface without collecting, "
            "load the clearly-marked demo data from <b>Settings &rarr; Demo data</b>."
        )

    controls = st.columns([1.1, 1.3, 1.3, 1.1, 2.2])
    with controls[0]:
        if st.button("\U0001F504 Refresh now", use_container_width=True, type="primary"):
            run_collection_callback()
    with controls[1]:
        feed_filter = st.selectbox("Filter", filter_mod.filter_options(),
                                   index=0, key="dash_filter")
    with controls[2]:
        default_sort = settings_repo.get_setting("default_sort", "Newest")
        sort_choices = filter_mod.sort_options()
        sort = st.selectbox("Sort", sort_choices,
                            index=sort_choices.index(default_sort) if default_sort in sort_choices else 0,
                            key="dash_sort")
    with controls[3]:
        min_relevance = st.number_input(
            "Min relevance", min_value=0, max_value=100,
            value=int(settings_repo.get_int("min_relevance", 0)), step=5, key="dash_min_rel",
        )
    with controls[4]:
        search = st.text_input("Search this feed", value="", key="dash_search",
                               placeholder="fighter, event, keyword...")

    if feed_filter != "ALL" or search:
        _render_single_feed(feed_filter, sort, min_relevance, search)
    else:
        _render_sections(sort, min_relevance, search)


def render_feed(feed_filter: str, title: str, note: str = "") -> None:
    """A single filtered feed (used by the Rumors / Developing / Trending pages)."""
    page_header(title, note)
    controls = st.columns([1.3, 1.2, 3.5])
    sort_choices = filter_mod.sort_options()
    with controls[0]:
        sort = st.selectbox("Sort", sort_choices, key=f"feed_sort_{feed_filter}")
    with controls[1]:
        min_relevance = st.number_input("Min relevance", 0, 100, 0, 5,
                                        key=f"feed_rel_{feed_filter}")
    with controls[2]:
        search = st.text_input("Search", "", key=f"feed_search_{feed_filter}",
                               placeholder="fighter, event, keyword...")
    _render_single_feed(feed_filter, sort, min_relevance, search)


def _render_single_feed(feed_filter: str, sort: str, min_relevance: float, search: str) -> None:
    stories = filter_mod.fetch_stories(
        feed_filter=feed_filter, sort=sort, min_relevance=min_relevance, search=search,
        limit=settings_repo.get_int("feed_max_stories", 60),
    )
    label = feed_filter if feed_filter != "ALL" else "ALL STORIES"
    section_header(f"{label}", len(stories),
                   f'Sorted by {sort}' + (f' · search: "{search}"' if search else ""))
    story_grid(stories, key_prefix=f"feed_{feed_filter}", columns=2,
               empty_message="No stories match this filter yet. Try Refresh, or widen the filter.")


def _render_sections(sort: str, min_relevance: float, search: str) -> None:
    sections: Dict[str, Any] = filter_mod.dashboard_sections(
        sort=sort, min_relevance=min_relevance, search=search or None,
        per_section=settings_repo.get_int("feed_max_stories", 60) // 8 or 6,
    )

    section_header("\U0001F525 BREAKING", len(sections["breaking"]),
                   "High-relevance developments from the last few hours.")
    story_grid(sections["breaking"], "brk", empty_message="Nothing is breaking right now.")

    section_header("⚡ IMPORTANT", len(sections["important"]),
                   "Ranked by the automated relevance score - a feed-ordering tool, not a truth claim.")
    story_grid(sections["important"], "imp", empty_message="No high-relevance stories yet.")

    section_header("\U0001F534 RUMORS & REPORTS", len(sections["rumors"]),
                   "Unconfirmed claims. Check the source support before reporting any of these.")
    story_grid(sections["rumors"], "rum", empty_message="No unconfirmed claims collected.")

    section_header("⚡ DEVELOPING", len(sections["developing"]),
                   "Stories that are still moving - open one to see the timeline of updates.")
    story_grid(sections["developing"], "dev", empty_message="Nothing is actively developing.")

    section_header("\U0001F4C8 TRENDING", len(sections["trending"]),
                   "Unusual measured activity: independent outlets, updates and X posts inside the window.")
    story_grid(sections["trending"], "trd", empty_message="No unusual activity measured.")

    section_header("\U0001F4F0 LATEST", len(sections["latest"]), "Everything else, newest first.")
    story_grid(sections["latest"], "lat", empty_message="No stories collected yet - press Refresh now.")
