# PROGRESS.md

Living status of UFC News Radar. Updated as work lands.

**Last updated:** 2026-09-18
**Test status:** ✅ 165 passed (`python -m pytest`)
**App status:** ✅ starts and runs; every page verified in Chromium, console clean

---

## 1. Completed functionality

### Foundation
- [x] Project structure (`collectors/ processors/ social/ ai/ database/ models/ ui/ utils/ tests/ scripts/`)
- [x] Config from environment only, `.env.example`, secrets never stored or logged
- [x] Resilient HTTP client: timeouts, retries with backoff, 429 handling, conditional GETs
- [x] Text/URL normalisation, UTC time handling, logging

### Database
- [x] SQLite schema with every specified table: `sources, articles, stories,
      story_sources, story_updates, social_posts, story_social_posts, fighters,
      events, fight_card_items, fight_card_changes, rankings, ranking_changes,
      summaries, watchlists, settings, source_classifications,
      monitored_social_accounts, collection_runs, x_query_cache, ai_cache,
      migrations`
- [x] Indexes on every lookup/sort column; consistent ISO-8601 UTC timestamps
- [x] Migration mechanism (`user_version` + `MIGRATIONS`), PostgreSQL-friendly types
- [x] Repositories split per domain area; idempotent seeding

### Collection
- [x] `collect() / normalize() / validate()` adapter interface with per-source failure isolation
- [x] RSS adapter with fallback URLs and last-known-good tracking
- [x] 14 built-in sources (UFC.com, ESPN, MMA Fighting, MMA Junkie, Sherdog,
      Google News ×2, MMA Mania, Bloody Elbow, Fightful, BJPenn, LowKick,
      The Mac Life, Reddit r/MMA) - all user-editable, new ones addable in the UI
- [x] Aggregator items re-attributed to the real publisher
- [x] UFC.com rankings adapter (reads the published system label; generic fallback parser)
- [x] UFC.com events adapter
- [x] Short-extract article text fetch (capped per run, never full articles)
- [x] Source health: status, last success, last error + kind, counts, resolved URL
- [x] Collection run history; CLI collector (`scripts/collect.py`)

### Processing
- [x] Fighter/event/matchup/weight-class extraction with ambiguity guards
- [x] Rule-based categorisation + hedging, official-language, attribution and denial signals
- [x] URL duplicate detection, TF-IDF + token similarity, story clustering with
      gates against false grouping, denial-joins-its-story exception
- [x] Verification engine: 6 statuses with reasons; independence accounting;
      derivative detection; conflict preservation
- [x] Automated source-support assessment (explicitly not a probability)
- [x] Relevance scoring with a visible per-component breakdown
- [x] Trending from measured activity only, with evidence listed
- [x] Developing stories: chronological timelines, status-change entries, dedupe
- [x] Fight-card tracking: new fight, cancellation, replacement, opponent change,
      main/co-main change, title-fight change, weight-class change - each with
      before/after/reason/status/source/timestamp, logged once per change
- [x] Ranking snapshots + change detection (up, down, new entry, exit, champion changes)

### X / Twitter
- [x] Official API v2 read endpoints only (recent search 7 days, user lookup, timelines)
- [x] Graceful "NOT CONFIGURED" state; nothing simulated, no scraping
- [x] Per-run budgets, query caching, rate-limit pauses using the API's reset time
- [x] Monitored account list with editable account types; classification drives weight
- [x] Posts treated as signals: linked to stories only on real overlap

### AI
- [x] Provider abstraction (Anthropic, OpenAI-compatible, none) selected by env var
- [x] Grounded context + prompts built only from collected sources
- [x] Template mode that fully works with no API key
- [x] Grounding checker for quotes and figures
- [x] Content-fingerprint caching of all generated text
- [x] Full service API: `summarize_story, classify_story, detect_duplicates,
      group_story, assess_source_support, detect_rumor, calculate_relevance,
      identify_fighters, identify_event, analyze_social_post,
      generate_reporting_check, generate_tiktok_script, generate_tiktok_hooks,
      generate_story_angle, generate_questions`

### Interface
- [x] Dark dashboard: header metrics + 🔥 BREAKING, ⚡ IMPORTANT,
      🔴 RUMORS & REPORTS, ⚡ DEVELOPING, 📈 TRENDING, 📰 LATEST
- [x] Story cards with status, summary, sources, independence, fighters, event,
      category, relevance bar, X activity, READ MORE
- [x] Research mode with all specified sections + source-support panel,
      status explanation and relevance breakdown
