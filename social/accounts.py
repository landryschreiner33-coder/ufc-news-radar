"""Monitored-account helpers shared by the UI and the collectors."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from database import repo_settings as settings_repo
from models.types import X_ACCOUNT_TYPES, normalize_source_type

CATEGORIES = [
    ("official", "Official (UFC, promotion, executives)"),
    ("journalist", "Trusted journalists"),
    ("reporter", "Established reporters / outlets"),
    ("insider", "Insiders"),
    ("fighter", "Fighters"),
    ("coach_team", "Coaches / teams"),
    ("promoter", "Managers / promoters"),
    ("other", "Other"),
]

CATEGORY_LABELS = dict(CATEGORIES)


def add_account(
    username: str,
    display_name: Optional[str] = None,
    account_type: str = "UNKNOWN",
    category: str = "other",
    notes: Optional[str] = None,
    priority: int = 100,
) -> int:
    return settings_repo.upsert_monitored_account(
        username=username,
        display_name=display_name,
        account_type=normalize_source_type(account_type),
        category=category,
        notes=notes,
        priority=priority,
        added_by="user",
    )


def remove_account(account_id: int) -> None:
    settings_repo.delete_monitored_account(account_id)


def set_enabled(account_id: int, enabled: bool) -> None:
    settings_repo.set_monitored_account_enabled(account_id, enabled)


def list_accounts(category: Optional[str] = None, enabled_only: bool = False) -> List[Dict[str, Any]]:
    return settings_repo.list_monitored_accounts(enabled_only=enabled_only, category=category)


def grouped_accounts() -> Dict[str, List[Dict[str, Any]]]:
    grouped: Dict[str, List[Dict[str, Any]]] = {key: [] for key, _ in CATEGORIES}
    for account in settings_repo.list_monitored_accounts():
        grouped.setdefault(account.get("category") or "other", []).append(account)
    return grouped


def account_type_options() -> List[str]:
    return list(X_ACCOUNT_TYPES)


def classify(username: str) -> str:
    return settings_repo.classify_x_account(username)
