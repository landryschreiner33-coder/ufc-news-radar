"""Global search across stories, articles, fighters, events, posts and accounts."""
from __future__ import annotations

import streamlit as st

from database import repo_articles as articles_repo
from database import repo_entities as entities_repo
from database import repo_settings as settings_repo
from database import repo_social as social_repo
from database import repo_stories as stories_repo
from ui.components import metric_row, navigate, page_header, section_header, story_grid
from utils.textutil import truncate
from utils.timeutil import format_display


def render() -> None:
    page_header("SEARCH", "Everything the app has collected.")
    term = st.text_input("Search", value=st.query_params.get("q", ""), key="global_search",
                         placeholder="fighter, event, outlet, keyword, @account...")
    if not term.strip():
        st.markdown('<div class="muted">Type something to search.</div>', unsafe_allow_html=True)
        return

    stories = stories_repo.list_stories(limit=40, search=term, sort="Most Relevant")
    articles = articles_repo.search_articles(term, limit=40)
    fighters = entities_repo.search_fighters(term, limit=20)
    events = entities_repo.search_events(term, limit=20)
    posts = social_repo.search_posts(term, limit=30)
    accounts = [
        account for account in settings_repo.list_monitored_accounts()
        if term.strip().lstrip("@").lower() in
        f"{account['username']} {(account.get('display_name') or '').lower()}"
    ]

    metric_row([
        ("Stories", len(stories)), ("Articles", len(articles)), ("Fighters", len(fighters)),
        ("Events", len(events)), ("X posts", len(posts)), ("X accounts", len(accounts)),
    ])

    tabs = st.tabs(["Stories", "Articles", "Fighters", "Events", "X posts", "X accounts"])
    with tabs[0]:
        story_grid(stories, "search_story", empty_message="No stories matched.")
    with tabs[1]:
        if not articles:
            st.markdown('<div class="muted">No articles matched.</div>', unsafe_allow_html=True)
        for article in articles:
            st.markdown(
                f'<div class="panel"><div class="kv"><b>{article["title"]}</b></div>'
                f'<div class="muted">{article.get("source_name")} · '
                f'{format_display(article.get("published_at"))} · {article.get("category")}</div>'
                f'<div class="kv">{truncate(article.get("excerpt"), 200)}</div>'
                f'<a href="{article["url"]}" target="_blank">Open original</a></div>',
                unsafe_allow_html=True,
            )
            if article.get("story_id") and st.button("Open the story",
                                                     key=f"search_art_{article['id']}"):
                navigate("research", story=article["story_id"])
    with tabs[2]:
        for fighter in fighters:
            if st.button(f"{fighter['name']} ({fighter.get('mention_count') or 0} mentions)",
                         key=f"search_fighter_{fighter['id']}"):
                navigate("fighter", name=fighter["name"])
        if not fighters:
            st.markdown('<div class="muted">No fighters matched.</div>', unsafe_allow_html=True)
    with tabs[3]:
        for event in events:
            if st.button(f"{event['name']}", key=f"search_event_{event['id']}"):
                navigate("event", event_id=event["id"])
        if not events:
            st.markdown('<div class="muted">No events matched.</div>', unsafe_allow_html=True)
    with tabs[4]:
        if not posts:
            st.markdown('<div class="muted">No X posts matched.</div>', unsafe_allow_html=True)
        for post in posts:
            st.markdown(
                f'<div class="panel"><div class="muted">@{post.get("username")} · '
                f'{post.get("account_type")} · {format_display(post.get("created_at_source"))}</div>'
                f'<div class="kv">{truncate(post.get("text"), 240)}</div></div>',
                unsafe_allow_html=True,
            )
    with tabs[5]:
        if not accounts:
            st.markdown('<div class="muted">No monitored accounts matched.</div>',
                        unsafe_allow_html=True)
        for account in accounts:
            st.markdown(
                f'<div class="kv">@{account["username"]} · {account["account_type"]} · '
                f'{account["category"]}</div>', unsafe_allow_html=True)
