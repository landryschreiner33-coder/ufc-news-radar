"""Research mode: everything known about one story, with its provenance."""
from __future__ import annotations

from typing import Any, Dict, List

import streamlit as st

from ai import service as ai_service
from ai.context import build_story_context
from database import repo_entities as entities_repo
from database import repo_social as social_repo
from database import repo_stories as stories_repo
from models.types import category_label, status_style
from processors import developing as developing_mod
from ui.components import (
    bullet_list,
    generated_block,
    metric_row,
    navigate,
    notice,
    page_header,
    section_header,
    source_list,
    story_card,
    support_block,
    timeline,
)
from ui import nav
from ui.theme import chip, relevance_bar, status_badge_html
from utils.textutil import truncate
from utils.timeutil import format_display, humanize_age


def render_entry() -> None:
    """Research page entry: open ?story=<id>, or let the user choose one."""
    story_id = nav.int_param("story")
    if story_id is not None:
        render(story_id)
        return

    page_header("\U0001F50E RESEARCH & VERIFICATION",
                "Pick a story to see what is known, what is only claimed, and what to check.")
    stories = stories_repo.list_stories(limit=40, sort="Most Relevant")
    if not stories:
        st.markdown(
            '<div class="emptystate"><div class="big">NOTHING TO RESEARCH YET</div>'
            '<div class="sub">Press <b>Refresh now</b> in the sidebar to collect the latest news, '
            'or load the clearly-marked demo data from Settings.</div></div>',
            unsafe_allow_html=True)
        return
    from ui.cards import card_grid

    section_header("PICK A STORY", len(stories), "Newest and most relevant first.")
    card_grid(stories, limit=24, key="pick")


def render(story_id: int) -> None:
    story = stories_repo.get_story(story_id)
    if story is None:
        st.error("That story no longer exists.")
        if st.button("← Back to dashboard"):
            nav.go("dashboard")
        return

    context = build_story_context(story_id, story)
    if context is None:
        st.error("Could not load the sources for this story.")
        return

    top = st.columns([1, 1.2, 5])
    with top[0]:
        if st.button("← Dashboard", width="stretch"):
            nav.go("dashboard")
    with top[1]:
        if st.button("\U0001F3AC TikTok Studio", width="stretch", key="research_to_studio"):
            st.query_params["story"] = str(story_id)
            page = nav.get("tiktok")
            if page is not None:
                st.switch_page(page)
            st.rerun()
    with top[2]:
        st.markdown(status_badge_html(story.get("status")), unsafe_allow_html=True)

    page_header(_escape(story.get("headline") or "Story"),
                f"First seen {format_display(story.get('first_seen_at'))} · "
                f"updated {humanize_age(story.get('last_updated_at'))}",
                story_style=True)

    if story.get("is_demo"):
        notice("This is DEMO DATA - a fictional example, not real UFC news.", kind="demo")

    metric_row([
        ("Status", status_style(story.get("status")).label),
        ("Sources", int(story.get("source_count") or 0)),
        ("Independent", int(story.get("independent_source_count") or 0)),
        ("X posts", int(story.get("social_post_count") or 0)),
        ("Relevance", f"{float(story.get('relevance') or 0):.0f}"),
        ("Source support", f"{float(story.get('support_score') or 0):.0f}"),
        ("Updates", int(story.get("update_count") or 0)),
    ])

    st.markdown(
        f'<div class="muted">{status_style(story.get("status")).meaning}</div>',
        unsafe_allow_html=True,
    )
    if story.get("has_conflict"):
        notice("Sources disagree on this story. Both sides are shown below - do not resolve it for them.")

    tabs = st.tabs([
        "\U0001F50E Research", "✅ Check before reporting", "\U0001F3AC TikTok tools",
        "\U0001F5C2 Sources & timeline",
    ])
    with tabs[0]:
        _render_research(context, story)
    with tabs[1]:
        _render_check(story_id)
    with tabs[2]:
        _render_tiktok(story_id, context)
    with tabs[3]:
        _render_sources(context, story_id)


