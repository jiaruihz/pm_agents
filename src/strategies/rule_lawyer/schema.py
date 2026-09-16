"""Database schema definitions and helpers."""

from typing import Iterable, Tuple


CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS markets_raw (
    market_id TEXT,
    fetched_at_utc TEXT,
    json TEXT,
    PRIMARY KEY (market_id, fetched_at_utc)
);

CREATE TABLE IF NOT EXISTS events_raw (
    event_id TEXT,
    fetched_at_utc TEXT,
    json TEXT,
    PRIMARY KEY (event_id, fetched_at_utc)
);

CREATE TABLE IF NOT EXISTS markets (
    market_id TEXT PRIMARY KEY,
    slug TEXT,
    question TEXT,
    description TEXT,
    rules TEXT,
    category TEXT,
    active INTEGER,
    resolved INTEGER,
    status TEXT,
    status_updated_at TEXT,
    end_at_utc TEXT,
    volume REAL,
    liquidity REAL,
    outcomes_json TEXT,
    outcome_prices_json TEXT,
    clob_token_ids_json TEXT,
    event_ids_json TEXT,
    event_slugs_json TEXT,
    event_titles_json TEXT,
    event_tickers_json TEXT,
    updated_at_utc TEXT,
    last_synced_at_utc TEXT
);

CREATE TABLE IF NOT EXISTS prices (
    token_id TEXT,
    fetched_at_utc TEXT,
    mid REAL,
    best_bid REAL,
    best_ask REAL,
    spread REAL,
    spread_pct_mid REAL,
    PRIMARY KEY (token_id, fetched_at_utc)
);

CREATE TABLE IF NOT EXISTS orderbook_levels (
    token_id TEXT,
    fetched_at_utc TEXT,
    side TEXT CHECK(side in ('bid','ask')),
    level INTEGER,
    price REAL,
    size REAL,
    PRIMARY KEY (token_id, fetched_at_utc, side, level)
);

CREATE TABLE IF NOT EXISTS token_orderbook_status (
    token_id TEXT PRIMARY KEY,
    last_enriched_at_utc TEXT
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    slug TEXT,
    title TEXT,
    description TEXT,
    ticker TEXT,
    tags_json TEXT,
    active INTEGER,
    closed INTEGER,
    start_at_utc TEXT,
    end_at_utc TEXT,
    volume REAL,
    liquidity REAL,
    updated_at_utc TEXT,
    last_synced_at_utc TEXT
);

CREATE TABLE IF NOT EXISTS market_rule_parses (
    id INTEGER PRIMARY KEY,
    market_id TEXT,
    parsed_at_utc TEXT,
    prompt_version TEXT,
    llm_model TEXT,
    strategy_tag TEXT,
    alpha_score INTEGER,
    rule_score INTEGER,
    rule_score_components_json TEXT,
    hard_constraints_json TEXT,
    search_keywords_json TEXT,
    parsed_json TEXT,
    raw_response TEXT,
    llm_confidence REAL,
    clarity_score REAL,
    dispute_risk_score REAL,
    ambiguity_flags_json TEXT,
    UNIQUE (market_id, prompt_version)
);

CREATE TABLE IF NOT EXISTS evidence_locker (
    id INTEGER PRIMARY KEY,
    market_id TEXT,
    search_summary TEXT,
    verification_result TEXT,
    source_links_json TEXT,
    UNIQUE (market_id)
);

CREATE TABLE IF NOT EXISTS scores (
    market_id TEXT,
    scored_at_utc TEXT,
    total_score REAL,
    features_json TEXT,
    PRIMARY KEY (market_id, scored_at_utc)
);

CREATE TABLE IF NOT EXISTS sync_state (
    source TEXT,
    active INTEGER,
    page_size INTEGER,
    offset INTEGER,
    updated_at_utc TEXT,
    PRIMARY KEY (source, active, page_size)
);

