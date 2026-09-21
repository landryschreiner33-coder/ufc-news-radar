"""Where the data actually lives: SQLite locally, PostgreSQL in production.

One SQL dialect is written throughout the repositories - SQLite's - and this
module translates it for PostgreSQL. That is deliberate: a second copy of
every query is a second place for the two to drift apart, and this app is
about not being wrong.

The schema was already written to port cleanly (ISO-8601 UTC text timestamps,
JSON held as TEXT, no SQLite-only column types), so the translation is small
and every rule below is listed with the reason it exists.

Choosing a backend
------------------
``DATABASE_URL`` (or ``UFC_RADAR_DATABASE_URL``), or a ``[database]`` section
in the Streamlit secrets, selects PostgreSQL; anything else uses the SQLite
file at ``UFC_RADAR_DB``. ``database/db_config.py`` owns that decision and
the validation behind it - this module only builds what it is told to.

If a PostgreSQL URL is configured but the driver is missing, start-up **fails
loudly**: quietly writing to a local file while the operator believes their
data is going to a managed database is exactly the kind of silent lie this
project refuses to ship.
"""
from __future__ import annotations

import re
import sqlite3
import threading
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional, Sequence

from database.db_config import database_url, safe_url as _safe_url
from utils.logging_setup import get_logger

logger = get_logger(__name__)

SQLITE = "sqlite"
POSTGRES = "postgresql"


class DriverMissingError(RuntimeError):
    """A PostgreSQL URL is configured but psycopg2 is not installed."""


# --------------------------------------------------------------- SQLite ----
class DatabaseBackend(ABC):
    """What the rest of the app needs from a database, and nothing more."""

    name: str = ""
    #: True when ``INSERT`` needs an explicit RETURNING clause for the new id.
    needs_returning: bool = False

    @abstractmethod
    def connect(self) -> Any:
        """A new connection, configured and ready to use."""

    @abstractmethod
    def translate(self, sql: str) -> str:
        """Rewrite one statement into this backend's dialect."""

    @abstractmethod
    def translate_schema(self, sql: str) -> str:
        """Rewrite ``schema.sql`` into this backend's dialect."""

    @abstractmethod
    def schema_version(self, connection: Any) -> int:
        """The stored schema version (0 for a database that has none)."""

    @abstractmethod
    def set_schema_version(self, connection: Any, version: int) -> None:
        ...

    def with_returning_id(self, sql: str) -> str:
        """The statement rewritten to hand back the new row's id.

        Translation has to happen first: ``ON CONFLICT DO NOTHING`` is added
        by the translation and must come before ``RETURNING``.
        """
        return sql

    def describe(self) -> str:
        return self.name


class SQLiteBackend(DatabaseBackend):
    """The default. One file, zero setup, and the dialect everything is written in."""

    name = SQLITE

    def __init__(self, path: str) -> None:
        self.path = path

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=15, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = NORMAL")
        connection.execute("PRAGMA busy_timeout = 8000")
        return connection

    def translate(self, sql: str) -> str:
        return sql

    def translate_schema(self, sql: str) -> str:
        return sql

    def schema_version(self, connection: Any) -> int:
        return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def set_schema_version(self, connection: Any, version: int) -> None:
        connection.execute(f"PRAGMA user_version = {int(version)}")

    def describe(self) -> str:
        return f"sqlite ({self.path})"


# ----------------------------------------------------------- PostgreSQL ----
#: ``?`` placeholders, outside string literals, become ``%s``.
_LITERAL_RE = re.compile(r"'(?:[^']|'')*'")

#: SQLite's LIKE is case-insensitive for ASCII; PostgreSQL's is not. ILIKE
#: keeps the behaviour the queries were written against - a feed search for
#: "Jones" must still match "jones".
_LIKE_RE = re.compile(r"\bLIKE\b", re.IGNORECASE)

