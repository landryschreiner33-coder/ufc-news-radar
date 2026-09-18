"""Settings: refresh, sources, X, AI, watchlists, thresholds, demo data."""
from __future__ import annotations

import streamlit as st

from ai.factory import available_providers
from ai import service as ai_service
from database import repo_settings as settings_repo
from database import repo_sources as sources_repo
from database.db import database_stats, current_db_path
from database.demo_data import clear_demo_data, demo_counts, demo_data_present, load_demo_data
from models.types import SORT_OPTIONS, FEED_FILTERS, SourceType
from social.x_monitor import x_status_panel
from ui.components import bullet_list, metric_row, notice, page_header, section_header
from utils.config import get_config


def render() -> None:
    page_header("SETTINGS", "Secrets live in your .env file - never in the app or the database.")
    tabs = st.tabs([
        "General", "Sources", "X / Twitter", "AI provider", "Source classifications",
        "Demo data", "Database",
    ])
    with tabs[0]:
        _render_general()
    with tabs[1]:
        _render_sources()
    with tabs[2]:
        _render_x()
    with tabs[3]:
        _render_ai()
    with tabs[4]:
        _render_classifications()
    with tabs[5]:
        _render_demo()
    with tabs[6]:
        _render_database()


def _render_general() -> None:
    section_header("FEED DEFAULTS")
    columns = st.columns(3)
    refresh_interval = columns[0].number_input(
        "Refresh interval (minutes)", 5, 360,
        settings_repo.get_int("refresh_interval_minutes", 20), 5,
        help="How often the sidebar suggests collecting again.",
    )
    min_relevance = columns[1].number_input(
        "Minimum relevance in the feed", 0, 100, settings_repo.get_int("min_relevance", 0), 5)
    feed_max = columns[2].number_input(
        "Max stories per feed", 10, 300, settings_repo.get_int("feed_max_stories", 60), 10)

    columns = st.columns(3)
    default_sort = columns[0].selectbox(
        "Default sort", SORT_OPTIONS,
        index=SORT_OPTIONS.index(settings_repo.get_setting("default_sort", "Newest"))
        if settings_repo.get_setting("default_sort", "Newest") in SORT_OPTIONS else 0)
    default_filter = columns[1].selectbox(
        "Default filter", FEED_FILTERS,
        index=FEED_FILTERS.index(settings_repo.get_setting("default_filter", "ALL"))
        if settings_repo.get_setting("default_filter", "ALL") in FEED_FILTERS else 0)
    theme = columns[2].selectbox("Theme", ["dark"], index=0,
                                 help="The dashboard is designed dark; light mode is not supported yet.")

    section_header("THRESHOLDS", note="These only affect how the feed is organised.")
    columns = st.columns(4)
    breaking_hours = columns[0].number_input(
        "Breaking window (hours)", 1, 48, settings_repo.get_int("breaking_window_hours", 8))
    breaking_relevance = columns[1].number_input(
        "Breaking min relevance", 0, 100, settings_repo.get_int("breaking_min_relevance", 70), 5)
    developing_hours = columns[2].number_input(
        "Developing window (hours)", 2, 168, settings_repo.get_int("developing_window_hours", 48))
    trending_hours = columns[3].number_input(
        "Trending window (hours)", 1, 72, settings_repo.get_int("trending_window_hours", 12))

    section_header("STORY GROUPING", note="Higher thresholds group less and keep stories separate.")
    columns = st.columns(3)
    match_window = columns[0].number_input(
        "Match window (hours)", 6, 240, settings_repo.get_int("story_match_window_hours", 72), 6)
    similarity = columns[1].slider(
        "Similarity threshold", 0.2, 0.9, settings_repo.get_float("story_similarity_threshold", 0.42), 0.02)
    title_similarity = columns[2].slider(
        "Headline-match threshold", 0.3, 0.95,
        settings_repo.get_float("story_title_similarity_threshold", 0.62), 0.02)

    section_header("ARTICLE TEXT")
    columns = st.columns(2)
    fetch_text = columns[0].toggle(
        "Fetch a short extract from article pages",
        value=settings_repo.get_bool("article_fetch_full_text", True),
        help="Only a short extract is stored for analysis; full articles are never copied.")
    fetch_limit = columns[1].number_input(
        "Max article fetches per run", 0, 60, settings_repo.get_int("article_fetch_limit_per_run", 12))

    if st.button("Save settings", type="primary"):
        settings_repo.set_setting("refresh_interval_minutes", int(refresh_interval), "int")
        settings_repo.set_setting("min_relevance", int(min_relevance), "int")
        settings_repo.set_setting("feed_max_stories", int(feed_max), "int")
        settings_repo.set_setting("default_sort", default_sort, "str")
        settings_repo.set_setting("default_filter", default_filter, "str")
        settings_repo.set_setting("theme", theme, "str")
        settings_repo.set_setting("breaking_window_hours", int(breaking_hours), "int")
        settings_repo.set_setting("breaking_min_relevance", int(breaking_relevance), "int")
        settings_repo.set_setting("developing_window_hours", int(developing_hours), "int")
        settings_repo.set_setting("trending_window_hours", int(trending_hours), "int")
        settings_repo.set_setting("story_match_window_hours", int(match_window), "int")
        settings_repo.set_setting("story_similarity_threshold", float(similarity), "float")
        settings_repo.set_setting("story_title_similarity_threshold", float(title_similarity), "float")
        settings_repo.set_setting("article_fetch_full_text", bool(fetch_text), "bool")
        settings_repo.set_setting("article_fetch_limit_per_run", int(fetch_limit), "int")
        st.success("Saved. New thresholds apply on the next collection run.")


