# CLAUDE.md - project memory for UFC News Radar

This file is the persistent technical memory of the project. Read it before
changing anything.

---

## 1. Purpose

A UFC/MMA news-intelligence and research dashboard for a TikTok creator.

It exists to answer: *what important UFC news is happening right now, what is
actually known, what is only reported or rumoured, what changed, and what must
be verified before reporting it?*

Priorities, in order: **accuracy → speed → discovery → verification → source
transparency → research → TikTok usefulness.**

The user is a beginner programmer. Simple, readable, well-commented code beats
clever code. Everything must run on Windows in Chrome.

---

## 2. Non-negotiable product rules

These are product requirements, not style preferences. Breaking one is a bug.

1. **Never invent** facts, quotes, sources, X posts, statistics, fight details,
   confirmations, rankings, injuries or event information.
2. **Copies are not confirmation.** An article that credits another outlet is
   marked derivative and never counts as an independent source. Outlets in the
   same publisher family count once (`independence_group`).
3. **Repetition is not confirmation.** Many low-quality accounts repeating a
   rumour never upgrade its status (enforced in `_decide_status`: DEVELOPING
   and above require at least one independent credible source).
4. **Preserve disagreement.** Conflicts are stored (`has_conflict`,
   `conflict_notes`), shown in the UI and never resolved by the app.
5. **Scores are tools, not truth.** Relevance orders the feed; source support
   is an *"automated source-support assessment"*, explicitly not a probability
   that a claim is true. Both always ship with their reasoning.
6. **No faked integrations.** No X token means "X MONITORING - NOT CONFIGURED".
   No AI key means clearly-labelled template mode. Nothing is simulated.
7. **Demo data is unmistakable**: fictional names, `[DEMO]` prefix,
   `is_demo = 1`, a banner while loaded, one-click removal.
8. **Copyright**: store a short extract (≤1500 chars) for analysis, show a
   short excerpt (~320 chars), always link the original. Never store or display
   a whole article.
9. **Secrets** come from the environment only - a real environment variable,
   `.env`, or `st.secrets` on Streamlit Cloud. Never in the database, the UI,
   logs, or git.
10. **A date is not evidence.** An event is never called finished because its
    date passed. COMPLETED requires reliable evidence that it happened;
    without it the status is UNKNOWN (`processors/event_lifecycle.py`).
11. **A preview is not a result.** PREVIEW and PREDICTION articles can never
    create a winner, a loser or a completed event, whatever words they use
    (`processors/result_safety.py`).
12. **An image never implies a claim.** A story with no collected image gets a
    flat generic graphic, clearly labelled. Never an unrelated fighter photo.

---

## 3. Architecture

```
                 collectors/                processors/
 sources ──▶ adapter.collect()  ──▶ enrich ──▶ cluster ──▶ verify ──▶ score ──▶ UI
             (per-source isolation)   │          │           │          │
                                      │          │           │          └ relevance,
                                      │          │           │            support,
                                      │          │           │            trending
                                      │          │           └ status + reasons
                                      │          └ story grouping (gates against
                                      │            false merges)
                                      └ entities, category, INTENT, hedging,
                                        attribution

 UFC.com ───▶ ufc_events ────▶ canonical identity ──▶ events table
              ufc_event_card    (event_identity)         │
              ufc_rankings                               ▼
                                                  event_lifecycle
 articles ──▶ event_reconcile ──▶ typed evidence ──▶ UPCOMING / LIVE /
                                                     COMPLETED / CANCELLED /
                                                     POSTPONED / UNKNOWN
```

Layers, bottom up:

* **`utils/`** - config (env only), HTTP client (timeouts, retries, 429,
  conditional GETs), text/URL normalisation, UTC time helpers, logging, paths.
* **`database/`** - SQLite schema, migrations, one repository module per domain
  area, seed data and demo data.
* **`models/types.py`** - the shared vocabulary (statuses, source types,
  categories, filters, sorts). Everything imports its labels from here.
* **`collectors/`** - `BaseCollector` defines `collect() / normalize() /
  validate()`; `run()` wraps them so any failure is captured, recorded against
  that source and returned as a result object. `registry.py` maps the
  `adapter` column to a class. `runner.py` runs every enabled source and hands
  results to the pipeline.
* **`processors/`** - the analysis. Entity extraction, rule-based
  categorisation, **intent classification (`result_safety.py`)**, TF-IDF/token
  similarity, clustering, verification, support, relevance, trending,
  developing timelines, fight-card changes, ranking diffs, **canonical event
  identity (`event_identity.py`), the event lifecycle (`event_lifecycle.py`)
  and reconciliation (`event_reconcile.py`)**, and `pipeline.py` which
  orchestrates them.
