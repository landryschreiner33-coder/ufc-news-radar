"""Where the database lives, and how to keep its history.

Streamlit Community Cloud gives an app a **temporary** filesystem: it is wiped
on every redeploy and whenever the container restarts. A SQLite file written
there is real working storage, but it is not permanent history.

Rather than pretend otherwise, this module:

* reports exactly where the database is and whether that location looks
  temporary, so the app can say so on screen instead of quietly losing months
  of collected news;
* provides a tested backup/restore path (a consistent copy via SQLite's own
  backup API - safe to run while the app is using the database);
* documents the permanent options in one place.

Permanent history, simplest first:

1. **Run it on your own PC** (the default). ``data/ufc_news_radar.db`` sits in
   the project folder and persists like any other file. Nothing to set up.
2. **A mounted persistent disk.** Point ``UFC_RADAR_DB`` at a path on a volume
   that survives restarts (a VPS disk, a container volume).
3. **PostgreSQL.** Point the app at a managed PostgreSQL instance, either
   with ``DATABASE_URL`` or with a ``[database]`` secrets section
   (``database/db_config.py``). This is implemented (``database/backends.py``)
   and the whole test suite runs against it, not just SQLite. It is the
   option for Streamlit Community Cloud, whose filesystem is wiped on every
   restart.

If PostgreSQL is configured but the driver is missing, start-up fails rather
than quietly writing to a local file the operator was not expecting.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from database import db_config
from database.backends import postgres_url
from database.db import backend_name, close_connection, current_db_path, get_connection
from utils.logging_setup import get_logger
from utils.timeutil import utcnow_iso


logger = get_logger(__name__)

#: Path prefixes that are wiped when a container restarts.
_EPHEMERAL_PREFIXES = ("/tmp", "/var/tmp", "/mount/src", "/app", "/home/appuser", "/workspace")

#: Environment giveaways that we are on Streamlit Community Cloud.
_CLOUD_MARKERS = ("STREAMLIT_SHARING_MODE", "STREAMLIT_SERVER_PORT", "STREAMLIT_RUNTIME_ENV")


@dataclass
class StorageReport:
    path: str
    exists: bool
    size_bytes: int
    is_ephemeral: bool
    is_cloud: bool
    backend: str
    headline: str
    detail: str
    advice: List[str]
    #: A description safe to put on screen. The real path is server-side
    #: information the reader cannot act on and does not need.
    location: str = "Local file"

    @property
    def size_mb(self) -> float:
        return round(self.size_bytes / (1024 * 1024), 2)


def describe_location(path: str) -> str:
    """Where the data lives, without printing a server filesystem path."""
    configured = db_config.resolve()
    if configured.is_postgres:
        # The source, not the URL: the reader needs to know which setting is
        # in charge, and the URL carries a password.
        return f"Managed PostgreSQL database (configured by {configured.source})"
    resolved = Path(path)
    try:
        inside_project = resolved.resolve().is_relative_to(Path.cwd())
    except (OSError, ValueError):
        inside_project = False
    if path == ":memory:":
        return "In memory (nothing is kept)"
    if inside_project:
        return "SQLite file in the project's data folder"
    return "SQLite file at a custom location (set by UFC_RADAR_DB)"


def looks_ephemeral(path: str) -> bool:
    """True when this location is wiped on restart/redeploy."""
    resolved = str(Path(path).resolve())
    if any(resolved.startswith(prefix) for prefix in _EPHEMERAL_PREFIXES):
        return True
    return running_on_cloud()


def running_on_cloud() -> bool:
    return any(os.getenv(marker) for marker in _CLOUD_MARKERS) and not os.getenv("UFC_RADAR_LOCAL")


def storage_report() -> StorageReport:
    """Describe the current storage honestly, for the Settings page."""
    path = current_db_path()
    file = Path(path)
    exists = file.exists()
    size = file.stat().st_size if exists else 0
    url = postgres_url()

    if url:
        # The PostgreSQL backend is implemented and tested, so this is a
        # statement of fact rather than a hopeful label.
        return StorageReport(
            path=path, exists=True, size_bytes=0, is_ephemeral=False,
            is_cloud=running_on_cloud(), backend="postgresql",
            location=describe_location(path),
            headline="✅ Storage is a managed PostgreSQL database",
            detail=(
                "Collected history lives in PostgreSQL and survives restarts and redeploys. "
                "This is the option to use on Streamlit Community Cloud, whose own "
                "filesystem is temporary."
            ),
            advice=["Back-ups are your database provider's; the export below covers SQLite only."],
        )

    ephemeral = looks_ephemeral(path)
    if ephemeral:
        return StorageReport(
            path=path, exists=exists, size_bytes=size, is_ephemeral=True,
            is_cloud=running_on_cloud(), backend="sqlite (temporary filesystem)",
            location=describe_location(path),
            headline="⚠️ This storage is temporary - collected history will be lost on restart",
            detail=(
                "The database is on a filesystem that gets wiped when the app restarts or "
                "redeploys. The app works normally, but the news it collects will not "
                "accumulate over time."
            ),
            advice=[
                "Point the app at a managed PostgreSQL database for permanent history: "
                "DATABASE_URL, or a [database] section in the Secrets box with host, "
                "port, database, username and password. Nothing else needs changing.",
                "Or download a backup below before any redeploy, and restore it afterwards.",
                "Or run the app on your own PC, where the file persists like any other.",
            ],
        )

    return StorageReport(
        path=path, exists=exists, size_bytes=size, is_ephemeral=False,
        is_cloud=running_on_cloud(), backend="sqlite (persistent file)",
        location=describe_location(path),
        headline="✅ Storage is persistent",
        detail="The database is an ordinary file that survives restarts. History accumulates.",
        advice=["Take an occasional backup below; it is a single file you can copy anywhere."],
    )


class BackupUnsupportedError(RuntimeError):
    """Backup/restore here covers SQLite only."""


def backup_supported() -> bool:
    """Whether the file backup/restore path applies to the current backend.

    On PostgreSQL the database is not a file this app owns, and backups are
    the provider's job (``pg_dump``, managed snapshots). Offering a one-click
    "backup" that silently did nothing would be worse than not offering one.
    """
    return backend_name() != "postgresql"


def _require_sqlite() -> None:
    if not backup_supported():
        raise BackupUnsupportedError(
            "This database is PostgreSQL. Use your provider's backups or pg_dump - "
            "the file export here only applies to SQLite."
        )


def export_database(destination: str) -> str:
    """Write a consistent copy of the database to ``destination``.

    Uses SQLite's backup API, which is safe to run while the app is reading and
    writing - a plain file copy can capture a torn page.
    """
    _require_sqlite()
    target = Path(destination)
    target.parent.mkdir(parents=True, exist_ok=True)
    source = get_connection()
    backup = sqlite3.connect(str(target))
    try:
        source.backup(backup)
        backup.commit()
    finally:
        backup.close()
    logger.info("Database exported to %s", target)
    return str(target)


def export_bytes() -> bytes:
    """The whole database as bytes, for a download button."""
    import tempfile

    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "ufc_news_radar_backup.db"
        export_database(str(path))
        return path.read_bytes()


def validate_backup(path: str) -> Dict[str, Any]:
    """Check a file really is one of our databases before restoring it."""
    result: Dict[str, Any] = {"ok": False, "error": None, "tables": 0, "articles": 0, "stories": 0}
    try:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        result["error"] = f"Not a readable SQLite file: {exc}"
        return result
    try:
        names = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        required = {"articles", "stories", "sources", "events", "settings"}
        missing = required - names
        if missing:
            result["error"] = ("This file does not look like a UFC News Radar backup "
                               f"(missing: {', '.join(sorted(missing))}).")
            return result
        result["tables"] = len(names)
        result["articles"] = connection.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
        result["stories"] = connection.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
        result["ok"] = True
        return result
    except sqlite3.Error as exc:
        result["error"] = f"Could not read the backup: {exc}"
        return result
    finally:
        connection.close()


def restore_database(source_path: str, keep_previous: bool = True) -> Dict[str, Any]:
    """Replace the live database with a validated backup.

    The current database is renamed aside first (never deleted), so a restore
    that turns out to be the wrong file can be undone by hand.
    """
    if not backup_supported():
        return {"ok": False,
                "error": "This database is PostgreSQL; restore it with your provider's tools."}
    check = validate_backup(source_path)

    if not check["ok"]:
        return {"ok": False, "error": check["error"]}

    live = Path(current_db_path())
    close_connection()
    previous: Optional[str] = None
    if keep_previous and live.exists():
        previous = str(live.with_suffix(f".replaced-{utcnow_iso().replace(':', '')}.db"))
        shutil.move(str(live), previous)
    live.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source_path, str(live))
    # Drop the stale write-ahead files belonging to the replaced database.
    for suffix in ("-wal", "-shm"):
        stale = Path(str(live) + suffix)
        if stale.exists():
            stale.unlink()

    from database.db import init_db

    init_db()
    logger.info("Database restored from %s (previous kept at %s)", source_path, previous)
    return {"ok": True, "articles": check["articles"], "stories": check["stories"],
            "previous": previous}
