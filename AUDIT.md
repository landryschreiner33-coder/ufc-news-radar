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

---

## Issue 5 - Card links could not carry a story id

**Severity:** high once the UI was rebuilt around cards - every card was dead.

**Reproduction:** story cards were rendered as `<a href="?story=12">`. Clicking
one landed on the right page with **no** query parameters, so the app showed
the story picker instead of the story.

**Root cause:** Streamlit's page router rewrites in-app anchors. A relative
link whose path matches one of its pages is turned into an internal page
navigation and the query string is discarded; an absolute URL is left intact
but forced to `target="_blank"`, opening a new tab. Neither can carry an id.

A second, self-inflicted problem made this hard to see: a stale Streamlit
process kept holding port 8501 after `kill`, so several rounds of "fixes" were
tested against old code. The restart helper now kills by port owner and asserts
that the log contains no "Port 8501 is not available".

**Fix:** cards are built with `st.container(horizontal=True, wrap=True)` and a
real `st.button` per card. That keeps the responsive reflow (measured 4/3/2/1
across 1800→430px) and makes navigation work. Because `st.switch_page` does not
carry query parameters either, the selection is also held in session state,
with the URL parameter kept so a story stays linkable.

**Verification:** driven in Chromium - READ MORE opens the story's research
page, OPEN EVENT opens the event with its status and card, and a menu click
away no longer bounces back to the previous item.

---

## Issue 6 - Smaller faults found and fixed

| Fault | Where | Fix |
| --- | --- | --- |
| The new navigation menu was invisible | `ui/theme.py` had `div[data-testid="stSidebarNav"] { display: none; }` from the old query-param router | Rule removed and restyled |
| Rankings table crashed Arrow | `#` column mixed `"C"` (champion) with integer positions | Column cast to text |
| Duplicate widget key `global_search` | the sidebar and the Search page both used it | Sidebar renamed to `sidebar_search` |
| Deprecated width API | 18 `use_container_width=True` calls | Migrated to `width="stretch"` |
| A future timestamp read as "just now" | `utils/timeutil.humanize_age` | Now reads "in 3h"; feed dates that are impossible or in the future fall back to collection time at ingest |

---

## Verification performed

* **288 tests pass**, including every regression listed above.
* **Production data validation**: 0 errors, 0 warnings on the live database;
  separately verified that it *does* report injected faults (duplicate
  fighters, an event completed before it starts, a prediction categorised as a
  result, a story with no sources, a future-dated article) and exits 1.
* **Full acceptance run on a fresh database** against recorded fixtures:
  empty first run shows no invented news; 10/14 sources OK with 4 failures
  isolated; statuses CONFIRMED / REPORTED / CONTESTED / RUMOR all produced;
  both events UPCOMING with reasons and no duplicates; rankings stored under
  "Meta UFC Rankings"; official bouts and card changes recorded; X and AI
  correctly reporting their unconfigured states.
* **UFC 331 case, end to end**: day-of before start → UPCOMING; during the card
  → LIVE; after the window with no evidence → UNKNOWN; with reliable evidence →
  COMPLETED. All three spellings resolve to `ufc:331`.
* **Browser**: all 12 pages at 1500px with no exceptions and no console errors;
  grid reflow measured at 1800 / 1400 / 1100 / 800 / 430px with no horizontal
  overflow.
* **Migration safety**: schema v2 → v4 applied to a copy of the live database
  with all 17 articles preserved and the duplicate event merged.

## Limitation of this audit

The sandbox has no outbound internet access. Every news domain returned a
connection error, which confirmed that per-source failure isolation works but
means **no live source URL was verified**. Parsing is tested against recorded
fixtures only. The first real collection happens on the user's machine, and
Source health reports exactly what each source did.

---

# Second audit - the running application (2026-09-21)

The first audit read the code and the database. This one drove the **running
app** in Chromium and compared what was on screen with what the data said.

**Audit date:** 2026-09-21
**Baseline:** 288 tests passing, schema v4, commit `2d0433c`
**Result:** 409 tests passing on SQLite, 403 + 6 skipped on PostgreSQL,
schema v5

## What could and could not be observed

The deployed Streamlit app could not be reached: the session's egress policy
refused `landryschreiner33-coder-ufc-news-radar-app-claudeadmirin-jpdwix.streamlit.app`
(`CONNECT tunnel failed, response 403`), and so did `ufc.com` and every news
domain. **No live endpoint and no deployed page was seen in this pass.**

Everything below was therefore observed by running the deployed commit locally
in Chromium in two states - an empty database (what a cold Streamlit Cloud
container shows) and a populated one - which is what the deployed code does,
on the deployed commit, with the deployed data shape.

