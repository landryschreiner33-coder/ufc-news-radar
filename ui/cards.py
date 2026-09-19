"""The news card grid.

Built as one HTML block using CSS grid rather than ``st.columns``, for two
reasons:

* ``st.columns`` takes a fixed count, so a four-column feed stays four columns
  on a phone. A grid with ``minmax()`` reflows 4 -> 3 -> 2 -> 1 by itself.
* One block of markup per section renders in a single pass instead of one
  Streamlit element per card.

Each card is an ``<a>`` pointing at ``?story=<id>``, which the router picks up,
so cards stay clickable, keyboard-focusable and bookmarkable.
"""
from __future__ import annotations

import html
from typing import Any, Dict, List, Optional

import streamlit as st

from models.types import category_label, status_style
from ui.images import image_for_story
from utils.textutil import truncate
from utils.timeutil import humanize_age

#: Status -> extra card class, so each state reads differently at a glance.
_STATUS_CLASS = {
    "RUMOR": "rumor",
    "CONTESTED": "contested",
    "DEVELOPING": "developing",
    "UNVERIFIED": "rumor",
}


def esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def badge_html(text: str, color: str, title: str = "") -> str:
    tooltip = f' title="{esc(title)}"' if title else ""
    return (f'<span class="badge" style="background:{color}22;color:{color};'
            f'border:1px solid {color}55"{tooltip}>{esc(text)}</span>')


def status_badge(status: Optional[str]) -> str:
    """Icon + word + colour. Never colour alone."""
    style = status_style(status)
    return badge_html(f"{style.emoji} {style.label}", style.color, style.meaning)


def _flag_badges(story: Dict[str, Any]) -> str:
    flags: List[str] = []
    if story.get("is_breaking"):
        flags.append(badge_html("BREAKING", "#ff3b3b", "Major, very recent story."))
    if story.get("is_developing") and story.get("status") != "DEVELOPING":
        flags.append(badge_html("UPDATING", "#ff9130", "New material is still arriving."))
    if story.get("is_trending"):
        flags.append(badge_html("TRENDING", "#3b82f6", "Unusual measured activity."))
    if story.get("has_conflict"):
        flags.append(badge_html("SOURCES DISAGREE", "#a855f7",
                                "Reporting on this story contradicts itself."))
    if story.get("is_demo"):
        flags.append(badge_html("DEMO DATA", "#c084fc", "Fictional sample data, not real news."))
    return "".join(flags)


def card_html(story: Dict[str, Any], event: Optional[Dict[str, Any]] = None) -> str:
    """One story card as markup."""
    status = str(story.get("status") or "UNVERIFIED")
    classes = ["ncard"]
    if story.get("is_breaking"):
        classes.append("breaking")
    extra = _STATUS_CLASS.get(status)
    if extra:
        classes.append(extra)

    image = image_for_story(story, event)
    placeholder_note = ('<div class="ph-note">GENERIC GRAPHIC - NO SOURCE IMAGE</div>'
                        if image["is_placeholder"] else "")

    sources = int(story.get("source_count") or 0)
    independent = int(story.get("independent_source_count") or 0)
    entities = [name for name in (story.get("fighters") or [])[:2]]
    entities += [name for name in (story.get("events") or [])[:1]]
    entity_line = " · ".join(esc(name) for name in entities) if entities else ""

    # Rumours lead with the claim and who made it, never with a bare headline
    # that could read as established fact.
    claim_block = ""
    if status in ("RUMOR", "UNVERIFIED"):
        claimant = esc(story.get("primary_source_name") or "an unnamed source")
        official = "NO" if not story.get("official_confirmed") else "YES"
        claim_block = (
            f'<div class="claimline"><b>Claim by:</b> {claimant}<br>'
            f'<b>UFC confirmation:</b> {official}</div>'
        )

    return f"""<a class="{' '.join(classes)}" href="?story={int(story.get('id') or 0)}"
   aria-label="Open research for: {esc(story.get('headline'))}">
  <div class="thumb"><img src="{esc(image['url'])}" alt="{esc(image['caption'])}" loading="lazy">{placeholder_note}</div>
  <div class="body">
    <div class="badges">{status_badge(status)}{_flag_badges(story)}</div>
    <h3>{esc(story.get('headline') or 'Untitled')}</h3>
    <p class="sum">{esc(truncate(story.get('summary') or '', 180))}</p>
    {claim_block}
    <div class="foot">
      <span>{sources} source{'s' if sources != 1 else ''} · {independent} independent<br>
            {esc(category_label(story.get('category')))} · {esc(humanize_age(story.get('last_updated_at')))}</span>
      <span class="more">READ MORE →</span>
    </div>
    {f'<div class="muted" style="font-size:0.7rem">{entity_line}</div>' if entity_line else ''}
  </div>
</a>"""


def card_grid(stories: List[Dict[str, Any]], empty_message: str = "Nothing here yet.",
              wide: bool = False, limit: Optional[int] = None) -> None:
    """Render a responsive grid of story cards."""
    items = list(stories or [])
    if limit:
        items = items[:limit]
    if not items:
        st.markdown(f'<div class="muted" style="padding:6px 2px">{esc(empty_message)}</div>',
                    unsafe_allow_html=True)
        return
    cards = "".join(card_html(story) for story in items)
    st.markdown(f'<div class="radar-grid{" wide" if wide else ""}">{cards}</div>',
                unsafe_allow_html=True)


def grouped_story_block(story: Dict[str, Any], related: List[Dict[str, Any]]) -> None:
    """A story plus its duplicate reports, collapsed.

    The feed shows the primary report only; the same story from five outlets is
    one item with the others one click away, and the independent-source count
    makes clear how much of it is genuinely separate reporting.
    """
    st.markdown(f'<div class="radar-grid">{card_html(story)}</div>', unsafe_allow_html=True)
    if not related:
        return
    independent = int(story.get("independent_source_count") or 0)
    with st.expander(f"View {len(related)} related report(s) · "
                     f"{independent} independent source(s) · {len(related) + 1} total"):
        for item in related:
            st.markdown(
                f'<div class="panel" style="padding:9px 12px;margin-bottom:6px">'
                f'<div class="kv"><a href="{esc(item.get("url"))}" target="_blank" '
                f'rel="noopener noreferrer">{esc(item.get("title"))}</a></div>'
                f'<div class="muted">{esc(item.get("source_name") or "unknown source")} · '
                f'{esc(humanize_age(item.get("published_at") or item.get("collected_at")))}'
                f'{" · credits another outlet" if item.get("is_derivative") else ""}</div></div>',
                unsafe_allow_html=True,
            )
