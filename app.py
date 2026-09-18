"""UFC News Radar - entry point.

Run it with:

    streamlit run app.py

The app answers one question: what important UFC news is happening right now,
what is actually known, what is only reported or rumoured, what changed, and
what needs verifying before you report it.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Make sure the project root is importable when Streamlit runs this file.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from ai import service as ai_service                      # noqa: E402
from database import repo_settings as settings_repo       # noqa: E402
from database import repo_stories as stories_repo         # noqa: E402
from database.db import init_db                           # noqa: E402
from database.demo_data import demo_data_present          # noqa: E402
from social.x_monitor import x_status_panel               # noqa: E402
from ui.components import current_page, int_param, navigate, param  # noqa: E402
from ui.pages import (                                    # noqa: E402
    cards, dashboard, events, fighters, rankings, research, search, settings, social,
    sources as sources_page, watchlists,
)
from ui.theme import inject_css                           # noqa: E402
from utils.logging_setup import configure_logging         # noqa: E402
from utils.timeutil import humanize_age                   # noqa: E402

NAV = [
    ("dashboard", "\U0001F4E1 Dashboard"),
    ("rumors", "\U0001F534 Rumors & Reports"),
    ("developing", "⚡ Developing"),
    ("trending", "\U0001F4C8 Trending"),
    ("fighters", "\U0001F94A Fighters"),
    ("events", "\U0001F4C5 Events"),
    ("rankings", "\U0001F3C6 Rankings"),
    ("cards", "\U0001F5D3 Fight card changes"),
    ("social", "\U0001F4AC X monitoring"),
    ("watchlists", "⭐ Watchlists"),
    ("search", "\U0001F50D Search"),
    ("sources", "\U0001F6E0 Source health"),
    ("settings", "⚙ Settings"),
]


@st.cache_resource(show_spinner=False)
def bootstrap() -> bool:
    """Create/upgrade the database once per Streamlit process."""
    configure_logging()
    init_db()
    return True


def run_collection() -> None:
    """Collect from every enabled source, then reprocess and rerun the page."""
    from collectors.runner import run_collection as collect

    with st.spinner("Collecting from sources..."):
        result = collect(trigger="manual")
    if result.sources_ok:
        st.toast(result.summary_line, icon="✅")
    if result.sources_failed:
        st.toast(f"{result.sources_failed} source(s) failed - see Source health", icon="⚠️")
    st.session_state["last_run_summary"] = result.summary_line
    st.session_state["last_run_errors"] = result.errors[:6]
    st.rerun()


def sidebar() -> None:
    with st.sidebar:
        st.markdown(
            '<div style="font-size:1.2rem;font-weight:800;letter-spacing:2px;margin-bottom:2px">'
            'UFC NEWS <span style="color:#ff3b3b">RADAR</span></div>'
            '<div style="color:#96a0b0;font-size:0.75rem;margin-bottom:14px">'
            'news intelligence for creators</div>',
            unsafe_allow_html=True,
        )
        if st.button("\U0001F504 Refresh now", use_container_width=True, type="primary"):
            run_collection()

        active = current_page()
        for key, label in NAV:
            is_active = active == key or (active in ("research",) and key == "dashboard")
            if st.button(label, key=f"nav_{key}", use_container_width=True,
                         type="secondary", disabled=is_active):
                navigate(key)

        st.divider()
        last_collection = settings_repo.get_setting("last_collection_at", "")
        x_status = x_status_panel()
        ai_status = ai_service.ai_status()
        counts = stories_repo.dashboard_counts()
        st.markdown(
            f'<div style="font-size:0.76rem;color:#96a0b0;line-height:1.7">'
            f'<b style="color:#e8eaed">Last collection</b><br>'
            f'{humanize_age(last_collection) if last_collection else "never"}<br><br>'
            f'<b style="color:#e8eaed">Stories</b><br>{counts["total"]} tracked · '
            f'{counts["new"]} new (24h)<br><br>'
            f'<b style="color:#e8eaed">X API</b><br>'
            + ("✅ configured" if x_status["configured"] else "❌ not configured")
            + f'<br><br><b style="color:#e8eaed">AI</b><br>'
            + ("✅ " + str(ai_status["active"]) if ai_status["configured"]
               else "⚪ template mode")
            + ("<br><br>\U0001F7E3 demo data loaded" if demo_data_present() else "")
            + "</div>",
            unsafe_allow_html=True,
        )
        if st.session_state.get("last_run_errors"):
            with st.expander("Last run issues"):
                for error in st.session_state["last_run_errors"]:
                    st.caption(error)


def route() -> None:
    page = current_page()
    if page == "research":
        story_id = int_param("story")
        if story_id is None:
            navigate("dashboard")
        else:
            research.render(story_id)
    elif page == "fighter":
        name = param("name")
        if not name:
            fighters.render_list()
        else:
            fighters.render_detail(name)
    elif page == "fighters":
        fighters.render_list()
    elif page == "event":
        event_id = int_param("event_id")
        if event_id is None:
            events.render_list()
        else:
            events.render_detail(event_id)
    elif page == "events":
        events.render_list()
    elif page == "rumors":
        dashboard.render_feed(
            "RUMORS", "\U0001F534 RUMORS & REPORTS",
            "Unconfirmed claims, with who said it and how much source support it has.")
    elif page == "developing":
        dashboard.render_feed(
            "DEVELOPING", "⚡ DEVELOPING",
            "Stories that are still moving. Open one for the chronological timeline.")
    elif page == "trending":
        dashboard.render_feed(
            "TRENDING", "\U0001F4C8 TRENDING",
            "Unusual measured activity - each story lists the evidence behind the flag.")
    elif page == "rankings":
        rankings.render()
    elif page == "cards":
        cards.render()
    elif page == "social":
        social.render()
    elif page == "watchlists":
        watchlists.render()
    elif page == "search":
        search.render()
    elif page == "sources":
        sources_page.render(run_collection)
    elif page == "settings":
        settings.render()
    else:
        dashboard.render(run_collection)


def main() -> None:
    st.set_page_config(
        page_title="UFC News Radar",
        page_icon="\U0001F94A",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_css()
    bootstrap()
    sidebar()
    route()


if __name__ == "__main__":
    main()
