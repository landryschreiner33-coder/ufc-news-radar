"""Event pages.

Two things this page must never blur:

* **status** - upcoming, live, completed, cancelled, postponed or unknown, with
  the reasoning and any conflict shown alongside it (see
  ``processors/event_lifecycle.py``);
* **the card** - what UFC officially lists, versus what has only been reported
  or rumoured. A rumoured matchup appearing as an official bout is exactly the
  kind of error that gets a creator called out.
"""
from __future__ import annotations

from typing import Any, Dict, List

import streamlit as st

from database import repo_entities as entities_repo
from database import repo_settings as settings_repo
from database import repo_social as social_repo
from database import repo_stories as stories_repo
from models.types import Category, EventStatus, event_status_style
from processors import bout_status as bout_model
from processors.event_lifecycle import format_countdown, status_explanation
from ui import nav

from ui.cards import card_grid, esc
from ui.components import metric_row, page_header, section_header
from ui.images import image_for_event
from utils.textutil import normalize_text, truncate
from utils.timeutil import format_display, humanize_age

SEGMENT_LABELS = {
    "main_event": "Main event",
    "co_main": "Co-main event",
    "main_card": "Main card",
    "prelims": "Prelims",
    None: "Unplaced",
    "": "Unplaced",
}

STATUS_GROUPS = [
    ("LIVE NOW", [EventStatus.LIVE.value]),
    ("UPCOMING", [EventStatus.UPCOMING.value]),
    ("STATUS UNKNOWN", [EventStatus.UNKNOWN.value]),
    ("POSTPONED / CANCELLED", [EventStatus.POSTPONED.value, EventStatus.CANCELLED.value]),
    ("COMPLETED", [EventStatus.COMPLETED.value]),
]


def render_router() -> None:
    """Event list, or one event when ?event_id is set."""
    event_id = nav.int_param("event_id")
    if event_id is None:
        render_list()
    else:
        render_detail(event_id)


def _status_pill(status: str) -> str:
    if status == EventStatus.LIVE.value:
        return '<span class="lifepill live"><span class="dot"></span>LIVE NOW</span>'
    style = event_status_style(status)
    return (f'<span class="lifepill" style="background:{style.color}22;color:{style.color};'
            f'border:1px solid {style.color}55" title="{esc(style.meaning)}">'
            f'{style.emoji} {style.label}</span>')


def _event_card_html(event: Dict[str, Any]) -> str:
    status = str(event.get("event_status") or EventStatus.UNKNOWN.value)
    countdown = format_countdown(event.get("scheduled_start_utc"))
    image = image_for_event(event)
    when = "date not collected"
    if event.get("scheduled_start_utc"):
        when = format_display(event["scheduled_start_utc"], "%a %d %b %Y · %H:%M UTC")
    elif event.get("event_date"):
        when = format_display(event["event_date"], "%a %d %b %Y") + " · start time not collected"
    location = event.get("city") or event.get("location") or ""
    return (
        f'<div class="evcard{" live" if status == EventStatus.LIVE.value else ""}">'
        f'<img src="{esc(image["url"])}" alt="{esc(image["caption"])}" '
        f'style="width:100%;aspect-ratio:40/15;object-fit:cover;border-radius:8px" loading="lazy">'
        f'{_status_pill(status)}'
        f'<div class="name">{esc(event["name"])}</div>'
        f'<div class="muted">{esc(when)}</div>'
        + (f'<div class="muted">{esc(location)}</div>' if location else "")
        + (f'<div class="count">{esc(countdown)}</div>'
           f'<div class="muted" style="margin-top:-4px">until first bout</div>' if countdown else "")
        + '</div>'
    )


def event_card_grid(events: List[Dict[str, Any]], key: str) -> None:
    """Wrapping grid of event cards, each with a real button.

    Same reason as the story grid: Streamlit rewrites in-app anchors and drops
    their query string, so navigation has to go through a widget.
    """
    with st.container(horizontal=True, wrap=True, key=f"evgrid_{key}", gap="small"):
        for event in events:
            event_id = int(event["id"])
            with st.container(width=300, key=f"ev_{key}_{event_id}"):
                st.markdown(_event_card_html(event), unsafe_allow_html=True)
                if st.button("OPEN EVENT →", key=f"evopen_{key}_{event_id}", width="stretch"):
                    nav.open_event(event_id)


