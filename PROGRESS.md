# PROGRESS.md

Living status of UFC News Radar. Updated as work lands.

**Last updated:** 2026-09-19
**Test status:** ✅ 288 passed (`python -m pytest`)
**Data status:** ✅ `python scripts/validate_production_data.py` - 0 errors, 0 warnings
**App status:** ✅ all 12 pages driven in Chromium; no exceptions, no console errors

---

## 1. The production overhaul (this round)

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
| Live feed verification | The build sandbox has no outbound internet (egress policy blocks every news domain; confirmed again this round - all 14 sources returned connection errors, each isolated, the run completed cleanly) | Collection, parsing and failure handling are tested against recorded fixtures. The first live run happens on your machine; Source health reports exactly what each source did. |
| PostgreSQL storage | A driver and a database | The schema is PostgreSQL-friendly and `DATABASE_URL` is recognised, but no driver is implemented. The app *says so* rather than half-working. |

---

## 4. Known bugs

None outstanding.

---

## 5. Next steps

- Run a real collection on a machine with internet and check Source health;
  feed URLs drift and each source carries fallbacks.
- Schedule `python scripts/collect.py --loop 20` (or Task Scheduler on Windows)
  if you want collection without pressing Refresh.
- If you deploy to Streamlit Cloud, read the storage note in Settings first:
  that filesystem is temporary, so take a backup before each redeploy.
