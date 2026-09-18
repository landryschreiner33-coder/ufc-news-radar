-- ============================================================================
-- UFC News Radar - SQLite schema (schema_version 1)
--
-- Conventions (kept PostgreSQL-friendly on purpose):
--   * every timestamp is an ISO-8601 UTC string: YYYY-MM-DDTHH:MM:SSZ
--   * booleans are INTEGER 0/1
--   * list/dict values are JSON TEXT
--   * no SQLite-only column types, so a later port to PostgreSQL is mostly
--     a matter of INTEGER PRIMARY KEY -> BIGSERIAL and TEXT -> TIMESTAMPTZ
-- ============================================================================

-- ---------------------------------------------------------------- sources --
CREATE TABLE IF NOT EXISTS sources (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    key                  TEXT    NOT NULL UNIQUE,
    name                 TEXT    NOT NULL,
    adapter              TEXT    NOT NULL DEFAULT 'rss',
    feed_url             TEXT,
    fallback_urls        TEXT,             -- JSON array of alternate feed URLs
    homepage             TEXT,
    domain               TEXT,
    source_type          TEXT    NOT NULL DEFAULT 'UNKNOWN',
    reliability_weight   REAL    NOT NULL DEFAULT 0.5,
    independence_group   TEXT,             -- publisher family (shared ownership)
    priority             INTEGER NOT NULL DEFAULT 100,
    enabled              INTEGER NOT NULL DEFAULT 1,
    status               TEXT    NOT NULL DEFAULT 'unknown',  -- ok|error|disabled|unknown
    last_attempt_at      TEXT,
    last_success_at      TEXT,
    last_error           TEXT,
    last_error_kind      TEXT,
    last_error_at        TEXT,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    article_count        INTEGER NOT NULL DEFAULT 0,
    last_article_at      TEXT,
    resolved_feed_url    TEXT,             -- which candidate URL actually worked
    notes                TEXT,
    is_builtin           INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT    NOT NULL,
    updated_at           TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sources_enabled ON sources(enabled, priority);
CREATE INDEX IF NOT EXISTS idx_sources_status  ON sources(status);

-- --------------------------------------------------------------- fighters --
CREATE TABLE IF NOT EXISTS fighters (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    normalized_name   TEXT    NOT NULL UNIQUE,
    aliases           TEXT,                -- JSON array of alternate spellings
    nickname          TEXT,
    division          TEXT,
    country           TEXT,
    x_username        TEXT,
    ufc_profile_url   TEXT,
    is_champion       INTEGER NOT NULL DEFAULT 0,   -- only set from collected rankings
    current_rank      INTEGER,                      -- only set from collected rankings
    rank_division     TEXT,
    rank_updated_at   TEXT,
    rank_source_url   TEXT,
    data_origin       TEXT    NOT NULL DEFAULT 'detected', -- builtin_name_list|detected|collected|user
    mention_count     INTEGER NOT NULL DEFAULT 0,
    last_mentioned_at TEXT,
    notes             TEXT,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fighters_division ON fighters(division);
CREATE INDEX IF NOT EXISTS idx_fighters_mentions ON fighters(mention_count DESC);

-- ----------------------------------------------------------------- events --
CREATE TABLE IF NOT EXISTS events (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    name              TEXT    NOT NULL,
    normalized_name   TEXT    NOT NULL UNIQUE,
    short_name        TEXT,
    event_date        TEXT,
    location          TEXT,
    venue             TEXT,
    status            TEXT    NOT NULL DEFAULT 'scheduled', -- scheduled|completed|cancelled|unknown
    ufc_url           TEXT,
    source_url        TEXT,
    data_origin       TEXT    NOT NULL DEFAULT 'detected',  -- collected|detected|user
    mention_count     INTEGER NOT NULL DEFAULT 0,
    last_mentioned_at TEXT,
    is_demo           INTEGER NOT NULL DEFAULT 0,
    created_at        TEXT    NOT NULL,
    updated_at        TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_date ON events(event_date);

-- ---------------------------------------------------------------- stories --
CREATE TABLE IF NOT EXISTS stories (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    slug                     TEXT,
    headline                 TEXT    NOT NULL,
    summary                  TEXT,
    status                   TEXT    NOT NULL DEFAULT 'UNVERIFIED',
    status_reasons           TEXT,          -- JSON array of human-readable reasons
    status_updated_at        TEXT,
    category                 TEXT    NOT NULL DEFAULT 'general',
    relevance                REAL    NOT NULL DEFAULT 0,
    relevance_breakdown      TEXT,          -- JSON {component: points}
    support_score            REAL    NOT NULL DEFAULT 0,
    support_label            TEXT,
    support_reasons          TEXT,          -- JSON array
    first_seen_at            TEXT    NOT NULL,
    last_updated_at          TEXT    NOT NULL,
    article_count            INTEGER NOT NULL DEFAULT 0,
    source_count             INTEGER NOT NULL DEFAULT 0,
    independent_source_count INTEGER NOT NULL DEFAULT 0,
    social_post_count        INTEGER NOT NULL DEFAULT 0,
    official_confirmed       INTEGER NOT NULL DEFAULT 0,
    has_conflict             INTEGER NOT NULL DEFAULT 0,
    conflict_notes           TEXT,          -- JSON array
    is_breaking              INTEGER NOT NULL DEFAULT 0,
    is_developing            INTEGER NOT NULL DEFAULT 0,
    is_trending              INTEGER NOT NULL DEFAULT 0,
    trending_score           REAL    NOT NULL DEFAULT 0,
    trending_reasons         TEXT,          -- JSON array
    fighters                 TEXT,          -- JSON array of fighter names
    events                   TEXT,          -- JSON array of event names
    keywords                 TEXT,          -- JSON array
    event_id                 INTEGER REFERENCES events(id) ON DELETE SET NULL,
    primary_article_id       INTEGER,
    image_url                TEXT,
    update_count             INTEGER NOT NULL DEFAULT 0,
    is_demo                  INTEGER NOT NULL DEFAULT 0,
    created_at               TEXT    NOT NULL,
    updated_at               TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_stories_last_updated ON stories(last_updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_stories_relevance    ON stories(relevance DESC);
CREATE INDEX IF NOT EXISTS idx_stories_status       ON stories(status);
CREATE INDEX IF NOT EXISTS idx_stories_category     ON stories(category);
CREATE INDEX IF NOT EXISTS idx_stories_flags        ON stories(is_breaking, is_developing, is_trending);
CREATE INDEX IF NOT EXISTS idx_stories_event        ON stories(event_id);

-- --------------------------------------------------------------- articles --
CREATE TABLE IF NOT EXISTS articles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id           INTEGER REFERENCES sources(id) ON DELETE SET NULL,
    source_key          TEXT,
    source_name         TEXT,
    external_id         TEXT,
    url                 TEXT    NOT NULL,
    canonical_url       TEXT    NOT NULL,
    url_hash            TEXT    NOT NULL UNIQUE,
    domain              TEXT,
    title               TEXT    NOT NULL,
    normalized_title    TEXT,
    title_hash          TEXT,
    author              TEXT,
    published_at        TEXT,
    collected_at        TEXT    NOT NULL,
    excerpt             TEXT,               -- short excerpt only (see COPYRIGHT)
    content_snippet     TEXT,               -- trimmed extract used for analysis
    content_chars       INTEGER NOT NULL DEFAULT 0,
    image_url           TEXT,
    category            TEXT    NOT NULL DEFAULT 'general',
    fighters            TEXT,               -- JSON array
    events              TEXT,               -- JSON array
    keywords            TEXT,               -- JSON array
    language            TEXT,
    source_type         TEXT,
    reliability_weight  REAL,
    independence_group  TEXT,
    attribution_outlets TEXT,               -- JSON array: outlets this piece credits
    is_derivative       INTEGER NOT NULL DEFAULT 0,
    speculation_score   REAL    NOT NULL DEFAULT 0,
    has_denial          INTEGER NOT NULL DEFAULT 0,
    is_official         INTEGER NOT NULL DEFAULT 0,
    story_id            INTEGER REFERENCES stories(id) ON DELETE SET NULL,
    is_demo             INTEGER NOT NULL DEFAULT 0,
    raw_json            TEXT,
    created_at          TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_articles_published ON articles(published_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_collected ON articles(collected_at DESC);
CREATE INDEX IF NOT EXISTS idx_articles_story     ON articles(story_id);
CREATE INDEX IF NOT EXISTS idx_articles_source    ON articles(source_id);
CREATE INDEX IF NOT EXISTS idx_articles_category  ON articles(category);
CREATE INDEX IF NOT EXISTS idx_articles_title     ON articles(title_hash);

-- ---------------------------------------------------------- story_sources --
CREATE TABLE IF NOT EXISTS story_sources (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id   INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    article_id INTEGER NOT NULL REFERENCES articles(id) ON DELETE CASCADE,
    role       TEXT    NOT NULL DEFAULT 'corroboration', -- origin|corroboration|duplicate
    similarity REAL,
    match_reasons TEXT,                                  -- JSON array
    added_at   TEXT    NOT NULL,
    UNIQUE (story_id, article_id)
);
CREATE INDEX IF NOT EXISTS idx_story_sources_story ON story_sources(story_id);

-- ---------------------------------------------------------- story_updates --
-- Chronological timeline shown on DEVELOPING stories and research pages.
CREATE TABLE IF NOT EXISTS story_updates (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id       INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    occurred_at    TEXT    NOT NULL,
    kind           TEXT    NOT NULL,   -- article|social|status_change|card_change|ranking|note
    headline       TEXT,
    detail         TEXT,
    source_name    TEXT,
    source_type    TEXT,
    url            TEXT,
    article_id     INTEGER REFERENCES articles(id) ON DELETE SET NULL,
    social_post_id INTEGER,
    dedup_key      TEXT    NOT NULL,   -- kind + time + headline/url, keeps the timeline clean
    created_at     TEXT    NOT NULL,
    UNIQUE (story_id, dedup_key)
);
CREATE INDEX IF NOT EXISTS idx_story_updates_story ON story_updates(story_id, occurred_at DESC);

-- ----------------------------------------------------------- social_posts --
CREATE TABLE IF NOT EXISTS social_posts (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    platform          TEXT    NOT NULL DEFAULT 'x',
    post_id           TEXT    NOT NULL,
    author_id         TEXT,
    username          TEXT,
    display_name      TEXT,
    account_type      TEXT    NOT NULL DEFAULT 'UNKNOWN',
    account_verified  INTEGER NOT NULL DEFAULT 0,
    text              TEXT    NOT NULL,
    lang              TEXT,
    created_at_source TEXT,               -- post creation time (from the API)
    url               TEXT,
    like_count        INTEGER,
    reply_count       INTEGER,
    repost_count      INTEGER,
    quote_count       INTEGER,
    impression_count  INTEGER,
    entities_json     TEXT,
    media_json        TEXT,
    referenced_json   TEXT,               -- reply/quote/retweet references
    query_source      TEXT,               -- which search/timeline produced it
    fighters          TEXT,
    events            TEXT,
    category          TEXT,
    collected_at      TEXT    NOT NULL,
    is_demo           INTEGER NOT NULL DEFAULT 0,
    UNIQUE (platform, post_id)
);
CREATE INDEX IF NOT EXISTS idx_social_created  ON social_posts(created_at_source DESC);
CREATE INDEX IF NOT EXISTS idx_social_username ON social_posts(username);
CREATE INDEX IF NOT EXISTS idx_social_type     ON social_posts(account_type);

-- ----------------------------------------------------- story_social_posts --
CREATE TABLE IF NOT EXISTS story_social_posts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id       INTEGER NOT NULL REFERENCES stories(id) ON DELETE CASCADE,
    social_post_id INTEGER NOT NULL REFERENCES social_posts(id) ON DELETE CASCADE,
    similarity     REAL,
    match_reasons  TEXT,
    added_at       TEXT    NOT NULL,
    UNIQUE (story_id, social_post_id)
);
CREATE INDEX IF NOT EXISTS idx_story_social_story ON story_social_posts(story_id);

-- --------------------------------------------- monitored_social_accounts --
CREATE TABLE IF NOT EXISTS monitored_social_accounts (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    platform        TEXT    NOT NULL DEFAULT 'x',
    username        TEXT    NOT NULL,
    user_id         TEXT,
    display_name    TEXT,
    account_type    TEXT    NOT NULL DEFAULT 'UNKNOWN',
    category        TEXT    NOT NULL DEFAULT 'other', -- official|fighter|journalist|reporter|insider|coach_team|promoter
    enabled         INTEGER NOT NULL DEFAULT 1,
    priority        INTEGER NOT NULL DEFAULT 100,
    notes           TEXT,
    last_checked_at TEXT,
    last_post_id    TEXT,
    last_error      TEXT,
    added_by        TEXT    NOT NULL DEFAULT 'builtin', -- builtin|user
    created_at      TEXT    NOT NULL,
    updated_at      TEXT    NOT NULL,
    UNIQUE (platform, username)
);
CREATE INDEX IF NOT EXISTS idx_monitored_enabled ON monitored_social_accounts(enabled, priority);

-- ------------------------------------------------------- fight_card_items --
CREATE TABLE IF NOT EXISTS fight_card_items (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id          INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    fighter_a         TEXT    NOT NULL,
    fighter_b         TEXT    NOT NULL,
    fighter_a_id      INTEGER REFERENCES fighters(id) ON DELETE SET NULL,
    fighter_b_id      INTEGER REFERENCES fighters(id) ON DELETE SET NULL,
    pair_key          TEXT    NOT NULL,    -- sorted normalised names, for matching
    weight_class      TEXT,
    is_title_fight    INTEGER NOT NULL DEFAULT 0,
    segment           TEXT,                -- main_event|co_main|main_card|prelims|unknown
    bout_order        INTEGER,
    status            TEXT    NOT NULL DEFAULT 'scheduled', -- scheduled|cancelled|changed|completed|rumored
    confidence        TEXT    NOT NULL DEFAULT 'reported',  -- official|reported|rumored
    source_article_id INTEGER REFERENCES articles(id) ON DELETE SET NULL,
    source_story_id   INTEGER REFERENCES stories(id) ON DELETE SET NULL,
    source_url        TEXT,
    source_name       TEXT,
    first_seen_at     TEXT    NOT NULL,
    last_updated_at   TEXT    NOT NULL,
    is_demo           INTEGER NOT NULL DEFAULT 0,
    UNIQUE (event_id, pair_key)
);
CREATE INDEX IF NOT EXISTS idx_card_items_event ON fight_card_items(event_id, bout_order);

-- ----------------------------------------------------- fight_card_changes --
CREATE TABLE IF NOT EXISTS fight_card_changes (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id          INTEGER REFERENCES events(id) ON DELETE CASCADE,
    event_name        TEXT,
    fight_card_item_id INTEGER REFERENCES fight_card_items(id) ON DELETE SET NULL,
    change_type       TEXT    NOT NULL,   -- new_fight|cancellation|replacement|opponent_change|
                                          -- main_event_change|co_main_change|title_fight_change|weight_class_change
    before_text       TEXT,
    after_text        TEXT,
    reason            TEXT,
    status            TEXT    NOT NULL DEFAULT 'REPORTED',
    story_id          INTEGER REFERENCES stories(id) ON DELETE SET NULL,
    source_article_id INTEGER REFERENCES articles(id) ON DELETE SET NULL,
    source_url        TEXT,
    source_name       TEXT,
    detected_at       TEXT    NOT NULL,
    occurred_at       TEXT,
    is_demo           INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_card_changes_event ON fight_card_changes(event_id, detected_at DESC);

-- --------------------------------------------------------------- rankings --
CREATE TABLE IF NOT EXISTS rankings (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    division        TEXT    NOT NULL,
    fighter_name    TEXT    NOT NULL,
    normalized_name TEXT    NOT NULL,
    fighter_id      INTEGER REFERENCES fighters(id) ON DELETE SET NULL,
    position        INTEGER,               -- 0 = champion, 1..15 = ranked
    is_champion     INTEGER NOT NULL DEFAULT 0,
    ranking_date    TEXT    NOT NULL,      -- date the ranking snapshot was published/collected
    system_name     TEXT    NOT NULL DEFAULT 'UFC Rankings',
    system_version  TEXT,                  -- e.g. the label UFC publishes on the page
    source_url      TEXT,
    collected_at    TEXT    NOT NULL,
    is_demo         INTEGER NOT NULL DEFAULT 0,
    UNIQUE (division, normalized_name, ranking_date, system_version)
);
CREATE INDEX IF NOT EXISTS idx_rankings_division ON rankings(division, ranking_date DESC);
CREATE INDEX IF NOT EXISTS idx_rankings_date     ON rankings(ranking_date DESC);

-- -------------------------------------------------------- ranking_changes --
CREATE TABLE IF NOT EXISTS ranking_changes (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    division              TEXT    NOT NULL,
    fighter_name          TEXT    NOT NULL,
    normalized_name       TEXT    NOT NULL,
    previous_position     INTEGER,
    new_position          INTEGER,
    change_type           TEXT    NOT NULL, -- up|down|new_entry|exit|new_champion|champion_change
    positions_moved       INTEGER,
    is_champion           INTEGER NOT NULL DEFAULT 0,
    ranking_date          TEXT    NOT NULL,
    previous_ranking_date TEXT,
    system_name           TEXT,
    system_version        TEXT,
    source_url            TEXT,
    detected_at           TEXT    NOT NULL,
    is_demo               INTEGER NOT NULL DEFAULT 0,
    UNIQUE (division, normalized_name, ranking_date, change_type, system_version)
);
CREATE INDEX IF NOT EXISTS idx_ranking_changes_date ON ranking_changes(ranking_date DESC);

-- -------------------------------------------------------------- summaries --
-- Cached generated text (AI or template mode) so identical material is never
-- sent to a provider twice.
CREATE TABLE IF NOT EXISTS summaries (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    story_id     INTEGER REFERENCES stories(id) ON DELETE CASCADE,
    kind         TEXT    NOT NULL,   -- summary|script_30|script_60|hooks|angle|questions|check|social_analysis
    provider     TEXT    NOT NULL,   -- anthropic|openai|template
    model        TEXT,
    content      TEXT    NOT NULL,
    content_json TEXT,
    prompt_hash  TEXT    NOT NULL,
    sources_json TEXT,               -- the exact sources the text was built from
    grounding_json TEXT,             -- result of the quote/fact grounding check
    created_at   TEXT    NOT NULL,
    UNIQUE (story_id, kind, prompt_hash, provider)
);
CREATE INDEX IF NOT EXISTS idx_summaries_story ON summaries(story_id, kind);

-- ------------------------------------------------------------- watchlists --
CREATE TABLE IF NOT EXISTS watchlists (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    kind             TEXT    NOT NULL,   -- fighter|event|topic|x_account
    value            TEXT    NOT NULL,
    normalized_value TEXT    NOT NULL,
    notes            TEXT,
    active           INTEGER NOT NULL DEFAULT 1,
    last_hit_at      TEXT,
    hit_count        INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL,
    UNIQUE (kind, normalized_value)
);

-- --------------------------------------------------------------- settings --
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    value_type TEXT NOT NULL DEFAULT 'str',  -- str|int|float|bool|json
    updated_at TEXT NOT NULL
);

-- --------------------------------------------- source_classifications ------
-- Editable reliability/independence table. Nothing here is AI-generated and
-- every row can be changed from the Settings page.
CREATE TABLE IF NOT EXISTS source_classifications (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    kind               TEXT    NOT NULL,   -- domain|x_account|author
    identifier         TEXT    NOT NULL,   -- espn.com | @username | author name
    normalized_id      TEXT    NOT NULL,
    display_name       TEXT,
    classification     TEXT    NOT NULL,   -- OFFICIAL|MAJOR_NEWS|ESTABLISHED_JOURNALIST|...
    reliability_weight REAL    NOT NULL DEFAULT 0.5,
    independence_group TEXT,
    notes              TEXT,
    is_user_defined    INTEGER NOT NULL DEFAULT 0,
    created_at         TEXT    NOT NULL,
    updated_at         TEXT    NOT NULL,
    UNIQUE (kind, normalized_id)
);
CREATE INDEX IF NOT EXISTS idx_classifications_kind ON source_classifications(kind);

-- -------------------------------------------------------- collection_runs --
CREATE TABLE IF NOT EXISTS collection_runs (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at         TEXT    NOT NULL,
    finished_at        TEXT,
    trigger            TEXT    NOT NULL DEFAULT 'manual',  -- manual|scheduled|cli|test
    sources_attempted  INTEGER NOT NULL DEFAULT 0,
    sources_ok         INTEGER NOT NULL DEFAULT 0,
    sources_failed     INTEGER NOT NULL DEFAULT 0,
    articles_seen      INTEGER NOT NULL DEFAULT 0,
    articles_new       INTEGER NOT NULL DEFAULT 0,
    stories_new        INTEGER NOT NULL DEFAULT 0,
    stories_updated    INTEGER NOT NULL DEFAULT 0,
    social_new         INTEGER NOT NULL DEFAULT 0,
    duration_ms        INTEGER,
    notes              TEXT,
    error              TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON collection_runs(started_at DESC);

-- ---------------------------------------------------------- x_query_cache --
-- Stops the app from spending the (small) X API quota on identical queries.
CREATE TABLE IF NOT EXISTS x_query_cache (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    query_hash             TEXT    NOT NULL UNIQUE,
    endpoint               TEXT    NOT NULL,
    query                  TEXT    NOT NULL,
    params_json            TEXT,
    last_run_at            TEXT    NOT NULL,
    newest_id              TEXT,
    result_count           INTEGER NOT NULL DEFAULT 0,
    status                 TEXT    NOT NULL DEFAULT 'ok',  -- ok|error|rate_limited
    error                  TEXT,
    rate_limit_reset_epoch INTEGER
);

-- ---------------------------------------------------------------- ai_cache --
CREATE TABLE IF NOT EXISTS ai_cache (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    cache_key  TEXT    NOT NULL UNIQUE,
    kind       TEXT    NOT NULL,
    provider   TEXT    NOT NULL,
    model      TEXT,
    response   TEXT    NOT NULL,
    created_at TEXT    NOT NULL
);

-- ------------------------------------------------------------- migrations --
CREATE TABLE IF NOT EXISTS migrations (
    version    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    applied_at TEXT NOT NULL
);
