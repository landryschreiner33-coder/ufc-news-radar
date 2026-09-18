"""Seed a database with reference data.

Every function is idempotent and *never* overwrites user edits: sources keep
their enabled flag and weights, classifications marked ``is_user_defined``
are left alone, and settings are only written when missing.
"""
from __future__ import annotations

from typing import Dict

from database import repo_settings as settings_repo
from database import repo_sources as sources_repo
from database.db import query_one
from database.repo_entities import upsert_fighter
from database.seed_data import (
    AUTHOR_CLASSIFICATIONS,
    BUILTIN_SOURCES,
    DEFAULT_SETTINGS,
    DEFAULT_WATCHLIST,
    DOMAIN_CLASSIFICATIONS,
    FIGHTER_NAMES,
    MONITORED_X_ACCOUNTS,
)
from utils.logging_setup import get_logger

logger = get_logger(__name__)


def seed_sources(overwrite: bool = False) -> int:
    count = 0
    for source in BUILTIN_SOURCES:
        sources_repo.upsert_source(source, overwrite=overwrite)
        count += 1
    return count


def seed_classifications(overwrite: bool = False) -> int:
    count = 0
    for domain, display_name, classification, weight, group in DOMAIN_CLASSIFICATIONS:
        existing = settings_repo.get_classification("domain", domain)
        if existing and existing.get("is_user_defined"):
            continue
        settings_repo.upsert_classification(
            "domain", domain, classification, reliability_weight=weight,
            independence_group=group, display_name=display_name, overwrite=overwrite or not existing,
        )
        count += 1
    for author, classification, weight in AUTHOR_CLASSIFICATIONS:
        existing = settings_repo.get_classification("author", author)
        if existing and existing.get("is_user_defined"):
            continue
        settings_repo.upsert_classification(
            "author", author, classification, reliability_weight=weight,
            display_name=author, notes="editable default", overwrite=overwrite or not existing,
        )
        count += 1
    return count


def seed_monitored_accounts(overwrite: bool = False) -> int:
    count = 0
    for account in MONITORED_X_ACCOUNTS:
        existing = settings_repo.get_monitored_account(account["username"])
        if existing and existing.get("added_by") == "user":
            continue
        settings_repo.upsert_monitored_account(
            username=account["username"],
            display_name=account.get("display_name"),
            account_type=account.get("account_type", "UNKNOWN"),
            category=account.get("category", "other"),
            priority=int(account.get("priority", 100)),
            notes=account.get("notes"),
            added_by="builtin",
            overwrite=overwrite or not existing,
        )
        count += 1
    return count


def seed_fighters() -> int:
    """Register known fighter names so the text matcher can recognise them.

    Names only - no records, rankings or statistics are seeded.
    """
    count = 0
    for name, nickname, aliases in FIGHTER_NAMES:
        upsert_fighter(name, nickname=nickname or None, aliases=aliases,
                       data_origin="builtin_name_list")
        count += 1
    return count


def seed_settings(overwrite: bool = False) -> int:
    count = 0
    for key, value, value_type in DEFAULT_SETTINGS:
        if overwrite or not settings_repo.setting_exists(key):
            settings_repo.set_setting(key, value, value_type)
            count += 1
    return count


def seed_watchlist() -> int:
    if query_one("SELECT 1 FROM watchlists LIMIT 1"):
        return 0
    for kind, value in DEFAULT_WATCHLIST:
        settings_repo.add_watchlist_item(kind, value)
    return len(DEFAULT_WATCHLIST)


def seed_all(overwrite: bool = False) -> Dict[str, int]:
    """Called on every app start; cheap and idempotent."""
    result = {
        "sources": seed_sources(overwrite),
        "classifications": seed_classifications(overwrite),
        "monitored_accounts": seed_monitored_accounts(overwrite),
        "fighters": seed_fighters(),
        "settings": seed_settings(overwrite),
        "watchlist": seed_watchlist(),
    }
    logger.debug("Seed complete: %s", result)
    return result
