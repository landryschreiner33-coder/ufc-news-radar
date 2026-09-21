"""Connection handling, schema creation and migrations.

Design notes
------------
* One connection per thread (Streamlit reruns scripts on several threads).
* SQLite locally, PostgreSQL when ``DATABASE_URL`` is set. Everything here
  speaks one dialect and ``database/backends.py`` translates it - see that
  module for why there is no second copy of every query.
* ``PRAGMA foreign_keys=ON`` so cascades actually happen (SQLite).
* WAL journal mode so the UI can read while a collection run writes (SQLite).
* The schema version is tracked by the backend plus the ``migrations`` table,
  so upgrading a live database is a matter of adding a numbered entry to
  ``MIGRATIONS`` below.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from typing import Any, Dict, Iterable, Iterator, List, Optional, Sequence

from database.backends import DatabaseBackend, POSTGRES, SQLITE, build_backend, postgres_url
from utils.config import get_config
from utils.logging_setup import get_logger
from utils.paths import PROJECT_ROOT, ensure_dirs, resolve
from utils.timeutil import utcnow_iso


logger = get_logger(__name__)

SCHEMA_FILE = PROJECT_ROOT / "database" / "schema.sql"
SCHEMA_VERSION = 5

# Future schema changes go here as (version, name, steps).  A step is either a
# SQL string or a callable taking the connection - data repairs (backfills,
# reclassification) need Python, and a migration that only reshapes columns
# leaves old rows exempt from the rules the new columns exist to enforce.
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
    (
        5,
        "canonical bout status, run outcomes, intent backfill, legacy column removal",
        [
            # -- bouts: one non-contradictory status model ------------------
            "ALTER TABLE fight_card_items ADD COLUMN evidence_level TEXT NOT NULL "
            "DEFAULT 'unconfirmed'",
            "ALTER TABLE fight_card_items ADD COLUMN source_type TEXT",
            lambda connection: _migrate_bout_status(connection),
            lambda connection: _drop_column(connection, "fight_card_items", "confidence"),
            # -- events: one canonical status column ------------------------
            lambda connection: _drop_column(connection, "events", "status"),
            # -- runs: what the run actually achieved -----------------------
            "ALTER TABLE collection_runs ADD COLUMN outcome TEXT NOT NULL DEFAULT 'NOT_RUN'",
            lambda connection: _backfill_run_outcomes(connection),
            # -- articles: the result-safety gate applies to old rows too ---
            lambda connection: _backfill_article_intent(connection),
            # -- settings: a seeded setting nothing ever read ----------------
            "DELETE FROM settings WHERE key = 'auto_collect_on_start'",
        ],
    ),
]


# ------------------------------------------------------- migration steps --
def table_columns(connection: Any, table: str) -> List[str]:
    """The column names of ``table`` - the portable form of PRAGMA table_info."""
    try:
        if current_backend().name == SQLITE:
            return [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]
        rows = connection.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = ? "
            "ORDER BY ordinal_position", (table,)).fetchall()
        return [row[0] for row in rows]
    except Exception:
        if current_backend().name == POSTGRES:
            connection.rollback()
        return []


def _columns(connection: Any, table: str) -> List[str]:
    return table_columns(connection, table)


def _drop_column(connection: sqlite3.Connection, table: str, column: str) -> None:
    """Remove a legacy column, tolerating a database that never had it.

    Keeping two columns that mean almost the same thing is how a fresh
    database and an upgraded one drift apart, and how a row ends up saying
    two different things at once.
    """
    if column not in _columns(connection, table):
        return
    try:
        connection.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        logger.info("Dropped legacy column %s.%s", table, column)
    except sqlite3.OperationalError as exc:
        # An index or view on the column blocks the drop. Say so rather than
        # failing the upgrade; the validator reports it as a schema warning.
        logger.warning("Could not drop legacy column %s.%s: %s", table, column, exc)


def _migrate_bout_status(connection: sqlite3.Connection) -> None:
    """Map legacy ``confidence`` onto official_status + evidence_level."""
    from processors.bout_status import from_legacy

    if "confidence" not in _columns(connection, "fight_card_items"):
        return
    rows = connection.execute(
        "SELECT id, confidence, official_status FROM fight_card_items").fetchall()
    for row in rows:
        official_status, evidence_level = from_legacy(row[1], row[2])
        connection.execute(
            "UPDATE fight_card_items SET official_status = ?, evidence_level = ? WHERE id = ?",
            (official_status, evidence_level, row[0]),
        )
    if rows:
        logger.info("Migrated %s bout(s) to the canonical status model", len(rows))


def _backfill_run_outcomes(connection: sqlite3.Connection) -> None:
    """Give historic runs the outcome their own numbers already imply."""
    connection.execute(
        "UPDATE collection_runs SET outcome = CASE "
        "  WHEN sources_attempted = 0 THEN 'NOT_RUN' "
        "  WHEN sources_ok = 0 THEN 'TOTAL_FAILURE' "
        "  WHEN sources_failed > 0 THEN 'PARTIAL' "
        "  ELSE 'SUCCESS' END"
    )


def _backfill_article_intent(connection: sqlite3.Connection) -> None:
    """Classify articles stored before the intent column existed.

    Without this, every pre-migration article keeps ``intent='UNKNOWN'`` and
    is therefore exempt from the preview/prediction gate in
    ``processors/result_safety.py`` - a safety rule that silently applies to
    new rows only is not a safety rule.
    """
    from processors.result_safety import classify_intent

    rows = connection.execute(
        "SELECT id, title, excerpt, content_snippet FROM articles "
        "WHERE intent IS NULL OR intent = '' OR intent = 'UNKNOWN'"
    ).fetchall()

    updated = 0
    for row in rows:
        try:
            verdict = classify_intent(
                title=row[1] or "", body=" ".join(filter(None, [row[2], row[3]])))
        except Exception:  # a single unclassifiable row must not block startup

            logger.exception("Could not classify intent for article %s", row[0])
            continue
        connection.execute(
            "UPDATE articles SET intent = ?, intent_reasons = ? WHERE id = ?",
            (verdict.intent, json.dumps(verdict.reasons, ensure_ascii=False), row[0]),
        )
        updated += 1
    if updated:
        logger.info("Backfilled article intent for %s row(s)", updated)

_local = threading.local()
_OVERRIDE_PATH: Optional[str] = None
_OVERRIDE_URL: Optional[str] = None


def set_database_path(path: Optional[str]) -> None:
    """Point the whole process at another database file (used by tests)."""
    global _OVERRIDE_PATH
    _OVERRIDE_PATH = str(path) if path else None
    close_connection()


def set_database_url(url: Optional[str]) -> None:
    """Point the whole process at a PostgreSQL database (used by tests)."""
    global _OVERRIDE_URL
    _OVERRIDE_URL = str(url) if url else None
    close_connection()


def current_db_path() -> str:
    if _OVERRIDE_PATH:
        return _OVERRIDE_PATH
    return get_config().db_file


def current_backend() -> DatabaseBackend:
    """The backend this process is using, built once per thread's connection."""
    backend = getattr(_local, "backend", None)
    key = (_OVERRIDE_URL or postgres_url() or "", current_db_path())
    if backend is not None and getattr(_local, "backend_key", None) == key:
        return backend
    backend = build_backend(current_db_path(), _OVERRIDE_URL or postgres_url())
    _local.backend = backend
    _local.backend_key = key
    return backend


