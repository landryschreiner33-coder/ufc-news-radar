"""Fighter pages: everything collected about one fighter, nothing invented."""
from __future__ import annotations

from typing import Any, Dict, List

import streamlit as st

from database import repo_articles as articles_repo
from database import repo_entities as entities_repo
from database import repo_rankings as rankings_repo
from database import repo_settings as settings_repo
from database import repo_social as social_repo
from database import repo_stories as stories_repo
from models.types import Category, StoryStatus
from ui import nav
from ui.components import (
    bullet_list, metric_row, navigate, page_header, section_header, story_grid,
)
from ui.theme import chip
from utils.textutil import normalize_text, truncate
from utils.timeutil import format_display, humanize_age


def render_router() -> None:
    """Fighter list, or one fighter when ?name is set."""
    name = nav.param("name")
    if not name:
        render_list()
    else:
        render_detail(name)


def render_list() -> None:
    page_header("FIGHTERS", "Names the app recognises in collected reporting.")
    columns = st.columns([2, 1, 1])
    term = columns[0].text_input("Search fighters", key="fighter_search", placeholder="name or nickname")
    order = columns[1].selectbox("Sort by", ["Most mentioned", "Recently mentioned", "Name"],
                                 key="fighter_order")
    only_watched = columns[2].toggle("Watchlist only", key="fighter_watch_only")

    order_key = {"Most mentioned": "mentions", "Recently mentioned": "recent", "Name": "name"}[order]
    fighters = (entities_repo.search_fighters(term, limit=200) if term
                else entities_repo.list_fighters(limit=300, order=order_key))
    if only_watched:
        watched = {normalize_text(item["value"]) for item in settings_repo.list_watchlist("fighter")}
        fighters = [f for f in fighters if normalize_text(f["name"]) in watched]

    section_header("FIGHTERS", len(fighters))
    for start in range(0, len(fighters), 4):
        cols = st.columns(4)
        for offset, fighter in enumerate(fighters[start:start + 4]):
            with cols[offset]:
                rank = ""
                if fighter.get("is_champion"):
                    rank = " · champion"
                elif fighter.get("current_rank"):
                    rank = f" · #{fighter['current_rank']}"
                label = f"{fighter['name']}{rank}"
                if st.button(label, key=f"fighter_btn_{fighter['id']}", width="stretch"):
                    nav.open_fighter(fighter["name"])
                st.markdown(
                    f'<div class="muted" style="margin:-6px 0 10px 2px">'
                    f'{fighter.get("mention_count") or 0} mentions'
                    + (f' · last {humanize_age(fighter["last_mentioned_at"])}'
                       if fighter.get("last_mentioned_at") else "")
                    + "</div>",
                    unsafe_allow_html=True,
                )


def render_detail(name: str) -> None:
    fighter = entities_repo.get_fighter_by_name(name)
    display_name = fighter["name"] if fighter else name
    if st.button("← Back to fighters"):
        nav.go("fighters")

    nickname = f'"{fighter["nickname"]}"' if fighter and fighter.get("nickname") else ""
    page_header(display_name.upper(), nickname)

    stories = stories_repo.stories_for_fighter(display_name, limit=60)
    ranking_rows = rankings_repo.fighter_ranking_history(display_name, limit=30)
    current = ranking_rows[0] if ranking_rows else None

    metric_row([
        ("Stories", len(stories)),
        ("Mentions", (fighter or {}).get("mention_count") or 0),
        ("Current ranking",
         ("Champion" if current and current.get("is_champion")
          else f"#{current['position']}" if current and current.get("position") is not None
          else "not collected")),
        ("Division", (current or {}).get("division") or (fighter or {}).get("division") or "unknown"),
        ("Last mention", humanize_age((fighter or {}).get("last_mentioned_at"))
         if (fighter or {}).get("last_mentioned_at") else "-"),
    ])

    watch_columns = st.columns([1, 1, 4])
    watched = any(normalize_text(item["value"]) == normalize_text(display_name)
                  for item in settings_repo.list_watchlist("fighter"))
    if watched:
        if watch_columns[0].button("★ Watching - remove", key="fighter_unwatch"):
            for item in settings_repo.list_watchlist("fighter"):
                if normalize_text(item["value"]) == normalize_text(display_name):
                    settings_repo.remove_watchlist_item(int(item["id"]))
            st.rerun()
    else:
        if watch_columns[0].button("☆ Add to watchlist", key="fighter_watch"):
            settings_repo.add_watchlist_item("fighter", display_name)
            st.rerun()

    if current:
        st.markdown(
            f'<div class="panel"><h4>Current ranking (from collected official data)</h4>'
            f'<div class="kv"><b>{current.get("division")}</b> · '
            + ("Champion" if current.get("is_champion") else f"#{current.get('position')}")
            + f' · ranking date {current.get("ranking_date")}'
            f' · system: {current.get("system_name")} ({current.get("system_version")})</div>'
            f'<div class="muted">Source: <a href="{current.get("source_url") or "#"}" '
            f'target="_blank">{current.get("source_url") or "n/a"}</a></div></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<div class="panel"><h4>Current ranking</h4><div class="muted">No official ranking has '
            'been collected for this fighter yet. Rankings are only shown when the app has actually '
            'collected them - nothing is estimated.</div></div>',
            unsafe_allow_html=True,
        )

    official = [s for s in stories if s.get("official_confirmed")]
    rumors = [s for s in stories if s.get("status") in
              (StoryStatus.RUMOR.value, StoryStatus.UNVERIFIED.value)]
    claims = [s for s in stories if s.get("status") == StoryStatus.FIGHTER_CLAIM.value
              or s.get("category") == Category.FIGHTER_STATEMENT.value]
    results = [s for s in stories if s.get("category") == Category.RESULT.value]

    section_header("RECENT STORIES", len(stories))
    story_grid(stories[:8], f"fighter_{display_name}", empty_message="No stories collected yet.")

    columns = st.columns(2)
    with columns[0]:
        section_header("OFFICIAL ANNOUNCEMENTS", len(official))
        story_grid(official[:4], f"off_{display_name}", columns=1,
                   empty_message="No officially confirmed stories collected.")
        section_header("CLAIMS & STATEMENTS", len(claims))
        story_grid(claims[:4], f"clm_{display_name}", columns=1, empty_message="None collected.")
    with columns[1]:
        section_header("RUMORS", len(rumors))
        story_grid(rumors[:4], f"rum_{display_name}", columns=1, empty_message="None collected.")
        section_header("RECENT RESULTS (as reported)", len(results))
        story_grid(results[:4], f"res_{display_name}", columns=1,
                   empty_message="No result coverage collected. The app never estimates records.")

    _render_upcoming(display_name)
    _render_ranking_history(display_name, ranking_rows)
    _render_social(display_name)