- [x] CHECK BEFORE REPORTING on every story
- [x] TikTok tools: 30s/60s scripts, hooks, key facts, angle, questions, sources used
- [x] Fighter, event, rankings, fight-card-change, X monitoring, watchlists,
      search, source health and settings pages
- [x] 14 filters, 5 sort orders, minimum-relevance threshold, feed search
- [x] Demo mode: fictional, labelled, one-click load/remove

### Quality
- [x] 165 tests across database, collectors, failures, normalisation, dedupe,
      clustering, entities, classification, rumours, status transitions,
      relevance/support/trending, rankings, X responses, malformed data,
      developing updates, watchlists, fight cards, demo data and the AI layer
- [x] Whole app driven in Chromium (every page, tabs, navigation, forms)
- [x] README (Windows-exact), CLAUDE.md, PROGRESS.md, .env.example

---

## 2. Blocked features (need something outside the app)

| Feature | Blocked by | State |
| --- | --- | --- |
| X monitoring (search, timelines, engagement) | `X_BEARER_TOKEN` from an X developer account | Integration built and tested against recorded API responses; shows **X MONITORING - NOT CONFIGURED** until a token exists. Setup steps in README. |
| AI-written summaries/scripts | `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` | Providers implemented; app runs in labelled template mode meanwhile. Setup steps in README. |
| Live feed verification | The build sandbox had no outbound internet (egress policy blocked every news domain) | All collection/parsing/failure paths are tested against recorded fixtures. The first live run happens on your machine; Source health reports exactly what each source does. |

---

## 3. Known bugs

None outstanding.

Fixed during the build (all now covered by tests):

- `canonical_url` treated `http://` and `https://` copies of one article as
  different articles, and turned non-URL strings into `https:///...`.
- `execute()` returned a stale `lastrowid` for `INSERT OR IGNORE` statements
  that inserted nothing, so repeat ranking snapshots reported phantom writes.
- A story could reach DEVELOPING with no credible source behind it, which let
  low-quality accounts repeating each other lift a rumour.
- "Jones vs. Aspinall" headlines produced no matchup, because a bare surname
  never resolved even with a known fighter on the other side.
- Fight-card changes were logged once per reporting outlet instead of once per
  change.
- A denial ("X denies he is out") started its own story instead of joining the
  report it disputes.
- Template scripts repeated the headline twice and could lose their closing
  line to length trimming.

---

## 4. Test status

```
python -m pytest        ->  165 passed
```

| Area | File |
| --- | --- |
| Database, settings, source health | `tests/test_database.py` |
| Collectors, fallbacks, failures, malformed feeds | `tests/test_collectors.py` |
| URL/title/time normalisation, extraction limits | `tests/test_normalization.py` |
| Fighter/event/matchup matching | `tests/test_entities.py` |
| Categories, hedging, attribution, denials | `tests/test_categorize.py` |
| Duplicates and story grouping | `tests/test_clustering.py` |
| Statuses, independence, conflicts, transitions | `tests/test_verification.py` |
| Relevance, support, trending | `tests/test_scoring.py` |
| Ranking snapshots and changes | `tests/test_rankings.py` |
| X API responses, rate limits, quota rules | `tests/test_x_api.py` |
| End-to-end pipeline, idempotency, malformed data | `tests/test_pipeline.py` |
| AI templates, grounding, caching, fallback | `tests/test_ai.py` |
| Watchlists, classifications, accounts, demo data | `tests/test_watchlists_settings.py` |
| Fight-card detection and change log | `tests/test_fight_cards.py` |
| Developing stories and timelines | `tests/test_developing.py` |

---

## 5. Remaining / next work

Nothing in the specification is unimplemented. Sensible next steps:

1. **Run it live on Windows** and check Source health - confirm each feed URL
   and fix any that moved (editable in Settings).
2. **Add your X token** if you want X monitoring, then tune the monitored
   account list to the reporters you actually trust.
3. **Add an AI key** if you want model-written scripts instead of template mode.
4. **Automatic collection**: Windows Task Scheduler running
   `python scripts/collect.py` on a schedule.
5. Possible later work (not specified, not started):
   - PostgreSQL migration (schema is already compatible)
   - push/desktop notifications for watchlist hits
   - per-source article-count charts over time
   - export a research page to PDF/markdown for offline scripting

---

## 6. Working agreements

- Update this file whenever a subsystem lands or a bug is found/fixed.
- Add a test with every bug fix.
- Never weaken a verification rule to make a test pass.
- Keep `CLAUDE.md` in step with architectural decisions.
