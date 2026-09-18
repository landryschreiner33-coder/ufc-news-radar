"""Demo data for trying the interface without network access.

Everything here is FICTIONAL and clearly marked:

* every fighter/event/outlet name is invented ("Demo Fighter Alpha",
  "DEMO FIGHT NIGHT 1", "Demo Wire");
* every row is stored with ``is_demo = 1``;
* every headline starts with "[DEMO]";
* all links point at example.invalid, a reserved domain that cannot resolve.

Demo rows are never presented as real UFC news: the dashboard shows a banner
whenever any are loaded, and one click removes them.
"""
from __future__ import annotations

from typing import Any, Dict, List

from database import repo_articles as articles_repo
from database import repo_settings as settings_repo
from database import repo_social as social_repo
from database.db import execute, query_value
from models.types import SourceType
from utils.logging_setup import get_logger
from utils.timeutil import to_iso, utcnow

logger = get_logger(__name__)

DEMO_PREFIX = "[DEMO]"
DEMO_BANNER = (
    "DEMO DATA IS LOADED. These stories are fictional examples used to try the interface. "
    "They are not real UFC news. Remove them from Settings when you are done."
)


def _hours_ago(hours: float) -> str:
    from datetime import timedelta

    return to_iso(utcnow() - timedelta(hours=hours))  # type: ignore[return-value]


def _demo_articles() -> List[Dict[str, Any]]:
    """Four fictional developments that exercise the different statuses."""
    return [
        # 1. Officially "confirmed" booking -> CONFIRMED
        {
            "source_key": "demo_official", "source_name": "Demo Promotion (official, fictional)",
            "url": "https://demo-promotion.example.invalid/news/alpha-vs-bravo-official",
            "title": f"{DEMO_PREFIX} Demo Fighter Alpha vs. Demo Fighter Bravo official for DEMO FIGHT NIGHT 1",
            "author": "Demo Staff", "published_at": _hours_ago(3),
            "excerpt": "The demo promotion officially announced that Demo Fighter Alpha will face "
                       "Demo Fighter Bravo in the DEMO FIGHT NIGHT 1 main event.",
            "source_type": SourceType.OFFICIAL.value, "reliability_weight": 1.0,
            "independence_group": "demo_official", "is_official": True,
        },
        {
            "source_key": "demo_wire", "source_name": "Demo Wire (fictional outlet)",
            "url": "https://demo-wire.example.invalid/alpha-bravo-booked",
            "title": f"{DEMO_PREFIX} Alpha vs. Bravo booked for DEMO FIGHT NIGHT 1 main event",
            "author": "Demo Reporter", "published_at": _hours_ago(2.5),
            "excerpt": "Demo Fighter Alpha and Demo Fighter Bravo will meet in the main event, "
                       "the promotion announced.",
            "source_type": SourceType.MAJOR_NEWS.value, "reliability_weight": 0.85,
            "independence_group": "demo_wire",
        },
        # 2. Hedged injury report from two outlets -> REPORTED/DEVELOPING
        {
            "source_key": "demo_wire", "source_name": "Demo Wire (fictional outlet)",
            "url": "https://demo-wire.example.invalid/charlie-injury-report",
            "title": f"{DEMO_PREFIX} Report: Demo Fighter Charlie reportedly dealing with an injury",
            "author": "Demo Reporter", "published_at": _hours_ago(6),
            "excerpt": "Demo Fighter Charlie is reportedly carrying an injury and could be forced "
                       "out, sources say. Nothing has been confirmed.",
            "source_type": SourceType.MAJOR_NEWS.value, "reliability_weight": 0.85,
            "independence_group": "demo_wire",
        },
        {
            "source_key": "demo_daily", "source_name": "Demo Daily (fictional outlet)",
            "url": "https://demo-daily.example.invalid/charlie-injury",
            "title": f"{DEMO_PREFIX} Demo Fighter Charlie injury: withdrawal expected, per sources",
            "author": "Demo Writer", "published_at": _hours_ago(5),
            "excerpt": "Sources say Demo Fighter Charlie is expected to withdraw from the card, "
                       "though the promotion has not confirmed it.",
            "source_type": SourceType.TRUSTED_REPORTER.value, "reliability_weight": 0.7,
            "independence_group": "demo_daily",
        },
        # 3. Low-quality repeat of someone else's report -> stays a RUMOR
        {
            "source_key": "demo_buzz", "source_name": "Demo Buzz (fictional fan site)",
            "url": "https://demo-buzz.example.invalid/delta-rumour",
            "title": f"{DEMO_PREFIX} Rumour: Demo Fighter Delta could be next in line for a title shot",
            "published_at": _hours_ago(9),
            "excerpt": "Speculation suggests Demo Fighter Delta may be targeting a title shot, "
                       "according to Demo Wire.",
            "source_type": SourceType.FAN_ACCOUNT.value, "reliability_weight": 0.15,
            "independence_group": "demo_buzz",
        },
        # 4. Claim and denial about the same thing -> conflict preserved
        {
            "source_key": "demo_daily", "source_name": "Demo Daily (fictional outlet)",
            "url": "https://demo-daily.example.invalid/echo-out",
            "title": f"{DEMO_PREFIX} Demo Fighter Echo out of DEMO FIGHT NIGHT 1 bout, per report",
            "published_at": _hours_ago(4),
            "excerpt": "Demo Fighter Echo is reportedly off the card, sources say, with a "
                       "replacement being sought.",
            "source_type": SourceType.TRUSTED_REPORTER.value, "reliability_weight": 0.7,
            "independence_group": "demo_daily",
        },
        {
            "source_key": "demo_wire", "source_name": "Demo Wire (fictional outlet)",
            "url": "https://demo-wire.example.invalid/echo-denies",
            "title": f"{DEMO_PREFIX} Demo Fighter Echo denies report of withdrawal from DEMO FIGHT NIGHT 1",
            "published_at": _hours_ago(3.2),
            "excerpt": "Demo Fighter Echo disputes the report, calling it false and saying the "
                       "bout is still on.",
            "source_type": SourceType.MAJOR_NEWS.value, "reliability_weight": 0.85,
            "independence_group": "demo_wire",
        },
    ]


