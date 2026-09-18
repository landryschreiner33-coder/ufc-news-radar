"""Settings, watchlists, source classifications and monitored X accounts."""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from database.db import execute, json_dump, json_load, query_all, query_one, rows_to_dicts, transaction
from models.types import normalize_source_type, reliability_for
from utils.textutil import normalize_text
from utils.timeutil import utcnow_iso

TRUE_VALUES = {"1", "true", "yes", "on"}


# ------------------------------------------------------------- settings ----
def set_setting(key: str, value: Any, value_type: Optional[str] = None) -> None:
    if value_type is None:
        if isinstance(value, bool):
            value_type = "bool"
        elif isinstance(value, int):
            value_type = "int"
        elif isinstance(value, float):
            value_type = "float"
        elif isinstance(value, (list, dict)):
            value_type = "json"
        else:
            value_type = "str"
    stored = json_dump(value) if value_type == "json" else (
        "1" if (value_type == "bool" and value) else "0" if value_type == "bool" else str(value)
    )
    execute(
        "INSERT INTO settings (key, value, value_type, updated_at) VALUES (?,?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, value_type=excluded.value_type, "
        "updated_at=excluded.updated_at",
        (key, stored, value_type, utcnow_iso()),
    )


def _cast(value: Any, value_type: str, default: Any) -> Any:
    try:
        if value_type == "int":
            return int(float(value))
        if value_type == "float":
            return float(value)
        if value_type == "bool":
            return str(value).strip().lower() in TRUE_VALUES
        if value_type == "json":
            return json_load(value, default)
        return value
    except (TypeError, ValueError):
        return default


def get_setting(key: str, default: Any = None) -> Any:
    row = query_one("SELECT value, value_type FROM settings WHERE key = ?", (key,))
    if row is None or row["value"] is None:
        return default
    return _cast(row["value"], row["value_type"], default)


def get_int(key: str, default: int) -> int:
    value = get_setting(key, default)
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def get_float(key: str, default: float) -> float:
    value = get_setting(key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_bool(key: str, default: bool = False) -> bool:
    value = get_setting(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in TRUE_VALUES


def all_settings() -> Dict[str, Any]:
    rows = query_all("SELECT key, value, value_type FROM settings ORDER BY key")
    return {row["key"]: _cast(row["value"], row["value_type"], row["value"]) for row in rows}


def setting_exists(key: str) -> bool:
    return query_one("SELECT 1 FROM settings WHERE key = ?", (key,)) is not None


# ----------------------------------------------- source classifications ----
def upsert_classification(
    kind: str,
    identifier: str,
    classification: str,
    reliability_weight: Optional[float] = None,
    independence_group: Optional[str] = None,
    display_name: Optional[str] = None,
    notes: Optional[str] = None,
    is_user_defined: bool = False,
    overwrite: bool = True,
) -> int:
    normalized = normalize_text(identifier).replace(" ", "")
    classification = normalize_source_type(classification)
    weight = reliability_weight if reliability_weight is not None else reliability_for(classification)
    now = utcnow_iso()
    existing = query_one(
        "SELECT id FROM source_classifications WHERE kind = ? AND normalized_id = ?", (kind, normalized)
    )
    if existing and not overwrite:
        return int(existing["id"])
    if existing:
        execute(
            "UPDATE source_classifications SET identifier=?, display_name=?, classification=?, "
            "reliability_weight=?, independence_group=?, notes=?, is_user_defined=?, updated_at=? WHERE id=?",
            (identifier, display_name, classification, weight, independence_group, notes,
             1 if is_user_defined else 0, now, existing["id"]),
        )
        return int(existing["id"])
    return execute(
        "INSERT INTO source_classifications (kind, identifier, normalized_id, display_name, classification, "
        "reliability_weight, independence_group, notes, is_user_defined, created_at, updated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (kind, identifier, normalized, display_name, classification, weight, independence_group,
         notes, 1 if is_user_defined else 0, now, now),
    )


def get_classification(kind: str, identifier: str) -> Optional[Dict[str, Any]]:
    normalized = normalize_text(identifier).replace(" ", "")
    row = query_one(
        "SELECT * FROM source_classifications WHERE kind = ? AND normalized_id = ?", (kind, normalized)
    )
    return dict(row) if row else None


def classify_domain(domain: Optional[str]) -> Optional[Dict[str, Any]]:
    """Look up a domain, walking up sub-domains (a.b.espn.com -> espn.com)."""
    if not domain:
        return None
    parts = str(domain).lower().lstrip(".").split(".")
    for index in range(len(parts) - 1):
        candidate = ".".join(parts[index:])
        found = get_classification("domain", candidate)
        if found:
            return found
    return None


def classify_author(author: Optional[str]) -> Optional[Dict[str, Any]]:
    if not author:
        return None
    cleaned = str(author).split(",")[0].strip()
    found = get_classification("author", cleaned)
    if found:
        return found
    # Bylines often look like "By Nolan King | MMA Junkie"
    for separator in ("|", " - ", " for "):
        if separator in cleaned:
            return get_classification("author", cleaned.split(separator)[0].strip())
    return None


def list_classifications(kind: Optional[str] = None) -> List[Dict[str, Any]]:
    if kind:
        rows = query_all(
            "SELECT * FROM source_classifications WHERE kind = ? ORDER BY classification, identifier", (kind,)
        )
    else:
        rows = query_all("SELECT * FROM source_classifications ORDER BY kind, classification, identifier")
    return rows_to_dicts(rows)


def delete_classification(classification_id: int) -> None:
    execute("DELETE FROM source_classifications WHERE id = ?", (classification_id,))


# ------------------------------------------------------------ watchlists ---
def add_watchlist_item(kind: str, value: str, notes: Optional[str] = None) -> int:
    value = str(value).strip()
    if not value:
        return 0
    normalized = normalize_text(value)
    now = utcnow_iso()
    execute(
        "INSERT INTO watchlists (kind, value, normalized_value, notes, active, hit_count, created_at) "
        "VALUES (?,?,?,?,1,0,?) ON CONFLICT(kind, normalized_value) DO UPDATE SET active=1, notes=excluded.notes",
        (kind, value, normalized, notes, now),
    )
    row = query_one(
        "SELECT id FROM watchlists WHERE kind = ? AND normalized_value = ?", (kind, normalized)
    )
    return int(row["id"]) if row else 0


def remove_watchlist_item(item_id: int) -> None:
    execute("DELETE FROM watchlists WHERE id = ?", (item_id,))


def list_watchlist(kind: Optional[str] = None, active_only: bool = True) -> List[Dict[str, Any]]:
    clauses, params = [], []
    if kind:
        clauses.append("kind = ?")
        params.append(kind)
    if active_only:
        clauses.append("active = 1")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return rows_to_dicts(query_all(f"SELECT * FROM watchlists {where} ORDER BY kind, value", params))


def watchlist_values(kind: Optional[str] = None) -> List[str]:
    return [row["value"] for row in list_watchlist(kind)]


def record_watchlist_hit(item_id: int) -> None:
    execute(
        "UPDATE watchlists SET hit_count = hit_count + 1, last_hit_at = ? WHERE id = ?",
        (utcnow_iso(), item_id),
    )


# --------------------------------------------- monitored social accounts ---
def upsert_monitored_account(
    username: str,
    display_name: Optional[str] = None,
    account_type: str = "UNKNOWN",
    category: str = "other",
    platform: str = "x",
    priority: int = 100,
    notes: Optional[str] = None,
    enabled: bool = True,
    added_by: str = "user",
    overwrite: bool = True,
) -> int:
    username = str(username).strip().lstrip("@").lower()
    if not username:
        return 0
    now = utcnow_iso()
    existing = query_one(
        "SELECT id FROM monitored_social_accounts WHERE platform = ? AND username = ?", (platform, username)
    )
    account_type = normalize_source_type(account_type)
    if existing:
        if overwrite:
            execute(
                "UPDATE monitored_social_accounts SET display_name=?, account_type=?, category=?, "
                "priority=?, notes=?, enabled=?, updated_at=? WHERE id=?",
                (display_name, account_type, category, priority, notes, 1 if enabled else 0, now,
                 existing["id"]),
            )
        return int(existing["id"])
    account_id = execute(
        "INSERT INTO monitored_social_accounts (platform, username, display_name, account_type, category, "
        "enabled, priority, notes, added_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (platform, username, display_name, account_type, category, 1 if enabled else 0, priority, notes,
         added_by, now, now),
    )
    # Keep the X-account classification table in step with the monitored list.
    upsert_classification(
        "x_account", username, account_type, display_name=display_name,
        notes="from monitored account list", is_user_defined=(added_by == "user"), overwrite=overwrite,
    )
    return account_id


