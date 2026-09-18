"""Watchlists: fighters, events, topics and X accounts you care about."""
from __future__ import annotations

from typing import Any, Dict, List

import streamlit as st

from database import repo_settings as settings_repo
from database import repo_stories as stories_repo
from social import accounts as accounts_mod
from ui.components import metric_row, navigate, page_header, section_header, story_grid
from utils.textutil import normalize_text

KINDS = [
    ("fighter", "Fighters", "e.g. Jon Jones"),
    ("event", "Events", "e.g. UFC 320"),
    ("topic", "Topics", "e.g. title fights, injuries"),
]


def render() -> None:
    page_header("WATCHLISTS", "Anything you watch is boosted in the feed and surfaced here.")

    watched_stories = _watchlist_stories()
    metric_row([
        ("Fighters", len(settings_repo.list_watchlist("fighter"))),
        ("Events", len(settings_repo.list_watchlist("event"))),
        ("Topics", len(settings_repo.list_watchlist("topic"))),
        ("X accounts", len(accounts_mod.list_accounts(enabled_only=True))),
        ("Matching stories", len(watched_stories), len(watched_stories) > 0),
    ])

    section_header("NEW ON YOUR WATCHLIST", len(watched_stories),
                   note="Stories mentioning anything you watch, newest first.")
    story_grid(watched_stories[:10], "watch",
               empty_message="No collected story matches your watchlist yet.")

    columns = st.columns(3)
    for index, (kind, label, placeholder) in enumerate(KINDS):
        with columns[index]:
            section_header(label.upper())
            with st.form(f"watch_form_{kind}", clear_on_submit=True):
                value = st.text_input(f"Add {label[:-1].lower()}", placeholder=placeholder,
                                      key=f"watch_input_{kind}")
                if st.form_submit_button("Add") and value.strip():
                    settings_repo.add_watchlist_item(kind, value.strip())
                    st.rerun()
            for item in settings_repo.list_watchlist(kind):
                row = st.columns([3, 1])
                row[0].markdown(f'<div class="kv">{item["value"]}</div>', unsafe_allow_html=True)
                if row[1].button("Remove", key=f"watch_del_{kind}_{item['id']}"):
                    settings_repo.remove_watchlist_item(int(item["id"]))
                    st.rerun()

    section_header("MONITORED X ACCOUNTS",
                   note="Managed on the X Monitoring page, shown here for convenience.")
    accounts = accounts_mod.list_accounts(enabled_only=True)
    if accounts:
        st.markdown(
            " ".join(f'<span class="chip">@{account["username"]}</span>' for account in accounts[:40]),
            unsafe_allow_html=True,
        )
    if st.button("Manage X accounts →"):
        navigate("social")


def _watchlist_stories() -> List[Dict[str, Any]]:
    terms = [item["value"] for item in settings_repo.list_watchlist()
             if item.get("kind") in ("fighter", "event", "topic")]
    if not terms:
        return []
    found: Dict[int, Dict[str, Any]] = {}
    for term in terms:
        for story in stories_repo.list_stories(limit=30, search=term, sort="Newest"):
            found[int(story["id"])] = story
    return sorted(found.values(), key=lambda story: story.get("first_seen_at") or "", reverse=True)
