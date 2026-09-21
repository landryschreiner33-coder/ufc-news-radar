# UFC News Radar

A UFC/MMA news-intelligence dashboard for a TikTok creator.

It answers one question, fast:

> **What important UFC news is happening right now, what is actually known,
> what is only being reported or rumoured, what changed, and what do I need to
> verify before I report it?**

It collects real MMA news from real sources, groups the coverage of one
development into a single **story**, works out how well that story is actually
sourced, keeps rumours clearly labelled as rumours, shows you every original
link, and helps you turn the research into a TikTok without inventing anything.

---

## Table of contents

1. [What it does](#what-it-does)
2. [Install on Windows](#install-on-windows-step-by-step)
3. [Run it](#run-it)
4. [Using the dashboard](#using-the-dashboard)
5. [Environment variables](#environment-variables)
6. [AI API setup (optional)](#ai-api-setup-optional)
7. [X / Twitter API setup (optional)](#x--twitter-api-setup-optional)
8. [Adding monitored X accounts](#adding-monitored-x-accounts)
9. [Adding news sources](#adding-news-sources)
10. [Running the tests](#running-the-tests)
11. [The database, backups and data checks](#the-database)
12. [Running it online (Streamlit Cloud)](#running-it-online-streamlit-community-cloud)
13. [Updating](#updating)
14. [Troubleshooting](#troubleshooting)
15. [What this app will not do](#what-this-app-will-not-do)

---

## What it does

* **Collects** from UFC.com, ESPN MMA, MMA Fighting, MMA Junkie, Sherdog,
  Google News and several other MMA outlets (all configurable). Each source
  runs on its own - one broken feed never stops the app.
* **Groups** articles into stories. UFC announces a fight, ESPN writes it up,
  MMA Fighting writes it up, a fighter posts about it - that is **one story**
  with several sources attached, not four items in a feed.
* **Verifies**. Every story gets a status:
  🟢 CONFIRMED · 🟡 REPORTED · 🟠 DEVELOPING · 🔴 RUMOR · ⚪ UNVERIFIED ·
  🔵 FIGHTER CLAIM - with the reasons shown. Copies of one report never count
  as independent confirmation, and repetition by low-quality accounts never
  upgrades a rumour.
* **Tracks changes**: fight-card changes (new fight, cancellation,
  replacement, opponent change, main-event change, weight-class change) and
  official ranking movements, each with before / after / reason / source /
  timestamp.
* **Researches**: a detail page per story with what we know, what is claimed,
  what is confirmed, what is *not* confirmed, conflicting information, a
  chronological timeline, every source link and any linked X posts.
* **Checks before you report**: a briefing that separates confirmed facts from
  reported claims, lists what is missing, and warns about the specific wording
  mistakes that story invites.
* **Helps you make the TikTok**: 30s and 60s scripts, hooks, key facts, a story
  angle and the questions viewers will ask - built from the collected sources,
  with the source list kept alongside.

---

## Install on Windows (step by step)

You need **Python 3.11 or newer**. Nothing else.

### 1. Install Python

1. Go to <https://www.python.org/downloads/windows/> and download the latest
   Python 3.11+ installer.
2. Run it and **tick "Add python.exe to PATH"** on the first screen.
3. Finish the install.

Check it worked - open **Command Prompt** (press `Win`, type `cmd`, `Enter`):

```bat
py --version
```

You should see `Python 3.11.x` or higher.

### 2. Get the project onto your PC

If you have Git:

```bat
cd %USERPROFILE%\Documents
git clone https://github.com/landryschreiner33-coder/ufc-news-radar.git
cd ufc-news-radar
```

No Git? Download the ZIP from GitHub, extract it to
`Documents\ufc-news-radar`, then:

```bat
cd %USERPROFILE%\Documents\ufc-news-radar
```

### 3. Create a virtual environment

A virtual environment keeps this project's packages separate from the rest of
your PC.

```bat
py -m venv .venv
.venv\Scripts\activate
```

Your prompt now starts with `(.venv)`. **Do this every time you open a new
Command Prompt for this project.**

> **PowerShell users:** if `.\.venv\Scripts\Activate.ps1` is blocked, run
> `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once, or just use
> Command Prompt instead.

### 4. Install the dependencies

```bat
pip install -r requirements.txt
```

This takes a couple of minutes the first time.

### 5. Create your settings file

```bat
copy .env.example .env
```

You do **not** need to put any keys in it to start. The app runs without them
and clearly labels the features that need one.

---

## Run it

With `(.venv)` active, in the project folder:

```bat
streamlit run app.py
```

Your browser opens at <http://localhost:8501>. If it does not open by itself,
paste that address into **Chrome**.

Then:

1. Click **🔄 Refresh now** in the sidebar. The first collection takes 10-60
   seconds.
2. Stories appear on the dashboard.
3. Click **READ MORE** on any story to open Research mode.

To stop the app, go back to the Command Prompt and press `Ctrl` + `C`.

### Collecting without the browser

```bat
python scripts\collect.py                 :: one collection run
python scripts\collect.py --loop 20       :: collect every 20 minutes
python scripts\collect.py --source ufc_com espn_mma
python scripts\collect.py --demo          :: load the clearly-marked demo data
python scripts\collect.py --clear-demo    :: remove it again
```

### Trying it with no internet (demo mode)

**Settings → Demo data → Load demo data** fills the dashboard with a handful of
**fictional** stories so you can click around. Every demo headline starts with
`[DEMO]`, every demo fighter/event/outlet name is invented, and a banner stays
on screen while demo data is loaded. It is never mixed into real news, and
**Remove demo data** deletes it without touching anything you collected.

---

## Using the dashboard

The menu on the left has four sections you will use constantly:

| Page | What it is for |
| --- | --- |
| 📰 **Dashboard** | Everything happening now, grouped by how solid it is |
| 🐦 **X / Twitter Radar** | Posts grouped by who posted them, badged as signals |
| 🔎 **Research & Verification** | One story in depth: what is known, claimed, missing |
| 🎬 **TikTok Studio** | Script on the left, the research backing it on the right |

Under **Reference** and **Tools**: Events, Fighters, Rankings, Fight card
changes, Watchlists, Search, Source health and Settings.

The dashboard itself is ordered the way you need it:

| Section | What it is for |
| --- | --- |
| 🔥 **BREAKING** | High-relevance developments from the last few hours |
| ⚡ **IMPORTANT** | Ranked by the automated relevance score |
| 🔴 **RUMORS & REPORTS** | Unconfirmed claims, showing who claimed it and whether UFC confirmed |
| ⚡ **DEVELOPING** | Stories still moving - open for the timeline |
| 📈 **TRENDING** | Unusual *measured* activity, with the evidence listed |
| 📅 **UPCOMING EVENTS** | With a live countdown to the first bout |
| 📰 **LATEST** | Everything else, newest first |

### What the status badges mean

Every story carries an icon **and** the word, never just a colour:

| Badge | Meaning |
| --- | --- |
| 🟢 CONFIRMED | Confirmed by UFC or another official party in the collected sources |
| 🟡 REPORTED | Credible outlets report it; not officially confirmed |
| 🟠 DEVELOPING | Still moving; details may change |
| 🔴 RUMOR | Speculative, or a single low-reliability origin |
| ⚪ UNVERIFIED | Not enough evidence collected yet |
| 🔵 FIGHTER CLAIM | A fighter, coach or team is the one saying it |
| 🟣 CONTESTED | Reliable sources disagree. Both versions are shown; the app does not pick one |

### Event status

Events are **never** called finished just because their date passed:

| Status | Meaning |
| --- | --- |
| 📅 UPCOMING | The collected start time is still ahead |
| 🔴 LIVE NOW | Inside the scheduled window |
| ✅ COMPLETED | Reliable evidence was collected that it finished |
| 🚫 CANCELLED / ⏸ POSTPONED | Officially, per the collected sources |
| ⚪ STATUS UNKNOWN | The window has passed but nothing confirms it took place |

That last one is deliberate. The app would rather say "I do not know" than
tell you a card happened when nothing has confirmed it. If UFC still lists an
event while a journalist reports a cancellation, the official status stands and
the report is shown next to it as a **conflict** - you decide.

Each event page separates the **official fight card** (listed by UFC) from
bouts that are only **reported**, **rumoured** or **cancelled**. A rumoured
matchup never appears as an official bout.

Two numbers appear on every story. Both are tools, not truth claims:

* **Relevance** decides feed order only.
* **Source support** is an *automated source-support assessment* - how much the
  collected sources back the story up. **It is not a probability that the claim
  is true**, and the app always shows the reasoning behind it.

---

## Environment variables

All secrets live in the `.env` file in the project folder. They are read from
the environment only - never written to the database, never shown in the UI,
never logged. `.env` is in `.gitignore`, so it is not committed.

The lookup order is: a real environment variable, then `.env`, then
`st.secrets` (which is how Streamlit Community Cloud supplies them). You never
need more than one of the three.

| Variable | Default | What it does |
| --- | --- | --- |
| `AI_PROVIDER` | `none` | `none`, `anthropic` or `openai` |
| `ANTHROPIC_API_KEY` | empty | Anthropic key (if you use Claude) |
| `ANTHROPIC_MODEL` | `claude-sonnet-5` | Model name |
| `OPENAI_API_KEY` | empty | OpenAI key (if you use OpenAI) |
| `OPENAI_MODEL` | `gpt-4o-mini` | Model name |
| `OPENAI_BASE_URL` | empty | For OpenAI-compatible providers |
| `AI_MAX_CONTEXT_CHARS` | `12000` | Max source text sent per request |
| `X_BEARER_TOKEN` | empty | X API App-only Bearer Token |
| `X_MAX_SEARCHES_PER_RUN` | `3` | Protects your X quota |
| `X_MAX_TIMELINES_PER_RUN` | `5` | Protects your X quota |
| `X_MAX_RESULTS_PER_QUERY` | `25` | Posts requested per query |
| `X_QUERY_CACHE_MINUTES` | `30` | Do not repeat an identical query sooner |
| `UFC_RADAR_DB` | `data/ufc_news_radar.db` | Where the SQLite file lives |
| `DATABASE_URL` | empty | Use PostgreSQL instead of SQLite (see *Running it online*) |
| `HTTP_TIMEOUT_SECONDS` | `20` | Per-request timeout |
| `HTTP_USER_AGENT` | `UFCNewsRadar/1.0 ...` | Sent with every request |
| `UFC_RADAR_DEBUG` | `0` | `1` for verbose logging |

Edit `.env` with Notepad, save, then restart the app.

---

## AI API setup (optional)

**The app is fully usable without an AI key.** Without one it runs in
**template mode**: summaries, scripts, hooks, angles and reporting checks are
assembled from your collected sources by built-in templates, and every panel
says so.

To use Claude:

1. Create a key at <https://console.anthropic.com/>.
2. Open `.env` and set:

   ```
   AI_PROVIDER=anthropic
   ANTHROPIC_API_KEY=sk-ant-your-key-here
   ```

3. Restart the app (`Ctrl` + `C`, then `streamlit run app.py`).
4. Check **Settings → AI provider** - it should say the provider is configured.

For OpenAI, set `AI_PROVIDER=openai` and `OPENAI_API_KEY=...` instead.

Whatever provider you use, the prompts contain **only** the collected source
material, the model is instructed never to invent facts or quotes and to
preserve disagreement between sources, and the output is checked: quotes and
figures that do not appear in your sources are flagged on screen. Generated
text is cached against the exact source material, so the same story is never
sent twice.

---

## X / Twitter API setup (optional)

Without a token, every X panel shows **X MONITORING - NOT CONFIGURED** and the
rest of the app works normally. The app never scrapes x.com and never
simulates X data.

1. Sign in at <https://developer.x.com/> and create a Project + App.
2. In the app's **Keys and tokens**, copy the **Bearer Token** (App-only
   OAuth 2.0).
3. Put it in `.env`:

   ```
   X_BEARER_TOKEN=paste-your-bearer-token-here
   ```

4. Restart the app, then open **X monitoring** and press **Collect from X now**.

**What the app uses, and what it assumes:**

* `GET /2/tweets/search/recent` - **the last 7 days only.** That is an X API
  limit, not an app setting. Full-archive search needs a different access
  level, so nothing in this app assumes you have it.
* `GET /2/users/by/username/:username` and `GET /2/users/:id/tweets`.

Access to these endpoints depends on your X access tier. If your tier does not
include them, the app shows the exact error X returned (for example HTTP 403)
instead of pretending it worked.

**Your quota is protected by default**: at most 3 searches and 5 timelines per
run, identical queries are not repeated within 30 minutes, and a rate limit
pauses that query until the reset time X reports. All four limits are
configurable in `.env`.

---

## Adding monitored X accounts

**X monitoring → Monitored accounts**:

1. Type the username without the `@`.
2. Pick the **account type** - this decides how much weight a post carries:
   `OFFICIAL`, `TRUSTED_JOURNALIST`, `ESTABLISHED_REPORTER`, `FIGHTER`,
   `COACH_TEAM`, `PROMOTER`, `INSIDER`, `UNKNOWN`, `FAN_ACCOUNT`.
3. Pick a category (official, journalist, reporter, insider, fighter,
   coach/team, promoter).
4. Press **Add**.

Use the **On/Off** button to pause an account and **Remove** to delete it. A
starter list is seeded for you and every row is editable.

Note: a blue check or a big follower count never counts as evidence in this
app. Only the account type you assign does.

---

## Adding news sources

**Source health → Add a source**: any RSS or Atom feed works.

1. **Name** - what you want it called.
2. **Feed URL** - the full URL of the feed.
3. **Source type** and **reliability** - how much weight its reports carry.
4. Press **Add**, then **Test** to fetch it once and see the result.

To turn a built-in source off, press **Disable** on its row (or use
**Settings → Sources**, where you can also change reliability weights).

The collector architecture is modular: a new *kind* of source (not just a new
feed) means writing one class with `collect()`, `normalize()` and `validate()`
in `collectors/` and registering it in `collectors/registry.py`. Nothing else
in the app has to change.

---

## Running the tests

```bat
.venv\Scripts\activate
python -m pytest
```

You should see all tests passing. Useful variations:

```bat
python -m pytest -v                    :: show each test name
python -m pytest tests\test_verification.py   :: one file
python -m pytest -k rumor              :: tests matching a word
```

The tests use their own temporary databases and never touch your real data or
the network.

### Testing against PostgreSQL

The same suite runs against PostgreSQL - the same tests, the same assertions,
the other backend. That is what makes `DATABASE_URL` a supported option rather
than a hopeful label:

```bash
createdb ufc_radar_test
UFC_RADAR_TEST_DATABASE_URL=postgresql://user:pass@localhost/ufc_radar_test \
    python -m pytest
```

The run needs a database of its own: it drops and recreates the schema between
tests. The upgrade-path tests are skipped there, because a fresh PostgreSQL
database is created at the current schema version and there is no older one in
the wild to upgrade.

---

## The database

* **SQLite** by default: a single file, `data\ufc_news_radar.db` (change with
  `UFC_RADAR_DB`). Zero setup.
* **PostgreSQL** when `DATABASE_URL` is set - the option for permanent history
  on a host whose filesystem is temporary. See *Running it online* above.
* Created automatically on first run; schema upgrades apply on start.
* Browse the row counts in **Settings → Database**.
* To start completely fresh on SQLite, close the app and delete the file - it
  is rebuilt on the next start. You will lose collected history.
* One SQL dialect is written throughout the code and translated for PostgreSQL
  in `database/backends.py`, so there is never a second copy of a query for
  the two to drift apart.

### Backups

**Settings → Database → Backup & restore.** On SQLite, Download gives you one
file containing everything collected; restoring validates the file first and
keeps your current database alongside the restored one rather than destroying
it. On PostgreSQL, backups are your provider's (managed snapshots or
`pg_dump`) and the page says so rather than offering an export that would
contain nothing.

### Checking your data

```bat
python scripts\validate_production_data.py
```

Reports duplicate events, fighters or stories; events marked completed before
they start and events left upcoming long after; predictions treated as
results; stories with no sources; impossible or future-dated timestamps;
ranking problems; official/reported contradictions on a bout; source counts
that cannot be true (more independent sources than sources); articles with no
classified intent; malformed links; legacy schema columns; and settings that
nothing reads. Add `--check-urls` to test the stored article links too (needs
internet, slower).

Exit code 0 means clean or warnings only; 1 means errors were found, so it
works in a scheduled job.


### Fixing data by hand

**Settings → Data corrections** can merge duplicate events or fighters,
reassign or recategorise a story, and reclassify a source. These only merge or
relabel what was collected - they never invent anything - and every change is
recorded with a reason in the correction history below the form.

---

## Running it online (Streamlit Community Cloud)

Optional - the app is designed to run on your own PC, and that is the setup
with the fewest surprises. If you do want it online:

1. Push the repository to GitHub.
2. At <https://share.streamlit.io> create an app pointing at `app.py`.
3. Put any keys in the app's **Secrets** box (Settings → Secrets), one per
   line, exactly as they appear in `.env`:

   ```toml
   ANTHROPIC_API_KEY = "sk-..."
   X_BEARER_TOKEN = "AAAA..."
   AI_PROVIDER = "anthropic"
   ```

   You do **not** need a `.env` file there - the app reads `st.secrets` too.

**Read this before you rely on it.** Streamlit Community Cloud gives an app a
*temporary* filesystem: it is wiped on every redeploy and whenever the
container restarts. A SQLite file there is real working storage, but it is not
history - the news the app collects will not build up over time.

### Permanent storage (PostgreSQL)

For history that lasts on a host like that, give the app a database instead of
a file. Add one line to the app's **Secrets** box:

```toml
DATABASE_URL = "postgresql://user:password@host:5432/ufc_news_radar"
```

That is the whole setup. The app creates its schema, runs its migrations and
uses PostgreSQL for everything; nothing else changes. Any managed PostgreSQL
works - Neon, Supabase, Railway and Amazon RDS all have a free or near-free
tier that is more than this app needs.

Two deliberate behaviours:

* **It will not quietly fall back.** If `DATABASE_URL` is set but the driver
  is missing, the app refuses to start and says why, rather than writing to a
  local file you were not expecting.
* **Backups change hands.** With PostgreSQL, backups are your provider's
  (managed snapshots or `pg_dump`). Settings says so instead of offering a
  file export that would contain nothing.

Both backends run the same test suite - see *Running the tests* below.

Without `DATABASE_URL`, the options in order of simplicity are: run it on your
PC (the default, nothing to set up); point `UFC_RADAR_DB` at a disk that
survives restarts (a VPS disk, a container volume); or take a backup
(Settings → Database) before each redeploy and restore it afterwards.

### Collecting without pressing Refresh

The dashboard *reads* news. Something else has to collect it, because a
Streamlit page only runs while somebody has it open, and a collection takes a
minute or two.

```bat
python scripts\scheduler.py --minutes 20
```

That keeps collecting whether or not the app is open. Point it at the same
database as the app (`UFC_RADAR_DB`, or `DATABASE_URL`) and the dashboard
simply reads what it wrote. Only one collection runs at a time, wherever it
was started from - the lock lives in the database.

For something that survives a reboot:

* **Windows**: Task Scheduler → *Start a program* →
  `<project>\.venv\Scripts\python.exe scripts\scheduler.py --once`, repeating
  every 20 minutes.
* **Linux/macOS**: `*/20 * * * * cd /path && .venv/bin/python
  scripts/scheduler.py --once`, or a systemd service without `--once`.
* **A container**: run it as a second service beside the web app.

There is also a background thread inside the app (Settings → *Collect in the
background while the app is open*). It is genuinely useful where a second
process is impossible, and it is honest about its limit: it stops when the app
stops. It is not a replacement for the above.


---

## Updating

```bat
cd %USERPROFILE%\Documents\ufc-news-radar
git pull
.venv\Scripts\activate
pip install -r requirements.txt
streamlit run app.py
```

Your database and `.env` are left alone.

---

## Troubleshooting

**`'python' is not recognized`**
Use `py` instead of `python`, or reinstall Python with "Add python.exe to
PATH" ticked.

**`streamlit: command not found` / `'streamlit' is not recognized`**
The virtual environment is not active. Run `.venv\Scripts\activate` first (the
prompt should start with `(.venv)`).

**PowerShell blocks `activate`**
Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, or use Command
Prompt.

**Port 8501 is already in use**
`streamlit run app.py --server.port 8502`

**The dashboard is empty**
Press **🔄 Refresh now**. If it stays empty, open **Source health** - every
source shows its status and its last error.

**A source shows ❌**
The message says why (timeout, HTTP 403, parse error, ...). Press **Test** on
that row to try it again immediately. One failing source never affects the
others. If a site changed its feed URL, edit it in Settings or add the new URL
as a new source.

**Everything shows ❌ and the errors mention proxy/connection**
Your network is blocking the requests - common on work or school networks, or
with a VPN or strict firewall. Try a normal home connection.

**X monitoring says NOT CONFIGURED**
That is expected until you add `X_BEARER_TOKEN` to `.env` and restart.

**X returns 401 or 403**
The token is wrong, or your access tier does not include that endpoint. Check
**X monitoring → API usage** for the exact response.

**X returns 429**
You hit the rate limit. The app pauses that query until the reset time X
reported. Lower `X_MAX_SEARCHES_PER_RUN` / `X_MAX_TIMELINES_PER_RUN` in `.env`.

**Scripts say "Template mode"**
No AI key is configured. That is a supported mode, not an error - see
[AI API setup](#ai-api-setup-optional).

**"database is locked"**
Two copies of the app are running. Close the extra Command Prompt.

**Stories look wrongly grouped**
Tune **Settings → Story grouping**: raise the similarity thresholds to group
less, lower them to group more. Changes apply on the next collection run.

---

## What this app will not do

* It does not invent facts, quotes, sources, X posts, statistics, rankings,
  injuries or event details. Anything it shows came from a source it collected,
  and the link is always there.
* It does not treat repetition as confirmation, and it does not resolve
  disagreements between sources - it shows you both.
* It does not present its scores as probabilities of truth.
* It does not scrape X or work around API access restrictions.
* It does not copy whole articles: it stores a short extract for analysis,
  shows a short excerpt, and links to the original.

---

## Project layout

```
app.py              Streamlit entry point, st.navigation menu
collectors/         source adapters (collect / normalize / validate) + runner,
                    UFC events, official fight cards, rankings
processors/         entities, categories, INTENT/result safety, similarity,
                    clustering, verification, support, relevance, trending,
                    developing, fight cards, rankings,
                    event identity + lifecycle + reconciliation
social/             X API client, query planning, monitored accounts
ai/                 provider abstraction, grounded prompts, templates, grounding
database/           schema, migrations, repositories, seed + demo data,
                    persistence (backup/restore), corrections
models/             shared statuses, event lifecycle, intents, source types
ui/                 theme, cards, images, nav registry, pages
utils/              config, HTTP, text/URL, time, logging, paths
tests/              pytest suite + fixtures
scripts/collect.py                   command-line collection
scripts/validate_production_data.py  data health check
```

See `CLAUDE.md` for the architecture and technical decisions, `PROGRESS.md`
for the current state and what is left, and `AUDIT.md` for the faults that were
found in the previous version, what caused them and how each one is now tested
against.