def _render_sources() -> None:
    section_header("ENABLED SOURCES",
                   note="Disabled sources are skipped entirely. Add new ones on the Source Health page.")
    for source in sources_repo.list_sources():
        columns = st.columns([2.2, 1.2, 1.1, 1, 1])
        columns[0].markdown(f'<div class="kv">{source["name"]}</div>', unsafe_allow_html=True)
        columns[1].markdown(f'<div class="muted">{source["source_type"]}</div>', unsafe_allow_html=True)
        new_weight = columns[2].number_input(
            "weight", 0.0, 1.0, float(source["reliability_weight"]), 0.05,
            key=f"set_weight_{source['id']}", label_visibility="collapsed")
        enabled = columns[3].toggle("enabled", value=bool(source["enabled"]),
                                    key=f"set_enabled_{source['id']}", label_visibility="collapsed")
        if columns[4].button("Save", key=f"set_save_{source['id']}"):
            sources_repo.update_source_fields(int(source["id"]), reliability_weight=float(new_weight))
            sources_repo.set_source_enabled(int(source["id"]), bool(enabled))
            st.success(f"Updated {source['name']}")
            st.rerun()


def _render_x() -> None:
    status = x_status_panel()
    section_header("X / TWITTER")
    if status["configured"]:
        st.success("X_BEARER_TOKEN is set - X monitoring is active.")
    else:
        notice(
            "<b>X MONITORING - NOT CONFIGURED.</b><br>"
            "1. Create a project + app at developer.x.com<br>"
            "2. Copy the App-only <b>Bearer Token</b><br>"
            "3. Put <code>X_BEARER_TOKEN=your-token</code> in the .env file<br>"
            "4. Restart the app<br>"
            "The token is read from the environment only - it is never stored in the database."
        )
    bullet_list([
        f"Recent search window: {status['search_window']}",
        f"Searches per run: {status['max_searches_per_run']} (X_MAX_SEARCHES_PER_RUN)",
        f"Timelines per run: {status['max_timelines_per_run']} (X_MAX_TIMELINES_PER_RUN)",
        f"Results per query: {status['max_results_per_query']} (X_MAX_RESULTS_PER_QUERY)",
        f"Repeat-query cache: {status['cache_minutes']} minutes (X_QUERY_CACHE_MINUTES)",
    ])
    columns = st.columns(3)
    x_enabled = columns[0].toggle("X monitoring on", value=settings_repo.get_bool("x_enabled", True))
    search_enabled = columns[1].toggle("Use recent search",
                                       value=settings_repo.get_bool("x_search_enabled", True))
    timelines_enabled = columns[2].toggle("Use account timelines",
                                          value=settings_repo.get_bool("x_timelines_enabled", True))
    if st.button("Save X settings"):
        settings_repo.set_setting("x_enabled", bool(x_enabled), "bool")
        settings_repo.set_setting("x_search_enabled", bool(search_enabled), "bool")
        settings_repo.set_setting("x_timelines_enabled", bool(timelines_enabled), "bool")
        st.success("Saved.")


