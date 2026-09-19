"""X monitoring: status, monitored accounts and collected posts."""
from __future__ import annotations

import streamlit as st

from ai import service as ai_service
from database import repo_settings as settings_repo
from database import repo_social as social_repo
from social import accounts as accounts_mod
from social.x_client import RECENT_SEARCH_DAYS
from social.queries import planned_queries
from social.x_monitor import collect_x_activity, x_status_panel
from ui.components import bullet_list, metric_row, navigate, notice, page_header, section_header
from utils.textutil import truncate
from utils.timeutil import format_display, humanize_age


def render() -> None:
    page_header("X MONITORING", "Official X API only - no scraping, nothing simulated.")
    status = x_status_panel()

    if not status["configured"]:
        notice(
            "<b>X MONITORING - NOT CONFIGURED.</b><br>"
            "Add <code>X_BEARER_TOKEN</code> to your .env file and restart the app to enable it. "
            "Everything else in UFC News Radar keeps working without it. "
            "See the README section \"X API setup\" for the steps."
        )

    metric_row([
        ("X API", "ACTIVE" if status["configured"] else "NOT CONFIGURED", not status["configured"]),
        ("Posts stored", status["stored_posts"]),
        ("Last 48h", status["recent_posts_48h"]),
        ("Monitored accounts", status["monitored_accounts"]),
        ("Searches/run", status["max_searches_per_run"]),
        ("Timelines/run", status["max_timelines_per_run"]),
    ])
    st.markdown(
        f'<div class="muted">Recent search covers the last {RECENT_SEARCH_DAYS} days only - '
        'that is an X API access limit, not an app setting. Full-archive search needs a different '
        'access level and is not assumed anywhere in this app.</div>',
        unsafe_allow_html=True,
    )

    tabs = st.tabs(["Collected posts", "Monitored accounts", "Planned queries", "API usage"])
    with tabs[0]:
        _render_posts()
    with tabs[1]:
        _render_accounts()
    with tabs[2]:
        _render_queries()
    with tabs[3]:
        _render_usage(status)


def _render_posts() -> None:
    columns = st.columns([1, 1, 3])
    if columns[0].button("Collect from X now", disabled=not x_status_panel()["configured"]):
        with st.spinner("Calling the X API..."):
            outcome = collect_x_activity()
        st.success(outcome.status_line)
        for note in outcome.notes + outcome.errors:
            st.caption(note)
    search_term = columns[2].text_input("Search collected posts", key="social_search")

    posts = social_repo.search_posts(search_term, limit=60) if search_term else social_repo.latest_posts(60)
    section_header("COLLECTED POSTS", len(posts))
    if not posts:
        st.markdown('<div class="muted">No posts collected yet.</div>', unsafe_allow_html=True)
        return
    for post in posts:
        metrics = []
        if post.get("like_count") is not None:
            metrics.append(f"{post['like_count']} likes")
        if post.get("repost_count") is not None:
            metrics.append(f"{post['repost_count']} reposts")
        st.markdown(
            f'<div class="panel"><div class="muted">@{post.get("username")} · '
            f'<b>{post.get("account_type")}</b> · {format_display(post.get("created_at_source"))} · '
            f'{" · ".join(metrics) if metrics else "no metrics returned"}</div>'
            f'<div class="kv">{truncate(post.get("text"), 280)}</div>'
            + (f'<a href="{post.get("url")}" target="_blank">Open on X</a>' if post.get("url") else "")
            + "</div>",
            unsafe_allow_html=True,
        )
        with st.expander("What this post does and does not establish"):
            analysis = ai_service.analyze_social_post(int(post["id"]))
            st.markdown(f"**How to treat it:** {analysis.get('how_to_treat_it','')}")
            bullet_list(analysis.get("what_it_does_not_say", []), empty="")
            bullet_list(analysis.get("related_reporting", []), empty="")
            st.caption(analysis.get("notice", ""))


