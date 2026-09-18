"""Convenience re-exports so callers can use one import for data access.

    from database import repositories as repo
    repo.stories.list_stories(...)
"""
from __future__ import annotations

from database import repo_ai as ai
from database import repo_articles as articles
from database import repo_entities as entities
from database import repo_rankings as rankings
from database import repo_runs as runs
from database import repo_settings as settings
from database import repo_social as social
from database import repo_sources as sources
from database import repo_stories as stories

__all__ = ["ai", "articles", "entities", "rankings", "runs", "settings", "social", "sources", "stories"]
