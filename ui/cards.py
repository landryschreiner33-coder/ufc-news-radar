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
from ui import nav
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

    return f"""<div class="{' '.join(classes)}">
  <div class="thumb"><img src="{esc(image['url'])}" alt="{esc(image['caption'])}" loading="lazy">{placeholder_note}</div>
  <div class="body">
    <div class="badges">{status_badge(status)}{_flag_badges(story)}</div>
    <h3>{esc(story.get('headline') or 'Untitled')}</h3>
    <p class="sum">{esc(truncate(story.get('summary') or '', 180))}</p>
    {claim_block}
    <div class="foot">
      <span>{sources} source{'s' if sources != 1 else ''} · {independent} independent<br>
            {esc(category_label(story.get('category')))} · {esc(humanize_age(story.get('last_updated_at')))}</span>
    </div>
    {f'<div class="muted" style="font-size:0.7rem">{entity_line}</div>' if entity_line else ''}
  </div>
</div>"""


def related_reports(story: Dict[str, Any], key: str) -> None:
    """The same story from other outlets, collapsed.

    The feed shows one card per underlying development, not five near-identical
    headlines. The count of *independent* sources is shown next to the total so
    it stays obvious that ten copies of one report are still one report.
    """
    from database import repo_articles as articles_repo

    story_id = int(story.get("id") or 0)
    total = int(story.get("article_count") or 0)
    if total < 2:
        return
    independent = int(story.get("independent_source_count") or 0)
    label = (f"View {total - 1} related report(s) · {independent} independent "
             f"of {total} total")
    with st.expander(label):
        articles = articles_repo.articles_for_story(story_id)
        primary_id = story.get("primary_article_id")
        for article in articles:
            if primary_id and int(article.get("id") or 0) == int(primary_id):
                continue
            credit = " · credits another outlet" if article.get("is_derivative") else ""
            link = (f'<a href="{esc(article.get("url"))}" target="_blank" '
                    f'rel="noopener noreferrer">{esc(article.get("title"))}</a>')
            st.markdown(
                f'<div style="margin-bottom:7px"><div class="kv">{link}</div>'
                f'<div class="muted">{esc(article.get("source_name") or "unknown source")} · '
                f'{esc(humanize_age(article.get("published_at") or article.get("collected_at")))}'
                f'{credit}</div></div>',
                unsafe_allow_html=True)


def card_grid(stories: List[Dict[str, Any]], empty_message: str = "Nothing here yet.",
              wide: bool = False, limit: Optional[int] = None, key: str = "grid",
              show_related: bool = True) -> None:
    """A responsive grid of story cards, each with a real READ MORE button.

    Built on ``st.container(horizontal=True, wrap=True)`` rather than a block of
    HTML anchors. Anchors would be simpler, but Streamlit's page router rewrites
    in-app links and drops their query string, so a card link cannot carry the
    story id - and an absolute URL is forced to open in a new tab. Native
    containers wrap exactly like a CSS grid and keep the buttons working.
    """
    items = list(stories or [])
    if limit:
        items = items[:limit]
    if not items:
        st.markdown(f'<div class="muted" style="padding:6px 2px">{esc(empty_message)}</div>',
                    unsafe_allow_html=True)
        return

    width = 350 if wide else 268
    with st.container(horizontal=True, wrap=True, key=f"cardgrid_{key}", gap="small"):
        for story in items:
            story_id = int(story.get("id") or 0)
            with st.container(width=width, key=f"card_{key}_{story_id}"):
                st.markdown(card_html(story), unsafe_allow_html=True)
                if st.button("READ MORE →", key=f"read_{key}_{story_id}", width="stretch"):
                    nav.open_story(story_id)
                if show_related:
                    related_reports(story, key)
