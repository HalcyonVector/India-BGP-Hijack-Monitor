"""
Read-only FastAPI backend serving events/baseline/status from SQLite to the
frontend dashboard. Run: uvicorn backend.api:app --reload --port 8000

Local dev keeps the monitor as a separate process (see README). A single
free hosting tier (e.g. Render's free Web Service) only gives you one
long-running process, so set RUN_MONITOR_INLINE=1 to have this API process
also start the live monitor as a background thread on boot -- see
docs/deployment.md.

Render's free tier also has no persistent disk, so rpki_coverage's table
resets on every restart too. RPKI coverage is self-healing on every boot:
the committed docs/rpki-coverage-summary.json (weekly CI snapshot) is
seeded into the DB synchronously and immediately -- no network calls, no
delay -- and if RUN_RPKI_REFRESH_INLINE=1, a background thread then
re-samples live RIPEstat data to bring it fully current within ~5 minutes.
No manual re-run required after a restart.
"""
import ipaddress
import json
import os
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backend.detector.baseline import build_baseline
from backend.detector.baseline_drift import compute_drift
from backend.detector.monitor import (
    build_expected_lookup, check_rpki, classify_announcement, lookup_asn_org, run_monitor,
    severity_for,
)
from backend.detector.rpki_coverage import compute_all as compute_rpki_coverage
from backend.detector.rpki_coverage import seed_from_committed_summary
from backend.detector.targets import TRACKED_ASNS
from db import store

app = FastAPI(title="India BGP Hijack/Leak Monitor")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local demo project, no auth/sensitive data
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.on_event("startup")
def maybe_start_inline_monitor():
    store.init_db()
    conn = store.get_connection()
    baseline_count = conn.execute("SELECT COUNT(*) c FROM baseline_prefixes").fetchone()["c"]
    conn.close()
    if baseline_count == 0:
        print("No baseline found on boot -- building one now (first deploy only)...")
        build_baseline()

    if os.environ.get("RUN_MONITOR_INLINE") == "1":
        print("RUN_MONITOR_INLINE=1: starting the live monitor in a background thread...")
        thread = threading.Thread(target=run_monitor, kwargs={"max_seconds": None}, daemon=True)
        thread.start()

    seeded = seed_from_committed_summary()
    if seeded:
        print(f"Seeded RPKI coverage for {seeded} ASNs from docs/rpki-coverage-summary.json "
              f"(committed snapshot -- self-healing after a restart, no manual re-run needed)")

    if os.environ.get("RUN_RPKI_REFRESH_INLINE") == "1":
        print("RUN_RPKI_REFRESH_INLINE=1: refreshing RPKI coverage with live data in the background "
              "(~5 min, 9 ASNs x 30 samples)...")
        thread = threading.Thread(target=compute_rpki_coverage, daemon=True)
        thread.start()


@app.get("/api/asns")
def list_tracked_asns():
    conn = store.get_connection()
    counts = conn.execute(
        "SELECT asn, COUNT(*) c FROM baseline_prefixes GROUP BY asn"
    ).fetchall()
    rpki = store.get_rpki_coverage(conn)
    sample_prefixes = {asn: store.top_prefixes_by_asn(conn, asn, n=1) for asn in TRACKED_ASNS}
    conn.close()
    prefix_counts = {row["asn"]: row["c"] for row in counts}
    result = []
    for asn, name in TRACKED_ASNS.items():
        cov = rpki.get(asn)
        result.append({
            "asn": asn,
            "name": name,
            "baseline_prefix_count": prefix_counts.get(asn, 0),
            "rpki_sample_size": cov["sample_size"] if cov else None,
            "rpki_covered_count": cov["covered_count"] if cov else None,
            "rpki_checked_at": cov["checked_at"] if cov else None,
            "sample_prefix": (sample_prefixes.get(asn) or [None])[0],
        })
    return result


