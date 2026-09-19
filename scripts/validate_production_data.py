#!/usr/bin/env python
"""Check the database for the data faults that produce wrong reporting.

    python scripts/validate_production_data.py
    python scripts/validate_production_data.py --check-urls   # also test links
    python scripts/validate_production_data.py --json         # machine readable

Written after an audit found two faults that every unit test had missed,
because the tests only ever saw freshly built fixtures: one real event stored
as several rows, and events presented as finished purely because their date had
passed. Both are checked here, against the data that actually exists.

Exit codes:  0 = clean or warnings only,  1 = errors found.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from database.db import init_db, query_all, query_one  # noqa: E402
from models.types import ArticleIntent, EventStatus, RESULT_FORBIDDEN_INTENTS  # noqa: E402
from processors.event_identity import canonical_key  # noqa: E402
from utils.textutil import normalize_text  # noqa: E402
from utils.timeutil import (  # noqa: E402
    EARLIEST_PLAUSIBLE,
    FUTURE_TOLERANCE_MINUTES,
    is_future,
    parse_iso,
    utcnow,
)

ERROR = "ERROR"
WARN = "WARN"


@dataclass
class Finding:
    level: str
    check: str
    message: str
    rows: List[str] = field(default_factory=list)
    fix: str = ""

    def as_dict(self) -> Dict[str, Any]:
        return {"level": self.level, "check": self.check, "message": self.message,
                "rows": self.rows[:25], "fix": self.fix}


# --------------------------------------------------------------- checks ----
def check_duplicate_events() -> List[Finding]:
    rows = query_all("SELECT id, name, canonical_key, ufc_url, official_event_id, event_date "
                     "FROM events WHERE merged_into_id IS NULL")
    groups: Dict[str, List[Any]] = defaultdict(list)
    for row in rows:
        groups[canonical_key(row["name"], row["ufc_url"], row["official_event_id"],
                             row["event_date"])].append(row)
    findings = []
    for key, members in groups.items():
        if len(members) > 1:
            findings.append(Finding(
                ERROR, "duplicate_events",
                f"{len(members)} rows describe one event ({key}).",
                [f"#{row['id']} {row['name']}" for row in members],
                "Restart the app - the identity backfill merges these automatically, "
                "or run database.event_migration.backfill().",
            ))
    return findings


def check_duplicate_fighters() -> List[Finding]:
    rows = query_all("SELECT id, name, normalized_name FROM fighters")
    groups: Dict[str, List[Any]] = defaultdict(list)
    for row in rows:
        groups[normalize_text(row["name"])].append(row)
    return [
        Finding(ERROR, "duplicate_fighters",
                f"{len(members)} fighter rows normalise to '{key}'.",
                [f"#{row['id']} {row['name']}" for row in members],
                "Merge them from Settings -> Data corrections.")
        for key, members in groups.items() if len(members) > 1 and key
    ]


def check_event_status_sanity() -> List[Finding]:
    """The two failure modes the lifecycle exists to prevent."""
    findings: List[Finding] = []
    now = utcnow()
    rows = query_all("SELECT id, name, event_status, event_date, scheduled_start_utc, "
                     "status_confidence, status_source FROM events WHERE merged_into_id IS NULL")

    future_completed = []
    for row in rows:
        start = parse_iso(row["scheduled_start_utc"]) or parse_iso(row["event_date"])
        if row["event_status"] == EventStatus.COMPLETED.value and start and start > now:
            future_completed.append(f"#{row['id']} {row['name']} (starts {start:%Y-%m-%d})")
    if future_completed:
        findings.append(Finding(
            ERROR, "future_event_completed",
            "Events are marked COMPLETED although they have not started yet.",
            future_completed,
            "A result must never come from a preview/prediction. Check processors/"
            "result_safety.py and re-run collection.",
        ))

    stale_upcoming = []
    for row in rows:
        start = parse_iso(row["scheduled_start_utc"])
        if (row["event_status"] == EventStatus.UPCOMING.value and start
                and (now - start).days > 2):
            stale_upcoming.append(f"#{row['id']} {row['name']} (started {start:%Y-%m-%d})")
    if stale_upcoming:
        findings.append(Finding(
            WARN, "stale_upcoming_event",
            "Events still marked UPCOMING more than two days after their start time.",
            stale_upcoming,
            "Run a collection so the lifecycle recomputes; they should become LIVE, "
            "COMPLETED (with evidence) or UNKNOWN.",
        ))

    no_schedule = [f"#{row['id']} {row['name']}" for row in rows
                   if not row["scheduled_start_utc"] and row["event_status"] in (
                       EventStatus.LIVE.value, EventStatus.COMPLETED.value)]
    if no_schedule:
        findings.append(Finding(
            WARN, "status_without_schedule",
            "Events claim LIVE/COMPLETED without a collected start time.",
            no_schedule, "Verify against the official UFC event page."))
    return findings


def check_results_from_predictions() -> List[Finding]:
    """Any article that is a preview/prediction yet sits in a result story."""
    placeholders = ",".join("?" * len(RESULT_FORBIDDEN_INTENTS))
    rows = query_all(
        f"SELECT a.id, a.title, a.intent, a.source_name FROM articles a "
        f"WHERE a.intent IN ({placeholders}) AND a.category = 'result'",
        tuple(RESULT_FORBIDDEN_INTENTS))
    if not rows:
        return []
    return [Finding(
        ERROR, "prediction_as_result",
        "Preview/prediction articles are categorised as fight results.",
        [f"#{row['id']} [{row['intent']}] {row['title']}" for row in rows],
        "processors/result_safety.py should have downgraded these - re-run enrichment.",
    )]


def check_stories_without_sources() -> List[Finding]:
    rows = query_all(
        "SELECT s.id, s.headline FROM stories s "
        "LEFT JOIN story_sources ss ON ss.story_id = s.id "
        "WHERE ss.id IS NULL AND s.is_demo = 0")
    if not rows:
        return []
    return [Finding(ERROR, "story_without_sources",
                    "Stories exist with no linked source article.",
                    [f"#{row['id']} {row['headline']}" for row in rows],
                    "Every story must be traceable to evidence. Re-run clustering.")]


def check_duplicate_stories() -> List[Finding]:
    rows = query_all("SELECT id, headline FROM stories WHERE is_demo = 0")
    counts: Dict[str, List[Any]] = defaultdict(list)
    for row in rows:
        counts[normalize_text(row["headline"])[:90]].append(row)
    return [
        Finding(WARN, "duplicate_stories",
                f"{len(members)} stories share a near-identical headline.",
                [f"#{row['id']} {row['headline']}" for row in members],
                "They may be one story the clustering gates kept apart. Review on the dashboard.")
        for key, members in counts.items() if len(members) > 1 and key
    ]


def check_stale_breaking() -> List[Finding]:
    rows = query_all("SELECT id, headline, last_updated_at FROM stories WHERE is_breaking = 1")
    now = utcnow()
    stale = []
    for row in rows:
        updated = parse_iso(row["last_updated_at"])
        if updated and (now - updated).total_seconds() > 48 * 3600:
            stale.append(f"#{row['id']} {row['headline']} (updated {updated:%Y-%m-%d})")
    if not stale:
        return []
    return [Finding(WARN, "stale_breaking",
                    "Stories are still flagged BREAKING after 48 hours.",
                    stale, "Re-run processing; the breaking flag is time-boxed.")]


def check_suspicious_timestamps() -> List[Finding]:
    findings: List[Finding] = []
    now = utcnow()
    rows = query_all("SELECT id, title, published_at, collected_at FROM articles")
    impossible, future = [], []
    for row in rows:
        if not row["published_at"]:
            continue
        parsed = parse_iso(row["published_at"])
        if parsed is None or parsed < EARLIEST_PLAUSIBLE:
            impossible.append(f"#{row['id']} {row['published_at']} - {row['title'][:60]}")
        elif is_future(row["published_at"], FUTURE_TOLERANCE_MINUTES, now):
            # An article cannot have been published later than now. Anything
            # beyond a little clock skew is a broken feed date.
            future.append(f"#{row['id']} {row['published_at']} - {row['title'][:60]}")
    if impossible:
        findings.append(Finding(
            WARN, "impossible_dates",
            f"Articles are dated before the sport existed ({EARLIEST_PLAUSIBLE:%Y}).",
            impossible, "utils.timeutil.sanitize_published_at falls these back to collected_at."))
    if future:
        findings.append(Finding(
            WARN, "future_dated_articles",
            "Articles are dated in the future, which would pin them to the top of the feed.",
            future, "Same fix: sanitize_published_at on ingest."))
    return findings


def check_rankings() -> List[Finding]:
    findings: List[Finding] = []
    # "Current" means the newest snapshot, which is what the app displays.
    from database import repo_rankings as rankings_repo

    rows = []
    for division in rankings_repo.divisions():
        rows.extend(rankings_repo.current_rankings(division))
    if not rows:
        return [Finding(WARN, "no_rankings", "No current rankings collected.", [],
                        "Run a collection; UFC.com rankings is a built-in source.")]
    by_division: Dict[str, List[Any]] = defaultdict(list)
    for row in rows:
        by_division[row["division"]].append(row)
    for division, entries in by_division.items():
        champions = [entry for entry in entries if entry["is_champion"]]
        if len(champions) > 1:
            findings.append(Finding(
                ERROR, "multiple_champions",
                f"{division} lists {len(champions)} champions.",
                [entry["fighter_name"] for entry in champions],
                "Check the rankings parser against the live UFC page."))
        positions = [entry["position"] for entry in entries if entry["position"]]
        duplicates = [position for position, count in Counter(positions).items() if count > 1]
        if duplicates:
            findings.append(Finding(
                ERROR, "duplicate_ranking_positions",
                f"{division} has more than one fighter at position(s) {duplicates}.",
                [f"#{entry['position']} {entry['fighter_name']}" for entry in entries
                 if entry["position"] in duplicates],
                "Check the rankings parser."))
    missing_system = [division for division, entries in by_division.items()
                      if not entries[0]["system_name"]]
    if missing_system:
        findings.append(Finding(
            WARN, "ranking_system_unknown",
            "Ranking snapshots have no recorded ranking system/version.",
            missing_system,
            "The system label is read off the UFC page and stored with every snapshot; "
            "a blank one means the page layout changed."))
    return findings


def check_official_conflicts() -> List[Finding]:
    rows = query_all("SELECT id, name, status_conflicts FROM events "
                     "WHERE status_conflicts IS NOT NULL AND status_conflicts NOT IN ('', '[]')")
    if not rows:
        return []
    items = []
    for row in rows:
        try:
            notes = json.loads(row["status_conflicts"])
        except (TypeError, ValueError):
            notes = []
        for note in notes:
            items.append(f"#{row['id']} {row['name']}: {note}")
    return [Finding(WARN, "official_conflict",
                    "Events where reporting disagrees with the official schedule.",
                    items,
                    "This is informational - the disagreement is preserved on purpose. "
                    "Verify before reporting either version.")]


def check_orphan_fights() -> List[Finding]:
    rows = query_all(
        "SELECT f.id, f.fighter_a, f.fighter_b FROM fight_card_items f "
        "LEFT JOIN events e ON e.id = f.event_id WHERE e.id IS NULL")
    if not rows:
        return []
    return [Finding(ERROR, "orphan_fights", "Bouts point at an event that no longer exists.",
                    [f"#{row['id']} {row['fighter_a']} vs {row['fighter_b']}" for row in rows],
                    "Re-run the event identity backfill.")]


def check_urls(limit: int = 40) -> List[Finding]:
    """Optional: actually request stored article links."""
    from utils.http import HttpClient

    rows = query_all("SELECT id, url, title FROM articles WHERE is_demo = 0 "
                     "ORDER BY id DESC LIMIT ?", (limit,))
    client = HttpClient()
    broken = []
    for row in rows:
        try:
            response = client.get(row["url"])
            if not response.ok and response.status_code and response.status_code >= 400:
                broken.append(f"#{row['id']} HTTP {response.status_code} {row['url']}")
        except Exception as exc:
            broken.append(f"#{row['id']} {type(exc).__name__} {row['url']}")
    if not broken:
        return []
    return [Finding(WARN, "broken_urls", f"{len(broken)} of {len(rows)} article links failed.",
                    broken, "Links can rot or block automated requests; verify by hand.")]


CHECKS = [
    check_duplicate_events,
    check_duplicate_fighters,
    check_event_status_sanity,
    check_results_from_predictions,
    check_stories_without_sources,
    check_duplicate_stories,
    check_stale_breaking,
    check_suspicious_timestamps,
    check_rankings,
    check_official_conflicts,
    check_orphan_fights,
]


def run_all(include_urls: bool = False) -> List[Finding]:
    findings: List[Finding] = []
    for check in CHECKS:
        try:
            findings.extend(check())
        except Exception as exc:  # a broken check must not hide the others
            findings.append(Finding(WARN, check.__name__, f"Check failed to run: {exc}"))
    if include_urls:
        try:
            findings.extend(check_urls())
        except Exception as exc:
            findings.append(Finding(WARN, "check_urls", f"Check failed to run: {exc}"))
    return findings


def render(findings: List[Finding]) -> str:
    errors = [item for item in findings if item.level == ERROR]
    warnings = [item for item in findings if item.level == WARN]
    lines = ["=" * 72, "UFC NEWS RADAR - PRODUCTION DATA VALIDATION", "=" * 72, ""]

    counts = {table: (query_one(f"SELECT COUNT(*) AS n FROM {table}") or {"n": 0})["n"]
              for table in ("articles", "stories", "events", "fighters", "fight_card_items",
                            "rankings", "social_posts")}
    lines.append("Contents: " + ", ".join(f"{value} {name}" for name, value in counts.items()))
    lines.append("")

    if not findings:
        lines += ["✅ No problems found.", ""]
        return "\n".join(lines)

    for level, group, symbol in ((ERROR, errors, "❌"), (WARN, warnings, "⚠️")):
        if not group:
            continue
        lines.append(f"{symbol}  {len(group)} {level}(S)")
        lines.append("-" * 72)
        for finding in group:
            lines.append(f"  [{finding.check}] {finding.message}")
            for row in finding.rows[:8]:
                lines.append(f"      · {row}")
            if len(finding.rows) > 8:
                lines.append(f"      · ... and {len(finding.rows) - 8} more")
            if finding.fix:
                lines.append(f"      fix: {finding.fix}")
            lines.append("")
    lines.append("=" * 72)
    lines.append(f"{len(errors)} error(s), {len(warnings)} warning(s)")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate collected UFC News Radar data.")
    parser.add_argument("--check-urls", action="store_true",
                        help="also request stored article links (slow, needs internet)")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    args = parser.parse_args()

    init_db(seed=False)
    findings = run_all(include_urls=args.check_urls)
    if args.json:
        print(json.dumps([finding.as_dict() for finding in findings], indent=2))
    else:
        print(render(findings))
    return 1 if any(finding.level == ERROR for finding in findings) else 0


if __name__ == "__main__":
    raise SystemExit(main())
