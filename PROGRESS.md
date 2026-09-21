# PROGRESS.md

Living status of UFC News Radar. Updated as work lands.

**Last updated:** 2026-09-21
**Test status:** ✅ 441 passed on SQLite; ✅ 435 passed / 6 skipped on PostgreSQL
(the same suite, `UFC_RADAR_TEST_DATABASE_URL=... python -m pytest`)
**Data status:** ✅ `python scripts/validate_production_data.py` - 0 errors, 0 warnings
**App status:** ✅ 12 pages × 7 viewport widths driven in Chromium; no exceptions,
no horizontal overflow, no escaped markup, no sidebar covering the feed. The
`[database]` secrets path was driven end to end against a real PostgreSQL
server: schema built, errors explained on screen, recovery without a restart.

---

## 1. The bug-fix and polish pass (this round)

A read-only inspection of the running app found twelve defects plus a set of
smaller ones. Every one is fixed at its cause and locked down by a named test
in `tests/test_bugfix_regressions.py`.

| # | Defect | Cause | Fix |
| --- | --- | --- | --- |
| D1 | Every non-rumour card printed `<div class="foot">` on screen as literal text | An empty `claim_block` left a blank line inside the card markup; CommonMark ended the HTML block and escaped the indented lines after it | `ui/cards.py` builds markup as fragments joined with no separator, and asserts the result contains no newline |
| D2 | At 430px the sidebar covered the feed | `initial_sidebar_state="expanded"` forced it open at every width | `"auto"`, plus phone CSS that makes fixed-width card containers full-width |
| D3 | A run where 0 of 14 sources succeeded still read "Last updated just now" | Nothing recorded what a run *achieved*, and the freshness stamp moved on every run | `CollectionOutcome` (SUCCESS / PARTIAL / TOTAL_FAILURE / NOT_RUN) derived from the run's own numbers; `last_collection_at` only moves when a source returned; a banner and status bar state it |
| D4 | "SOURCES 2 / INDEPENDENT 3" | Independence counted linked X posts; the total counted only news outlets | News sources and social signals are separate pools in `processors/verification.py`, with an invariant assert; one vocabulary in `models/types.py` |
| D5 | "OFFICIAL BOUTS 0" above a bout reading "confidence: official" | Two fields answering different questions, one defaulted by a migration | `processors/bout_status.py`: `official_status` (on UFC's card) + `evidence_level` (how strong), constrained so the contradiction cannot be stored |
| D6 | One story shown twice on an event page | Each section filtered the whole list independently | Sections consume from a shared pool, as the dashboard already did |
| D7 | "Safe to state: Alpha vs Bravo official for DEMO FIGHT NIGHT 1" beside "No event has been named" | Entities came only from the fighter registry, and demo data never registered its event | Entity detection learns events from the events table and fighters from a headline matchup; demo data registers its own event; gaps and sourcing are separate sections |
| D8 | `/dashboard` raised Streamlit's "page not found" dialog | The default page is served at `/` only - `st.Page.url_path` returns `""` for it | `/` is canonical; a hidden alias page owns `/dashboard` and redirects |
| D9 | An opened story sat at `/` with no parameters | `st.switch_page` drops the query string | The selection crosses in session state and `nav.sync_url` writes the URL after render. Refresh, bookmark, copy, back and forward all verified in Chromium |
| D10 | Scripts said "The UFC just made it official - this story." | `subject_of` returned a filler word when nothing was detected | It returns `""` and every sentence has a subject-free variant; `build_script` asserts no placeholder survives |
| D11 | "The start time … is still in the future" above "No start time has been collected" | A fixed sentence per status, whatever data the event had | `event_lifecycle.status_explanation` builds one sentence from the fields that exist |
| D12 | "No stories collected yet" while the status bar read "Stories 9" | An empty LATEST section means "nothing left over", not "nothing collected" | The message says which |

### Smaller items in the same pass

- [x] **Source health reconciles**: one state per source
      (HEALTHY / PARTIAL / STALE / ERROR / NOT_RUN / NOT_CONFIGURED / DISABLED),
      exhaustive and mutually exclusive, so the counts add up to the total.
- [x] **Placeholder art matches its subject** - an event card no longer says
      "CARD CHANGE".
- [x] **Ranking labels cleaned**: the stored system label is the phrase itself,
      not the hundred surrounding characters of page navigation.
- [x] **Article intent backfilled** by migration 5, so the preview/prediction
      result gate applies to rows collected before the column existed.
- [x] **Legacy columns dropped** (`events.status`, `fight_card_items.confidence`)
      so a fresh database and an upgraded one have the same shape - with tests
      that upgrade a v4 database for real.
- [x] **The dead `auto_collect_on_start` setting removed**, replaced by
      background collection that actually runs.
- [x] **No filesystem path on the Settings page**.
- [x] **Boolean settings fixed**: `set_setting(key, "0", "bool")` stored `1`,
      because `if value` on the string `"0"` is true. Every boolean default
      seeded as off was on.
- [x] **`app.pid` removed from git** and added to `.gitignore`.

### Test fixtures

- [x] **Recorded feeds no longer rot.** Their `pubDate`s were real dates, so
      the suite quietly aged out: once the fixtures were more than three days
      old, a story-linking test started failing for reasons that had nothing
      to do with the code. Feed fixtures now carry `{{minutes_ago:N}}`
      placeholders that `tests/fake_http.py` fills in when it serves them,
      keeping the original spacing between items.

### Persistence and background collection

- [x] **PostgreSQL implemented, not promised.** `DATABASE_URL` switches the
      whole app over; `database/backends.py` translates the one SQL dialect
      the repositories are written in. The entire test suite runs against a
      real PostgreSQL 16 server, not just SQLite.
- [x] **Two ways to configure it, one place that validates them.**
      `DATABASE_URL` (environment, `.env`, or a top-level Streamlit secret), or
      a `[database]` section in the Secrets box with `host`, `port`,
      `database`, `username` and `password` - the shape providers actually
      print. `database/db_config.py` assembles and percent-encodes the URL, so
      a password containing `@` or `/` works as typed; `DATABASE_URL` wins when
      both exist, so adding a section never changes a working deployment.
      Extra keys (`sslmode`, ...) pass straight through.
- [x] **Configuration mistakes are explained, not crashed on.** A missing
      field, a connection string pasted into the `host` box, a port that is
      not a number: each fails at start-up naming the field, and the app shows
      that message instead of a traceback. No message, log line or screen ever
      contains the password.
- [x] **No silent fallback.** A configured database with no driver - or a
      value that cannot be understood - fails at start-up with an explanation
      instead of writing to a local file.
- [x] **`scripts/scheduler.py`** - the collector as its own process, on an
      interval, with a lock held in the database so an app and a cron job
      cannot collect over each other. Windows/cron/systemd/container recipes
      are in the README.
- [x] **In-app background collector** for hosts where a second process is
      impossible, off by default and honest that it stops with the app.

### Validator

`scripts/validate_production_data.py` grew from 11 checks to 18. The new ones
are the faults above expressed as data rules: impossible source counts,
official/reported contradictions, unclassified intent, malformed links, legacy
schema columns, dead settings and source-health reconciliation. Run against the
real development database it found the D4 defect immediately and reported clean
after a reprocess.

---

## 1b. The production overhaul (previous round)


Three faults were reproduced in the *live* database - none of them visible to
the old test suite, because those tests only ever saw freshly built fixtures.
`AUDIT.md` has the full write-up; the summary:

| # | Fault | Fix | Tests |
| --- | --- | --- | --- |
| 1 | Events shown as finished once their date passed (the UFC 331 report). There was no lifecycle at all - the only date logic was an upcoming-list filter comparing `event_date` with today | `processors/event_lifecycle.py`: UPCOMING / LIVE / COMPLETED / CANCELLED / POSTPONED / UNKNOWN from the official schedule plus evidence. Past its window with no reliable evidence stays **UNKNOWN** | `test_event_lifecycle.py` |
| 2 | One event stored as several rows (`UFC 320` and `UFC 320: Jones vs Aspinall` were separate) because identity was the display name | `processors/event_identity.py` canonical keys + `event_aliases` + a merge migration that keeps the old id as a redirect | `test_event_lifecycle.py` |
| 3 | Prediction/preview articles could become fight results, and so complete events that had not happened | `processors/result_safety.py`: intent classification where `may_establish_result` is structurally False for PREVIEW/PREDICTION | `test_result_safety.py` |

### Also delivered

- [x] **CONTESTED status** (🟣) when reliable sources on both sides contradict
      each other. Two low-quality accounts disagreeing does not qualify.
- [x] **Event reconciliation** - reporting never overwrites a live official
      listing; the disagreement is shown as a conflict instead.
- [x] **Official fight cards** from UFC's own event page, with bouts marked
      `official` and kept visually separate from reported and rumoured ones.
- [x] **Official schedule data**: segment start timestamps, broadcast timezone,
      venue city, official event id, event image.
- [x] **Time correctness**: UTC storage, `zoneinfo` display with DST applied
      per instant, future timestamps shown as "in 3h", and impossible or
      future publication dates falling back to collection time at ingest.
- [x] **Navigation** rebuilt on `st.Page` / `st.navigation`: Dashboard,
      X Radar, Research, TikTok Studio, with Reference and Tools secondary.
- [x] **Responsive card grid** - measured 4 cards per row at 1800px, 3 at 1400,
      2 at 1100, 1 at 800 and 430, with no horizontal overflow.
- [x] **Story images** with honest fallbacks (generic category graphics, never
      an unrelated fighter photo).
- [x] **TikTok Studio** page: script left, the research backing it right.
- [x] **X Radar** grouped into Breaking signals / Journalists / Fighter posts /
      Rumors / Trending discussions, every post badged as a social signal.
- [x] **Streamlit Cloud support**: `st.secrets` read alongside env and `.env`.
- [x] **Backup and restore** via SQLite's backup API, with validation.
- [x] **Storage honesty**: the Settings page says plainly when the filesystem
      is temporary and history will not accumulate.
- [x] **Data corrections** with a recorded history (merge events/fighters,
      reassign, recategorise, reclassify a source).
- [x] **`scripts/validate_production_data.py`** - 11 checks plus optional URL
      testing; verified it catches injected faults and exits 1.

---

## 2. Completed functionality (cumulative)

### Foundation
- [x] Project structure (`collectors/ processors/ social/ ai/ database/ models/ ui/ utils/ tests/ scripts/`)
- [x] Config from the environment only (env var → `.env` → `st.secrets`), `.env.example`
- [x] Resilient HTTP client: timeouts, retries with backoff, 429 handling, conditional GETs
- [x] Text/URL normalisation, UTC time handling with timezone display, logging

### Database
- [x] Schema v4 with every specified table plus `event_aliases` and `data_corrections`
- [x] Indexes on every lookup/sort column; ISO-8601 UTC timestamps throughout
- [x] Migrations (`user_version` + `MIGRATIONS`), verified idempotent and
      verified by upgrading a copy of the live v2 database with no data loss
- [x] Canonical event identity with automatic duplicate merging on start-up
- [x] Repositories split per domain area; idempotent seeding

### Collection
- [x] `collect() / normalize() / validate()` adapter interface, per-source failure isolation
- [x] RSS adapter with fallback URLs and last-known-good tracking
- [x] 14 built-in sources, all user-editable
- [x] Aggregator items re-attributed to the real publisher
- [x] UFC.com rankings (reads the published system label), events, and official cards
- [x] Publication dates sanitised at ingest
- [x] Source health, collection run history, CLI collector (`scripts/collect.py`)

### Processing
- [x] Fighter/event/matchup/weight-class extraction with ambiguity guards
- [x] Rule-based categorisation, **intent classification**, hedging, attribution, denial
- [x] Clustering with gates against false grouping; denial-joins-its-story exception
- [x] Verification: 7 statuses with reasons; independence accounting; conflict preservation
- [x] Automated source-support assessment (explicitly not a probability)
- [x] Relevance with a visible breakdown; trending from measured activity only
- [x] Developing timelines, fight-card change tracking, ranking snapshots and diffs
- [x] Event lifecycle + reconciliation on every collection

### Interface
- [x] Four primary sections, eight secondary, built on the current navigation API
- [x] Responsive card grid; status badges with icon **and** word (never colour alone)
- [x] Dashboard sections: 🔥 BREAKING, ⚡ IMPORTANT, 🔴 RUMORS, ⚡ DEVELOPING,
      📈 TRENDING, 📅 UPCOMING EVENTS, 📰 LATEST
- [x] Event pages with lifecycle, countdown, and official/reported/rumored/cancelled split
- [x] Research mode with all specified sections; CHECK BEFORE REPORTING
- [x] TikTok Studio; fighter, rankings, card-change, X, watchlist, search,
      source-health and settings pages
- [x] 14 filters, 5 sort orders, polished first-run empty state, demo mode

### Quality
- [x] 288 tests
- [x] Production data validator
- [x] Whole app driven in Chromium at four viewport widths

---

## 3. Blocked features (need something outside the app)

| Feature | Blocked by | State |
| --- | --- | --- |
| X monitoring | `X_BEARER_TOKEN` from an X developer account | Integration built and tested against recorded API responses; shows **X MONITORING - NOT CONFIGURED** until a token exists. |
| AI-written summaries/scripts | `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` | Providers implemented; app runs in labelled template mode meanwhile. |
| Live feed verification | The build sandbox has no outbound internet (egress policy blocks every news domain, `ufc.com`, and the deployed Streamlit app itself; confirmed again this round) | Collection, parsing and failure handling are tested against recorded fixtures. **No live UFC endpoint, feed, image or deployed page was verified in this pass.** The first live run happens on your machine; Source health reports exactly what each source did. |
| Permanent history on Streamlit Cloud | A PostgreSQL database of your own | **The app side is done and tested.** Paste the provider's fields into the Secrets box under `[database]` (host, port, database, username, password), or set `DATABASE_URL`; either one switches the whole app to PostgreSQL. What remains is external: create a database with any provider (Neon, Supabase, Railway, RDS). |


---

## 4. Known bugs

None outstanding.

Two behaviours worth knowing about, neither a bug:

* Pressing **back** from a story lands on `/research` with the story still on
  screen until the next interaction, because Streamlit does not rerun on a
  query-string-only popstate. The URL is always one that works if reloaded,
  and back again reaches the dashboard.
* The in-app background collector only runs while the app process is alive.
  That is a property of running inside a web page, and the Settings page says
  so; `scripts/scheduler.py` is the answer that does not have it.


---

## 5. Next steps

- Run a real collection on a machine with internet and check Source health;
  feed URLs drift and each source carries fallbacks.
- Schedule `python scripts/collect.py --loop 20` (or Task Scheduler on Windows)
  if you want collection without pressing Refresh.
- If you deploy to Streamlit Cloud, read the storage note in Settings first:
  that filesystem is temporary, so take a backup before each redeploy.