CREATE INDEX IF NOT EXISTS idx_markets_active ON markets(active, resolved);
CREATE INDEX IF NOT EXISTS idx_prices_token_time ON prices(token_id, fetched_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_orderbook_token_time ON orderbook_levels(token_id, fetched_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_token_orderbook_status_time ON token_orderbook_status(last_enriched_at_utc DESC);
CREATE INDEX IF NOT EXISTS idx_events_slug ON events(slug);
CREATE INDEX IF NOT EXISTS idx_market_rule_parses_market ON market_rule_parses(market_id);
CREATE INDEX IF NOT EXISTS idx_evidence_locker_market ON evidence_locker(market_id);
CREATE INDEX IF NOT EXISTS idx_scores_market ON scores(market_id);
CREATE INDEX IF NOT EXISTS idx_sync_state_source ON sync_state(source);
"""


def build_upsert_sql(table: str, columns: Iterable[str], conflict_cols: Iterable[str]) -> str:
    cols = list(columns)
    conflicts = list(conflict_cols)
    placeholders = ", ".join([":" + c for c in cols])
    updates = ", ".join([f"{c}=excluded.{c}" for c in cols if c not in conflicts])
    return (
        f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT({', '.join(conflicts)}) DO UPDATE SET {updates};"
    )


def build_markets_upsert_sql() -> str:
    cols = [
        "market_id",
        "slug",
        "question",
        "description",
        "rules",
        "category",
        "active",
        "resolved",
        "status",
        "status_updated_at",
        "end_at_utc",
        "volume",
        "liquidity",
        "outcomes_json",
        "outcome_prices_json",
        "clob_token_ids_json",
        "event_ids_json",
        "event_slugs_json",
        "event_titles_json",
        "event_tickers_json",
        "updated_at_utc",
        "last_synced_at_utc",
    ]
    placeholders = ", ".join([":" + c for c in cols])
    updates = [
        "slug=excluded.slug",
        "question=excluded.question",
        "description=excluded.description",
        "rules=excluded.rules",
        "category=excluded.category",
        "active=excluded.active",
        "resolved=excluded.resolved",
        "status=COALESCE(excluded.status, markets.status)",
        "status_updated_at=COALESCE(excluded.status_updated_at, markets.status_updated_at)",
        "end_at_utc=excluded.end_at_utc",
        "volume=excluded.volume",
        "liquidity=excluded.liquidity",
        "outcomes_json=excluded.outcomes_json",
        "outcome_prices_json=excluded.outcome_prices_json",
        "clob_token_ids_json=excluded.clob_token_ids_json",
        "event_ids_json=excluded.event_ids_json",
        "event_slugs_json=excluded.event_slugs_json",
        "event_titles_json=excluded.event_titles_json",
        "event_tickers_json=excluded.event_tickers_json",
        "updated_at_utc=excluded.updated_at_utc",
        "last_synced_at_utc=excluded.last_synced_at_utc",
    ]
    return (
        f"INSERT INTO markets ({', '.join(cols)}) VALUES ({placeholders}) "
        f"ON CONFLICT(market_id) DO UPDATE SET {', '.join(updates)};"
    )


UPSERT_STATEMENTS = {
    "markets_raw": build_upsert_sql(
        "markets_raw", ["market_id", "fetched_at_utc", "json"], ["market_id", "fetched_at_utc"]
    ),
    "events_raw": build_upsert_sql(
        "events_raw", ["event_id", "fetched_at_utc", "json"], ["event_id", "fetched_at_utc"]
    ),
    "markets": build_markets_upsert_sql(),
    "prices": build_upsert_sql(
        "prices",
        ["token_id", "fetched_at_utc", "mid", "best_bid", "best_ask", "spread", "spread_pct_mid"],
        ["token_id", "fetched_at_utc"],
    ),
    "orderbook_levels": build_upsert_sql(
        "orderbook_levels",
        ["token_id", "fetched_at_utc", "side", "level", "price", "size"],
        ["token_id", "fetched_at_utc", "side", "level"],
    ),
    "token_orderbook_status": build_upsert_sql(
        "token_orderbook_status",
        ["token_id", "last_enriched_at_utc"],
        ["token_id"],
    ),
    "events": build_upsert_sql(
        "events",
        [
            "event_id",
            "slug",
            "title",
            "description",
            "ticker",
            "tags_json",
            "active",
            "closed",
            "start_at_utc",
            "end_at_utc",
            "volume",
            "liquidity",
            "updated_at_utc",
            "last_synced_at_utc",
        ],
        ["event_id"],
    ),
    "market_rule_parses": build_upsert_sql(
        "market_rule_parses",
        [
            "market_id",
            "parsed_at_utc",
            "prompt_version",
            "llm_model",
            "strategy_tag",
            "alpha_score",
            "rule_score",
            "rule_score_components_json",
            "hard_constraints_json",
            "search_keywords_json",
            "parsed_json",
            "raw_response",
            "llm_confidence",
            "clarity_score",
            "dispute_risk_score",
            "ambiguity_flags_json",
        ],
        ["market_id", "prompt_version"],
    ),
    "evidence_locker": build_upsert_sql(
        "evidence_locker",
        ["market_id", "search_summary", "verification_result", "source_links_json"],
        ["market_id"],
    ),
    "scores": build_upsert_sql(
        "scores", ["market_id", "scored_at_utc", "total_score", "features_json"], ["market_id", "scored_at_utc"]
    ),
    "sync_state": build_upsert_sql(
        "sync_state", ["source", "active", "page_size", "offset", "updated_at_utc"], ["source", "active", "page_size"]
    ),
}
