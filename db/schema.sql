-- India BGP Hijack/Leak Monitor schema

CREATE TABLE IF NOT EXISTS baseline_prefixes (
    asn INTEGER NOT NULL,
    prefix TEXT NOT NULL,
    fetched_at INTEGER NOT NULL,
    PRIMARY KEY (asn, prefix)
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    event_type TEXT NOT NULL,       -- 'origin_mismatch' | 'unexpected_more_specific'
    prefix TEXT NOT NULL,
    observed_origin_asn INTEGER,
    observed_origin_org TEXT,       -- holder name of the observed ASN (RIPEstat as-overview)
    expected_asn INTEGER NOT NULL,
    as_path TEXT,                   -- JSON-encoded full AS path
    rpki_status TEXT,               -- RIPEstat: 'valid' | 'invalid_asn' | 'invalid_length' | 'unknown' | NULL
    peer TEXT,                      -- RIS collector peer that observed it
    raw_message TEXT,               -- full JSON of the ris_message for audit
    severity TEXT NOT NULL DEFAULT 'info'  -- 'info' | 'warning' | 'critical'
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp ON events(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_expected_asn ON events(expected_asn);

CREATE TABLE IF NOT EXISTS monitor_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    started_at REAL,
    last_message_at REAL,
    messages_seen INTEGER NOT NULL DEFAULT 0
);
INSERT OR IGNORE INTO monitor_status (id, messages_seen) VALUES (1, 0);

-- Sample-based RPKI ROA coverage per ASN. Checking all ~20k baseline
-- prefixes individually isn't practical (thousands of RIPEstat calls), so
-- this stores a periodically-refreshed sample result instead.
CREATE TABLE IF NOT EXISTS rpki_coverage (
    asn INTEGER PRIMARY KEY,
    sample_size INTEGER NOT NULL,
    covered_count INTEGER NOT NULL,   -- status 'valid' or 'invalid_*', i.e. a ROA exists
    checked_at INTEGER NOT NULL
);

-- Normal (non-hijack) BGP activity for the tracked ASNs. The monitor used to
-- discard every update that matched the expected origin; these keep a small,
-- pruned record of it so the dashboard can show the real traffic it watches.
-- All three are bounded: activity_minutely keeps 24h, recent_updates keeps
-- the newest few hundred rows, upstream_edges is one row per (asn, upstream).
CREATE TABLE IF NOT EXISTS activity_minutely (
    asn INTEGER NOT NULL,
    minute INTEGER NOT NULL,          -- epoch seconds floored to the minute
    announcements INTEGER NOT NULL DEFAULT 0,
    withdrawals INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (asn, minute)
);

CREATE TABLE IF NOT EXISTS recent_updates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL NOT NULL,
    kind TEXT NOT NULL,               -- 'announce' | 'withdraw'
    prefix TEXT NOT NULL,             -- first matched prefix in the message
    prefix_count INTEGER NOT NULL,    -- how many tracked prefixes the message touched
    asn INTEGER NOT NULL,             -- tracked ASN that owns the prefix
    origin_asn INTEGER,
    as_path TEXT,                     -- JSON-encoded, prepending collapsed
    collector TEXT,                   -- RIS collector host, e.g. rrc00.ripe.net
    peer_asn INTEGER
);
CREATE INDEX IF NOT EXISTS idx_recent_updates_ts ON recent_updates(timestamp DESC);

-- The hop right before the origin in announced paths: who actually carries
-- each Indian ISP's routes out to the rest of the internet.
CREATE TABLE IF NOT EXISTS upstream_edges (
    asn INTEGER NOT NULL,
    upstream_asn INTEGER NOT NULL,
    seen_count INTEGER NOT NULL DEFAULT 0,
    last_seen REAL NOT NULL,
    PRIMARY KEY (asn, upstream_asn)
);

-- Holder names for ASNs seen as upstreams, resolved once per ASN.
CREATE TABLE IF NOT EXISTS asn_names (
    asn INTEGER PRIMARY KEY,
    holder TEXT,
    fetched_at INTEGER NOT NULL
);
