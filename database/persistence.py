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
3. **PostgreSQL.** The schema was written to port cleanly - ISO-8601 UTC text
   timestamps, no SQLite-only column types, JSON held as TEXT. ``DATABASE_URL``
   is recognised and reported here, but the Postgres driver is *not*
   implemented, and this module says so rather than half-working.
"""
from __future__ import annotations

import os
import shutil
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from database.db import close_connection, current_db_path, get_connection
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

    @property
    def size_mb(self) -> float:
        return round(self.size_bytes / (1024 * 1024), 2)


def postgres_url() -> Optional[str]:
    """A configured PostgreSQL URL, if the operator set one."""
    for name in ("UFC_RADAR_DATABASE_URL", "DATABASE_URL"):
        value = os.getenv(name)
        if value and value.strip().startswith(("postgres://", "postgresql://")):
            return value.strip()
    return None


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
        return StorageReport(
            path=path, exists=exists, size_bytes=size, is_ephemeral=False,
            is_cloud=running_on_cloud(), backend="postgresql (not implemented)",
            headline="⚠️ PostgreSQL URL set, but this build stores data in SQLite",
            detail=(
                "A PostgreSQL connection string was found in the environment. This version "
                "does not include a PostgreSQL driver, so the app is still reading and writing "
                "the SQLite file below. Nothing has been sent to PostgreSQL."
            ),
            advice=["Remove the variable to avoid confusion, or keep it for a future version."],
        )

    ephemeral = looks_ephemeral(path)
    if ephemeral:
        return StorageReport(
            path=path, exists=exists, size_bytes=size, is_ephemeral=True,
            is_cloud=running_on_cloud(), backend="sqlite (temporary filesystem)",
            headline="⚠️ This storage is temporary - collected history will be lost on restart",
            detail=(
                "The database is on a filesystem that gets wiped when the app restarts or "
                "redeploys. The app works normally, but the news it collects will not "
                "accumulate over time."
            ),
            advice=[
                "Download a backup below before any redeploy, and restore it afterwards.",
                "For permanent history, run the app on your own PC, or point UFC_RADAR_DB at "
                "a disk that survives restarts.",
            ],
        )

    return StorageReport(
        path=path, exists=exists, size_bytes=size, is_ephemeral=False,
        is_cloud=running_on_cloud(), backend="sqlite (persistent file)",
        headline="✅ Storage is persistent",
        detail="The database is an ordinary file that survives restarts. History accumulates.",
        advice=["Take an occasional backup below; it is a single file you can copy anywhere."],
    )


def export_database(destination: str) -> str:
    """Write a consistent copy of the database to ``destination``.

    Uses SQLite's backup API, which is safe to run while the app is reading and
    writing - a plain file copy can capture a torn page.
    """
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