def _render_upcoming(name: str) -> None:
    section_header("UPCOMING / TRACKED BOUTS")
    rows: List[Dict[str, Any]] = []
    for event in entities_repo.list_events(limit=60, upcoming_only=True):
        for fight in entities_repo.find_fights_for_fighter(int(event["id"]), name):
            rows.append({**fight, "event_name": event["name"], "event_date": event.get("event_date")})
    if not rows:
        st.markdown(
            '<div class="muted">No bout for this fighter has been collected from an event card '
            'or from reporting yet.</div>', unsafe_allow_html=True)
        return
    for row in rows:
        st.markdown(
            f'<div class="panel"><div class="kv"><b>{row["fighter_a"]} vs. {row["fighter_b"]}</b> · '
            f'{row["event_name"]}'
            + (f' · {format_display(row.get("event_date"), "%b %d, %Y")}' if row.get("event_date") else "")
            + f'</div><div class="muted">status: {row.get("status")} · confidence: '
            f'{row.get("confidence")} · source: {row.get("source_name") or "n/a"}</div></div>',
            unsafe_allow_html=True,
        )


def _render_ranking_history(name: str, rows: List[Dict[str, Any]]) -> None:
    section_header("RANKING HISTORY", len(rows))
    if not rows:
        st.markdown('<div class="muted">No ranking snapshots collected for this fighter.</div>',
                    unsafe_allow_html=True)
        return
    import pandas as pd

    frame = pd.DataFrame([
        {
            "Date": row.get("ranking_date"),
            "Division": row.get("division"),
            "Position": "C" if row.get("is_champion") else row.get("position"),
            "System": row.get("system_name"),
        }
        for row in rows
    ])
    st.dataframe(frame, width="stretch", hide_index=True)
    changes = rankings_repo.ranking_changes_for_fighter(name, limit=10)
    if changes:
        from processors.rankings_diff import describe_change

        bullet_list([describe_change(change) for change in changes])


def _render_social(name: str) -> None:
    section_header("X POSTS MENTIONING THIS FIGHTER")
    posts = [
        post for post in social_repo.recent_posts(hours=24 * 14, limit=200)
        if any(normalize_text(name) == normalize_text(value) for value in (post.get("fighters") or []))
    ][:10]
    if not posts:
        st.markdown(
            '<div class="muted">No collected X posts mention this fighter. X monitoring needs '
            'X_BEARER_TOKEN to be configured.</div>', unsafe_allow_html=True)
        return
    for post in posts:
        st.markdown(
            f'<div class="panel"><div class="muted">@{post.get("username")} · '
            f'{post.get("account_type")} · {format_display(post.get("created_at_source"))}</div>'
            f'<div class="kv">{truncate(post.get("text"), 260)}</div>'
            + (f'<a href="{post.get("url")}" target="_blank">Open on X</a>' if post.get("url") else "")
            + "</div>",
            unsafe_allow_html=True,
        )