def render_list() -> None:
    page_header("\U0001F4C5 EVENTS",
                "Collected from UFC.com and from names detected in reporting. "
                "Status never comes from the date alone.")
    columns = st.columns([2, 1])
    term = columns[0].text_input("Search events", key="event_search", placeholder="UFC 331...")
    show_all = columns[1].toggle("Include finished / cancelled", value=False, key="event_all")

    events = (entities_repo.search_events(term, limit=100) if term
              else entities_repo.list_events(limit=150, upcoming_only=not show_all))
    if not events:
        st.markdown(
            '<div class="emptystate"><div class="big">NO EVENTS COLLECTED YET</div>'
            '<div class="sub">Press <b>Refresh now</b> in the sidebar - the official UFC schedule '
            'is one of the built-in sources.</div></div>', unsafe_allow_html=True)
        return

    # Statuses are never mixed together in one list.
    remaining = list(events)
    for label, statuses in STATUS_GROUPS:
        group = [event for event in remaining
                 if str(event.get("event_status")) in statuses]
        if not group:
            continue
        remaining = [event for event in remaining if event not in group]
        section_header(label, len(group))
        event_card_grid(group, key=label.split()[0].lower())


def render_detail(event_id: int) -> None:
    event = entities_repo.get_event(event_id)
    if event is None:
        st.error("Event not found.")
        if st.button("← Back to events"):
            nav.go("events")
        return
    if st.button("← Back to events"):
        nav.go("events")

    status = str(event.get("event_status") or EventStatus.UNKNOWN.value)
    style = event_status_style(status)

    header = st.columns([1, 2.4])
    with header[0]:
        image = image_for_event(event)
        st.image(image["url"], caption=image["caption"], width="stretch")
    with header[1]:
        st.markdown(_status_pill(status), unsafe_allow_html=True)
        page_header(esc(event["name"]).upper(), "", story_style=True)
        when = "No date collected"
        if event.get("scheduled_start_utc"):
            when = format_display(event["scheduled_start_utc"], "%A %d %B %Y · %H:%M UTC")
            if event.get("local_timezone"):
                when += f' (venue timezone: {event["local_timezone"]})'
        elif event.get("event_date"):
            when = (format_display(event["event_date"], "%A %d %B %Y")
                    + " · no start time collected")
        location = " · ".join(filter(None, [event.get("venue"), event.get("city")
                                            or event.get("location")]))
        countdown = format_countdown(event.get("scheduled_start_utc"))
        st.markdown(
            f'<div class="kv">{esc(when)}</div>'
            + (f'<div class="muted">{esc(location)}</div>' if location else "")
            + (f'<div class="count" style="font-size:1.6rem">{esc(countdown)} until first bout</div>'
               if countdown else ""),
            unsafe_allow_html=True)

    _render_status_panel(event, style)

    card = entities_repo.fight_card(event_id)
    changes = entities_repo.card_changes(event_id, limit=40)
    stories = stories_repo.stories_for_event(event_id, event["name"], limit=60)
    official = [bout for bout in card if bout.get("official_status") == bout_model.OFFICIAL]
    reported = [bout for bout in card if bout.get("official_status") == bout_model.REPORTED]

    metric_row([
        ("Status", style.label),
        ("On official card", len(official)),
        ("Reported only", len(reported)),
        ("Bouts tracked", len(card)),
        ("Card changes", len(changes)),
        ("Stories", len(stories)),
        ("Data origin", event.get("data_origin") or "detected"),
    ])

    watch_columns = st.columns([1, 4])
    watched = any(normalize_text(item["value"]) == normalize_text(event["name"])
                  for item in settings_repo.list_watchlist("event"))
    if not watched and watch_columns[0].button("☆ Watch this event", key="event_watch"):
        settings_repo.add_watchlist_item("event", event["name"])
        st.rerun()

    _render_card(card)
    _render_changes(changes)

    _render_event_stories(event_id, stories)

    _render_social(event["name"])


