"""Official UFC rankings: current snapshot, system in use, and what changed."""
from __future__ import annotations

import streamlit as st

from database import repo_rankings as rankings_repo
from processors.rankings_diff import describe_change
from ui import nav
from ui.components import bullet_list, metric_row, navigate, page_header, section_header
from utils.timeutil import format_display


def render() -> None:
    page_header("UFC RANKINGS", "Collected from the official UFC rankings page.")

    systems = rankings_repo.ranking_systems()
    divisions = rankings_repo.divisions()
    latest_date = rankings_repo.latest_ranking_date()

    metric_row([
        ("Snapshots", rankings_repo.ranking_snapshot_count()),
        ("Divisions", len(divisions)),
        ("Latest snapshot", latest_date or "none"),
        ("Systems seen", len(systems)),
    ])

    if not systems:
        st.markdown(
            '<div class="panel"><h4>No rankings collected yet</h4><div class="muted">'
            'Press <b>Refresh now</b> on the dashboard. The app reads the ranking system label '
            'off the UFC page rather than assuming one, and stores it with every snapshot.'
            '</div></div>', unsafe_allow_html=True)
        return

    section_header("RANKING SYSTEM IN USE",
                   note="The app records whichever system UFC publishes, exactly as the page "
                        "labels it. It does not assume any particular ranking method.")
    for system in systems:
        # Only the cleaned label is shown. The raw page text around it is page
        # navigation and table headers, not information about the system.
        version = str(system.get("system_version") or "").strip()
        version_html = (f'<br>Published on the page as: "{version}"'
                        if version and version.lower() != "unlabelled"
                        and version != system.get("system_name") else "")
        st.markdown(
            f'<div class="panel"><div class="kv"><b>{system.get("system_name")}</b></div>'
            f'<div class="muted">{system.get("rows_stored")} rows · '
            f'first seen {system.get("first_seen")} · last seen {system.get("last_seen")}'
            f'{version_html}</div></div>',
            unsafe_allow_html=True,
        )


    section_header("CURRENT RANKINGS")
    division = st.selectbox("Division", divisions, key="rank_division")
    rows = rankings_repo.current_rankings(division)
    if rows:
        import pandas as pd

        frame = pd.DataFrame([
            {
                "#": "C" if row.get("is_champion") else str(row.get("position") or ""),
                "Fighter": row.get("fighter_name"),
                "Ranking date": row.get("ranking_date"),
                "System": row.get("system_name"),
            }
            for row in rows
        ])
        st.dataframe(frame, width="stretch", hide_index=True)
        picker = st.selectbox("Open a fighter page", ["-"] + [row["fighter_name"] for row in rows],
                              key="rank_fighter_pick")
        if picker != "-":
            nav.open_fighter(picker)
    else:
        st.markdown('<div class="muted">No rows for this division.</div>', unsafe_allow_html=True)

    _render_changes()


def _render_changes() -> None:
    changes = rankings_repo.recent_ranking_changes(limit=60)
    section_header("RANKING CHANGES", len(changes),
                   note="Detected by comparing consecutive collected snapshots. "
                        "Nothing here is estimated.")
    if not changes:
        st.markdown(
            '<div class="muted">No changes detected yet. The app needs at least two collected '
            'snapshots before it can compare them.</div>', unsafe_allow_html=True)
        return
    import pandas as pd

    frame = pd.DataFrame([
        {
            "Date": change.get("ranking_date"),
            "Division": change.get("division"),
            "Fighter": change.get("fighter_name"),
            "Change": describe_change(change),
            "From": change.get("previous_position"),
            "To": change.get("new_position"),
            "System": change.get("system_version"),
        }
        for change in changes
    ])
    st.dataframe(frame, width="stretch", hide_index=True)