* **`social/`** - X API v2 client (read-only), query planning, monitored
  accounts.
* **`ai/`** - provider abstraction, grounded context + prompts, template mode,
  grounding checker, and `service.py`, the single API the UI calls.
* **`ui/`** - theme, components, `cards.py` (the responsive card grid),
  `images.py` (image resolution and generic fallbacks), `nav.py` (the page
  registry) and one module per page. `app.py` builds the `st.navigation` menu.

---

## 4. Important technical decisions

**SQLite, PostgreSQL-friendly.** One file, zero setup for a beginner. All
timestamps are ISO-8601 UTC strings in exactly `YYYY-MM-DDTHH:MM:SSZ` so string
comparison sorts chronologically and the values parse straight into
`timestamptz` later. Lists/dicts are JSON text. No SQLite-only column types.

**Schema versioning.** `PRAGMA user_version` + a `migrations` table.
`SCHEMA_VERSION` in `database/db.py` must equal the highest entry in
`MIGRATIONS`. A fresh database is created at `SCHEMA_VERSION`, so migrations
only run when upgrading an existing file. Duplicate-column errors are tolerated.

**Rule-based analysis, not model-based.** Categories, hedging, attribution,
independence, status and support are all keyword/rule driven. A dashboard about
verification has to be able to explain itself, and it must work with no API
key. The AI layer adds *prose*, never *judgements*.

**Clustering gates.** A candidate story is rejected unless: it is inside the
match window; both items share a fighter when both name fighters; and they are
not two different kinds of development (announcement vs. cancellation). One
deliberate exception: an article that *denies* something joins the story it
disputes, because a claim and its denial are one story.

**Aggregators are re-attributed.** Google News items are re-classified as the
publisher they link to (`<source url>`), so a syndicated copy scores as ESPN,
not as "Google", and never inflates the independent-source count.

**Independence is explicit.** `independence_group` on sources and articles.
Same group = one voice. Derivative articles (detected via "according to X",
"per X", "first reported by X") are excluded from the independent count.

**Rankings make no assumption about the system.** The collector *reads* the
system label and date off the UFC page and stores them with every snapshot
(`system_name`, `system_version`). It never assumes a particular ranking
method. If the page cannot be parsed, it fails loudly instead of guessing.

**Events have one identity and one lifecycle.** `canonical_key` is derived
from the strongest identifier available (official id > URL slug > numbered
event > dated Fight Night > matchup > normalised name), sponsor branding is
stripped, and an `event_aliases` table maps every name an event answers to.
Status is decided by `event_lifecycle.resolve_event_status` from the official
schedule plus typed evidence - never from the calendar - and is stored with its
source, confidence and reasons.

**Weaker evidence never overwrites stronger evidence.** When reporting
contradicts a live official listing, the official status stands and the report
is preserved as a conflict (`event_reconcile.py`). Nothing is resolved for the
user.

**Intent gates results.** Every article is classified PREVIEW / PREDICTION /
ANNOUNCEMENT / RESULT / POST_FIGHT / INTERVIEW / RUMOR. `may_establish_result`
is structurally False for PREVIEW and PREDICTION, and a result also needs a
source trusted to report outcomes.

**Cards are widgets, not links.** Streamlit's page router rewrites in-app
anchors and drops their query string, and forces absolute URLs into a new tab,
so a card built as `<a href="?story=12">` silently loses the id. The grid is
`st.container(horizontal=True, wrap=True)` with a real button per card: it
reflows 4/3/2/1 like a CSS grid and navigation actually works. Selections are
also held in session state because `st.switch_page` does not carry query
parameters across.

**Time is stored in UTC and converted only for display.** `zoneinfo` applies
the right DST offset per instant. Publication dates that are impossible or in
the future fall back to collection time at ingest, so a broken feed cannot pin
an item to the top of the feed.

**X access is assumed to be minimal.** Recent search only (7 days). No
full-archive assumptions. Hard per-run budgets, a query cache, and rate-limit
pauses driven by the reset the API reports.

**Generation is cached by content.** The cache key is a fingerprint of the
story plus the exact set of articles/posts behind it, so identical material is
never sent to a provider twice, and new material invalidates it automatically.

**Grounding is checked, not assumed.** `ai/grounding.py` verifies that quoted
spans and precise figures in generated text appear in the collected sources,
and the UI shows warnings when they do not.

---

## 5. Commands

```bash
# check the collected data for the faults that cause wrong reporting
python scripts/validate_production_data.py [--check-urls] [--json]

# setup (Windows)
py -m venv .venv && .venv\Scripts\activate && pip install -r requirements.txt
copy .env.example .env

# run
streamlit run app.py

# collect without the UI
python scripts/collect.py [--loop 20] [--source ufc_com] [--demo|--clear-demo]

# tests
python -m pytest
python -m pytest tests/test_verification.py -v
```