# ------------------------------------------------------------- research ----
def _render_research(context, story: Dict[str, Any]) -> None:
    section_header("QUICK SUMMARY")
    summary = ai_service.summarize_story(int(story["id"]))
    generated_block(summary, show_sources=False)

    breakdown = ai_service.knowledge_breakdown(int(story["id"]))
    left, right = st.columns(2)
    with left:
        section_header("WHAT WE KNOW")
        bullet_list(breakdown.get("what_we_know", []))
        section_header("WHAT IS CONFIRMED")
        bullet_list(breakdown.get("what_is_confirmed", []),
                    empty="Nothing is confirmed by an official source yet.")
        section_header("WHY IT MATTERS")
        bullet_list(breakdown.get("why_it_matters", []))
    with right:
        section_header("WHAT IS CLAIMED")
        bullet_list(breakdown.get("what_is_claimed", []), empty="No claims collected.")
        section_header("WHAT IS NOT CONFIRMED")
        bullet_list(breakdown.get("what_is_not_confirmed", []), empty="No obvious gaps.")
        section_header("CONFLICTING INFORMATION")
        bullet_list(breakdown.get("conflicting", []),
                    empty="No contradictions detected in the collected sources.")

    section_header("SOURCE SUPPORT")
    assessment = ai_service.assess_source_support(int(story["id"]))
    support_block(assessment.get("score", 0), assessment.get("label", ""),
                  assessment.get("reasons", []), assessment.get("disclaimer", ""))

    with st.expander("Why this status?"):
        bullet_list(story.get("status_reasons") or [], empty="No reasons recorded.")
    with st.expander("How the relevance score was built"):
        breakdown_scores = story.get("relevance_breakdown") or {}
        if breakdown_scores:
            bullet_list([f"{key}: {value:+.0f}" for key, value in breakdown_scores.items()])
        st.markdown(
            '<div class="muted">Relevance orders the feed. It is not a measure of truth or '
            'objective importance.</div>', unsafe_allow_html=True)
    if story.get("is_trending"):
        with st.expander("Why this is marked trending"):
            bullet_list(story.get("trending_reasons") or [])

    _render_entities(context, story)


def _render_entities(context, story: Dict[str, Any]) -> None:
    left, right = st.columns(2)
    with left:
        section_header("FIGHTERS INVOLVED")
        fighters = context.fighters
        if not fighters:
            st.markdown('<div class="muted">No fighter detected in the collected text.</div>',
                        unsafe_allow_html=True)
        for name in fighters:
            if st.button(f"\U0001F94A {name}", key=f"res_fighter_{name}"):
                nav.open_fighter(name)
    with right:
        section_header("EVENT INVOLVED")
        events = context.events
        if not events:
            st.markdown('<div class="muted">No event detected in the collected text.</div>',
                        unsafe_allow_html=True)
        for name in events:
            event = entities_repo.get_event_by_name(name)
            label = name
            if event and event.get("event_date"):
                label += f" · {format_display(event['event_date'], '%b %d, %Y')}"
            if st.button(f"\U0001F4C5 {label}", key=f"res_event_{name}"):
                if event:
                    nav.open_event(int(event["id"]))
                else:
                    nav.go("events")

    section_header("RELATED STORIES")
    related = stories_repo.related_stories(story, limit=4)
    if not related:
        st.markdown('<div class="muted">No related stories yet.</div>', unsafe_allow_html=True)
    for item in related:
        story_card(item, key_prefix="rel")


# --------------------------------------------------- check before reporting -
def _render_check(story_id: int) -> None:
    section_header("CHECK BEFORE REPORTING",
                   note="Run through this before you record. Every line traces to the sources below.")
    refresh = st.button("Regenerate", key="check_refresh")
    check = ai_service.generate_reporting_check(story_id, force_refresh=refresh)
    generated_block(check)


