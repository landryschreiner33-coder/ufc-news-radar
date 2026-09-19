"""SQLite connection handling, schema creation and migrations.

Design notes
------------
* One connection per thread (Streamlit reruns scripts on several threads).
* ``PRAGMA foreign_keys=ON`` so cascades actually happen.
* WAL journal mode so the UI can read while a collection run writes.
* Schema version is tracked in both ``PRAGMA user_version`` and the
  ``migrations`` table, so upgrading a live database is a matter of adding a
  numbered entry to ``MIGRATIONS`` below.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

from utils.config import get_config
from utils.logging_setup import get_logger
from utils.paths import PROJECT_ROOT, ensure_dirs, resolve
from utils.timeutil import utcnow_iso

logger = get_logger(__name__)

SCHEMA_FILE = PROJECT_ROOT / "database" / "schema.sql"
SCHEMA_VERSION = 4

# Future schema changes go here as (version, name, list-of-SQL-statements).
# Version 1 is the base schema in schema.sql; a fresh database is created at
# SCHEMA_VERSION so these only run when upgrading an older file.
MIGRATIONS: List[tuple] = [
    (
        2,
        "articles.has_denial",
        ["ALTER TABLE articles ADD COLUMN has_denial INTEGER NOT NULL DEFAULT 0"],
    ),
    (
        3,
        "event lifecycle, canonical identity and article intent",
        [
            # -- events: lifecycle + canonical identity ---------------------
            "ALTER TABLE events ADD COLUMN canonical_key TEXT",
            "ALTER TABLE events ADD COLUMN official_event_id TEXT",
            "ALTER TABLE events ADD COLUMN scheduled_start_utc TEXT",
            "ALTER TABLE events ADD COLUMN scheduled_end_utc TEXT",
            "ALTER TABLE events ADD COLUMN local_timezone TEXT",
            "ALTER TABLE events ADD COLUMN city TEXT",
            "ALTER TABLE events ADD COLUMN event_status TEXT NOT NULL DEFAULT 'UNKNOWN'",
            "ALTER TABLE events ADD COLUMN status_source TEXT",
            "ALTER TABLE events ADD COLUMN status_confidence TEXT",
            "ALTER TABLE events ADD COLUMN status_updated_at TEXT",
            "ALTER TABLE events ADD COLUMN status_reasons TEXT",
            "ALTER TABLE events ADD COLUMN status_conflicts TEXT",
            "ALTER TABLE events ADD COLUMN official_source_url TEXT",
            "ALTER TABLE events ADD COLUMN image_url TEXT",
            "ALTER TABLE events ADD COLUMN merged_into_id INTEGER",
            """CREATE TABLE IF NOT EXISTS event_aliases (
                   id          INTEGER PRIMARY KEY AUTOINCREMENT,
                   event_id    INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                   alias_key   TEXT    NOT NULL UNIQUE,
                   alias_name  TEXT,
                   created_at  TEXT    NOT NULL
               )""",
            "CREATE INDEX IF NOT EXISTS idx_event_aliases_event ON event_aliases(event_id)",
            "CREATE INDEX IF NOT EXISTS idx_events_status ON events(event_status)",
            "CREATE INDEX IF NOT EXISTS idx_events_start ON events(scheduled_start_utc)",
            # -- articles: what the piece is actually doing ------------------
            "ALTER TABLE articles ADD COLUMN intent TEXT NOT NULL DEFAULT 'UNKNOWN'",
            "ALTER TABLE articles ADD COLUMN intent_reasons TEXT",
            "ALTER TABLE articles ADD COLUMN event_occurred_at TEXT",
            "ALTER TABLE articles ADD COLUMN updated_at_source TEXT",
            # -- fight cards: official vs reported separation ----------------
            "ALTER TABLE fight_card_items ADD COLUMN official_status TEXT NOT NULL DEFAULT 'reported'",
            "ALTER TABLE fight_card_items ADD COLUMN canonical_fighter_a TEXT",
            "ALTER TABLE fight_card_items ADD COLUMN canonical_fighter_b TEXT",
        ],
    ),
    (
        4,
        "data correction history",
        [
            """CREATE TABLE IF NOT EXISTS data_corrections (
                   id           INTEGER PRIMARY KEY AUTOINCREMENT,
                   kind         TEXT    NOT NULL,
                   target_table TEXT    NOT NULL,
                   target_id    INTEGER,
                   before_value TEXT,
                   after_value  TEXT,
                   reason       TEXT,
                   applied_by   TEXT    NOT NULL DEFAULT 'user',
                   applied_at   TEXT    NOT NULL
               )""",
            "CREATE INDEX IF NOT EXISTS idx_corrections_kind ON data_corrections(kind)",
        ],
    ),
]

_local = threading.local()
_OVERRIDE_PATH: Optional[str] = None


def set_database_path(path: Optional[str]) -> None:
    """Point the whole process at another database file (used by tests)."""
    global _OVERRIDE_PATH
    _OVERRIDE_PATH = str(path) if path else None
    close_connection()


def current_db_path() -> str:
    if _OVERRIDE_PATH:
        return _OVERRIDE_PATH
    return get_config().db_file


def _configure(connection: sqlite3.Connection) -> None:
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA busy_timeout = 8000")


def get_connection() -> sqlite3.Connection:
    """Thread-local connection, created on first use."""
    path = current_db_path()
    existing = getattr(_local, "connection", None)
    if existing is not None and getattr(_local, "path", None) == path:
        return existing
    if existing is not None:
        try:
            existing.close()
        except sqlite3.Error:
            pass
    if path != ":memory:":
        ensure_dirs()
        resolve(path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=15, check_same_thread=False)
    _configure(connection)
    _local.connection = connection
    _local.path = path
    return connection


def close_connection() -> None:
    connection = getattr(_local, "connection", None)
    if connection is not None:
        try:
            connection.close()
        except sqlite3.Error:
            pass
    _local.connection = None
    _local.path = None


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """Commit on success, roll back on any exception."""
    connection = get_connection()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise


# --------------------------------------------------------------- helpers --
def query_all(sql: str, params: Sequence[Any] | Dict[str, Any] = ()) -> List[sqlite3.Row]:
    return get_connection().execute(sql, params).fetchall()


def query_one(sql: str, params: Sequence[Any] | Dict[str, Any] = ()) -> Optional[sqlite3.Row]:
    return get_connection().execute(sql, params).fetchone()


def query_value(sql: str, params: Sequence[Any] | Dict[str, Any] = (), default: Any = None) -> Any:
    row = query_one(sql, params)
    if row is None:
        return default
    value = row[0]
    return default if value is None else value


def execute(sql: str, params: Sequence[Any] | Dict[str, Any] = ()) -> int:
    """Run a statement.

    Returns the new row id for an INSERT that actually inserted, and the
    affected row count otherwise.  The distinction matters for
    ``INSERT OR IGNORE``: SQLite leaves ``lastrowid`` pointing at the previous
    insert, so returning it blindly would report a write that never happened.
    """
    with transaction() as connection:
        cursor = connection.execute(sql, params)
        is_insert = sql.lstrip()[:6].upper() == "INSERT"
        if is_insert and cursor.rowcount > 0:
            return cursor.lastrowid or cursor.rowcount
        return cursor.rowcount if cursor.rowcount > 0 else 0


def rows_to_dicts(rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
    return [dict(row) for row in rows]


def json_dump(value: Any) -> Optional[str]:
    """Serialise a list/dict for a JSON TEXT column."""
    if value is None:
        return None
    try:
        return json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        return json.dumps(str(value))


def json_load(value: Any, default: Any = None) -> Any:
    """Read a JSON TEXT column; bad data returns the default, never raises."""
    if value is None or value == "":
        return default if default is not None else None
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, json.JSONDecodeError):
        return default if default is not None else None


# ------------------------------------------------------------ schema init --
def _table_exists(connection: sqlite3.Connection, name: str) -> bool:
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def apply_schema(connection: Optional[sqlite3.Connection] = None) -> None:
    connection = connection or get_connection()
    sql = SCHEMA_FILE.read_text(encoding="utf-8")
    connection.executescript(sql)
    connection.commit()


def apply_migrations(connection: Optional[sqlite3.Connection] = None) -> List[int]:
    """Apply any migration whose version is above the stored user_version."""
    connection = connection or get_connection()
    current = connection.execute("PRAGMA user_version").fetchone()[0]
    applied: List[int] = []
    for version, name, statements in MIGRATIONS:
        if version <= current:
            continue
        logger.info("Applying migration %s (%s)", version, name)
        for statement in statements:
            try:
                connection.execute(statement)
            except sqlite3.OperationalError as exc:
                # A column the base schema already contains is not an error.
                if "duplicate column" not in str(exc).lower():
                    raise
                logger.debug("Migration %s: %s (already applied)", version, exc)
        connection.execute(
            "INSERT OR REPLACE INTO migrations (version, name, applied_at) VALUES (?,?,?)",
            (version, name, utcnow_iso()),
        )
        connection.execute(f"PRAGMA user_version = {version}")
        applied.append(version)
    connection.commit()
    return applied


def _backfill_event_identity(connection: sqlite3.Connection) -> None:
    """Canonical event keys + duplicate merging (idempotent, never fatal)."""
    try:
        from database.event_migration import backfill

        backfill(connection)
    except Exception:  # pragma: no cover - data repair must never block startup
        logger.exception("Event identity backfill failed; continuing without it")


def init_db(seed: bool = True, force_schema: bool = False) -> sqlite3.Connection:
    """Create the database if needed, apply migrations and seed reference rows.

    Safe to call on every app start.
    """
    connection = get_connection()
    if force_schema or not _table_exists(connection, "sources"):
        apply_schema(connection)
        connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        connection.execute(
            "INSERT OR REPLACE INTO migrations (version, name, applied_at) VALUES (?,?,?)",
            (SCHEMA_VERSION, "base schema", utcnow_iso()),
        )
        connection.commit()
        logger.info("Created database schema at %s", current_db_path())
    else:
        apply_schema(connection)  # CREATE TABLE IF NOT EXISTS - adds new tables
        apply_migrations(connection)
    _backfill_event_identity(connection)
    if seed:
        from database import seed as seed_module

        seed_module.seed_all()
    return connection


def database_stats() -> Dict[str, int]:
    """Row counts used by the Source Health / Settings pages."""
    tables = [
        "sources", "articles", "stories", "story_sources", "story_updates",
        "social_posts", "story_social_posts", "fighters", "events",
        "fight_card_items", "fight_card_changes", "rankings", "ranking_changes",
        "summaries", "watchlists", "settings", "source_classifications",
        "monitored_social_accounts", "collection_runs",
    ]
    stats: Dict[str, int] = {}
    connection = get_connection()
    for table in tables:
        try:
            stats[table] = connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        except sqlite3.Error:
            stats[table] = -1
    return stats