def backend_name() -> str:
    return current_backend().name


def get_connection() -> Any:
    """Thread-local connection, created on first use."""
    backend = current_backend()
    key = getattr(_local, "backend_key", None)
    existing = getattr(_local, "connection", None)
    if existing is not None and getattr(_local, "connection_key", None) == key:
        return existing
    if existing is not None:
        try:
            existing.close()
        except Exception:
            pass
    if backend.name == SQLITE and backend.path != ":memory:":
        ensure_dirs()
        resolve(backend.path).parent.mkdir(parents=True, exist_ok=True)
    connection = backend.connect()
    _local.connection = connection
    _local.connection_key = key
    return connection


def close_connection() -> None:
    connection = getattr(_local, "connection", None)
    if connection is not None:
        try:
            connection.close()
        except Exception:
            pass
    _local.connection = None
    _local.connection_key = None
    _local.backend = None
    _local.backend_key = None
    _local.id_columns = None


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
def _run(connection: Any, sql: str, params: Sequence[Any] | Dict[str, Any] = ()) -> Any:
    """Execute one statement, translated for the active backend."""
    backend = current_backend()
    if backend.name == SQLITE:
        return connection.execute(sql, params)
    # The PostgreSQL connection wrapper translates on the way through.
    return connection.execute(sql, params)


def query_all(sql: str, params: Sequence[Any] | Dict[str, Any] = ()) -> List[Any]:
    return _run(get_connection(), sql, params).fetchall()


def query_one(sql: str, params: Sequence[Any] | Dict[str, Any] = ()) -> Optional[Any]:
    return _run(get_connection(), sql, params).fetchone()


def query_value(sql: str, params: Sequence[Any] | Dict[str, Any] = (), default: Any = None) -> Any:
    row = query_one(sql, params)
    if row is None:
        return default
    value = row[0]
    return default if value is None else value


_INSERT_TARGET_RE = __import__("re").compile(
    r"INSERT\s+(?:OR\s+\w+\s+)?INTO\s+([A-Za-z_][A-Za-z0-9_]*)", __import__("re").IGNORECASE)


def _insert_target(sql: str) -> Optional[str]:
    match = _INSERT_TARGET_RE.search(sql)
    return match.group(1) if match else None


def _table_has_id(connection: Any, table: Optional[str]) -> bool:
    """Whether a table has an ``id`` column, cached per connection.

    Needed because PostgreSQL has no ``lastrowid``: the id has to be asked for
    with RETURNING, and asking a table that has none (``settings`` is keyed by
    ``key``) is an error rather than a no-op.
    """
    if not table:
        return False
    cache = getattr(_local, "id_columns", None)
    if cache is None:
        cache = {}
        _local.id_columns = cache
    if table not in cache:
        cache[table] = "id" in table_columns(connection, table)
    return cache[table]