def _demo_posts() -> List[Dict[str, Any]]:
    return [
        {
            "platform": "x", "post_id": "demo-post-1", "username": "demo_promotion",
            "display_name": "Demo Promotion (fictional)", "account_type": SourceType.OFFICIAL.value,
            "text": f"{DEMO_PREFIX} Official: Demo Fighter Alpha vs. Demo Fighter Bravo headlines "
                    "DEMO FIGHT NIGHT 1.",
            "created_at_source": _hours_ago(2.9),
            "url": "https://example.invalid/demo-post-1",
            "like_count": 120, "repost_count": 30, "reply_count": 12,
        },
        {
            "platform": "x", "post_id": "demo-post-2", "username": "demo_fighter_echo",
            "display_name": "Demo Fighter Echo (fictional)", "account_type": SourceType.FIGHTER.value,
            "text": f"{DEMO_PREFIX} I'm not out of anything. See you on fight night.",
            "created_at_source": _hours_ago(3.1),
            "url": "https://example.invalid/demo-post-2",
            "like_count": 540, "repost_count": 80, "reply_count": 45,
        },
    ]


def load_demo_data(run_pipeline: bool = True) -> Dict[str, int]:
    """Insert the demo rows and run them through the normal pipeline."""
    from processors import pipeline

    articles = [dict(item, is_demo=True, collected_at=item.get("published_at")) for item in _demo_articles()]
    stats = pipeline.ingest_articles(articles)

    posts_stored = 0
    for post in _demo_posts():
        post = dict(post, is_demo=True)
        if social_repo.upsert_post(post) is not None:
            posts_stored += 1

    if run_pipeline:
        pipeline.cluster_unassigned()
        pipeline.link_social_posts()
        from database import repo_stories as stories_repo

        pipeline.recompute_stories([story["id"] for story in stories_repo.list_stories(limit=200)])

    # Anything created from demo articles is itself demo data.
    execute(
        "UPDATE stories SET is_demo = 1 WHERE id IN ("
        "  SELECT DISTINCT story_id FROM articles WHERE is_demo = 1 AND story_id IS NOT NULL)"
    )
    settings_repo.set_setting("demo_data_loaded", True, "bool")
    logger.info("Demo data loaded: %s articles, %s posts", stats.articles_new, posts_stored)
    return {"articles": stats.articles_new, "posts": posts_stored}


def clear_demo_data() -> Dict[str, int]:
    """Remove every demo row (and only demo rows)."""
    counts = {
        "stories": int(query_value("SELECT COUNT(*) FROM stories WHERE is_demo = 1", (), 0)),
        "articles": int(query_value("SELECT COUNT(*) FROM articles WHERE is_demo = 1", (), 0)),
        "social_posts": int(query_value("SELECT COUNT(*) FROM social_posts WHERE is_demo = 1", (), 0)),
    }
    execute("DELETE FROM story_updates WHERE story_id IN (SELECT id FROM stories WHERE is_demo = 1)")
    execute("DELETE FROM story_sources WHERE story_id IN (SELECT id FROM stories WHERE is_demo = 1)")
    execute("DELETE FROM story_social_posts WHERE story_id IN (SELECT id FROM stories WHERE is_demo = 1)")
    execute("DELETE FROM summaries WHERE story_id IN (SELECT id FROM stories WHERE is_demo = 1)")
    execute("DELETE FROM fight_card_changes WHERE is_demo = 1")
    execute("DELETE FROM fight_card_items WHERE is_demo = 1")
    execute("DELETE FROM articles WHERE is_demo = 1")
    execute("DELETE FROM social_posts WHERE is_demo = 1")
    execute("DELETE FROM stories WHERE is_demo = 1")
    execute("DELETE FROM events WHERE is_demo = 1")
    execute("DELETE FROM rankings WHERE is_demo = 1")
    execute("DELETE FROM ranking_changes WHERE is_demo = 1")
    settings_repo.set_setting("demo_data_loaded", False, "bool")
    logger.info("Demo data cleared: %s", counts)
    return counts


def demo_data_present() -> bool:
    return int(query_value("SELECT COUNT(*) FROM stories WHERE is_demo = 1", (), 0)) > 0


def demo_counts() -> Dict[str, int]:
    return {
        "stories": int(query_value("SELECT COUNT(*) FROM stories WHERE is_demo = 1", (), 0)),
        "articles": int(query_value("SELECT COUNT(*) FROM articles WHERE is_demo = 1", (), 0)),
        "social_posts": int(query_value("SELECT COUNT(*) FROM social_posts WHERE is_demo = 1", (), 0)),
    }
