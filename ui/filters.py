"""Feed filtering + sorting shared by the dashboard and the search page."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from database import repo_stories as stories_repo
from models.types import FEED_FILTERS, FILTER_TO_CATEGORY, SORT_OPTIONS, Category, StoryStatus
from utils.textutil import normalize_text

TITLE_TERMS = ("title", "champion", "championship", "belt", "interim", "undisputed")


def filter_options() -> List[str]:
    return list(FEED_FILTERS)


def sort_options() -> List[str]:
    return list(SORT_OPTIONS)


def fetch_stories(
    feed_filter: str = "ALL",
    sort: str = "Newest",
    min_relevance: float = 0,
    search: Optional[str] = None,
    limit: int = 60,
    include_demo: bool = True,
    since_hours: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Stories for one filter/sort combination."""
    feed_filter = (feed_filter or "ALL").upper()
    kwargs: Dict[str, Any] = {
        "limit": limit,
        "sort": sort,
        "min_relevance": min_relevance,
        "search": search or None,
        "include_demo": include_demo,
        "since_hours": since_hours,
    }

    if feed_filter == "BREAKING":
        kwargs["breaking"] = True
    elif feed_filter == "DEVELOPING":
        kwargs["developing"] = True
    elif feed_filter == "TRENDING":
        kwargs["trending"] = True
    elif feed_filter == "RUMORS":
        kwargs["statuses"] = [
            StoryStatus.RUMOR.value, StoryStatus.UNVERIFIED.value, StoryStatus.FIGHTER_CLAIM.value,
        ]
    elif feed_filter == "IMPORTANT":
        kwargs["min_relevance"] = max(min_relevance, 55)
    elif feed_filter == "TITLE":
        # Title news is a property of the story, not only its category.
        stories = stories_repo.list_stories(**{**kwargs, "limit": max(limit * 3, 120)})
        return [story for story in stories if is_title_story(story)][:limit]
    elif feed_filter in FILTER_TO_CATEGORY:
        kwargs["category"] = FILTER_TO_CATEGORY[feed_filter]

    return stories_repo.list_stories(**kwargs)


def is_title_story(story: Dict[str, Any]) -> bool:
    haystack = normalize_text(
        f"{story.get('headline','')} {story.get('summary','')} "
        f"{' '.join(story.get('keywords') or [])}"
    )
    if story.get("category") == Category.TITLE.value:
        return True
    return any(term in haystack for term in TITLE_TERMS)


def dashboard_sections(
    sort: str = "Newest",
    min_relevance: float = 0,
    search: Optional[str] = None,
    include_demo: bool = True,
    per_section: int = 8,
) -> Dict[str, List[Dict[str, Any]]]:
    """The six dashboard sections, with each story shown only once."""
    common = {
        "sort": sort, "min_relevance": min_relevance, "search": search,
        "include_demo": include_demo,
    }
    breaking = stories_repo.list_stories(limit=per_section, breaking=True, **common)
    seen = {story["id"] for story in breaking}

    important = [
        story for story in stories_repo.list_stories(
            limit=per_section * 3, sort="Most Relevant",
            min_relevance=max(min_relevance, 55), search=search, include_demo=include_demo,
        ) if story["id"] not in seen
    ][:per_section]
    seen.update(story["id"] for story in important)

    rumors = [
        story for story in stories_repo.list_stories(
            limit=per_section * 3,
            statuses=[StoryStatus.RUMOR.value, StoryStatus.UNVERIFIED.value,
                      StoryStatus.FIGHTER_CLAIM.value],
            **common,
        ) if story["id"] not in seen
    ][:per_section]
    seen.update(story["id"] for story in rumors)

    developing = [
        story for story in stories_repo.list_stories(limit=per_section * 3, developing=True, **common)
        if story["id"] not in seen
    ][:per_section]
    seen.update(story["id"] for story in developing)

    trending = [
        story for story in stories_repo.list_stories(limit=per_section * 3, trending=True, **common)
        if story["id"] not in seen
    ][:per_section]
    seen.update(story["id"] for story in trending)

    latest = [
        story for story in stories_repo.list_stories(
            limit=per_section * 4, sort="Newest", min_relevance=min_relevance,
            search=search, include_demo=include_demo,
        ) if story["id"] not in seen
    ][:per_section * 2]

    return {
        "breaking": breaking,
        "important": important,
        "rumors": rumors,
        "developing": developing,
        "trending": trending,
        "latest": latest,
    }