## Issue 7 - Cards printed their own markup on screen

**Severity:** critical, and the first thing any visitor saw.

Every card whose status was not RUMOR or UNVERIFIED rendered a grey code block
containing `<div class="foot">` and the lines after it - 21 occurrences on the
dashboard, 21 on Research, 15 on Watchlists.

**Cause.** `card_html` interpolated `{claim_block}`, which is empty for every
status except RUMOR/UNVERIFIED, on a line of its own. `st.markdown` parses
CommonMark before it renders HTML: the resulting blank line terminated the
HTML block, and the next line - indented four spaces - became an indented code
block, which Streamlit escapes.

**Fix.** `ui/cards.py` assembles markup as a list of fragments joined with no
separator and asserts the result contains no newline. `ui/components.py` was
given the same treatment, since it was one empty interpolation away from the
same bug.

**Tests.** `test_d1_card_markup_never_contains_a_newline` and
`test_d1_card_footer_is_real_markup_for_every_status`, parametrised over all
seven statuses.

## Issue 8 - A failed collection reported as an update

**Severity:** critical. It is a false statement about the data.

A run in which 0 of 14 sources succeeded left the dashboard reading "Last
updated just now". The only trace of the failure was a collapsed sidebar
expander; the toasts had auto-dismissed during the 149-second run.

**Cause.** Nothing recorded what a run *achieved*. `last_collection_at` was
written unconditionally at the end of every run, and again by
`pipeline.process_all`, which is not collection at all.

**Fix.** `CollectionOutcome` (SUCCESS / PARTIAL / TOTAL_FAILURE / NOT_RUN),
derived in `repo_runs.finish_run` from the run's own numbers rather than
trusted from the caller. `last_collection_at` moves only when a source
returned; the attempt is recorded separately. `repo_runs.collection_status()`
is the single answer to "is what I am looking at current?", and the dashboard
leads with it.

**Tests.** `test_d3_*` - five tests including
`test_d3_the_outcome_is_derived_from_the_run_numbers_not_trusted`.

## Issue 9 - Impossible source counts

A story displayed "SOURCES 2 / INDEPENDENT 3".

**Cause.** `verification.evaluate_story` counted independence across articles
*and* linked X posts, while `pipeline.recompute_story` overwrote the total
with a count of articles only. Two numbers computed from different pools.

**Fix.** News sources and social signals are separate pools, computed together
in one place, with an assertion that independence can never exceed the total.
`models/types.py` holds one vocabulary - "Total news sources", "Independent
news sources", "Social posts" - used verbatim on every screen. The pipeline no
longer recomputes either number.

**Tests.** `test_d4_*`, including an end-to-end one that links a real X post to
a two-outlet story and asserts the story still has two news sources.

## Issue 10 - A bout that was official and not official at once

The event page showed `OFFICIAL BOUTS 0` above a bout filed under "REPORTED -
not on the official card yet" whose own line read "confidence: official ·
source: UFC.com".

**Cause.** Two fields answering different questions. `confidence` meant "an
official source was behind this"; `official_status` meant "UFC lists this on
its card". Migration 3 added `official_status` with a default and never
backfilled it, so old rows carried both claims. Worse, an *article* published
by UFC.com set `confidence='official'`, conflating "UFC published a story" with
"UFC listed the bout".

**Fix.** `processors/bout_status.py` is the one place the pair is decided.
`official_status` is set to `official` only by the official card collector;
`evidence_level` records strength separately (`official_card` >
`official_source` > `credible_reporting` > `unconfirmed`), and `is_consistent`
makes the contradiction unrepresentable. Migration 5 maps every legacy row and
drops the old column.

**Tests.** `test_an_article_from_ufc_com_is_reporting_not_an_official_card_entry`,
plus a validator check and a migration test that upgrades a v4 database
carrying the exact contradictory row.

## Issue 11 - The app contradicted itself about what it knew

Three separate versions of the same failure:

* "✅ Safe to state as fact: Alpha vs. Bravo official for DEMO FIGHT NIGHT 1"
  beside "❌ No event has been named in the collected sources", with "FIGHTERS
  INVOLVED: none detected" under a headline naming two.
* "The start time collected from the official schedule is still in the future"
  directly above "No start time has been collected".
* "Still unknown" repeating "Not safe to state as fact" word for word.

**Causes.** Entity detection consulted only the fighter registry and a fixed
set of event-name regexes; the demo data never registered the event it talks
about; event status used a fixed sentence per status whatever data the event
had; and the missing-details list was rendered under two different headings.