# ------------------------------------------------------------ tiktok -------
def _render_tiktok(story_id: int, context) -> None:
    section_header("TIKTOK CREATOR TOOLS",
                   note="Scripts are built from the collected sources and keep the verification "
                        "status intact. Check them before recording.")
    controls = st.columns([1, 1, 1, 1, 2])
    refresh = controls[0].button("Regenerate all", key="tiktok_refresh")

    st.markdown("**[30-SECOND SCRIPT]**")
    script_30 = ai_service.generate_tiktok_script(story_id, 30, force_refresh=refresh)
    st.code(script_30.text, language=None, wrap_lines=True)
    st.markdown(f'<div class="muted">{script_30.notice}</div>', unsafe_allow_html=True)

    st.markdown("**[60-SECOND SCRIPT]**")
    script_60 = ai_service.generate_tiktok_script(story_id, 60, force_refresh=refresh)
    st.code(script_60.text, language=None, wrap_lines=True)
    if script_30.warnings or script_60.warnings:
        notice("Grounding check: " + " ".join(script_30.warnings + script_60.warnings))

    columns = st.columns(2)
    with columns[0]:
        st.markdown("**[HOOKS]**")
        hooks = ai_service.generate_tiktok_hooks(story_id, force_refresh=refresh)
        bullet_list(hooks.items or [hooks.text])
        st.markdown("**[KEY FACTS]**")
        bullet_list(ai_service.key_facts(story_id))
    with columns[1]:
        st.markdown("**[STORY ANGLE]**")
        angle = ai_service.generate_story_angle(story_id, force_refresh=refresh)
        st.markdown(f'<div class="kv">{_escape(angle.text)}</div>', unsafe_allow_html=True)
        st.markdown("**[QUESTIONS PEOPLE WILL ASK]**")
        questions = ai_service.generate_questions(story_id, force_refresh=refresh)
        bullet_list(questions.items or [questions.text])

    st.markdown("**[CHECK BEFORE REPORTING]**")
    check = ai_service.generate_reporting_check(story_id)
    for heading in ("confirmed_facts", "reported_claims", "unconfirmed", "conflicting"):
        lines = check.sections.get(heading) or []
        if lines:
            st.markdown(f"*{heading.replace('_', ' ').title()}*")
            bullet_list(lines)

    section_header("SOURCES USED",
                   note="Keep these with the script - every claim above comes from one of them.")
    source_list(context.sources_for_display())


# ------------------------------------------------------------- sources -----
def _render_sources(context, story_id: int) -> None:
    section_header("TIMELINE", note="Oldest first - how the story developed.")
    timeline(developing_mod.timeline_view(story_id))

    section_header("SOURCES", len(context.sources))
    source_list(context.sources_for_display())

    engagement = social_repo.engagement_for_story(story_id)
    engagement_note = ""
    if engagement.get("posts"):
        engagement_note = (
            f" Measured across linked posts: {engagement['likes']:,} likes, "
            f"{engagement['reposts']:,} reposts, {engagement['replies']:,} replies."
        )
    section_header("SOCIAL POSTS", len(context.social_posts),
                   note="X posts are signals, not confirmation. Open one for the account type "
                        "and what it does and does not establish." + engagement_note)
    if not context.social_posts:
        st.markdown(
            '<div class="muted">No X posts linked to this story. Posts are only linked when they '
            'genuinely overlap - the app will not invent context for a vague post.</div>',
            unsafe_allow_html=True)
    for post in context.social_posts:
        with st.expander(f"@{post.get('username')} · {post.get('account_type')} · "
                         f"{format_display(post.get('created_at_source'))}"):
            st.markdown(f'<div class="kv">{_escape(post.get("text"))}</div>', unsafe_allow_html=True)
            if post.get("url"):
                st.markdown(f"[Open on X]({post['url']})")
            analysis = ai_service.analyze_social_post(int(post["id"]))
            st.markdown(f"**How to treat it:** {analysis.get('how_to_treat_it', '')}")
            bullet_list(analysis.get("what_it_does_not_say", []), empty="")
            if analysis.get("ai_text"):
                st.markdown(f'<div class="kv">{_escape(analysis["ai_text"])}</div>',
                            unsafe_allow_html=True)


def _escape(value: Any) -> str:
    text = str(value or "")
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
