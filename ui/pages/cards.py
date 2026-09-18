"""Fight-card change tracking across every event."""
from __future__ import annotations

import streamlit as st

from database import repo_entities as entities_repo
from ui.components import metric_row, navigate, page_header, section_header
from utils.timeutil import format_display

CHANGE_LABELS = {
    "new_fight": "New fight",
    "cancellation": "Cancellation",
    "replacement": "Replacement",
    "opponent_change": "Opponent change",
    "main_event_change": "Main event change",
    "co_main_change": "Co-main change",
    "title_fight_change": "Title fight change",
    "weight_class_change": "Weight class change",
    "status_change": "Status change",
}


def render() -> None:
    page_header("FIGHT CARD CHANGES", "New fights, cancellations, replacements and card shuffles.")
    changes = entities_repo.card_changes(limit=200)

    by_type = {}
    for change in changes:
        by_type[change.get("change_type")] = by_type.get(change.get("change_type"), 0) + 1
    metric_row([
        ("Changes tracked", len(changes)),
        ("New fights", by_type.get("new_fight", 0)),
        ("Cancellations", by_type.get("cancellation", 0)),
        ("Replacements", by_type.get("replacement", 0) + by_type.get("opponent_change", 0)),
        ("Events", len({change.get("event_name") for change in changes if change.get("event_name")})),
    ])

    if not changes:
        st.markdown(
            '<div class="panel"><h4>No card changes detected yet</h4><div class="muted">'
            'Changes are detected from collected reporting: a booking, a withdrawal, a replacement '
            'or a cancellation. Press Refresh on the dashboard to collect.</div></div>',
            unsafe_allow_html=True)
        return

    types = ["ALL"] + sorted({change.get("change_type") for change in changes if change.get("change_type")})
    selected = st.selectbox("Change type", types, key="cards_type")
    filtered = [c for c in changes if selected == "ALL" or c.get("change_type") == selected]

    section_header("CHANGE LOG", len(filtered))
    for change in filtered[:80]:
        status_color = {"CONFIRMED": "#19c37d", "REPORTED": "#e8c547",
                        "DEVELOPING": "#ff9130", "RUMOR": "#ef4444"}.get(change.get("status"), "#9aa4b2")
        st.markdown(
            f'<div class="panel" style="border-left:3px solid {status_color}">'
            f'<div class="kv"><b>{CHANGE_LABELS.get(change.get("change_type"), change.get("change_type"))}</b>'
            f' · {change.get("event_name") or "event not identified"}</div>'
            f'<div class="kv">BEFORE: {change.get("before_text") or "-"}</div>'
            f'<div class="kv">AFTER: {change.get("after_text") or "-"}</div>'
            f'<div class="muted">REASON: {change.get("reason") or "not stated in the sources"} · '
            f'STATUS: {change.get("status")} · '
            f'SOURCE: {change.get("source_name") or "n/a"} · '
            f'{format_display(change.get("detected_at"), "%b %d %H:%M UTC")}'
            + (f' · <a href="{change.get("source_url")}" target="_blank">open source</a>'
               if change.get("source_url") else "")
            + '</div></div>',
            unsafe_allow_html=True,
        )
        if change.get("story_id"):
            if st.button("Open the story", key=f"card_story_{change['id']}"):
                navigate("research", story=change["story_id"])
