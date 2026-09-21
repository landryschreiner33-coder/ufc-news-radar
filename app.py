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

import logging
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
    # "auto" is the only honest answer on a phone: "expanded" forces the menu
    # open on every screen size, and at 430px it covers most of the feed the
    # reader came for. Streamlit collapses it on narrow viewports and leaves
    # it open on a desktop.
    initial_sidebar_state="auto",
)

from ai import service as ai_service                      # noqa: E402
from database import repo_runs as runs_repo               # noqa: E402
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
    """Create/upgrade the database once per Streamlit process.

    Also starts the in-app background collector, but only if it has been
    switched on in Settings. Collection is never something the app begins
    doing on its own, and the collector that keeps working when the app is
    closed is ``scripts/scheduler.py`` - see ``collectors/scheduler.py``.
    """
    configure_logging()
    init_db()
    try:
        from collectors import scheduler
        from database.backends import postgres_url
        from database.db import current_db_path

        scheduler.start(database_path=current_db_path(), database_url=postgres_url())
    except Exception:  # a background helper must never stop the app starting
        logging.getLogger(__name__).exception("Background collector could not start")
    return True


def run_collection() -> None:
    """Collect from every enabled source, then reprocess and rerun the page.

    The toast is not the report: toasts auto-dismiss, and a 2-minute run means
    the user is very likely looking elsewhere when it lands. The durable
    statement of what the run achieved is the status bar and the banner the
    dashboard renders from ``repo_runs.collection_status()``.
    """
    from collectors.runner import run_collection as collect

    with st.spinner("Collecting from sources..."):
        result = collect(trigger="manual")
    icon = {"SUCCESS": "✅", "PARTIAL": "⚠️", "TOTAL_FAILURE": "❌"}.get(result.outcome, "ℹ️")
    st.toast(result.headline, icon=icon)
    st.session_state["last_run_summary"] = result.summary_line
    st.session_state["last_run_outcome"] = result.outcome
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


def page_dashboard_alias() -> None:
    """Serve a bookmarked ``/dashboard`` link.

    Streamlit's default page always lives at ``/`` - ``st.Page.url_path``
    returns "" for it by design - so a request for ``/dashboard`` resolves to
    nothing and raises a visible "the page you have requested does not seem to
    exist" dialog over the dashboard it then renders anyway.

    ``/`` is therefore the canonical dashboard route, and this hidden page
    exists only so the obvious URL redirects to it instead of showing an
    error. It is not in the menu and never renders anything itself.
    """
    st.switch_page(PAGES["dashboard"])


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
            nav.open_search(term)

        st.divider()
        status = runs_repo.collection_status()
        x_status = x_status_panel()
        ai_status = ai_service.ai_status()
        counts = stories_repo.dashboard_counts()
        # A failed run is stated here too, in the same words as the dashboard
        # banner, so the sidebar can never look reassuring while the feed is
        # stale.
        st.markdown(
            f'<div style="font-size:0.76rem;color:#96a0b0;line-height:1.7">'
            f'<b style="color:{status["color"]}">{status["emoji"]} {status["label"]}</b><br>'
            f'{status["sources_ok"]}/{status["sources_attempted"]} sources OK<br>'
            f'<b style="color:#e8eaed">Data from</b><br>'
            f'{humanize_age(status["succeeded_at"]) if status["succeeded_at"] else "never collected"}'
            f'<br><br>'
            f'<b style="color:#e8eaed">Stories</b><br>{counts["total"]} tracked · '
            f'{counts["new"]} new (24h)<br><br>'
            f'<b style="color:#e8eaed">X API</b><br>'
            + ("✅ configured" if x_status["configured"] else "⚪ not configured")
            + '<br><br><b style="color:#e8eaed">AI</b><br>'
            + ("🟢 " + str(ai_status["active"]) if ai_status["configured"]
               else "⚪ template mode")
            + ("<br><br>\U0001F7E3 demo data loaded" if demo_data_present() else "")
            + "</div>",
            unsafe_allow_html=True,
        )
        if st.session_state.get("last_run_errors"):
            with st.expander("Last run issues", expanded=status["needs_attention"]):
                for error in st.session_state["last_run_errors"]:
                    st.caption(error)