def _render_ai() -> None:
    status = ai_service.ai_status()
    section_header("AI PROVIDER")
    st.markdown(
        f'<div class="panel"><div class="kv"><b>{status["label"]}</b></div>'
        f'<div class="muted">{status["note"]}<br>'
        f'Selected in .env: AI_PROVIDER={status["selected"]} · active: {status["active"]} · '
        f'cached generations: {status["cached_generations"]}</div></div>',
        unsafe_allow_html=True,
    )
    if not status["configured"]:
        notice(
            "Running in <b>template mode</b>. Summaries, scripts and reporting checks are built "
            "from your collected sources by built-in templates. To use an AI provider, set "
            "<code>AI_PROVIDER=anthropic</code> and <code>ANTHROPIC_API_KEY=...</code> (or the "
            "OpenAI equivalents) in .env and restart."
        )
    bullet_list([
        f"Providers available: {', '.join(available_providers())}",
        "Keys are read from environment variables only and are never written to the database.",
        "Generated text is cached against the exact source material, so the same story is never "
        "sent twice.",
        "All generation is grounded: prompts contain only collected sources, and quotes/figures "
        "that do not appear in them are flagged.",
    ])


def _render_classifications() -> None:
    section_header("SOURCE & ACCOUNT CLASSIFICATIONS",
                   note="These decide how much weight a source carries. Editable - nothing here is "
                        "an AI-generated reputation score.")
    kind = st.selectbox("Type", ["domain", "author", "x_account"], key="class_kind")
    rows = settings_repo.list_classifications(kind)
    with st.form("add_classification", clear_on_submit=True):
        columns = st.columns([1.6, 1.4, 1, 1, 0.8])
        identifier = columns[0].text_input("Identifier (domain, author or @account)")
        classification = columns[1].selectbox("Classification", [member.value for member in SourceType])
        weight = columns[2].number_input("Reliability", 0.0, 1.0, 0.5, 0.05)
        group = columns[3].text_input("Independence group")
        if columns[4].form_submit_button("Save") and identifier.strip():
            settings_repo.upsert_classification(
                kind, identifier.strip(), classification, float(weight),
                group.strip() or None, is_user_defined=True,
            )
            st.success(f"Saved {identifier.strip()}")
            st.rerun()

    st.caption(f"{len(rows)} entries")
    for row in rows[:150]:
        columns = st.columns([2, 1.4, 0.8, 1.2, 0.8])
        columns[0].markdown(f'<div class="kv">{row["identifier"]}</div>', unsafe_allow_html=True)
        columns[1].markdown(f'<div class="muted">{row["classification"]}</div>', unsafe_allow_html=True)
        columns[2].markdown(f'<div class="muted">{row["reliability_weight"]}</div>',
                            unsafe_allow_html=True)
        columns[3].markdown(f'<div class="muted">{row.get("independence_group") or "-"}</div>',
                            unsafe_allow_html=True)
        if columns[4].button("Delete", key=f"class_del_{row['id']}"):
            settings_repo.delete_classification(int(row["id"]))
            st.rerun()


def _render_demo() -> None:
    section_header("DEMO DATA",
                   note="Fictional examples for trying the interface. They are never presented as "
                        "real UFC news.")
    counts = demo_counts()
    metric_row([
        ("Demo stories", counts["stories"]),
        ("Demo articles", counts["articles"]),
        ("Demo X posts", counts["social_posts"]),
    ])
    columns = st.columns(2)
    if columns[0].button("Load demo data", disabled=demo_data_present()):
        result = load_demo_data()
        st.success(f"Loaded {result['articles']} demo articles and {result['posts']} demo posts.")
        st.rerun()
    if columns[1].button("Remove demo data", disabled=not demo_data_present(), type="primary"):
        removed = clear_demo_data()
        st.success(f"Removed {removed['stories']} demo stories.")
        st.rerun()
    bullet_list([
        "Every demo headline starts with [DEMO] and every row is flagged is_demo.",
        "Demo fighters, events and outlets are invented names; links point at example.invalid.",
        "Real collected news is never touched by loading or removing demo data.",
    ])


def _render_database() -> None:
    section_header("DATABASE")
    config = get_config()
    st.markdown(f'<div class="kv">SQLite file: <code>{current_db_path()}</code></div>',
                unsafe_allow_html=True)
    stats = database_stats()
    import pandas as pd

    frame = pd.DataFrame([{"Table": key, "Rows": value} for key, value in stats.items()])
    st.dataframe(frame, use_container_width=True, hide_index=True, height=420)
    bullet_list([
        "Change the location with UFC_RADAR_DB in .env.",
        "Deleting the file resets everything; the app rebuilds the schema on the next start.",
        "The schema is kept PostgreSQL-friendly (ISO-8601 UTC timestamps, JSON text columns).",
    ])