---

## 6. Environment variables

`AI_PROVIDER`, `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL`, `OPENAI_API_KEY`,
`OPENAI_MODEL`, `OPENAI_BASE_URL`, `AI_MAX_CONTEXT_CHARS`, `X_BEARER_TOKEN`,
`X_MAX_SEARCHES_PER_RUN`, `X_MAX_TIMELINES_PER_RUN`, `X_MAX_RESULTS_PER_QUERY`,
`X_QUERY_CACHE_MINUTES`, `UFC_RADAR_DB`, `DATABASE_URL` (recognised, reported, not implemented),
`HTTP_TIMEOUT_SECONDS`,
`HTTP_USER_AGENT`, `UFC_RADAR_DEBUG`. See `.env.example` for descriptions.

User *preferences* (thresholds, enabled sources, watchlists, monitored
accounts, sort order) live in the `settings` table and are edited in the UI -
never in `.env`.

---

## 7. Coding conventions

* Python 3.11+, standard library first, `from __future__ import annotations`.
* Type hints on public functions; dataclasses for structured results.
* Docstrings explain **why**, not what. Comments earn their place.
* Collectors, processors and providers **return result objects, never raise**
  for expected failures. Only programming errors propagate.
* Repositories own all SQL. No SQL in `ui/`, `processors/` or `collectors/`.
* `ui/` renders; it never analyses. Analysis belongs in `processors/`.
* Every new user-visible number must ship with its explanation.
* New settings: add to `DEFAULT_SETTINGS` in `database/seed_data.py` and to the
  Settings page.

---

## 8. External integrations

| Integration | Status | Notes |
| --- | --- | --- |
| RSS/Atom feeds | working | 14 built-in sources, user-extendable |
| UFC.com rankings | working | HTML parse, structured + generic fallback |
| UFC.com events | working | HTML parse, card + fallback link scan |
| Article extraction | working | trafilatura, short extract only, capped per run |
| X API v2 | **needs `X_BEARER_TOKEN`** | recent search + timelines; disabled and labelled without it |
| Anthropic | **needs `ANTHROPIC_API_KEY`** | optional; template mode otherwise |
| OpenAI-compatible | **needs `OPENAI_API_KEY`** | optional; template mode otherwise |

---

## 9. Known limitations

* **Feed URLs can change.** Each source carries fallback URLs and records which
  one worked; failures are visible on Source health. Verify with **Test**.
* **Entity matching is heuristic.** Full names, aliases and nicknames always
  match; a bare surname only matches when it is unambiguous (or when a known
  fighter appears on the other side of a "vs."). Fighters outside the built-in
  name list are learned as they are mentioned.
* **Category rules are keyword-based**, so an unusual headline can be
  mis-categorised. Categories affect grouping and feed order, never status.
* **X recent search covers 7 days**, so older posts are invisible unless
  already collected.
* **Ranking changes need two snapshots.** The first collection has nothing to
  compare against.
* **Fight cards are built from reporting**, so a card is only as complete as
  what has been collected; each bout carries a confidence value.
* **No scheduler.** Collection is manual (button or `scripts/collect.py
  --loop`). Use Task Scheduler on Windows if you want it automatic.
* **The sandbox this was built in had no outbound internet access**, so live
  feeds could not be fetched during development. Collection, parsing, failure
  handling and the whole pipeline are tested against recorded fixtures, and the
  first real run happens on the user's machine - Source health reports exactly
  what each source did.

---

## 10. Current state

All specified subsystems are implemented and tested: collection, story
grouping, verification (7 statuses), rumour handling, developing timelines,
trending, relevance, fight-card changes (official vs reported), rankings +
change detection, canonical event identity, the event lifecycle and
reconciliation, result safety, X integration (disabled without a token), the AI
layer with template fallback, the full dashboard with research mode, TikTok
Studio, check-before-reporting, watchlists, search, filters, source health,
settings, backups, data corrections and demo mode.

**283 pytest tests pass.** `scripts/validate_production_data.py` reports 0
errors and 0 warnings. The app was driven end to end in Chromium: all 12 pages
render with no exceptions and no console errors, and the card grid was measured
reflowing 4 → 3 → 2 → 1 columns between 1800px and 430px with no horizontal
overflow.

A fresh-database acceptance run against the recorded fixtures produced
`10/14 sources OK` with the four failures isolated, statuses CONFIRMED /
REPORTED / CONTESTED / RUMOR, two events both UPCOMING with their reasons and
no duplicates, rankings under "Meta UFC Rankings", and official bouts with
card changes recorded.

See `AUDIT.md` for the faults found in the previous version and `PROGRESS.md`
for the detailed status.
