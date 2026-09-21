#!/usr/bin/env python
"""Run the collector as its own process - the production answer.

    python scripts/scheduler.py                  # every 20 minutes
    python scripts/scheduler.py --minutes 30
    python scripts/scheduler.py --once           # one cycle, then exit (cron)

The dashboard only reads what this writes, so it can be closed, restarted or
redeployed without losing a collection. Point this and the app at the same
database:

    export UFC_RADAR_DB=/var/lib/ufc-radar/ufc_news_radar.db   # SQLite
    export DATABASE_URL=postgresql://user:pass@host/db          # PostgreSQL

Only one collection runs at a time, wherever it was started from: the lock is
held in the database (see collectors/scheduler.py).

Running it for real
-------------------
* **Windows**: Task Scheduler, "Start a program",
  ``<project>\\.venv\\Scripts\\python.exe scripts\\scheduler.py --once``,
  repeating every 20 minutes.
* **Linux/macOS**: a cron entry - ``*/20 * * * * cd /path && .venv/bin/python
  scripts/scheduler.py --once`` - or a systemd service running without
  ``--once``.
* **A container**: run this as a second process/service beside the web app.
"""
from __future__ import annotations

import argparse
import signal
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from collectors import scheduler                      # noqa: E402
from database.db import init_db                       # noqa: E402
from utils.logging_setup import configure_logging, get_logger  # noqa: E402

logger = get_logger("scheduler")
_running = True


def _stop(_signum, _frame) -> None:
    global _running
    _running = False
    logger.info("Stopping after the current cycle")


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect UFC news on a schedule.")
    parser.add_argument("--minutes", type=int, default=scheduler.DEFAULT_INTERVAL_MINUTES,
                        help=f"minutes between collections "
                             f"(minimum {scheduler.MIN_INTERVAL_MINUTES})")
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    args = parser.parse_args()

    interval = max(scheduler.MIN_INTERVAL_MINUTES, args.minutes)
    configure_logging()
    init_db()
    signal.signal(signal.SIGINT, _stop)
    signal.signal(signal.SIGTERM, _stop)

    while _running:
        result = scheduler.collect_once(trigger="scheduled")
        if result is None:
            print("Skipped: another collection is already running.")
        else:
            print(result.headline)
            print("  " + result.summary_line)
        if args.once:
            return 0
        print(f"Next run in {interval} minute(s). Ctrl+C to stop.")
        for _ in range(interval * 60):
            if not _running:
                break
            time.sleep(1)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
