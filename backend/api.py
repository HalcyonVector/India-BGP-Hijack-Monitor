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
import os
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from backend.detector.baseline import build_baseline
from backend.detector.baseline_drift import compute_drift
from backend.detector.monitor import run_monitor
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
    baseline_count = conn.execute("SELECT COUNT(*) c FROM baseline_prefixes").fetchone()["c"]
    conn.close()
    return {
        **(dict(row) if row else {}),
        "total_events": event_count,
        "baseline_prefix_count": baseline_count,
        "tracked_asn_count": len(TRACKED_ASNS),
    }