def _render_event_stories(event_id: int, stories: List[Dict[str, Any]]) -> None:
    """The event's stories, each shown once.

    A story can legitimately carry several attributes - a cancellation that is
    also still developing, for instance - but showing the same card twice on
    one page makes it look like two separate developments. Sections therefore
    consume from a shared pool in priority order, exactly as the dashboard
    does, instead of each filtering the whole list independently.
    """
    remaining = list(stories)

    def take(predicate) -> List[Dict[str, Any]]:
        taken = [story for story in remaining if predicate(story)]
        ids = {story["id"] for story in taken}
        remaining[:] = [story for story in remaining if story["id"] not in ids]
        return taken

    changes = take(lambda story: story.get("category") in (
        Category.INJURY.value, Category.REPLACEMENT.value, Category.CANCELLATION.value))
    rumors = take(lambda story: story.get("is_developing")
                  or story.get("status") in ("RUMOR", "UNVERIFIED"))

    section_header("INJURIES, REPLACEMENTS & CANCELLATIONS", len(changes))
    card_grid(changes, "None collected for this event.", limit=6, key=f"evchg_{event_id}")

    section_header("RUMORS & DEVELOPING", len(rumors),
                   note="Card changes above are not repeated here.")
    card_grid(rumors, "None collected for this event.", limit=6, key=f"evrum_{event_id}")

    section_header("OTHER EVENT NEWS", len(remaining))
    card_grid(remaining,
              "Every collected story about this event is shown in the sections above."
              if stories else "No stories mention this event yet.",
              limit=8, key=f"evnews_{event_id}")


def _render_status_panel(event: Dict[str, Any], style: Any) -> None:
    """The status, why it says that, and anything that disagrees.

    The explanation is generated from this event's own fields
    (``processors.event_lifecycle.status_explanation``) rather than from a
    fixed sentence per status, so it can never claim a start time the app
    does not have while the reasons underneath say none was collected.
    """
    explanation = status_explanation(event)
    conflicts = event.get("status_conflicts") or []
    # The stored reasons appear only where they add something the one-line
    # explanation does not already say.
    reasons = [reason for reason in (event.get("status_reasons") or [])
               if normalize_text(reason) != normalize_text(explanation)]
    body = "".join(f"<li>{esc(reason)}</li>" for reason in reasons)

    conflict_html = ""
    if conflicts:
        items = "".join(f"<li>{esc(note)}</li>" for note in conflicts)
        conflict_html = (
            '<div style="margin-top:8px;padding:8px 11px;background:#2a1d0f;'
            'border:1px solid #5a3d16;border-radius:8px;color:#ffce87">'
            f'<b>CONFLICT</b><ul style="margin:6px 0 0 16px">{items}</ul></div>')
    source = event.get("status_source") or "not recorded"
    confidence = event.get("status_confidence") or "unknown"
    st.markdown(
        f'<div class="panel"><h4>EVENT STATUS</h4>'
        f'<div class="kv"><b style="color:{style.color}">{style.emoji} {style.label}</b> · '
        f'from <b>{esc(source)}</b> · confidence <b>{esc(confidence)}</b></div>'
        f'<div class="kv">{esc(explanation)}</div>'
        + (f'<ul class="muted" style="margin:8px 0 0 16px">{body}</ul>' if body else "")
        + conflict_html + '</div>',
        unsafe_allow_html=True)


