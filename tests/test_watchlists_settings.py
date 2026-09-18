"""Watchlists, classifications, monitored accounts and demo data."""
from __future__ import annotations

from database import repo_settings as settings_repo
from database import repo_stories as stories_repo
from database.demo_data import clear_demo_data, demo_counts, demo_data_present, load_demo_data
from processors import pipeline, relevance
from social import accounts as accounts_mod
from tests.conftest import hours_ago


def test_watchlist_add_remove_and_dedupe():
    first = settings_repo.add_watchlist_item("fighter", "Jon Jones")
    again = settings_repo.add_watchlist_item("fighter", "jon jones")
    assert first == again, "the same entry must not be added twice"
    assert [item["value"] for item in settings_repo.list_watchlist("fighter")] == ["Jon Jones"]
    settings_repo.remove_watchlist_item(first)
    assert settings_repo.list_watchlist("fighter") == []


def test_watchlist_ignores_blank_values():
    assert settings_repo.add_watchlist_item("topic", "   ") == 0


def test_watchlist_boosts_matching_stories(ingest, make_article):
    ingest([make_article(title="Jon Jones vs. Tom Aspinall official for UFC 320",
                         excerpt="The title fight is official.")])
    pipeline.cluster_unassigned()
    story = stories_repo.list_stories(limit=1)[0]
    without = relevance.score_story(story).score
    settings_repo.add_watchlist_item("fighter", "Jon Jones")
    pipeline.recompute_story(int(story["id"]))
    boosted = stories_repo.get_story(int(story["id"]))
    assert boosted["relevance"] > without
    assert "on your watchlist" in boosted["relevance_breakdown"]


def test_watchlist_surfaces_new_stories(ingest, make_article):
    settings_repo.add_watchlist_item("fighter", "Tom Aspinall")
    ingest([make_article(title="Tom Aspinall responds to the callout",
                         excerpt="Aspinall answered the challenge.")])
    pipeline.cluster_unassigned()
    matches = stories_repo.list_stories(limit=10, search="Tom Aspinall")
    assert matches


def test_classification_lookup_walks_subdomains():
    found = settings_repo.classify_domain("edition.mma.espn.com")
    assert found and found["classification"] == "MAJOR_NEWS"
    assert settings_repo.classify_domain("unknown-site.test") is None


def test_user_classifications_override_and_persist():
    settings_repo.upsert_classification("domain", "myblog.test", "TRUSTED_REPORTER", 0.66,
                                        is_user_defined=True)
    found = settings_repo.classify_domain("myblog.test")
    assert found["reliability_weight"] == 0.66
    assert found["is_user_defined"] == 1
    from database.seed import seed_classifications

    seed_classifications()  # re-seeding must not clobber a user's edit
    assert settings_repo.classify_domain("myblog.test")["reliability_weight"] == 0.66


def test_author_classification_upgrades_unknown_sources():
    from processors.enrich import enrich_article

    article = enrich_article({
        "url": "https://unknown-outlet.test/story", "title": "Jones vs. Aspinall booked",
        "author": "Ariel Helwani", "excerpt": "The bout is booked.",
        "domain": "unknown-outlet.test", "published_at": hours_ago(1),
    })
    assert article["source_type"] == "ESTABLISHED_JOURNALIST"
    assert article["reliability_weight"] >= 0.8


def test_monitored_accounts_crud():
    account_id = accounts_mod.add_account("newreporter", "New Reporter",
                                          "ESTABLISHED_REPORTER", "reporter")
    assert account_id
    assert settings_repo.classify_x_account("@newreporter") == "TRUSTED_REPORTER"
    accounts_mod.set_enabled(account_id, False)
    assert settings_repo.get_monitored_account("newreporter")["enabled"] == 0
    accounts_mod.remove_account(account_id)
    assert settings_repo.get_monitored_account("newreporter") is None


def test_monitored_account_seed_does_not_overwrite_user_entries():
    accounts_mod.add_account("ufc", "My custom label", "OFFICIAL", "official")
    from database.seed import seed_monitored_accounts

    seed_monitored_accounts()
    assert settings_repo.get_monitored_account("ufc")["display_name"] == "My custom label"


def test_x_account_classification_defaults_to_unknown():
    assert settings_repo.classify_x_account("@some_random_account") == "UNKNOWN"
    assert settings_repo.classify_x_account(None) == "UNKNOWN"


def test_demo_data_is_marked_and_removable():
    result = load_demo_data()
    assert result["articles"] > 0
    assert demo_data_present() is True
    stories = stories_repo.list_stories(limit=20)
    assert stories and all(story["is_demo"] == 1 for story in stories)
    assert all(story["headline"].startswith("[DEMO]") for story in stories)
    statuses = {story["status"] for story in stories}
    assert "CONFIRMED" in statuses and len(statuses) > 1

    counts = demo_counts()
    removed = clear_demo_data()
    assert removed["stories"] == counts["stories"]
    assert demo_data_present() is False
    assert stories_repo.list_stories(limit=20) == []


def test_demo_removal_leaves_real_data_untouched(ingest, make_article):
    ingest([make_article(title="A real collected headline about UFC 320",
                         excerpt="Real collected content.")])
    pipeline.cluster_unassigned()
    load_demo_data()
    assert len(stories_repo.list_stories(limit=50)) > 1
    clear_demo_data()
    remaining = stories_repo.list_stories(limit=50)
    assert len(remaining) == 1
    assert remaining[0]["is_demo"] == 0
