"""DuckDB schema DDL for prediction-markets historical analytics.

Schema reference: docs/prediction-markets-historical-analytics-upgrade.md §8.

All tables are created idempotently via CREATE TABLE IF NOT EXISTS. Column
order matches the design doc to keep diffs readable. Primary key choices are
load-bearing and documented inline below.
"""

from __future__ import annotations

# -- pm_markets ---------------------------------------------------------------
# One row per Polymarket market. Resolution fields are nullable until the
# market closes; resolution_method records how winning_outcome was determined
# (see storage/resolution.py for the 6-rule waterfall).
CREATE_PM_MARKETS = """
CREATE TABLE IF NOT EXISTS pm_markets (
    condition_id           VARCHAR PRIMARY KEY,
    slug                   VARCHAR,
    question               VARCHAR NOT NULL,
    description            VARCHAR,
    category               VARCHAR,
    event_slug             VARCHAR,
    event_title            VARCHAR,
    start_date             TIMESTAMP,
    end_date               TIMESTAMP,
    closed_time            TIMESTAMP,
    active                 BOOLEAN,
    closed                 BOOLEAN,
    accepting_orders       BOOLEAN,
    volume                 DOUBLE,
    volume_24h             DOUBLE,
    liquidity              DOUBLE,
    resolution_source      VARCHAR,
    uma_resolution_status  VARCHAR,
    winning_outcome        VARCHAR,
    resolution_status      VARCHAR,
    resolution_method      VARCHAR,
    resolution_confidence  DOUBLE,
    last_refreshed         TIMESTAMP NOT NULL
)
"""

# -- pm_tokens ----------------------------------------------------------------
# One row per outcome token (YES/NO for binary markets, more for multi-outcome).
# Keyed by token_id because that is the CLOB asset identifier used for price
# history requests; condition_id is non-unique here.
CREATE_PM_TOKENS = """
CREATE TABLE IF NOT EXISTS pm_tokens (
    token_id       VARCHAR PRIMARY KEY,
    condition_id   VARCHAR NOT NULL,
    outcome_index  INTEGER NOT NULL,
    outcome_label  VARCHAR NOT NULL
)
"""

# -- pm_price_history ---------------------------------------------------------
# Sampled outcome price history. PK includes fidelity_minutes and source so
# the same token can hold parallel coarse + fine sample series from different
# sources without collisions (§9.2 fidelity rules).
CREATE_PM_PRICE_HISTORY = """
CREATE TABLE IF NOT EXISTS pm_price_history (
    token_id          VARCHAR NOT NULL,
    condition_id      VARCHAR NOT NULL,
    timestamp         TIMESTAMP NOT NULL,
    price             DOUBLE NOT NULL,
    fidelity_minutes  INTEGER NOT NULL,
    source            VARCHAR NOT NULL,
    fetched_at        TIMESTAMP NOT NULL,
    PRIMARY KEY (token_id, timestamp, fidelity_minutes, source)
)
"""

# -- pm_trades ----------------------------------------------------------------
# Recent/API trades plus (eventually) on-chain fills. trade_key is a
# deterministic composite key whose formula is documented in §8.4; the
# composition is built in storage/store.py rather than the DB to keep the
# normalization rules in one place.
CREATE_PM_TRADES = """
CREATE TABLE IF NOT EXISTS pm_trades (
    trade_key         VARCHAR PRIMARY KEY,
    source            VARCHAR NOT NULL,
    source_trade_id   VARCHAR,
    transaction_hash  VARCHAR,
    log_index         BIGINT,
    asset_id          VARCHAR,
    condition_id      VARCHAR NOT NULL,
    timestamp         TIMESTAMP,
    price             DOUBLE,
    size              DOUBLE,
    side              VARCHAR,
    outcome           VARCHAR,
    wallet            VARCHAR,
    fetched_at        TIMESTAMP NOT NULL
)
"""

# -- _pm_meta -----------------------------------------------------------------
# Coverage and freshness metadata, keyed by (entity_type, entity_id, source,
# fidelity_minutes). The CHECK constraint enumerates valid entity_type values
# so a typo at insertion time fails loud rather than silently fragmenting
# coverage stats.
CREATE_PM_META = """
CREATE TABLE IF NOT EXISTS _pm_meta (
    entity_type       VARCHAR NOT NULL CHECK (
        entity_type IN ('market', 'token', 'price_history', 'trades', 'analysis')
    ),
    entity_id         VARCHAR NOT NULL,
    source            VARCHAR NOT NULL,
    first_timestamp   TIMESTAMP,
    last_timestamp    TIMESTAMP,
    row_count         BIGINT,
    fidelity_minutes  INTEGER NOT NULL DEFAULT 0,
    quality_flags     VARCHAR,
    last_refreshed    TIMESTAMP NOT NULL,
    PRIMARY KEY (entity_type, entity_id, source, fidelity_minutes)
)
"""

# -- pm_analysis_cache --------------------------------------------------------
# Optional cached analysis outputs keyed by analysis_key. data_fingerprint is
# what invalidates a stale entry when the underlying market/resolution data
# changes (see storage/fingerprint.py).
CREATE_PM_ANALYSIS_CACHE = """
CREATE TABLE IF NOT EXISTS pm_analysis_cache (
    analysis_key      VARCHAR PRIMARY KEY,
    data_fingerprint  VARCHAR NOT NULL,
    result_json       VARCHAR NOT NULL,
    created_at        TIMESTAMP NOT NULL
)
"""

# -- Secondary indexes --------------------------------------------------------
# DuckDB primary keys imply an index on the PK columns; these are the
# non-PK lookups the historical tools will hit.
CREATE_INDEX_PM_TOKENS_CONDITION = """
CREATE INDEX IF NOT EXISTS idx_pm_tokens_condition
    ON pm_tokens (condition_id)
"""

CREATE_INDEX_PM_PRICE_HISTORY_CONDITION = """
CREATE INDEX IF NOT EXISTS idx_pm_price_history_condition
    ON pm_price_history (condition_id, timestamp)
"""

CREATE_INDEX_PM_TRADES_CONDITION = """
CREATE INDEX IF NOT EXISTS idx_pm_trades_condition
    ON pm_trades (condition_id, timestamp)
"""

CREATE_INDEX_PM_MARKETS_END_DATE = """
CREATE INDEX IF NOT EXISTS idx_pm_markets_end_date
    ON pm_markets (end_date)
"""

# DDL execution order: tables first, then indexes. Iterating in this order
# keeps schema init idempotent and lets the manager bail loudly on the
# first failing statement.
ALL_DDL: tuple[str, ...] = (
    CREATE_PM_MARKETS,
    CREATE_PM_TOKENS,
    CREATE_PM_PRICE_HISTORY,
    CREATE_PM_TRADES,
    CREATE_PM_META,
    CREATE_PM_ANALYSIS_CACHE,
    CREATE_INDEX_PM_TOKENS_CONDITION,
    CREATE_INDEX_PM_PRICE_HISTORY_CONDITION,
    CREATE_INDEX_PM_TRADES_CONDITION,
    CREATE_INDEX_PM_MARKETS_END_DATE,
)
