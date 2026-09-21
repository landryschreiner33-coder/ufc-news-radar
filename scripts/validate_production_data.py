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
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse


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


def check_source_counts() -> List[Finding]:
    """The arithmetic behind "2 sources, 3 independent" being on screen."""
    rows = query_all(
        "SELECT id, headline, source_count, independent_source_count, social_post_count, "
        "article_count FROM stories")
    impossible, no_sources = [], []
    for row in rows:
        total = int(row["source_count"] or 0)
        independent = int(row["independent_source_count"] or 0)
        if independent > total:
            impossible.append(
                f"#{row['id']} {row['headline'][:60]} - {independent} independent of {total} total")
        if int(row["article_count"] or 0) > 0 and total == 0:
            no_sources.append(f"#{row['id']} {row['headline'][:60]}")
    findings: List[Finding] = []
    if impossible:
        findings.append(Finding(
            ERROR, "independent_exceeds_total",
            "Stories claim more independent news sources than they have news sources.",
            impossible,
            "News sources and social posts must be counted in separate pools - see "
            "processors/verification.py. Re-run processing."))
    if no_sources:
        findings.append(Finding(
            WARN, "articles_without_sources",
            "Stories have articles but a news-source count of zero.",
            no_sources, "Re-run processing so the counts are recomputed."))
    return findings


def check_bout_status_contradictions() -> List[Finding]:
    """A bout cannot be off the official card and official at the same time."""
    from processors import bout_status as bout_model

    rows = query_all(
        "SELECT f.id, f.fighter_a, f.fighter_b, f.official_status, f.evidence_level, "
        "e.name AS event_name FROM fight_card_items f "
        "LEFT JOIN events e ON e.id = f.event_id")
    bad = [
        f"#{row['id']} {row['fighter_a']} vs {row['fighter_b']} ({row['event_name']}): "
        f"official_status={row['official_status']} evidence_level={row['evidence_level']}"
        for row in rows
        if not bout_model.is_consistent(row["official_status"], row["evidence_level"])
    ]
    if not bad:
        return []
    return [Finding(
        ERROR, "bout_status_contradiction",
        "Bouts describe themselves two different ways at once.",
        bad,
        "Only the official card collector may set official_status='official'. "
        "See processors/bout_status.py; re-run the schema migration.")]


def check_article_intent() -> List[Finding]:
    """Articles exempt from the preview/prediction gate."""
    rows = query_all(
        "SELECT id, title FROM articles WHERE intent IS NULL OR intent = '' OR intent = ?",
        (ArticleIntent.UNKNOWN.value,))
    if not rows:
        return []
    return [Finding(
        WARN, "unclassified_intent",
        "Articles have no intent, so the preview/prediction result gate does not apply to them.",
        [f"#{row['id']} {row['title'][:70]}" for row in rows],
        "Migration 5 backfills these. Restart the app, or re-run enrichment.")]


def check_malformed_urls() -> List[Finding]:
    """Links that cannot be opened, without making a single request."""
    findings: List[Finding] = []
    for table, label in (("articles", "url"), ("events", "ufc_url"),
                         ("fight_card_items", "source_url")):
        rows = query_all(f"SELECT id, {label} AS link FROM {table} WHERE {label} IS NOT NULL "
                         f"AND {label} != ''")
        bad = []
        for row in rows:
            parsed = urlparse(str(row["link"]))
            if parsed.scheme not in ("http", "https") or not parsed.netloc:
                bad.append(f"#{row['id']} {str(row['link'])[:80]}")
        if bad:
            findings.append(Finding(
                ERROR, "malformed_url",
                f"{table}.{label} contains values that are not usable links.",
                bad, "Every displayed source must link to something openable."))
    return findings


def check_schema_consistency() -> List[Finding]:
    """A fresh database and an upgraded one must have the same shape."""
    from database.db import SCHEMA_FILE, get_connection, table_columns

    connection = get_connection()
    findings: List[Finding] = []
    legacy = [
        ("events", "status", "superseded by event_status"),
        ("fight_card_items", "confidence", "superseded by evidence_level"),
    ]
    stale = [f"{table}.{column} ({why})" for table, column, why in legacy
             if column in table_columns(connection, table)]
    if stale:
        findings.append(Finding(
            ERROR, "legacy_schema",
            "Legacy columns survive in this database that a fresh one does not have.",
            stale,
            "Restart the app so migration 5 runs, or check why the column could not be "
            "dropped (an index or view on it will block it)."))

    # Every table in schema.sql must exist here.
    expected = set(re.findall(r"CREATE TABLE IF NOT EXISTS (\w+)",
                              SCHEMA_FILE.read_text(encoding="utf-8")))
    missing = sorted(name for name in expected if not table_columns(connection, name))
    if missing:
        findings.append(Finding(
            ERROR, "missing_tables", "Tables in schema.sql are missing from this database.",
            missing, "Restart the app; the schema is applied on every start."))
    return findings


def check_dead_settings() -> List[Finding]:
    """Settings stored but read by nothing."""
    known_dead = ["auto_collect_on_start"]
    rows = query_all(
        "SELECT key FROM settings WHERE key IN ({})".format(
            ",".join("?" * len(known_dead))), tuple(known_dead))
    if not rows:
        return []
    return [Finding(
        WARN, "dead_setting", "Settings are stored that nothing reads.",
        [row["key"] for row in rows],
        "Migration 5 removes these. Restart the app.")]


def check_source_health_reconciles() -> List[Finding]:
    """The Source health counts must add up to the number of sources."""
    from database import repo_sources as sources_repo

    summary = sources_repo.health_summary()
    if sum(summary["counts"].values()) == summary["total"]:
        return []
    return [Finding(
        ERROR, "source_health_mismatch",
        "Source health states do not account for every source.",
        [f"{state}: {count}" for state, count in summary["counts"].items()],
        "database/repo_sources.health_state must be exhaustive.")]


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
    check_source_counts,
    check_bout_status_contradictions,
    check_article_intent,
    check_malformed_urls,
    check_schema_consistency,
    check_dead_settings,
    check_source_health_reconciles,
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
