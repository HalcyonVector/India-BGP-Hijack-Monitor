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
    rpki_status TEXT,               -- 'valid' | 'invalid' | 'unknown' | NULL (not checked)
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
    covered_count INTEGER NOT NULL,   -- status in ('valid','invalid'), i.e. a ROA exists
    checked_at INTEGER NOT NULL
);