_INSERT_OR_IGNORE_RE = re.compile(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", re.IGNORECASE)
_INSERT_OR_REPLACE_RE = re.compile(r"\bINSERT\s+OR\s+REPLACE\s+INTO\b", re.IGNORECASE)

_SCHEMA_TYPE_RULES = [
    # SQLite's rowid alias becomes a real sequence-backed column.
    (re.compile(r"\bINTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT\b", re.IGNORECASE),
     "BIGSERIAL PRIMARY KEY"),
    # SQLite REAL is a 64-bit float; PostgreSQL REAL is 32-bit.
    (re.compile(r"\bREAL\b", re.IGNORECASE), "DOUBLE PRECISION"),
]


class _PgCursorWrapper:
    """A cursor that also answers ``fetchone``/``fetchall`` like sqlite3."""

    def __init__(self, cursor: Any) -> None:
        self._cursor = cursor

    def __getattr__(self, item: str) -> Any:
        return getattr(self._cursor, item)

    def __iter__(self):
        return iter(self._cursor)


class _PgConnection:
    """A psycopg2 connection that behaves like ``sqlite3.Connection``.

    The repositories call ``connection.execute(sql, params)`` directly, which
    psycopg2 does not provide. Wrapping it here keeps every repository free of
    backend-specific code.
    """

    def __init__(self, raw: Any, backend: "PostgresBackend") -> None:
        self._raw = raw
        self._backend = backend

    def execute(self, sql: str, params: Sequence[Any] | Dict[str, Any] = ()) -> Any:
        cursor = self._raw.cursor()
        cursor.execute(self._backend.translate(sql), tuple(params) if params else None)
        return _PgCursorWrapper(cursor)

    def executescript(self, sql: str) -> None:
        cursor = self._raw.cursor()
        cursor.execute(sql)
        cursor.close()

    def cursor(self) -> Any:
        return self._raw.cursor()

    def commit(self) -> None:
        self._raw.commit()

    def rollback(self) -> None:
        self._raw.rollback()

    def close(self) -> None:
        self._raw.close()


class PostgresBackend(DatabaseBackend):
    """A managed PostgreSQL database - the production option.

    Everything the schema needs is standard SQL; the differences handled here
    are SQLite spellings (``INSERT OR IGNORE``, ``AUTOINCREMENT``, ``?``
    placeholders, case-insensitive ``LIKE``) rather than semantics.
    """

    name = POSTGRES
    needs_returning = True

    def __init__(self, url: str) -> None:
        self.url = url

    @staticmethod
    def driver():
        try:
            import psycopg2  # noqa: F401
            import psycopg2.extras  # noqa: F401
        except ImportError as exc:  # pragma: no cover - depends on the install
            raise DriverMissingError(
                "The configuration points at PostgreSQL but the psycopg2 driver is "
                "not installed. Install it with `pip install psycopg2-binary` (it is "
                "in requirements.txt), or remove DATABASE_URL / the [database] "
                "secrets section to use the local SQLite file. The app will not "
                "quietly write somewhere other than the database you configured."
            ) from exc
        import psycopg2

        return psycopg2

    def connect(self) -> _PgConnection:
        psycopg2 = self.driver()
        import psycopg2.extras

        raw = psycopg2.connect(self.url, cursor_factory=psycopg2.extras.DictCursor)
        raw.autocommit = False
        return _PgConnection(raw, self)

    # ------------------------------------------------------- translation --
    def translate(self, sql: str) -> str:
        statement = _INSERT_OR_REPLACE_RE.sub("INSERT INTO", sql)
        had_or_ignore = bool(_INSERT_OR_IGNORE_RE.search(statement))
        statement = _INSERT_OR_IGNORE_RE.sub("INSERT INTO", statement)
        statement = _translate_outside_literals(statement, self._translate_fragment)
        if had_or_ignore and "ON CONFLICT" not in statement.upper():
            statement = statement.rstrip().rstrip(";") + " ON CONFLICT DO NOTHING"
        return statement

    @staticmethod
    def _translate_fragment(fragment: str) -> str:
        fragment = fragment.replace("?", "%s")
        return _LIKE_RE.sub("ILIKE", fragment)

    def translate_schema(self, sql: str) -> str:
        for pattern, replacement in _SCHEMA_TYPE_RULES:
            sql = pattern.sub(replacement, sql)
        return sql

    # ----------------------------------------------------- schema version --
    def schema_version(self, connection: Any) -> int:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT)")
        row = connection.execute(
            "SELECT value FROM schema_meta WHERE key = 'user_version'").fetchone()
        try:
            return int(row[0]) if row else 0
        except (TypeError, ValueError):
            return 0

    def set_schema_version(self, connection: Any, version: int) -> None:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_meta (key TEXT PRIMARY KEY, value TEXT)")
        connection.execute(
            "INSERT INTO schema_meta (key, value) VALUES ('user_version', %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (str(int(version)),))

    def with_returning_id(self, sql: str) -> str:
        return self.translate(sql).rstrip().rstrip(";") + " RETURNING id"

    def describe(self) -> str:
        return f"postgresql ({_safe_url(self.url)})"


def _translate_outside_literals(sql: str, translate) -> str:
    """Apply ``translate`` to everything that is not inside a quoted literal."""
    output: List[str] = []
    position = 0
    for match in _LITERAL_RE.finditer(sql):
        output.append(translate(sql[position:match.start()]))
        output.append(match.group(0))
        position = match.end()
    output.append(translate(sql[position:]))
    return "".join(output)


# ------------------------------------------------------------- selection ---
_lock = threading.Lock()


def postgres_url() -> Optional[str]:
    """A configured PostgreSQL URL, if the operator set one.

    Either from ``DATABASE_URL`` or assembled from the ``[database]`` secrets
    section; ``database/db_config.py`` decides which and validates it.
    """
    return database_url()


def build_backend(sqlite_path: str, url: Optional[str] = None) -> DatabaseBackend:
    """The backend this process should use.

    A configured PostgreSQL URL always wins, and a missing driver raises
    rather than falling back to SQLite. Pass ``url`` to override the
    configuration (tests do); leave it out to use what was configured.
    """
    url = url if url is not None else postgres_url()
    if url:
        backend = PostgresBackend(url)
        backend.driver()  # fail now, with an explanation, not on the first query
        return backend
    return SQLiteBackend(sqlite_path)