**Fixes.** `find_events` also matches every event already in the database, so
an event the app knows about is found by its own name - that is what makes
UFC 331 work without a special case. `find_fighters` learns both sides of an
"A vs. B" headline, guarded by a non-person word list. `StoryContext` falls
back to the canonical event the story is linked to. Demo data registers its
own event. `event_lifecycle.status_explanation` builds one sentence from the
fields that exist. Gaps and sourcing became separate sections that cannot
overlap.

**Tests.** `test_d7_*` (five), `test_d11_*`, `test_ufc331_explanation_matches_the_data_it_has`.

## Issue 12 - Routing

`/dashboard` raised Streamlit's "the page you have requested does not seem to
exist" dialog, and an opened story sat at `/` with no parameters - not
refreshable, bookmarkable or shareable.

**Causes.** Streamlit serves the default page at `/` only (`st.Page.url_path`
returns `""` for it), so its declared `url_path` resolves to nothing. And
`st.switch_page` navigates without carrying the query string.

**Fixes.** `/` is the canonical dashboard route and a hidden alias page owns
`/dashboard` and redirects to it. Selections cross the page switch in session
state and `nav.sync_url` writes them into the URL *after* the page renders -
doing it before produced a history entry for a URL that never existed and broke
the forward button.

**Verified in Chromium**: `/dashboard` → `/` with no dialog; READ MORE →
`/research?story=4`; that URL survives a reload; back reaches the dashboard and
forward returns to the story; the same for `/events?event_id=2` and
`/fighters?name=Demo+Fighter+Alpha`.

## Issue 13 - Smaller faults found and fixed

| Fault | Fix |
| --- | --- |
| One story shown twice on an event page | Sections consume from a shared pool |
| "No stories collected yet" beside "Stories 9" | An empty LATEST means "nothing left over"; the message says so |
| TikTok scripts said "That's locked in for this story." | `subject_of` returns `""` when nothing was detected, every sentence has a subject-free variant, and `build_script` asserts no placeholder survives |
| SOURCES 16 / HEALTHY 9 / FAILING 5 | Seven mutually exclusive, exhaustive source states whose counts sum to the total |
| Event placeholder art labelled "CARD CHANGE" | Placeholders have a kind: UFC EVENT / FIGHTER IMAGE UNAVAILABLE / NEWS IMAGE UNAVAILABLE / CARD CHANGE |
| Ranking label stored as 100 characters of page navigation | The matched phrase only, cleaned of headers and dates |
| Pre-migration articles stuck on `intent='UNKNOWN'` | Migration 5 reclassifies them, so the result gate is not new-rows-only |
| `events.status` surviving only in upgraded databases | Dropped by migration 5, with a test asserting a fresh and an upgraded database have identical columns |
| Absolute filesystem path on the Settings page | A description of the storage, not its location |
| Clipped heading under the toolbar, and the brand line printed twice on the dashboard | Top padding, and the brand line rendered once per screen |
| `auto_collect_on_start` seeded and read by nothing | Removed, and replaced by background collection that runs |
| `app.pid` committed to the repository | Removed and ignored |

## Issue 14 - Every boolean default was on

Found while testing the new background-collection setting.
`set_setting(key, "0", "bool")` stored `"1"`, because the stored form was
chosen with `if value` and the string `"0"` is truthy. Every boolean default
seeded as off - including `demo_data_loaded` - was stored as on. Fixed by
interpreting the value rather than testing its truthiness, with a regression
test over all three input forms.

## Persistence

`DATABASE_URL` was previously "recognised and reported, not implemented". It is
now implemented: `database/backends.py` translates the single SQL dialect the
repositories are written in, and **the whole test suite runs against a real
PostgreSQL 16 server** - 403 passed, 6 skipped (the SQLite upgrade-path tests
and the file backup). A configured URL with no driver now fails at start-up
rather than silently writing to a local file.

What remains is external and cannot be done from here: creating the database
with a provider and pasting its URL into the app's Secrets box.

## Verification performed

* 409 tests on SQLite; the same suite on PostgreSQL.
* `scripts/validate_production_data.py` (18 checks, up from 11) run against the
  real development database: it found the impossible source count immediately
  and reported clean after a reprocess.
* 12 pages × 7 viewport widths (1920 → 390) in Chromium: no horizontal
  overflow, no clipped heading, no sidebar covering the feed, no escaped
  markup, no page errors.
* Migrations exercised by upgrading a v4-shaped database for real, including
  the contradictory bout row and `intent='UNKNOWN'` articles.

## Limitation of this audit

The same as the first one, and worth repeating because it bounds every claim
above: **no live source, no UFC page and no deployed instance was reachable.**
Everything was verified against the local app, recorded fixtures and a local
PostgreSQL server. The first real fetch happens on the user's machine, and
Source health reports exactly what each source did.
