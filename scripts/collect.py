"""Run a collection from the command line (no browser needed).

    python scripts/collect.py              # one run
    python scripts/collect.py --loop 20    # keep running every 20 minutes
    python scripts/collect.py --source ufc_com espn_mma
    python scripts/collect.py --demo       # load the clearly-marked demo data
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from collectors.runner import run_collection          # noqa: E402
from database.db import init_db                       # noqa: E402
from database.demo_data import clear_demo_data, load_demo_data  # noqa: E402
from utils.logging_setup import configure_logging     # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect UFC news into the local database.")
    parser.add_argument("--loop", type=int, metavar="MINUTES",
                        help="keep collecting every N minutes until interrupted")
    parser.add_argument("--source", nargs="*", help="only these source keys")
    parser.add_argument("--no-social", action="store_true", help="skip the X API step")
    parser.add_argument("--no-text", action="store_true", help="skip article full-text extraction")
    parser.add_argument("--demo", action="store_true", help="load demo data and exit")
    parser.add_argument("--clear-demo", action="store_true", help="remove demo data and exit")
    args = parser.parse_args()

    configure_logging()
    init_db()

    if args.demo:
        print("Loaded demo data:", load_demo_data())
        return 0
    if args.clear_demo:
        print("Removed demo data:", clear_demo_data())
        return 0

    while True:
        result = run_collection(
            trigger="cli",
            source_keys=args.source,
            collect_social=not args.no_social,
            fetch_full_text=None if not args.no_text else False,
        )
        print(result.summary_line)
        for outcome in result.outcomes:
            mark = "OK " if outcome.ok else "ERR"
            detail = f"{outcome.items} items" if outcome.ok else (outcome.error or "")
            print(f"  {mark} {outcome.name}: {detail}")
        if result.rankings:
            print(f"  rankings: {result.rankings.rows_inserted} rows, "
                  f"{result.rankings.changes_detected} change(s), "
                  f"system: {result.rankings.system_name}")
        if not args.loop:
            return 0
        print(f"\nSleeping {args.loop} minute(s). Press Ctrl+C to stop.\n")
        try:
            time.sleep(args.loop * 60)
        except KeyboardInterrupt:
            print("Stopped.")
            return 0


if __name__ == "__main__":
    raise SystemExit(main())