@app.get("/api/events")
def list_events(limit: int = Query(50, le=500), severity: str | None = None):
    conn = store.get_connection()
    if severity:
        rows = conn.execute(
            "SELECT * FROM events WHERE severity = ? ORDER BY timestamp DESC LIMIT ?",
            (severity, limit),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM events ORDER BY timestamp DESC LIMIT ?", (limit,)
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@app.get("/api/baseline-drift")
def baseline_drift():
    """Per-ASN prefix count drift between the oldest and newest committed
    docs/baseline-summary.json snapshots. Needs 2+ daily CI commits to show
    real data; reports that plainly rather than fabricating a trend."""
    try:
        return compute_drift()
    except RuntimeError as e:
        return {"status": "error", "message": str(e)}


@app.get("/api/status")
def status():
    conn = store.get_connection()
    row = conn.execute("SELECT * FROM monitor_status WHERE id = 1").fetchone()
    event_count = conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
    by_severity = {r["severity"]: r["c"] for r in conn.execute(
        "SELECT severity, COUNT(*) c FROM events GROUP BY severity")}
    baseline_count = conn.execute("SELECT COUNT(*) c FROM baseline_prefixes").fetchone()["c"]
    conn.close()
    return {
        **(dict(row) if row else {}),
        "total_events": event_count,
        "events_by_severity": {sev: by_severity.get(sev, 0) for sev in ("critical", "warning", "info")},
        "baseline_prefix_count": baseline_count,
        "tracked_asn_count": len(TRACKED_ASNS),
    }


ACTIVITY_WINDOW_MINUTES = 60


@app.get("/api/live")
def live(feed_limit: int = Query(40, le=200)):
    """Everything the dashboard's live panels need in one request (keeps
    the free-tier instance's request count flat): the rolling feed of real
    updates, per-ASN per-minute activity for the last hour, and the
    upstream ASNs observed carrying each tracked network's routes."""
    conn = store.get_connection()
    now_minute = int(time.time() // 60 * 60)
    start_minute = now_minute - (ACTIVITY_WINDOW_MINUTES - 1) * 60

    names = {r["asn"]: r["holder"] for r in conn.execute("SELECT asn, holder FROM asn_names")}
    names.update(TRACKED_ASNS)

    feed = []
    for r in conn.execute("SELECT * FROM recent_updates ORDER BY id DESC LIMIT ?", (feed_limit,)):
        row = dict(r)
        row["as_path"] = json.loads(row["as_path"]) if row["as_path"] else None
        feed.append(row)

    activity = {asn: {"announcements": [0] * ACTIVITY_WINDOW_MINUTES,
                      "withdrawals": [0] * ACTIVITY_WINDOW_MINUTES} for asn in TRACKED_ASNS}
    for r in conn.execute("SELECT * FROM activity_minutely WHERE minute >= ?", (start_minute,)):
        if r["asn"] not in activity:
            continue
        i = (r["minute"] - start_minute) // 60
        if 0 <= i < ACTIVITY_WINDOW_MINUTES:
            activity[r["asn"]]["announcements"][i] = r["announcements"]
            activity[r["asn"]]["withdrawals"][i] = r["withdrawals"]

    edges = [dict(r) for r in conn.execute(
        "SELECT asn, upstream_asn, seen_count, last_seen FROM upstream_edges "
        "ORDER BY seen_count DESC LIMIT 60")]
    conn.close()

    for e in edges:
        e["upstream_name"] = names.get(e["upstream_asn"])
        e["upstream_is_tracked"] = e["upstream_asn"] in TRACKED_ASNS

    return {
        "window_start": start_minute,
        "window_minutes": ACTIVITY_WINDOW_MINUTES,
        "feed": feed,
        "activity": [{"asn": asn, **series} for asn, series in activity.items()],
        "upstreams": edges,
    }


_lookup_cache = {"key": None, "exact": None, "networks": None}


def _expected_lookup():
    """build_expected_lookup parses ~21k prefixes; cache it until the
    baseline actually changes."""
    conn = store.get_connection()
    key = tuple(conn.execute("SELECT COUNT(*), MAX(fetched_at) FROM baseline_prefixes").fetchone())
    if _lookup_cache["key"] != key:
        _lookup_cache["exact"], _lookup_cache["networks"] = build_expected_lookup(conn)
        _lookup_cache["key"] = key
    conn.close()
    return _lookup_cache["exact"], _lookup_cache["networks"]


@app.get("/api/check")
def check(prefix: str, origin: int = Query(..., ge=0, le=4294967295), rpki: bool = True):
    """Runs the live monitor's exact detection logic on a hypothetical
    announcement ("what if AS<origin> announced <prefix>?"). Read-only:
    nothing is written to the events table. With rpki=true (default) the RPKI
    status and origin holder name are real RIPEstat lookups."""
    try:
        net = ipaddress.ip_network(prefix.strip(), strict=False)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"'{prefix}' is not a valid IP prefix")
    prefix_str = str(net)

    exact, networks = _expected_lookup()
    verdict = classify_announcement(prefix_str, origin, exact, networks)
    if verdict is None:
        return {"prefix": prefix_str, "origin": origin, "tracked": False, "flagged": False,
                "explanation": "Not inside any tracked Indian ISP's baseline -- the monitor ignores it."}

    expected = verdict["expected_asn"]
    rpki_status = check_rpki(origin, prefix_str) if rpki else None
    origin_name = TRACKED_ASNS.get(origin) or (lookup_asn_org(origin) if rpki else None)
    severity = severity_for(rpki_status) if verdict["flagged"] else None
    return {
        "prefix": prefix_str,
        "origin": origin,
        "origin_name": origin_name,
        "tracked": True,
        "expected_asn": expected,
        "expected_name": TRACKED_ASNS.get(expected),
        "match_kind": verdict["match_kind"],
        "flagged": verdict["flagged"],
        "event_type": verdict["event_type"],
        "rpki_status": rpki_status,
        "severity": severity,
    }
