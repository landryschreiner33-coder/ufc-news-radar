"""Shared pytest fixtures.

Every test gets its own SQLite file, so tests never see each other's data and
never touch the real database.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.db import close_connection, init_db, set_database_path  # noqa: E402
from processors.entities import reset_fighter_index                   # noqa: E402
from utils.timeutil import to_iso, utcnow                             # noqa: E402


@pytest.fixture(autouse=True)
def temp_database(tmp_path, monkeypatch):
    """Fresh, seeded database per test."""
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("AI_PROVIDER", "none")
    from utils.config import get_config

    get_config(refresh=True)
    database_file = tmp_path / "test_radar.db"
    set_database_path(str(database_file))
    init_db()
    reset_fighter_index()
    yield database_file
    close_connection()
    set_database_path(None)
    reset_fighter_index()


def hours_ago(hours: float) -> str:
    from datetime import timedelta

    return to_iso(utcnow() - timedelta(hours=hours))


@pytest.fixture
def make_article():
    """Factory for a normalised article dict (as a collector would produce)."""
    counter = {"n": 0}

    def _make(**overrides: Any) -> Dict[str, Any]:
        counter["n"] += 1
        index = counter["n"]
        article: Dict[str, Any] = {
            "source_key": f"source_{index}",
            "source_name": f"Test Source {index}",
            "url": f"https://source{index}.example.test/story-{index}",
            "domain": f"source{index}.example.test",
            "title": f"Test headline number {index}",
            "author": None,
            "published_at": hours_ago(1),
            "collected_at": hours_ago(1),
            "excerpt": "Excerpt text for the test article.",
            "content_snippet": "Excerpt text for the test article.",
            "source_type": "MAJOR_NEWS",
            "reliability_weight": 0.85,
            "independence_group": f"group_{index}",
        }
        article.update(overrides)
        return article

    return _make


@pytest.fixture
def ingest(make_article):
    """Ingest articles through the real pipeline and return the stored rows."""
    from database import repo_articles as articles_repo
    from processors import pipeline

    def _ingest(articles: List[Dict[str, Any]]):
        pipeline.ingest_articles(articles)
        return articles_repo.latest_articles(limit=100)

    return _ingest


@pytest.fixture
def fixtures_dir() -> Path:
    return Path(__file__).parent / "fixtures"
