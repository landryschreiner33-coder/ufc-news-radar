# AUDIT.md - UFC News Radar production overhaul

Audit of the existing application, the problems reproduced in it, their root
causes and the fixes. Written during the overhaul; each issue lists the
regression test that stops it coming back.

**Audit date:** 2026-09-19
**Baseline:** 171 tests passing, schema v2, commit `33d5969`

---

## Method

1. Read `CLAUDE.md`, `README.md`, `PROGRESS.md`, the database layer, models,
   collectors, processors, social, AI, UI and tests.
2. Ran the existing suite (171 passed) to establish a green baseline.
3. Inspected the *live* database (`data/ufc_news_radar.db`) rather than trusting
   the tests, because the reported faults were data faults.
4. Reproduced each fault, fixed the cause, added a regression test.

The live database is what exposed the two most serious issues. Both were
invisible to the old test suite because the tests only ever exercised freshly
built fixtures.

---

## Issue 1 - Events reported as completed although they are still scheduled

**Severity:** critical. This is the reported UFC 331 fault.

**Reproduction:** UFC's schedule lists Crypto.com UFC 331 for Saturday
19 September 2026. On that date the app treated the event as finished.

**Root cause:** there was *no event lifecycle at all*. The `events` table had
`status TEXT DEFAULT 'scheduled'` and nothing ever computed it - no code path
in the entire repository ever set an event to completed. The apparent
"completed" state came from the only date logic that existed, in
`repo_entities.list_events()`:

```sql
SELECT * FROM events WHERE event_date IS NULL OR event_date >= ?   -- today
```

An event whose date had arrived or passed dropped out of the upcoming list,
so the interface presented it as over. That is exactly the banned
`event_date < current_date => COMPLETED` inference, implemented as a filter.
There was also no concept of LIVE, POSTPONED or UNKNOWN, no `scheduled_start_utc`,
no timezone, and no record of where a status came from.

**Affected files:** `database/schema.sql`, `database/repo_entities.py`,
`ui/pages/events.py`.

**Fix:** a real lifecycle (`processors/event_lifecycle.py`) with
UPCOMING / LIVE / COMPLETED / CANCELLED / POSTPONED / UNKNOWN, decided from the
official schedule plus *evidence*, never from the calendar:

* before `scheduled_start_utc` -> UPCOMING
* inside the scheduled window -> LIVE
* officially cancelled/postponed -> CANCELLED / POSTPONED
* after the window **with reliable completion evidence** -> COMPLETED
* after the window **without** it -> UNKNOWN, stating plainly that no source
  has confirmed the event took place

Every status is stored with `status_source`, `status_confidence`,
`status_updated_at` and a human-readable `status_reasons` array.
`list_events(upcoming_only=True)` now filters on lifecycle status, so a date
that has passed no longer hides an event.

**Regression tests:** `tests/test_event_lifecycle.py` - UFC 331 before start,
on the day before start, after start, after the window without evidence, after
reliable completion evidence, same-day without a start time, past-dated without
evidence, plus a test that the upcoming listing is not calendar-driven.

---

## Issue 2 - One event stored as several events

**Severity:** critical. Splits a card, its stories and its changes.

**Reproduction:** the live database contained both

| id | name | mentions |
| --- | --- | --- |
| 1 | `UFC 320: Jones vs Aspinall` | 3 |
| 3 | `UFC 320` | 3 |

Two rows, one real event. Anything attached to one was invisible from the other.

**Root cause:** `events.normalized_name TEXT NOT NULL UNIQUE` - identity was the
*display name*. Every naming variant created a row: the bare number from article
prose, the full title from the official page, and any sponsor prefix
("Crypto.com UFC 331") on top.

**Affected files:** `database/schema.sql`, `database/repo_entities.py`.

**Fix:** `processors/event_identity.py` derives a stable `canonical_key`,
strongest identifier first: official UFC event id -> official URL slug ->
numbered event parsed from any spelling -> dated Fight Night -> matchup ->
normalised name. Sponsor branding is stripped, so all of `UFC 331`,
`Crypto.com UFC 331` and `Crypto.com UFC 331: Van vs Pantoja 2` resolve to
`ufc:331`.

