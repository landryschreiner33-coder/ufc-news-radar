"""Reusable UI pieces: header, metrics, story cards, source lists, timelines."""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional

import streamlit as st

from models.types import category_label, status_style
from ui import nav
from ui.theme import chip, relevance_bar, status_badge_html, support_meter_html
from utils.textutil import truncate
from utils.timeutil import format_display, humanize_age


# ------------------------------------------------------------ navigation ---
def navigate(page: str, **params: Any) -> None:
    """Switch page via query parameters so links are shareable/bookmarkable."""
    st.query_params.clear()
    st.query_params["page"] = page
    for key, value in params.items():
        if value is not None:
            st.query_params[key] = str(value)
    st.rerun()


def current_page() -> str:
    return st.query_params.get("page", "dashboard")


def param(name: str, default: Optional[str] = None) -> Optional[str]:
    return st.query_params.get(name, default)


def int_param(name: str) -> Optional[int]:
    value = st.query_params.get(name)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------- headers ---
def page_header(title: str, subtitle: str = "", story_style: bool = False) -> None:
    """Page heading. ``story_style`` keeps a headline's own capitalisation."""
    css_class = "radar-title story" if story_style else "radar-title"
    st.markdown(
        f'<div class="radar-header"><h1 class="{css_class}">{title}</h1>'
        f'<span class="radar-sub">{subtitle}</span></div>',
        unsafe_allow_html=True,
    )


def section_header(title: str, count: Optional[int] = None, note: str = "") -> None:
    count_html = f'<span class="count">{count}</span>' if count is not None else ""
    st.markdown(
        f'<div class="section-head"><h2>{title}</h2>{count_html}</div>'
        + (f'<div class="section-note">{note}</div>' if note else ""),
        unsafe_allow_html=True,
    )


def metric_row(metrics: List[tuple]) -> None:
    """metrics: list of (label, value, alert?)"""
    boxes = []
    for entry in metrics:
        label, value = entry[0], entry[1]
        alert = entry[2] if len(entry) > 2 else False
        css = "metric-box alert" if alert else "metric-box"
        boxes.append(
            f'<div class="{css}"><div class="label">{label}</div><div class="value">{value}</div></div>'
        )
    st.markdown(f'<div class="metric-row">{"".join(boxes)}</div>', unsafe_allow_html=True)


def notice(message: str, kind: str = "warn") -> None:
    css = {"warn": "warn-box", "demo": "demo-box"}.get(kind, "warn-box")
    st.markdown(f'<div class="{css}">{message}</div>', unsafe_allow_html=True)


def panel(title: str, body_html: str) -> None:
    st.markdown(f'<div class="panel"><h4>{title}</h4>{body_html}</div>', unsafe_allow_html=True)


# ----------------------------------------------------------- story cards ---
def story_card(story: Dict[str, Any], key_prefix: str = "", show_button: bool = True) -> None:
    """One scannable story card."""
    style = status_style(story.get("status"))
    fighters = "".join(chip(name, "fighter") for name in (story.get("fighters") or [])[:4])
    events = "".join(chip(name, "event") for name in (story.get("events") or [])[:2])
    social_count = int(story.get("social_post_count") or 0)
    social_html = f' · <b>{social_count}</b> X post(s)' if social_count else ""
    conflict_html = ' · <b style="color:#ff9130">sources disagree</b>' if story.get("has_conflict") else ""
    flags = []
    if story.get("is_breaking"):
        flags.append('<span class="badge" style="background:#d20a0a22;color:#ff6b6b;border:1px solid #d20a0a55">BREAKING</span>')
    if story.get("is_developing") and story.get("status") != "DEVELOPING":
        flags.append('<span class="badge" style="background:#ff913022;color:#ff9130;border:1px solid #ff913055">UPDATING</span>')
    if story.get("is_trending"):
        flags.append('<span class="badge" style="background:#3b82f622;color:#7fb0ff;border:1px solid #3b82f655">TRENDING</span>')
    if story.get("is_demo"):
        flags.append('<span class="badge" style="background:#6b2b7a33;color:#f0b9ff;border:1px solid #6b2b7a">DEMO DATA</span>')

    st.markdown(
        f"""<div class="story-card" style="border-left-color:{style.color}">
  <div>{status_badge_html(story.get('status'))} {' '.join(flags)}</div>
  <h3>{_escape(story.get('headline') or 'Untitled')}</h3>
  <div class="summary">{_escape(truncate(story.get('summary') or '', 260))}</div>
  <div class="meta">
    <b>{int(story.get('source_count') or 0)}</b> source(s) ·
    <b>{int(story.get('independent_source_count') or 0)}</b> independent ·
    {category_label(story.get('category'))} ·
    updated {humanize_age(story.get('last_updated_at'))}{social_html}{conflict_html}
  </div>
  <div style="margin-top:6px">{fighters}{events}</div>
  {relevance_bar(story.get('relevance') or 0)}
  <div class="meta" style="margin-top:4px">relevance {float(story.get('relevance') or 0):.0f}/100
    · source support {float(story.get('support_score') or 0):.0f}/100</div>
</div>""",
        unsafe_allow_html=True,
    )
    if show_button:
        if st.button("READ MORE →", key=f"{key_prefix}_read_{story.get('id')}",
                     width="stretch"):
            nav.open_story(int(story.get("id")))