def list_monitored_accounts(
    enabled_only: bool = False, category: Optional[str] = None, platform: str = "x"
) -> List[Dict[str, Any]]:
    clauses, params = ["platform = ?"], [platform]
    if enabled_only:
        clauses.append("enabled = 1")
    if category:
        clauses.append("category = ?")
        params.append(category)
    return rows_to_dicts(query_all(
        f"SELECT * FROM monitored_social_accounts WHERE {' AND '.join(clauses)} ORDER BY priority, username",
        params,
    ))


def get_monitored_account(username: str, platform: str = "x") -> Optional[Dict[str, Any]]:
    row = query_one(
        "SELECT * FROM monitored_social_accounts WHERE platform = ? AND username = ?",
        (platform, str(username).strip().lstrip("@").lower()),
    )
    return dict(row) if row else None


def delete_monitored_account(account_id: int) -> None:
    execute("DELETE FROM monitored_social_accounts WHERE id = ?", (account_id,))


def set_monitored_account_enabled(account_id: int, enabled: bool) -> None:
    execute(
        "UPDATE monitored_social_accounts SET enabled = ?, updated_at = ? WHERE id = ?",
        (1 if enabled else 0, utcnow_iso(), account_id),
    )


def update_monitored_account_state(
    username: str,
    user_id: Optional[str] = None,
    last_post_id: Optional[str] = None,
    last_error: Optional[str] = None,
    platform: str = "x",
) -> None:
    username = str(username).strip().lstrip("@").lower()
    with transaction() as connection:
        connection.execute(
            "UPDATE monitored_social_accounts SET last_checked_at = ?, "
            "user_id = COALESCE(?, user_id), last_post_id = COALESCE(?, last_post_id), last_error = ?, "
            "updated_at = ? WHERE platform = ? AND username = ?",
            (utcnow_iso(), user_id, last_post_id, last_error, utcnow_iso(), platform, username),
        )


def classify_x_account(username: Optional[str]) -> str:
    """Account type for a username, from the monitored list or classifications."""
    if not username:
        return "UNKNOWN"
    handle = str(username).strip().lstrip("@").lower()
    account = get_monitored_account(handle)
    if account:
        return normalize_source_type(account["account_type"])
    found = get_classification("x_account", handle)
    if found:
        return normalize_source_type(found["classification"])
    return "UNKNOWN"
