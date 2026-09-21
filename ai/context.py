"""Build the grounded source context for a story.

Everything the AI layer (or the template builders) is allowed to use comes
from here: the collected articles, the collected X posts and the values the
processors derived from them.  Nothing else is ever put in a prompt, and every
statement can be traced back to a numbered source.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from database import repo_articles as articles_repo
from database import repo_social as social_repo
from database import repo_stories as stories_repo
from models.types import (
    CREDIBLE_REPORTING_TYPES,
    FIRST_PERSON_TYPES,
    SourceType,
    normalize_source_type,
    source_counts,
    status_badge,
)
from utils.config import get_config
from utils.textutil import sha1, truncate
from utils.timeutil import format_display, humanize_age


@dataclass
class SourceRef:
    index: int
    name: str
    source_type: str
    title: str
    url: str
    published_at: Optional[str]
    excerpt: str
    is_official: bool = False
    is_derivative: bool = False
    kind: str = "article"        # article | social
    username: Optional[str] = None

    @property
    def label(self) -> str:
        return f"[{self.index}] {self.name}"

    def as_dict(self) -> Dict[str, Any]:
        return {
            "index": self.index, "name": self.name, "source_type": self.source_type,
            "title": self.title, "url": self.url, "published_at": self.published_at,
            "when": format_display(self.published_at), "kind": self.kind,
            "is_official": self.is_official, "is_derivative": self.is_derivative,
        }


@dataclass
class StoryContext:
    story: Dict[str, Any] = field(default_factory=dict)
    articles: List[Dict[str, Any]] = field(default_factory=list)
    social_posts: List[Dict[str, Any]] = field(default_factory=list)
    sources: List[SourceRef] = field(default_factory=list)

    # ------------------------------------------------------------ helpers --
    @property
    def headline(self) -> str:
        return self.story.get("headline") or "Untitled story"

    @property
    def status(self) -> str:
        return self.story.get("status") or "UNVERIFIED"

    @property
    def fighters(self) -> List[str]:
        """Fighters this story is about.

        The stored list first, then the matchup in the headline. A headline
        that plainly names two fighters must never sit above "no fighter
        detected in the collected text" - the app was contradicting itself on
        one screen whenever the fighters were not yet in the registry.
        """
        names = [name for name in (self.story.get("fighters") or []) if name]
        if names:
            return names
        from processors.entities import find_fighters

        try:
            return find_fighters(self.headline)
        except Exception:  # entity lookup must never break a research page
            return []

    @property
    def events(self) -> List[str]:
        """Events this story is about, including the one it is linked to.

        ``event_id`` is the canonical relationship established by the
        pipeline. If the story is attached to an event, that event *is* named
        in this story's material, whatever the text matching found.
        """
        names = [name for name in (self.story.get("events") or []) if name]
        if names:
            return names
        event_id = self.story.get("event_id")
        if event_id:
            try:
                from database import repo_entities as entities_repo

                event = entities_repo.get_event(int(event_id))
                if event and event.get("name"):
                    return [str(event["name"])]
            except Exception:
                pass
        from processors.entities import find_events

        try:
            return find_events(self.headline)
        except Exception:
            return []


    @property
    def official_sources(self) -> List[SourceRef]:
        return [source for source in self.sources if source.is_official]

    @property
    def credible_sources(self) -> List[SourceRef]:
        return [
            source for source in self.sources
            if source.source_type in CREDIBLE_REPORTING_TYPES and not source.is_derivative
        ]

    @property
    def first_person_sources(self) -> List[SourceRef]:
        return [source for source in self.sources if source.source_type in FIRST_PERSON_TYPES]

    @property
    def counts(self) -> Dict[str, int]:
        """News sources / independent news sources / social posts."""
        return source_counts(self.story)

    @property
    def conflict_notes(self) -> List[str]:
        return list(self.story.get("conflict_notes") or [])

    @property
    def source_text(self) -> str:
        """All collected text - used by the grounding checker."""
        parts: List[str] = []
        for article in self.articles:
            parts.extend([
                str(article.get("title") or ""), str(article.get("excerpt") or ""),
                str(article.get("content_snippet") or ""),
            ])
        for post in self.social_posts:
            parts.append(str(post.get("text") or ""))
        return "\n".join(part for part in parts if part)

    def fingerprint(self) -> str:
        """Stable hash of the material - the cache key for generated text."""
        parts = [str(self.story.get("id")), self.status, self.headline]
        parts += [str(article.get("id")) for article in self.articles]
        parts += [str(post.get("id")) for post in self.social_posts]
        parts.append(str(self.story.get("last_updated_at")))
        return sha1("|".join(parts))

    def sources_block(self, max_chars: Optional[int] = None) -> str:
        """The numbered source material that goes into a prompt."""
        limit = max_chars or get_config().ai.max_context_chars
        lines: List[str] = []
        for source in self.sources:
            marker = " (OFFICIAL)" if source.is_official else ""
            marker += " (credits another outlet)" if source.is_derivative else ""
            when = format_display(source.published_at)
            body = truncate(source.excerpt, 600)
            lines.append(
                f"[{source.index}] {source.name}{marker} - {source.source_type} - {when}\n"
                f"    HEADLINE: {source.title}\n"
                f"    TEXT: {body}\n"
                f"    LINK: {source.url}"
            )
        block = "\n\n".join(lines)
        return block[:limit]

    def facts_block(self) -> str:
        """What the processors derived, stated plainly for the prompt."""
        story = self.story
        lines = [
            f"HEADLINE: {self.headline}",
            f"STATUS: {status_badge(self.status)}",
            f"CATEGORY: {story.get('category')}",
            f"FIGHTERS MENTIONED: {', '.join(self.fighters) or 'none detected'}",
            f"EVENT MENTIONED: {', '.join(self.events) or 'none detected'}",
            f"NEWS SOURCES: {self.counts['total']} "
            f"({self.counts['independent']} independent)",
            f"SOCIAL POSTS (signals, never news sources): {self.counts['social']}",
            f"OFFICIALLY CONFIRMED: {'yes' if story.get('official_confirmed') else 'no'}",
            f"SOURCE-SUPPORT SCORE: {story.get('support_score')} ({story.get('support_label')})",
            f"FIRST SEEN: {format_display(story.get('first_seen_at'))}",
            f"LAST UPDATED: {format_display(story.get('last_updated_at'))} "
            f"({humanize_age(story.get('last_updated_at'))})",
        ]
        if story.get("status_reasons"):
            lines.append("WHY THIS STATUS: " + "; ".join(story["status_reasons"][:5]))
        if self.conflict_notes:
            lines.append("SOURCES DISAGREE: " + "; ".join(self.conflict_notes))
        return "\n".join(lines)

    def sources_for_display(self) -> List[Dict[str, Any]]:
        return [source.as_dict() for source in self.sources]


def build_story_context(
    story_id: int,
    story: Optional[Dict[str, Any]] = None,
    max_articles: int = 12,
    max_posts: int = 10,
) -> Optional[StoryContext]:
    """Collect everything known about one story into a context object."""
    story = story or stories_repo.get_story(story_id)
    if story is None:
        return None
    articles = articles_repo.articles_for_story(story_id)[:max_articles]
    posts = social_repo.posts_for_story(story_id)[:max_posts]
    context = StoryContext(story=story, articles=articles, social_posts=posts)

    index = 1
    for article in articles:
        source_type = normalize_source_type(article.get("source_type"))
        context.sources.append(SourceRef(
            index=index,
            name=str(article.get("source_name") or article.get("domain") or "unknown source"),
            source_type=source_type,
            title=str(article.get("title") or ""),
            url=str(article.get("url") or ""),
            published_at=article.get("published_at") or article.get("collected_at"),
            excerpt=str(article.get("content_snippet") or article.get("excerpt") or ""),
            is_official=bool(article.get("is_official")) or source_type == SourceType.OFFICIAL.value,
            is_derivative=bool(article.get("is_derivative")),
            kind="article",
        ))
        index += 1
    for post in posts:
        account_type = normalize_source_type(post.get("account_type"))
        username = post.get("username") or "unknown"
        context.sources.append(SourceRef(
            index=index,
            name=f"@{username} (X post)",
            source_type=account_type,
            title=truncate(post.get("text") or "", 120),
            url=str(post.get("url") or ""),
            published_at=post.get("created_at_source") or post.get("collected_at"),
            excerpt=str(post.get("text") or ""),
            is_official=account_type == SourceType.OFFICIAL.value,
            kind="social",
            username=username,
        ))
        index += 1
    return context