Because one event legitimately answers to several identities (an article says
"Silva vs Costa", the official page says "october-31-2026"), an `event_aliases`
table maps every known identity to the event, and lookups try all of them
before creating anything.

`database/event_migration.py` merges pre-existing duplicates: child rows
(fight card items, card changes, stories) move to the surviving event, mention
counts add up, gaps are filled from the loser, and the losing row is kept as a
redirect (`merged_into_id`) so existing links still resolve. Nothing is deleted.
Verified against the live database: 1 duplicate merged, 17 articles preserved.

**Regression tests:** `tests/test_event_lifecycle.py` - every spelling maps to
one key, official id wins, digits elsewhere in a headline are not mistaken for
an event number, duplicate names resolve to one row, the fuller official name
becomes the display name, merged ids still resolve.

---

## Issue 3 - Prediction and preview articles could become fight results

**Severity:** critical for accuracy. This is how fiction enters the database.

**Reproduction:** `Category` had a single `RESULT` bucket driven by keyword
matching. The headline

> "UFC 331 predictions: Who wins Van vs Pantoja 2?"

matches `wins`, and was categorised as a fight result. Ahead of a big card,
prediction and preview pieces outnumber every other kind of coverage, so this
is the most likely path to a fabricated winner - and, with completion evidence
wired to results, to an event wrongly marked finished.

**Affected files:** `processors/categorize.py`, `processors/enrich.py`.

**Fix:** `processors/result_safety.py` classifies what an article is *doing* -
PREVIEW, PREDICTION, ANNOUNCEMENT, RESULT, POST_FIGHT, INTERVIEW, RUMOR - and
enforces the guard structurally rather than by score: `may_establish_result()`
returns False for PREVIEW and PREDICTION whatever else the text says. A second
gate requires a source trusted to report outcomes. Headline-level overrides
catch the realistic traps: a question headline, future/conditional wording
("will defeat"), and result vocabulary with nothing described as having
happened. Historic-present headlines ("Van defeats Pantoja") are still read as
results. Enrichment stores `intent` and `intent_reasons` on every article and
downgrades a RESULT category that the intent rules contradict.

**Regression tests:** `tests/test_result_safety.py` - 5 prediction headlines,
5 preview headlines and 3 genuine results classified; predictions/previews
proven unable to establish a result from *any* source type; results proven to
require a reliable source; future-tense and question headlines blocked; and an
end-to-end test that a prediction article leaves an event UPCOMING.

---

## Issue 4 - Missing CONTESTED status

**Severity:** moderate. Required by the specification.

`StoryStatus` had six statuses; reliable sources disagreeing had nowhere to
land, so a conflict was recorded in `has_conflict` but the status still picked
a side. Added `CONTESTED` with its style, and the reconciliation layer
(`processors/event_reconcile.py`) preserves disagreement rather than resolving
it: when reporting contradicts the official schedule, the official status
stands and the report is surfaced as a conflict.

---

## Fixed data faults in the live database

| Fault | Rows | Action |
| --- | --- | --- |
| Duplicate `UFC 320` event | 2 -> 1 | merged, old id redirects |
| Events with no lifecycle status | 3 | backfilled to UNKNOWN, recomputed per collection |
| Articles with no intent | 17 | default UNKNOWN; reclassified on next enrichment |

---

## Schema changes (v2 -> v3)

`events`: `canonical_key` (UNIQUE), `official_event_id`, `scheduled_start_utc`,
`scheduled_end_utc`, `local_timezone`, `city`, `event_status`, `status_source`,
`status_confidence`, `status_updated_at`, `status_reasons`, `status_conflicts`,
`official_source_url`, `image_url`, `merged_into_id`.
New table `event_aliases`.
`articles`: `intent`, `intent_reasons`, `event_occurred_at`, `updated_at_source`.
`fight_card_items`: `official_status`, `canonical_fighter_a`, `canonical_fighter_b`.

The migration is idempotent and was verified both on a fresh database and by
upgrading a copy of the live v2 database with no data loss.
