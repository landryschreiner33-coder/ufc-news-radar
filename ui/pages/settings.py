"""Settings: refresh, sources, X, AI, watchlists, thresholds, demo data."""
from __future__ import annotations

import streamlit as st

from ai.factory import available_providers
from ai import service as ai_service
from collectors import scheduler
from database import repo_ai as ai_repo
from database import repo_settings as settings_repo
from database import repo_sources as sources_repo
from database.db import database_stats
from database.demo_data import clear_demo_data, demo_counts, demo_data_present, load_demo_data
from models.types import SORT_OPTIONS, FEED_FILTERS, SourceType
from social.x_monitor import x_status_panel
from ui.components import bullet_list, metric_row, notice, page_header, section_header
from utils.config import get_config


def render() -> None:
    page_header("SETTINGS", "Secrets live in your .env file, or the Secrets box if you deploy - never in the app or the database.")
    tabs = st.tabs([
        "General", "Sources", "X / Twitter", "AI provider", "Source classifications",
        "Demo data", "Database", "Data corrections",
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
    with tabs[7]:
        _render_corrections()


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

    section_header(
        "BACKGROUND COLLECTION",
        note="The dashboard reads results; something has to produce them. The collector that "
             "keeps working while this app is closed is `python scripts/scheduler.py` - run it "
             "beside the app and point both at the same database.")
    columns = st.columns([1.4, 1, 2])
    auto_collect = columns[0].toggle(
        "Collect in the background while the app is open",
        value=settings_repo.get_bool(scheduler.ENABLED_SETTING, False),
        help="A thread inside this app process. It stops when the app stops, so it is a "
             "fallback, not a replacement for running the collector separately.")
    auto_interval = columns[1].number_input(
        "Every (minutes)", scheduler.MIN_INTERVAL_MINUTES, 240,
        settings_repo.get_int(scheduler.INTERVAL_SETTING, scheduler.DEFAULT_INTERVAL_MINUTES), 5,
        help=f"Never below {scheduler.MIN_INTERVAL_MINUTES} minutes - news feeds do not change "
             "faster than that and the sources deserve better.")
    columns[2].caption(scheduler.status().summary)

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
        settings_repo.set_setting(scheduler.ENABLED_SETTING, bool(auto_collect), "bool")
        settings_repo.set_setting(scheduler.INTERVAL_SETTING, int(auto_interval), "int")
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
    if st.button("Clear cached generations",
                 help="Summaries, scripts and checks are regenerated on next open."):
        ai_repo.clear_cache()
        st.success("Cleared the AI response cache.")
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
    from database.persistence import storage_report

    section_header("DATABASE")
    report = storage_report()
    # No filesystem path: it is server-side detail the reader cannot act on,
    # and printing it tells anyone looking over their shoulder where the app
    # lives. The description below is what is actually useful.
    st.markdown(
        f'<div class="kv">Storage: <b>{report.location}</b><br>'
        f'Backend: <b>{report.backend}</b>'
        + (f' · size <b>{report.size_mb} MB</b>' if report.backend.startswith("sqlite") else "")
        + '</div>',
        unsafe_allow_html=True)
    stats = database_stats()

    import pandas as pd

    frame = pd.DataFrame([{"Table": key, "Rows": value} for key, value in stats.items()])
    st.dataframe(frame, width="stretch", hide_index=True, height=420)
    bullet_list([
        "Change the SQLite location with UFC_RADAR_DB in .env.",
        "Use PostgreSQL instead for permanent history on a host with a temporary "
        "filesystem: either DATABASE_URL, or a [database] section in the Secrets box "
        "with host, port, database, username and password.",
        "Deleting the database resets everything; the app rebuilds the schema on the next start.",
    ])

    _render_storage()
    _render_backup()


def _render_storage() -> None:
    """Say plainly whether collected history will actually survive a restart."""
    from database.persistence import storage_report

    report = storage_report()
    section_header("STORAGE")
    box = "warn-box" if report.is_ephemeral else "panel"
    st.markdown(
        f'<div class="{box}"><b>{report.headline}</b><br>{report.detail}</div>',
        unsafe_allow_html=True)
    st.markdown(
        f'<div class="kv">Backend: <b>{report.backend}</b> · size <b>{report.size_mb} MB</b></div>',
        unsafe_allow_html=True)
    bullet_list(report.advice)


def _render_backup() -> None:
    from database.persistence import (
        backup_supported, export_bytes, restore_database, validate_backup,
    )

    section_header("BACKUP & RESTORE",
                   note="A backup is a single file. Keep one before any redeploy.")
    if not backup_supported():
        st.markdown(
            '<div class="kv">This app is using PostgreSQL, so backups belong to your database '
            'provider (managed snapshots, or <code>pg_dump</code>). The file export here only '
            'applies to SQLite, and pretending otherwise would give you a backup that '
            'contained nothing.</div>', unsafe_allow_html=True)
        return

    columns = st.columns([1, 1])
    with columns[0]:
        try:
            st.download_button(
                "⬇ Download a backup", data=export_bytes(),
                file_name="ufc_news_radar_backup.db", mime="application/octet-stream",
                width="stretch", key="backup_download")
        except Exception as exc:
            st.error(f"Could not build a backup: {exc}")
    with columns[1]:
        upload = st.file_uploader("Restore from a backup file", type=["db", "sqlite"],
                                  key="backup_upload")
        if upload is not None:
            import tempfile
            from pathlib import Path as _Path

            folder = tempfile.mkdtemp()
            target = _Path(folder) / "restore.db"
            target.write_bytes(upload.getvalue())
            check = validate_backup(str(target))
            if not check["ok"]:
                st.error(check["error"])
            else:
                st.info(f"This backup holds {check['articles']} articles and "
                        f"{check['stories']} stories. Restoring replaces everything "
                        "currently collected.")
                if st.button("Replace the current database", type="primary",
                             key="backup_restore_confirm"):
                    outcome = restore_database(str(target))
                    if outcome.get("ok"):
                        st.success("Restored. The previous database was kept alongside it.")
                        st.rerun()
                    else:
                        st.error(outcome.get("error", "Restore failed."))


def _render_corrections() -> None:
    """Manual fixes for the things the automated rules get wrong.

    Nothing here invents data - these only merge, relabel or reassign what was
    already collected, and every change is recorded with a reason.
    """
    from database import corrections
    from database import repo_entities as entities_repo
    from database import repo_sources as sources_repo
    from database import repo_stories as stories_repo

    section_header("DATA CORRECTIONS",
                   note="These only merge or relabel collected data. They never invent "
                        "facts, sources or quotes, and every change is logged below.")

    st.markdown("**Merge two events**")
    events = entities_repo.all_events()
    if len(events) >= 2:
        labels = {f"#{event['id']} {event['name']}": int(event["id"]) for event in events}
        columns = st.columns([2, 2, 2])
        keep = columns[0].selectbox("Keep", list(labels), key="fix_ev_keep")
        drop = columns[1].selectbox("Merge away", list(labels), index=1, key="fix_ev_drop")
        reason = columns[2].text_input("Reason", key="fix_ev_reason",
                                       placeholder="same event, two spellings")
        if st.button("Merge events", key="fix_ev_go"):
            outcome = corrections.merge_events(labels[keep], labels[drop], reason)
            (st.success if outcome["ok"] else st.error)(
                outcome.get("message") or outcome.get("error"))
            if outcome["ok"]:
                st.rerun()
    else:
        st.markdown('<div class="muted">Need at least two events.</div>', unsafe_allow_html=True)

    st.markdown("**Merge two fighters**")
    fighters = entities_repo.list_fighters(limit=400, order="mentions")
    named = [fighter for fighter in fighters if fighter.get("mention_count")]
    pool = named or fighters[:80]
    if len(pool) >= 2:
        labels = {f"#{fighter['id']} {fighter['name']}": int(fighter["id"]) for fighter in pool}
        columns = st.columns([2, 2, 2])
        keep = columns[0].selectbox("Keep", list(labels), key="fix_fi_keep")
        drop = columns[1].selectbox("Merge away", list(labels), index=1, key="fix_fi_drop")
        reason = columns[2].text_input("Reason", key="fix_fi_reason",
                                       placeholder="duplicate spelling")
        if st.button("Merge fighters", key="fix_fi_go"):
            outcome = corrections.merge_fighters(labels[keep], labels[drop], reason)
            (st.success if outcome["ok"] else st.error)(
                outcome.get("message") or outcome.get("error"))
            if outcome["ok"]:
                st.rerun()

    st.markdown("**Fix a story's category or event**")
    stories = stories_repo.list_stories(limit=80, sort="Recently Updated")
    if stories:
        from models.types import CATEGORY_LABELS, Category

        labels = {f"#{story['id']} {story['headline'][:70]}": int(story["id"])
                  for story in stories}
        chosen = st.selectbox("Story", list(labels), key="fix_st_pick")
        story_id = labels[chosen]
        current = next(story for story in stories if int(story["id"]) == story_id)

        columns = st.columns([2, 2, 2])
        categories = [member.value for member in Category]
        category = columns[0].selectbox(
            "Category", categories,
            index=categories.index(current["category"]) if current["category"] in categories else 0,
            format_func=lambda value: CATEGORY_LABELS.get(value, value), key="fix_st_cat")
        events = entities_repo.all_events()
        event_labels = {"(none)": None}
        event_labels.update({f"#{event['id']} {event['name']}": int(event["id"])
                             for event in events})
        event_choice = columns[1].selectbox("Event", list(event_labels), key="fix_st_ev")
        reason = columns[2].text_input("Reason", key="fix_st_reason",
                                       placeholder="mis-categorised by the keyword rules")

        buttons = st.columns([1, 1, 3])
        if buttons[0].button("Set category", key="fix_st_cat_go"):
            outcome = corrections.recategorize_story(story_id, category, reason)
            (st.success if outcome["ok"] else st.error)(
                outcome.get("message") or outcome.get("error"))
        if buttons[1].button("Set event", key="fix_st_ev_go"):
            outcome = corrections.reassign_story_event(
                story_id, event_labels[event_choice], reason)
            (st.success if outcome["ok"] else st.error)(
                outcome.get("message") or outcome.get("error"))

    st.markdown("**Reclassify a source**")
    st.markdown(
        '<div class="muted">Changes how much weight a source carries from now on. '
        'It never rewrites stories that were already scored.</div>',
        unsafe_allow_html=True)
    sources = sources_repo.list_sources()
    if sources:
        from models.types import SourceType

        source_labels = {f"{source['name']} ({source['source_type']})": int(source["id"])
                         for source in sources}
        columns = st.columns([2, 1.5, 1.2, 2])
        picked = columns[0].selectbox("Source", list(source_labels), key="fix_src_pick")
        types = [member.value for member in SourceType]
        new_type = columns[1].selectbox("New type", types, key="fix_src_type")
        weight = columns[2].number_input("Reliability", 0.0, 1.0, 0.5, 0.05, key="fix_src_weight")
        reason = columns[3].text_input("Reason", key="fix_src_reason",
                                       placeholder="they have been reliable/unreliable")
        if st.button("Reclassify source", key="fix_src_go"):
            outcome = corrections.reclassify_source(
                source_labels[picked], new_type, weight, reason=reason)
            (st.success if outcome["ok"] else st.error)(
                outcome.get("message") or outcome.get("error"))

    section_header("CORRECTION HISTORY")
    rows = corrections.history(limit=60)
    if not rows:
        st.markdown('<div class="muted">No manual corrections have been made.</div>',
                    unsafe_allow_html=True)
        return
    import pandas as pd

    st.dataframe(pd.DataFrame([
        {"When": row["applied_at"], "Kind": row["kind"], "Table": row["target_table"],
         "Before": str(row["before_value"])[:60], "After": str(row["after_value"])[:60],
         "Reason": row["reason"] or "-"}
        for row in rows
    ]), width="stretch", hide_index=True)