def _render_accounts() -> None:
    section_header("MONITORED ACCOUNTS",
                   note="Account type decides how much weight a post carries. "
                        "Verification badges and engagement never count as proof.")
    with st.form("add_account_form"):
        columns = st.columns([1.4, 1.4, 1.2, 1.2, 0.8])
        username = columns[0].text_input("Username (without @)")
        display_name = columns[1].text_input("Display name")
        account_type = columns[2].selectbox("Account type", accounts_mod.account_type_options())
        category = columns[3].selectbox("Category", [key for key, _ in accounts_mod.CATEGORIES],
                                        format_func=lambda key: accounts_mod.CATEGORY_LABELS[key])
        submitted = columns[4].form_submit_button("Add")
        if submitted and username.strip():
            accounts_mod.add_account(username, display_name or None, account_type, category)
            st.success(f"Added @{username.strip().lstrip('@')}")
            st.rerun()

    grouped = accounts_mod.grouped_accounts()
    for key, label in accounts_mod.CATEGORIES:
        rows = grouped.get(key) or []
        if not rows:
            continue
        st.markdown(f"**{label}** ({len(rows)})")
        for account in rows:
            columns = st.columns([2.4, 1.6, 1.2, 0.9, 0.9])
            columns[0].markdown(
                f'<div class="kv">@{account["username"]}'
                + (f' · {account["display_name"]}' if account.get("display_name") else "")
                + "</div>", unsafe_allow_html=True)
            columns[1].markdown(f'<div class="muted">{account["account_type"]}</div>',
                                unsafe_allow_html=True)
            last_checked = (humanize_age(account["last_checked_at"])
                            if account.get("last_checked_at") else "never checked")
            columns[2].markdown(f'<div class="muted">{last_checked}</div>', unsafe_allow_html=True)
            toggle_label = "On" if account["enabled"] else "Off"
            if columns[3].button(toggle_label, key=f"acct_toggle_{account['id']}"):
                accounts_mod.set_enabled(int(account["id"]), not account["enabled"])
                st.rerun()
            if columns[4].button("Remove", key=f"acct_del_{account['id']}"):
                accounts_mod.remove_account(int(account["id"]))
                st.rerun()
            if account.get("last_error"):
                st.caption(f"last error: {account['last_error']}")


def _render_queries() -> None:
    budget = settings_repo.get_setting("x_max_searches", None)
    section_header("QUERY PLAN",
                   note="These are the searches the app would run next, built from your watchlists "
                        "and the standing UFC news terms. Few and narrow, to protect your quota.")
    queries = planned_queries(6)
    if not queries:
        st.markdown('<div class="muted">No queries planned.</div>', unsafe_allow_html=True)
    for query in queries:
        st.markdown(
            f'<div class="panel"><div class="kv"><b>{query["name"]}</b> · '
            f'<span class="muted">{query["purpose"]}</span></div>'
            f'<div class="muted" style="font-family:monospace;font-size:0.76rem">'
            f'{query["query"]}</div></div>',
            unsafe_allow_html=True,
        )


def _render_usage(status) -> None:
    section_header("RECENT API CALLS",
                   note="Identical queries are skipped inside the cache window, and a rate limit "
                        "pauses that query until the reset time the API reported.")
    rows = status.get("recent_queries") or []
    if not rows:
        st.markdown('<div class="muted">No X API calls recorded.</div>', unsafe_allow_html=True)
        return
    import pandas as pd

    frame = pd.DataFrame([
        {
            "When": format_display(row.get("last_run_at"), "%b %d %H:%M"),
            "Endpoint": row.get("endpoint"),
            "Results": row.get("result_count"),
            "Status": row.get("status"),
            "Query": truncate(row.get("query"), 70),
            "Error": truncate(row.get("error") or "", 60),
        }
        for row in rows
    ])
    st.dataframe(frame, width="stretch", hide_index=True)
