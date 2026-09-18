"""Source health: which feeds work, which are failing, and why."""
from __future__ import annotations

import streamlit as st

from collectors.registry import available_adapters
from database import repo_articles as articles_repo
from database import repo_runs as runs_repo
from database import repo_sources as sources_repo
from social.x_monitor import x_status_panel
from ui.components import metric_row, notice, page_header, section_header
from utils.textutil import truncate
from utils.timeutil import format_display, humanize_age

STATUS_ICONS = {"ok": "✅", "error": "❌", "disabled": "⚪", "unknown": "❓"}


def render(run_collection_callback) -> None:
    page_header("SOURCE HEALTH", "Every source runs on its own - one failure never stops the app.")
    rows = sources_repo.source_health()
    ok_count = len([row for row in rows if row["status"] == "ok"])
    error_count = len([row for row in rows if row["status"] == "error"])
    x_status = x_status_panel()
    last_run = runs_repo.last_run()

    metric_row([
        ("Sources", len(rows)),
        ("Healthy", ok_count),
        ("Failing", error_count, error_count > 0),
        ("Articles stored", articles_repo.article_count()),
        ("X API", "OK" if x_status["configured"] else "NOT CONFIGURED", not x_status["configured"]),
        ("Last run", humanize_age(last_run["started_at"]) if last_run else "never"),
    ])

    if st.button("\U0001F504 Run collection now", type="primary"):
        run_collection_callback()

    section_header("SOURCES", len(rows))
    for row in rows:
        icon = STATUS_ICONS.get(row["status"], "❓")
        enabled_label = "enabled" if row["enabled"] else "disabled"
        last_success = (f"last success {humanize_age(row['last_success_at'])}"
                        if row["last_success_at"] else "no successful fetch yet")
        error_line = ""
        if row.get("last_error"):
            error_line = (
                f'<div class="muted">error ({row.get("last_error_kind")}): '
                f'{truncate(row["last_error"], 160)} · {humanize_age(row.get("last_error_at"))}</div>'
            )
        st.markdown(
            f'<div class="panel"><div class="kv">{icon} <b>{row["name"]}</b> '
            f'<span class="muted">· {row["source_type"]} · weight {row["reliability_weight"]} · '
            f'{enabled_label} · {row["adapter"]}</span></div>'
            f'<div class="muted">{last_success} · {row["article_count"]} items collected · '
            f'latest item {humanize_age(row["last_article_at"]) if row["last_article_at"] else "n/a"}</div>'
            f'<div class="muted">{row.get("resolved_feed_url") or row.get("feed_url") or ""}</div>'
            f'{error_line}</div>',
            unsafe_allow_html=True,
        )
        columns = st.columns([1, 1, 1, 5])
        if columns[0].button("Disable" if row["enabled"] else "Enable", key=f"src_toggle_{row['id']}"):
            sources_repo.set_source_enabled(int(row["id"]), not row["enabled"])
            st.rerun()
        if columns[1].button("Test", key=f"src_test_{row['id']}"):
            _test_source(int(row["id"]))
        source = sources_repo.get_source(int(row["id"]))
        if source and not source.get("is_builtin"):
            # Built-in sources can be disabled but not deleted; ones you added can go.
            if columns[2].button("Delete", key=f"src_del_{row['id']}"):
                sources_repo.delete_source(int(row["id"]))
                st.rerun()

    section_header("X API")
    if x_status["configured"]:
        st.markdown(f'<div class="kv">✅ {x_status["label"]}</div>', unsafe_allow_html=True)
    else:
        notice("❌ <b>X API - NOT CONFIGURED.</b> Add X_BEARER_TOKEN to .env to enable "
               "X monitoring. Everything else keeps working.")

    _render_runs()
    _render_add_source()


def _test_source(source_id: int) -> None:
    """Fetch this one source now and report exactly what happened."""
    from collectors.registry import build_collector
    from utils.http import HttpClient

    source = sources_repo.get_source(source_id)
    if source is None:
        st.error("Source not found.")
        return
    # Short timeout, no retries: a test should answer quickly either way.
    collector = build_collector(source, client=HttpClient(timeout=12, max_retries=0))
    if collector is None:
        st.error(f"No adapter registered for '{source.get('adapter')}'.")
        return
    with st.spinner(f"Testing {source['name']}..."):
        result = collector.run()
    if result.ok:
        st.success(f"{source['name']}: fetched {result.count} item(s) from "
                   f"{result.resolved_url or source.get('feed_url')}")
        for item in result.items[:3]:
            st.caption(f"· {item.get('title', '')[:110]}")
    else:
        st.error(f"{source['name']} failed ({result.error_kind}): {result.error}")


def _render_runs() -> None:
    runs = runs_repo.recent_runs(limit=10)
    section_header("RECENT COLLECTION RUNS", len(runs))
    if not runs:
        st.markdown('<div class="muted">No runs recorded yet.</div>', unsafe_allow_html=True)
        return
    import pandas as pd

    frame = pd.DataFrame([
        {
            "Started": format_display(run.get("started_at"), "%b %d %H:%M"),
            "Trigger": run.get("trigger"),
            "Sources OK": f'{run.get("sources_ok")}/{run.get("sources_attempted")}',
            "New articles": run.get("articles_new"),
            "New stories": run.get("stories_new"),
            "X posts": run.get("social_new"),
            "Duration": f'{(run.get("duration_ms") or 0) / 1000:.1f}s',
            "Errors": truncate(run.get("error") or "", 60),
        }
        for run in runs
    ])
    st.dataframe(frame, use_container_width=True, hide_index=True)


def _render_add_source() -> None:
    section_header("ADD A SOURCE",
                   note="Any RSS/Atom feed works. New sources are isolated like the built-in ones.")
    with st.form("add_source_form", clear_on_submit=True):
        columns = st.columns([1.2, 1.6, 1.2, 1, 0.8])
        name = columns[0].text_input("Name")
        feed_url = columns[1].text_input("Feed URL")
        source_type = columns[2].selectbox("Source type", [
            "MAJOR_NEWS", "ESTABLISHED_JOURNALIST", "TRUSTED_REPORTER", "OFFICIAL", "INSIDER",
            "UNKNOWN", "FAN_ACCOUNT",
        ])
        weight = columns[3].number_input("Reliability", 0.0, 1.0, 0.5, 0.05)
        submitted = columns[4].form_submit_button("Add")
        if submitted:
            if not name.strip() or not feed_url.strip().startswith("http"):
                st.error("A name and a full feed URL (starting with http) are required.")
            else:
                from utils.textutil import domain_of, slugify

                sources_repo.upsert_source({
                    "key": slugify(name)[:40] or f"custom_{abs(hash(feed_url)) % 10000}",
                    "name": name.strip(),
                    "adapter": "rss",
                    "feed_url": feed_url.strip(),
                    "homepage": feed_url.strip(),
                    "domain": domain_of(feed_url),
                    "source_type": source_type,
                    "reliability_weight": float(weight),
                    "independence_group": domain_of(feed_url),
                    "priority": 60,
                    "enabled": 1,
                    "is_builtin": 0,
                    "notes": "added from the Source Health page",
                })
                st.success(f"Added {name.strip()}. Press Test to check it.")
                st.rerun()
    st.caption(f"Available adapters: {', '.join(available_adapters())}")