#: The URL each page answers to. Declared here rather than read back off the
#: ``st.Page`` objects so the routing contract is visible in one place and can
#: be asserted in tests without a running Streamlit server.
#:
#: The dashboard is the default page, which Streamlit always serves at "/" -
#: ``st.Page.url_path`` returns "" for it whatever is passed in. "home" is
#: therefore only an internal identifier, and "dashboard" belongs to the
#: hidden alias page that redirects to "/".
PAGE_URL_PATHS = {
    "dashboard": "home",
    "x_radar": "x-radar",
    "research": "research",
    "tiktok": "tiktok-studio",
    "events": "events",
    "fighters": "fighters",
    "rankings": "rankings",
    "cards": "card-changes",
    "watchlists": "watchlists",
    "search": "search",
    "sources": "sources",
    "settings": "settings",
    "dashboard_alias": "dashboard",
}

#: The canonical route for the dashboard. Bookmarking "/dashboard" works, but
#: it redirects here rather than being a second URL for the same page.
CANONICAL_DASHBOARD_PATH = "/"

PAGES = {
    "dashboard": st.Page(page_dashboard, title="Dashboard", icon="\U0001F4F0",
                         url_path=PAGE_URL_PATHS["dashboard"], default=True),
    "x_radar": st.Page(page_x_radar, title="X / Twitter Radar", icon="\U0001F426",
                       url_path=PAGE_URL_PATHS["x_radar"]),
    "research": st.Page(page_research, title="Research & Verification", icon="\U0001F50E",
                        url_path=PAGE_URL_PATHS["research"]),
    "tiktok": st.Page(page_tiktok, title="TikTok Studio", icon="\U0001F3AC",
                      url_path=PAGE_URL_PATHS["tiktok"]),
    "events": st.Page(page_events, title="Events", icon="\U0001F4C5", url_path=PAGE_URL_PATHS["events"]),
    "fighters": st.Page(page_fighters, title="Fighters", icon="\U0001F94A", url_path=PAGE_URL_PATHS["fighters"]),
    "rankings": st.Page(page_rankings, title="Rankings", icon="\U0001F3C6", url_path=PAGE_URL_PATHS["rankings"]),
    "cards": st.Page(page_card_changes, title="Fight card changes", icon="\U0001F5D3",
                     url_path=PAGE_URL_PATHS["cards"]),
    "watchlists": st.Page(page_watchlists, title="Watchlists", icon="⭐", url_path=PAGE_URL_PATHS["watchlists"]),
    "search": st.Page(page_search, title="Search", icon="\U0001F50D", url_path=PAGE_URL_PATHS["search"]),
    "sources": st.Page(page_sources, title="Source health", icon="\U0001F6E0", url_path=PAGE_URL_PATHS["sources"]),
    "settings": st.Page(page_settings, title="Settings", icon="⚙", url_path=PAGE_URL_PATHS["settings"]),
    "dashboard_alias": st.Page(page_dashboard_alias, title="Dashboard", icon="\U0001F4F0",
                               url_path=PAGE_URL_PATHS["dashboard_alias"], visibility="hidden"),
}

NAVIGATION = {
    "News": [PAGES["dashboard"], PAGES["dashboard_alias"], PAGES["x_radar"],
             PAGES["research"], PAGES["tiktok"]],
    "Reference": [PAGES["events"], PAGES["fighters"], PAGES["rankings"], PAGES["cards"]],
    "Tools": [PAGES["watchlists"], PAGES["search"], PAGES["sources"], PAGES["settings"]],
}


nav.register(PAGES)


def main() -> None:
    inject_css()
    bootstrap()
    page = st.navigation(NAVIGATION, position="sidebar")

    # The app name, once per screen. The dashboard's own heading already says
    # it in full, so repeating it there was both duplication and the line that
    # sat clipped under the toolbar.
    if (page.url_path or "") != "":
        st.markdown('<div class="brandline">UFC NEWS <span>RADAR</span></div>',
                    unsafe_allow_html=True)


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

    for parameter, owners, target in nav.ITEM_OWNERS:
        if current in owners or nav.selection(parameter) is None:
            continue
        if same_page and st.query_params.get(parameter):
            nav.remember(parameter)
            st.switch_page(PAGES[target])
        nav.clear_items(parameter)


    sidebar_extras()
    page.run()

    # Put the open item back in the address bar, after the page has rendered.
    # ``st.switch_page`` navigates without carrying the query string, which is
    # why an opened story used to sit at "/" with no parameters and could not
    # be refreshed, bookmarked or shared. Writing the parameter only updates
    # the URL - it does not rerun the script - and doing it last means the
    # browser applies it to the page it has already navigated to, instead of
    # leaving a history entry for a URL that never existed.
    for parameter, owners, _target in nav.ITEM_OWNERS:
        if current in owners:
            nav.sync_url(parameter)


if __name__ == "__main__":
    main()
