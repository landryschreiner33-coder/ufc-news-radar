"""TikTok Studio: script on the left, the research backing it on the right.

The point of the split is that nothing gets said on camera that the right-hand
column cannot back up. Scripts are built from collected sources only, keep the
story's verification status, and never turn a rumour into a fact.

On a narrow screen the two columns stack - script first, then research.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

import streamlit as st

from ai import service as ai_service
from ai.context import build_story_context
from database import repo_stories as stories_repo
from models.types import status_style
from ui import nav
from ui.cards import esc, status_badge
from ui.components import bullet_list, notice, page_header, section_header, source_list
from utils.timeutil import humanize_age


def _story_picker() -> Optional[int]:
    """Choose a story to work on when none was opened from a card."""
    page_header("\U0001F3AC TIKTOK STUDIO",
                "Pick a story, then build the script beside its research.")
    stories = stories_repo.list_stories(limit=40, sort="Most Relevant")
    if not stories:
        st.markdown(
            '<div class="emptystate"><div class="big">NOTHING TO WORK WITH YET</div>'
            '<div class="sub">Collect some news first - press <b>Refresh now</b> in the sidebar, '
            'or load the clearly-marked demo data from Settings.</div></div>',
            unsafe_allow_html=True)
        return None

    labels = {
        f"{status_style(story.get('status')).emoji} {story['headline'][:90]}": int(story["id"])
        for story in stories
    }
    choice = st.selectbox("Story", list(labels.keys()), key="studio_pick")
    if st.button("OPEN IN STUDIO", type="primary", key="studio_open"):
        st.query_params["story"] = str(labels[choice])
        st.rerun()
    return None


def render() -> None:
    story_id = nav.int_param("story")
    if story_id is None:
        _story_picker()
        return

    story = stories_repo.get_story(story_id)
    if story is None:
        st.error("That story no longer exists.")
        if st.button("← Back to the dashboard"):
            nav.go("dashboard")
        return

    context = build_story_context(story_id, story)
    if context is None:
        st.error("Could not load the sources for this story.")
        return

    top = st.columns([1, 1, 5])
    with top[0]:
        if st.button("← Dashboard", width="stretch", key="studio_back"):
            nav.go("dashboard")
    with top[1]:
        if st.button("\U0001F50E Research", width="stretch", key="studio_to_research"):
            nav.open_story(story_id)

    st.markdown(status_badge(story.get("status")), unsafe_allow_html=True)
    page_header(esc(story.get("headline") or "Story"),
                f"Updated {humanize_age(story.get('last_updated_at'))} · "
                f"{int(story.get('source_count') or 0)} source(s) · "
                f"{int(story.get('independent_source_count') or 0)} independent",
                story_style=True)

    if story.get("is_demo"):
        notice("This is DEMO DATA - a fictional example, not real UFC news.", kind="demo")
    if story.get("status") in ("RUMOR", "UNVERIFIED", "CONTESTED"):
        notice(
            f"This story is <b>{status_style(story.get('status')).label}</b>. "
            "Any script must attribute it - do not state it as fact."
        )

    refresh = st.button("↻ Regenerate everything", key="studio_regen")

    # Two columns on desktop; Streamlit stacks them on narrow screens, which
    # puts the script first and the research underneath - the order we want.
    left, right = st.columns([1.05, 1], gap="medium")
    with left:
        _script_column(story_id, refresh)
    with right:
        _research_column(story_id, story, context)


# ---------------------------------------------------------------- script ---
def _script_column(story_id: int, refresh: bool) -> None:
    section_header("SCRIPT")

    hooks = ai_service.generate_tiktok_hooks(story_id, force_refresh=refresh)
    st.markdown("**HOOKS** - the first line, where people decide to stay")
    bullet_list(hooks.items or [hooks.text])

    angle = ai_service.generate_story_angle(story_id, force_refresh=refresh)
    st.markdown("**ANGLE**")
    st.markdown(f'<div class="kv">{esc(angle.text)}</div>', unsafe_allow_html=True)

    tabs = st.tabs(["30 seconds", "60 seconds"])
    scripts = {}
    for tab, seconds in zip(tabs, (30, 60)):
        with tab:
            script = ai_service.generate_tiktok_script(story_id, seconds, force_refresh=refresh)
            scripts[seconds] = script
            st.code(script.text, language=None, wrap_lines=True)
            st.caption(script.notice)
            edited = st.text_area(
                f"Edit the {seconds}-second script", value=script.text, height=220,
                key=f"studio_edit_{seconds}",
                help="Edit freely - your changes are not checked against the sources.")
            st.download_button(
                f"⬇ Download {seconds}s script", data=edited,
                file_name=f"ufc-radar-{story_id}-{seconds}s.txt", mime="text/plain",
                key=f"studio_dl_{seconds}", width="stretch")

    warnings: List[str] = []
    for script in scripts.values():
        warnings.extend(script.warnings or [])
    if warnings:
        notice("Grounding check: " + " ".join(warnings))


# -------------------------------------------------------------- research ---
def _research_column(story_id: int, story: Dict[str, Any], context: Any) -> None:
    section_header("RESEARCH", note="Everything the script is allowed to lean on.")

    check = ai_service.generate_reporting_check(story_id)
    st.markdown("**CHECK BEFORE REPORTING**")
    headings = [
        ("confirmed_facts", "✅ Safe to state as fact"),
        ("reported_claims", "\U0001F7E1 Must be attributed"),
        ("unconfirmed", "⚪ Not safe to state as fact"),
        ("conflicting", "\U0001F7E3 Sources disagree"),
        ("missing", "❔ Still unknown"),
    ]
    for key, label in headings:
        lines = check.sections.get(key) or []
        if lines:
            st.markdown(f"*{label}*")
            bullet_list(lines)

    with st.expander("Key facts", expanded=True):
        bullet_list(ai_service.key_facts(story_id))

    with st.expander("Questions people will ask"):
        questions = ai_service.generate_questions(story_id)
        bullet_list(questions.items or [questions.text])

    if story.get("has_conflict"):
        with st.expander("Conflicting information", expanded=True):
            bullet_list(story.get("conflict_notes") or [], "No details recorded.")

    with st.expander("Timeline"):
        from processors import developing as developing_mod
        from ui.components import timeline

        timeline(developing_mod.timeline_view(story_id))

    with st.expander("Sources", expanded=True):
        source_list(context.sources_for_display())

    posts = getattr(context, "social_posts", None) or []
    if posts:
        with st.expander(f"X posts ({len(posts)})"):
            for post in posts[:10]:
                st.markdown(
                    f'<div class="panel" style="padding:8px 11px;margin-bottom:6px">'
                    f'<div class="muted">@{esc(post.get("username"))} · '
                    f'{esc(post.get("account_type"))}</div>'
                    f'<div class="kv">{esc(post.get("text"))[:240]}</div></div>',
                    unsafe_allow_html=True)