def _render_card(card: List[Dict[str, Any]]) -> None:
    """Official bouts first, then reported, then rumoured and cancelled.

    Buckets are driven by ``official_status`` and the provenance line is built
    from the same model, so the heading and the detail under it can never
    disagree - see ``processors/bout_status.py``.
    """
    section_header("FIGHT CARD", len(card),
                   note="OFFICIAL CARD DATA = read from UFC's own event page. "
                        "OFFICIAL SOURCE REPORTING = an article UFC published; that is still "
                        "reporting, not a card entry. A reported bout is not an official bout.")
    if not card:
        st.markdown(
            '<div class="muted">No bouts collected for this event yet. Bouts appear when the app '
            'collects an official card or reporting that names a matchup.</div>',
            unsafe_allow_html=True)
        return

    buckets = [
        ("✅ OFFICIAL FIGHT CARD", "Read from UFC's own event page.",
         lambda bout: bout.get("official_status") == bout_model.OFFICIAL
         and bout.get("status") != "cancelled"),
        ("\U0001F7E1 REPORTED / DEVELOPING", "Reported by journalists; not on the official card yet.",
         lambda bout: bout.get("official_status") == bout_model.REPORTED
         and bout.get("status") != "cancelled"),
        ("\U0001F534 UNCONFIRMED / RUMORED", "Rumoured only. Do not present these as booked.",
         lambda bout: bout.get("official_status") == bout_model.RUMORED
         and bout.get("status") != "cancelled"),
        ("\U0001F6AB CANCELLED", "Removed from the card.",
         lambda bout: bout.get("status") == "cancelled"),
    ]
    for label, note, predicate in buckets:
        bouts = [bout for bout in card if predicate(bout)]
        if not bouts:
            continue
        st.markdown(f'<div class="section-head"><h2>{label}</h2>'
                    f'<span class="count">{len(bouts)}</span></div>'
                    f'<div class="section-note">{note}</div>', unsafe_allow_html=True)
        for segment in ("main_event", "co_main", "main_card", "prelims", None, ""):
            in_segment = [bout for bout in bouts if (bout.get("segment") or "") == (segment or "")]
            if not in_segment:
                continue
            st.markdown(f"**{SEGMENT_LABELS.get(segment, 'Other')}**")
            for bout in in_segment:
                colour = {"scheduled": "#19c37d", "cancelled": "#ef4444",
                          "changed": "#ff9130"}.get(bout.get("status"), "#9aa4b2")
                flags = " · TITLE FIGHT" if bout.get("is_title_fight") else ""
                st.markdown(
                    f'<div class="panel" style="border-left:3px solid {colour};padding:9px 13px">'
                    f'<div class="kv"><b>{esc(bout["fighter_a"])} vs. {esc(bout["fighter_b"])}</b>'
                    f'{flags}</div>'
                    f'<div class="muted">{esc(bout.get("weight_class") or "weight class not collected")} · '
                    f'{esc(bout_model.describe_bout(bout))} · '
                    f'First seen {esc(humanize_age(bout.get("first_seen_at")))}.</div></div>',
                    unsafe_allow_html=True)


def _render_changes(changes: List[Dict[str, Any]]) -> None:
    section_header("FIGHT CARD CHANGES", len(changes),
                   note="Before / after / reason / status / source / timestamp for every detected change.")
    if not changes:
        st.markdown('<div class="muted">No card changes detected for this event.</div>',
                    unsafe_allow_html=True)
        return
    import pandas as pd

    frame = pd.DataFrame([
        {
            "Detected": format_display(change.get("detected_at"), "%b %d %H:%M"),
            "Change": (change.get("change_type") or "").replace("_", " ").title(),
            "Before": change.get("before_text") or "-",
            "After": change.get("after_text") or "-",
            "Reason": change.get("reason") or "not stated",
            "Status": change.get("status"),
            "Source": change.get("source_name") or "-",
        }
        for change in changes
    ])
    st.dataframe(frame, width="stretch", hide_index=True)


def _render_social(event_name: str) -> None:
    section_header("X ACTIVITY")
    posts = [
        post for post in social_repo.recent_posts(hours=24 * 14, limit=200)
        if any(normalize_text(event_name) == normalize_text(value)
               for value in (post.get("events") or []))
    ][:8]
    if not posts:
        st.markdown('<div class="muted">No collected X posts mention this event.</div>',
                    unsafe_allow_html=True)
        return
    for post in posts:
        st.markdown(
            f'<div class="panel"><div class="muted">@{esc(post.get("username"))} · '
            f'{esc(post.get("account_type"))} · {esc(format_display(post.get("created_at_source")))}</div>'
            f'<div class="kv">{esc(truncate(post.get("text"), 240))}</div></div>',
            unsafe_allow_html=True,
        )
