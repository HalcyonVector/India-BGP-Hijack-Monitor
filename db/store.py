import sqlite3
import time
from pathlib import Path

DB_PATH = Path(__file__).parent / "bgp_monitor.db"
SCHEMA_PATH = Path(__file__).parent / "schema.sql"


def get_connection():
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_connection()
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()
    conn.close()


def replace_baseline(conn, asn, prefixes):
    now = int(time.time())
    conn.execute("DELETE FROM baseline_prefixes WHERE asn = ?", (asn,))
    conn.executemany(
        "INSERT OR REPLACE INTO baseline_prefixes (asn, prefix, fetched_at) VALUES (?, ?, ?)",
        [(asn, p, now) for p in prefixes],
    )


def get_baseline_asns_for_prefix_owner(conn):
    """Return {prefix: asn} for the whole baseline (used for fast lookup)."""
    rows = conn.execute("SELECT asn, prefix FROM baseline_prefixes").fetchall()
    return {row["prefix"]: row["asn"] for row in rows}


def top_prefixes_by_asn(conn, asn, n=8):
    """
    Largest (least-specific, smallest prefix-length number) blocks for an
    ASN -- used to pick a manageable subscription shortlist instead of
    subscribing to every individual announced prefix (thousands per ASN).
    """
    rows = conn.execute(
        "SELECT prefix FROM baseline_prefixes WHERE asn = ?", (asn,)
    ).fetchall()
    def plen(p):
        try:
            return int(p["prefix"].split("/")[1])
        except (IndexError, ValueError):
            return 32
    rows = sorted(rows, key=plen)
    return [r["prefix"] for r in rows[:n]]


def insert_event(conn, event_type, prefix, expected_asn, observed_origin_asn=None,
                  observed_origin_org=None, as_path=None, rpki_status=None, peer=None,
                  raw_message=None, severity="info", timestamp=None):
    conn.execute(
        "INSERT INTO events (timestamp, event_type, prefix, observed_origin_asn, "
        "observed_origin_org, expected_asn, as_path, rpki_status, peer, raw_message, severity) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (timestamp or time.time(), event_type, prefix, observed_origin_asn, observed_origin_org,
         expected_asn, as_path, rpki_status, peer, raw_message, severity),
    )


def touch_monitor_status(conn, messages_delta=1):
    now = time.time()
    conn.execute(
        "UPDATE monitor_status SET last_message_at = ?, "
        "messages_seen = messages_seen + ?, "
        "started_at = COALESCE(started_at, ?) WHERE id = 1",
        (now, messages_delta, now),
    )


def set_rpki_coverage(conn, asn, sample_size, covered_count, checked_at=None):
    conn.execute(
        "INSERT OR REPLACE INTO rpki_coverage (asn, sample_size, covered_count, checked_at) "
        "VALUES (?, ?, ?, ?)",
        (asn, sample_size, covered_count, checked_at if checked_at is not None else int(time.time())),
    )


def get_rpki_coverage(conn):
    """Return {asn: {'sample_size', 'covered_count', 'checked_at'}}."""
    rows = conn.execute("SELECT * FROM rpki_coverage").fetchall()
    return {row["asn"]: dict(row) for row in rows}


ACTIVITY_RETENTION_SECONDS = 24 * 3600
RECENT_UPDATES_KEEP = 300


def record_activity(conn, asn, timestamp, announcements=0, withdrawals=0):
    minute = int(timestamp // 60 * 60)
    conn.execute(
        "INSERT INTO activity_minutely (asn, minute, announcements, withdrawals) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(asn, minute) DO UPDATE SET "
        "announcements = announcements + excluded.announcements, "
        "withdrawals = withdrawals + excluded.withdrawals",
        (asn, minute, announcements, withdrawals),
    )


def insert_recent_update(conn, timestamp, kind, prefix, prefix_count, asn,
                          origin_asn=None, as_path=None, collector=None, peer_asn=None):
    conn.execute(
        "INSERT INTO recent_updates (timestamp, kind, prefix, prefix_count, asn, origin_asn, "
        "as_path, collector, peer_asn) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (timestamp, kind, prefix, prefix_count, asn, origin_asn, as_path, collector, peer_asn),
    )


def record_upstream(conn, asn, upstream_asn, timestamp):
    """Returns True the first time this (asn, upstream) pair is seen."""
    cur = conn.execute(
        "UPDATE upstream_edges SET seen_count = seen_count + 1, last_seen = ? "
        "WHERE asn = ? AND upstream_asn = ?",
        (timestamp, asn, upstream_asn),
    )
    if cur.rowcount:
        return False
    conn.execute(
        "INSERT INTO upstream_edges (asn, upstream_asn, seen_count, last_seen) VALUES (?, ?, 1, ?)",
        (asn, upstream_asn, timestamp),
    )
    return True


def has_asn_name(conn, asn):
    return conn.execute("SELECT 1 FROM asn_names WHERE asn = ?", (asn,)).fetchone() is not None


def set_asn_name(conn, asn, holder):
    conn.execute(
        "INSERT OR REPLACE INTO asn_names (asn, holder, fetched_at) VALUES (?, ?, ?)",
        (asn, holder, int(time.time())),
    )


def prune_activity(conn):
    """Keeps the activity tables bounded -- the Render free tier has a small
    ephemeral disk and this runs for weeks at a time."""
    conn.execute("DELETE FROM activity_minutely WHERE minute < ?",
                 (int(time.time()) - ACTIVITY_RETENTION_SECONDS,))
    conn.execute(
        "DELETE FROM recent_updates WHERE id <= "
        "(SELECT id FROM recent_updates ORDER BY id DESC LIMIT 1 OFFSET ?)",
        (RECENT_UPDATES_KEEP,),
    )


if __name__ == "__main__":
    init_db()
    print(f"Initialized schema at {DB_PATH}")