def insert_with_id(connection: Any, sql: str,
                   params: Sequence[Any] | Dict[str, Any] = ()) -> Optional[int]:
    """Insert one row inside an open transaction and return its new id.

    PostgreSQL has no ``lastrowid``, so the id is requested with RETURNING.
    Call sites use this instead of touching the cursor, which keeps every
    repository free of backend-specific code.
    """
    backend = current_backend()
    if backend.needs_returning and "RETURNING" not in sql.upper():
        cursor = _run(connection, backend.with_returning_id(sql), params)
        row = cursor.fetchone() if cursor.rowcount else None
        return int(row[0]) if row is not None else None
    cursor = _run(connection, sql, params)
    return getattr(cursor, "lastrowid", None)


def execute(sql: str, params: Sequence[Any] | Dict[str, Any] = ()) -> int:
    """Run a statement.


    Returns the new row id for an INSERT that actually inserted, and the
    affected row count otherwise.  The distinction matters for
    ``INSERT OR IGNORE``: SQLite leaves ``lastrowid`` pointing at the previous
    insert, so returning it blindly would report a write that never happened.
    """
    backend = current_backend()
    is_insert = sql.lstrip()[:6].upper() == "INSERT"
    with transaction() as connection:
        if (is_insert and backend.needs_returning and "RETURNING" not in sql.upper()
                and _table_has_id(connection, _insert_target(sql))):
            # PostgreSQL has no lastrowid, so the id is asked for explicitly.
            cursor = _run(connection, backend.with_returning_id(sql), params)
            row = cursor.fetchone() if cursor.rowcount else None
            if row is not None:
                return int(row[0])
            return 0
        cursor = _run(connection, sql, params)
        if is_insert and cursor.rowcount > 0:
            return (getattr(cursor, "lastrowid", None) or cursor.rowcount)
        return cursor.rowcount if cursor.rowcount > 0 else 0


def rows_to_dicts(rows: Iterable[Any]) -> List[Dict[str, Any]]:
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
def _table_exists(connection: Any, name: str) -> bool:
    if current_backend().name == SQLITE:
        row = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
    else:
        row = connection.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = current_schema() AND table_name = ?", (name,)
        ).fetchone()
    return row is not None


def apply_schema(connection: Optional[Any] = None) -> None:
    connection = connection or get_connection()
    sql = current_backend().translate_schema(SCHEMA_FILE.read_text(encoding="utf-8"))
    connection.executescript(sql)
    connection.commit()


def apply_migrations(connection: Optional[Any] = None) -> List[int]:
    """Apply any migration whose version is above the stored schema version."""
    connection = connection or get_connection()
    backend = current_backend()
    current = backend.schema_version(connection)
    applied: List[int] = []

    for version, name, steps in MIGRATIONS:
        if version <= current:
            continue
        logger.info("Applying migration %s (%s)", version, name)
        for step in steps:
            try:
                if callable(step):
                    step(connection)
                else:
                    connection.execute(step)
            except Exception as exc:
                # A column the base schema already contains is not an error.
                message = str(exc).lower()
                if "duplicate column" not in message and "already exists" not in message:
                    raise
                logger.debug("Migration %s: %s (already applied)", version, exc)
                if backend.name == POSTGRES:
                    # PostgreSQL aborts the whole transaction on any error, so
                    # the tolerated one has to be rolled back before the next
                    # statement can run.
                    connection.rollback()
        _record_migration(connection, version, name)
        backend.set_schema_version(connection, version)
        applied.append(version)
    connection.commit()
    return applied


def _record_migration(connection: Any, version: int, name: str) -> None:
    """Upsert the migration log entry, in a form both backends accept."""
    connection.execute(
        "INSERT INTO migrations (version, name, applied_at) VALUES (?,?,?) "
        "ON CONFLICT(version) DO UPDATE SET name = excluded.name, "
        "applied_at = excluded.applied_at",
        (version, name, utcnow_iso()),
    )


def _backfill_event_identity(connection: Any) -> None:
    """Canonical event keys + duplicate merging (idempotent, never fatal)."""
    try:
        from database.event_migration import backfill

        backfill(connection)
    except Exception:  # pragma: no cover - data repair must never block startup
        logger.exception("Event identity backfill failed; continuing without it")


def init_db(seed: bool = True, force_schema: bool = False) -> Any:
    """Create the database if needed, apply migrations and seed reference rows.

    Safe to call on every app start.
    """
    connection = get_connection()
    backend = current_backend()
    if force_schema or not _table_exists(connection, "sources"):
        apply_schema(connection)
        backend.set_schema_version(connection, SCHEMA_VERSION)
        _record_migration(connection, SCHEMA_VERSION, "base schema")
        connection.commit()
        logger.info("Created database schema on %s", backend.describe())

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
            stats[table] = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except Exception:
            stats[table] = -1
            if current_backend().name == POSTGRES:
                connection.rollback()
    return stats

