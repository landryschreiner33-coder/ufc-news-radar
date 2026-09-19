"""UFC News Radar - entry point.

Run it with:

    streamlit run app.py

The app answers one question: what important UFC news is happening right now,
what is actually known, what is only reported or rumoured, what changed, and
what needs verifying before you report it.

Navigation uses Streamlit's multipage API (``st.Page`` / ``st.navigation``).
Four sections do the real work - Dashboard, X Radar, Research and TikTok Studio
- and everything else sits under a secondary heading so the main choice stays
obvious.
"""
from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

# Make sure the project root is importable when Streamlit runs this file.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

st.set_page_config(
    page_title="UFC News Radar",
    page_icon="\U0001F94A",
    layout="wide",
    initial_sidebar_state="expanded",
)

from ai import service as ai_service                      # noqa: E402
from database import repo_settings as settings_repo       # noqa: E402
from database import repo_stories as stories_repo         # noqa: E402
from database.db import init_db                           # noqa: E402
from database.demo_data import demo_data_present          # noqa: E402
from social.x_monitor import x_status_panel               # noqa: E402
from ui.pages import (                                    # noqa: E402
    cards, dashboard, events, fighters, rankings, research, search, settings, social,
    sources as sources_page, tiktok_studio, watchlists,
)
from ui import nav                                        # noqa: E402
from ui.theme import inject_css                           # noqa: E402
from utils.logging_setup import configure_logging         # noqa: E402
from utils.timeutil import humanize_age                   # noqa: E402


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


# ------------------------------------------------------------- page bodies --
def page_dashboard() -> None:
    dashboard.render(run_collection)


def page_x_radar() -> None:
    social.render()


def page_research() -> None:
    research.render_entry()


def page_tiktok() -> None:
    tiktok_studio.render()


def page_events() -> None:
    events.render_router()


def page_fighters() -> None:
    fighters.render_router()


def page_rankings() -> None:
    rankings.render()


def page_card_changes() -> None:
    cards.render()


def page_watchlists() -> None:
    watchlists.render()


def page_search() -> None:
    search.render()


def page_sources() -> None:
    sources_page.render(run_collection)


def page_settings() -> None:
    settings.render()


def sidebar_extras() -> None:
    """Refresh, search and live status. Deliberately short - the sidebar is for
    getting somewhere, not for running the application."""
    with st.sidebar:
        if st.button("\U0001F504 Refresh now", width="stretch", type="primary"):
            run_collection()

        term = st.text_input("Search", key="sidebar_search", placeholder="fighter, event, topic...",
                             label_visibility="collapsed")
        if term and term != st.session_state.get("_last_global_search"):
            st.session_state["_last_global_search"] = term
            st.query_params["q"] = term
            st.switch_page(PAGES["search"])

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
            + ("✅ configured" if x_status["configured"] else "⚪ not configured")
            + f'<br><br><b style="color:#e8eaed">AI</b><br>'
            + ("🟢 " + str(ai_status["active"]) if ai_status["configured"]
               else "⚪ template mode")
            + ("<br><br>\U0001F7E3 demo data loaded" if demo_data_present() else "")
            + "</div>",
            unsafe_allow_html=True,
        )
        if st.session_state.get("last_run_errors"):
            with st.expander("Last run issues"):
                for error in st.session_state["last_run_errors"]:
                    st.caption(error)


PAGES = {
    "dashboard": st.Page(page_dashboard, title="Dashboard", icon="\U0001F4F0",
                         url_path="dashboard", default=True),
    "x_radar": st.Page(page_x_radar, title="X / Twitter Radar", icon="\U0001F426",
                       url_path="x-radar"),
    "research": st.Page(page_research, title="Research & Verification", icon="\U0001F50E",
                        url_path="research"),
    "tiktok": st.Page(page_tiktok, title="TikTok Studio", icon="\U0001F3AC",
                      url_path="tiktok-studio"),
    "events": st.Page(page_events, title="Events", icon="\U0001F4C5", url_path="events"),
    "fighters": st.Page(page_fighters, title="Fighters", icon="\U0001F94A", url_path="fighters"),
    "rankings": st.Page(page_rankings, title="Rankings", icon="\U0001F3C6", url_path="rankings"),
    "cards": st.Page(page_card_changes, title="Fight card changes", icon="\U0001F5D3",
                     url_path="card-changes"),
    "watchlists": st.Page(page_watchlists, title="Watchlists", icon="⭐", url_path="watchlists"),
    "search": st.Page(page_search, title="Search", icon="\U0001F50D", url_path="search"),
    "sources": st.Page(page_sources, title="Source health", icon="\U0001F6E0", url_path="sources"),
    "settings": st.Page(page_settings, title="Settings", icon="⚙", url_path="settings"),
}

NAVIGATION = {
    "News": [PAGES["dashboard"], PAGES["x_radar"], PAGES["research"], PAGES["tiktok"]],
    "Reference": [PAGES["events"], PAGES["fighters"], PAGES["rankings"], PAGES["cards"]],
    "Tools": [PAGES["watchlists"], PAGES["search"], PAGES["sources"], PAGES["settings"]],
}


nav.register(PAGES)


def main() -> None:
    inject_css()
    bootstrap()
    st.markdown(
        '<div style="font-size:1.15rem;font-weight:800;letter-spacing:2px;padding:2px 0 10px 4px">'
        'UFC NEWS <span style="color:#ff3b3b">RADAR</span></div>',
        unsafe_allow_html=True,
    )
    page = st.navigation(NAVIGATION, position="sidebar")

    # Item parameters ("?story=12", "?event_id=3") belong to the page that set
    # them. Two cases have to be told apart:
    #
    #   * the parameter appeared while staying on the same page - that is a card
    #     click, so open the item;
    #   * the page itself changed - the user used the menu, so the parameter is
    #     left over and must be dropped, or every menu click would bounce back
    #     to the old item.
    current = page.url_path or "dashboard"
    previous = st.session_state.get("_radar_page")
    st.session_state["_radar_page"] = current
    # ``previous is None`` means a fresh browser session: either a card link
    # (which is a real navigation, so session state is gone) or a bookmarked
    # item URL. Both should open the item rather than drop it.
    same_page = previous is None or previous == current

    for parameter, owners, target in (
        ("story", ("research", "tiktok-studio"), "research"),
        ("event_id", ("events",), "events"),
        ("name", ("fighters",), "fighters"),
    ):
        if current in owners or nav.selection(parameter) is None:
            continue
        if same_page and st.query_params.get(parameter):
            st.switch_page(PAGES[target])
        nav.clear_items(parameter)

    sidebar_extras()
    page.run()


if __name__ == "__main__":
    main()
