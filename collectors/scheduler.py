"""Background collection: the collector is not the dashboard's job.

The dashboard *reads* results. Something else has to produce them, because a
Streamlit page only runs while somebody is looking at it, and a collection
takes a minute or two - long enough that the person who pressed the button
has usually moved on.

Two ways to run it, in order of preference:

1. **A separate process** - ``python scripts/collect.py --loop 20`` or
   ``python scripts/scheduler.py``. This is the production answer: the
   collector keeps running whether or not anyone has the app open, and the
   dashboard simply reads what it wrote. Point both at the same database
   (``UFC_RADAR_DB``, or ``DATABASE_URL`` for PostgreSQL).

2. **A thread inside the app** - what this module provides, enabled by the
   ``auto_collect_enabled`` setting. It is genuinely useful on a host where
   you cannot run a second process, and it is honest about its limit: it only
   collects while the app process is alive. It is not a substitute for (1).

Whichever runs, only one collection happens at a time. The lock lives in the
database rather than in memory, so two app processes - or an app and a cron
job - cannot collect over each other.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

from database import repo_settings as settings_repo
from utils.logging_setup import get_logger
from utils.timeutil import minutes_between, parse_iso, utcnow_iso

logger = get_logger(__name__)

#: Setting keys. Collection is opt-in; nothing starts fetching on its own.
ENABLED_SETTING = "auto_collect_enabled"
INTERVAL_SETTING = "auto_collect_interval_minutes"
LOCK_SETTING = "collection_lock_until"

#: Collecting more often than this is rude to the sources and pointless: a
#: news feed does not change every two minutes.
MIN_INTERVAL_MINUTES = 15
DEFAULT_INTERVAL_MINUTES = 20

#: How long a held lock is trusted before it is assumed to be a crashed run.
LOCK_MINUTES = 30

_thread: Optional[threading.Thread] = None
_stop = threading.Event()
_lock = threading.Lock()


@dataclass
class SchedulerStatus:
    enabled: bool
    interval_minutes: int
    running: bool
    locked_by_another_process: bool
    minutes_since_last_collection: Optional[float]

    @property
    def summary(self) -> str:
        if not self.enabled:
            return ("Background collection is off. Run `python scripts/collect.py --loop 20` "
                    "for a collector that keeps working when the app is closed.")
        state = "running" if self.running else "not started in this process"
        return (f"Background collection every {self.interval_minutes} minutes ({state}). "
                "It only runs while this app process is alive.")


def interval_minutes() -> int:
    """The configured interval, never below the floor."""
    return max(MIN_INTERVAL_MINUTES,
               settings_repo.get_int(INTERVAL_SETTING, DEFAULT_INTERVAL_MINUTES))


def is_enabled() -> bool:
    return settings_repo.get_bool(ENABLED_SETTING, False)


# ------------------------------------------------------------------ lock --
def acquire_lock(minutes: int = LOCK_MINUTES) -> bool:
    """Claim the right to collect. False when someone else already has it.

    Stored in the database on purpose: a lock in memory only stops one
    process from collecting twice, which is not the case that matters when a
    cron job and an open dashboard share a database.
    """
    held_until = settings_repo.get_setting(LOCK_SETTING, "")
    if held_until:
        remaining = minutes_between(utcnow_iso(), held_until)
        if remaining is not None and remaining > 0:
            return False
    from datetime import timedelta

    from utils.timeutil import to_iso, utcnow

    settings_repo.set_setting(LOCK_SETTING, to_iso(utcnow() + timedelta(minutes=minutes)), "str")
    return True


def release_lock() -> None:
    settings_repo.set_setting(LOCK_SETTING, "", "str")


def lock_is_held() -> bool:
    held_until = settings_repo.get_setting(LOCK_SETTING, "")
    if not held_until:
        return False
    remaining = minutes_between(utcnow_iso(), held_until)
    return remaining is not None and remaining > 0


# ------------------------------------------------------------- one cycle --
def collect_once(trigger: str = "scheduled") -> Optional[object]:
    """Run one collection if nothing else is collecting. Never raises."""
    from collectors.runner import run_collection

    if not acquire_lock():
        logger.info("Skipping scheduled collection: another process holds the lock")
        return None
    try:
        return run_collection(trigger=trigger)
    except Exception:  # a background run must never take the app down
        logger.exception("Scheduled collection failed")
        return None
    finally:
        release_lock()


def due(now: Optional[str] = None) -> bool:
    """Whether enough time has passed since the last successful collection."""
    last = settings_repo.get_setting("last_collection_attempt_at", "") or \
        settings_repo.get_setting("last_collection_at", "")
    if not last:
        return True
    elapsed = minutes_between(last, now or utcnow_iso())
    return elapsed is None or elapsed >= interval_minutes()


# ----------------------------------------------------------------- thread --
def _loop(database_path: Optional[str], database_url: Optional[str]) -> None:
    """The background thread body.

    The thread gets its own database connection (connections are per-thread),
    so it is pointed at the same database the app is using.
    """
    from database.db import set_database_path, set_database_url

    if database_url:
        set_database_url(database_url)
    elif database_path:
        set_database_path(database_path)
    logger.info("Background collector started (every %s minutes)", interval_minutes())
    while not _stop.is_set():
        try:
            if is_enabled() and due():
                collect_once()
        except Exception:  # pragma: no cover - the thread must stay alive
            logger.exception("Background collector cycle failed")
        # Wake often enough to notice the setting being switched off.
        _stop.wait(60)
    logger.info("Background collector stopped")


def start(database_path: Optional[str] = None, database_url: Optional[str] = None) -> bool:
    """Start the background collector once per process. Returns whether it runs."""
    global _thread
    with _lock:
        if _thread is not None and _thread.is_alive():
            return True
        if not is_enabled():
            return False
        _stop.clear()
        _thread = threading.Thread(
            target=_loop, args=(database_path, database_url),
            name="ufc-radar-collector", daemon=True)
        _thread.start()
        return True


def stop() -> None:
    _stop.set()


def status() -> SchedulerStatus:
    last = settings_repo.get_setting("last_collection_at", "")
    return SchedulerStatus(
        enabled=is_enabled(),
        interval_minutes=interval_minutes(),
        running=bool(_thread is not None and _thread.is_alive()),
        locked_by_another_process=lock_is_held(),
        minutes_since_last_collection=minutes_between(last, utcnow_iso()) if last else None,
    )