def story_grid(stories: List[Dict[str, Any]], key_prefix: str = "", columns: int = 2,
               empty_message: str = "Nothing here yet.") -> None:
    """Responsive story grid.

    ``columns`` is kept for the existing call sites but is no longer a fixed
    count: the grid reflows 4 -> 3 -> 2 -> 1 with the viewport, so a phone gets
    one readable column instead of four squashed ones.
    """
    from ui.cards import card_grid

    card_grid(stories, empty_message=empty_message, wide=columns <= 2,
              key=key_prefix or "legacy")


# ------------------------------------------------------------- listings ----
def source_list(sources: Iterable[Dict[str, Any]], show_index: bool = True) -> None:
    rows = []
    for source in sources:
        marker = ""
        if source.get("is_official"):
            marker = ' <span class="badge" style="background:#19c37d22;color:#19c37d;border:1px solid #19c37d55">OFFICIAL</span>'
        elif source.get("is_derivative"):
            marker = ' <span class="badge" style="background:#9aa4b222;color:#9aa4b2;border:1px solid #9aa4b255">CREDITS ANOTHER OUTLET</span>'
        index_html = f"[{source.get('index')}] " if show_index and source.get("index") else ""
        url = source.get("url") or ""
        title = _escape(source.get("title") or url)
        rows.append(
            f'<div style="margin-bottom:9px"><div class="kv">{index_html}'
            f'<b>{_escape(source.get("name") or "source")}</b> · '
            f'<span class="muted">{source.get("source_type") or "UNKNOWN"} · '
            f'{format_display(source.get("published_at"))}</span>{marker}</div>'
            f'<div class="kv">{title}</div>'
            f'<div><a href="{url}" target="_blank">{_escape(truncate(url, 90))}</a></div></div>'
        )
    st.markdown("".join(rows) or '<div class="muted">No sources.</div>', unsafe_allow_html=True)


def timeline(entries: List[Dict[str, Any]]) -> None:
    if not entries:
        st.markdown('<div class="muted">No timeline entries yet.</div>', unsafe_allow_html=True)
        return
    html = []
    for entry in entries:
        link = (f' · <a href="{entry.get("url")}" target="_blank">open</a>'
                if entry.get("url") else "")
        detail = f'<div class="muted">{_escape(entry.get("detail") or "")}</div>' if entry.get("detail") else ""
        html.append(
            f'<div class="timeline-item"><div class="when">{entry.get("when")} · '
            f'{_escape(entry.get("source_name") or "")}{link}</div>'
            f'<div>{entry.get("icon", "")} {_escape(entry.get("headline") or "")}</div>{detail}</div>'
        )
    st.markdown("".join(html), unsafe_allow_html=True)


def bullet_list(items: Iterable[str], empty: str = "None.") -> None:
    items = [item for item in items if item]
    if not items:
        st.markdown(f'<div class="muted">{empty}</div>', unsafe_allow_html=True)
        return
    st.markdown(
        "".join(f'<div class="kv">• {_escape(item)}</div>' for item in items),
        unsafe_allow_html=True,
    )


def support_block(score: float, label: str, reasons: List[str], disclaimer: str) -> None:
    st.markdown(support_meter_html(score, label or ""), unsafe_allow_html=True)
    st.markdown(f'<div class="muted" style="margin:6px 0 8px 0">{disclaimer}</div>',
                unsafe_allow_html=True)
    bullet_list(reasons, empty="No supporting evidence recorded.")


def generated_block(generated: Any, show_sources: bool = True) -> None:
    """Render generated text with its provenance and any grounding warnings."""
    if generated.warnings:
        notice("Grounding check: " + " ".join(generated.warnings))
    if generated.sections:
        for heading, lines in generated.sections.items():
            st.markdown(f"**{heading.replace('_', ' ').title()}**")
            bullet_list(lines)
    elif generated.items:
        bullet_list(generated.items)
    else:
        st.markdown(f'<div class="kv">{_escape(generated.text)}</div>', unsafe_allow_html=True)
    st.markdown(
        f'<div class="muted" style="margin-top:8px">{_escape(generated.notice)}'
        + (" (cached)" if generated.from_cache else "")
        + "</div>",
        unsafe_allow_html=True,
    )
    if show_sources and generated.sources:
        with st.expander(f"Sources used ({len(generated.sources)})"):
            source_list(generated.sources)


def _escape(value: Any) -> str:
    text = str(value or "")
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )
