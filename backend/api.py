"""
Read-only FastAPI backend serving events/baseline/status from SQLite to the
frontend dashboard. Run: uvicorn backend.api:app --reload --port 8000
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware

from backend.detector.targets import TRACKED_ASNS
from db import store

app = FastAPI(title="India BGP Hijack/Leak Monitor")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # local demo project, no auth/sensitive data
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/asns")
def list_tracked_asns():
    conn = store.get_connection()
    counts = conn.execute(
        "SELECT asn, COUNT(*) c FROM baseline_prefixes GROUP BY asn"
    ).fetchall()
    conn.close()
    prefix_counts = {row["asn"]: row["c"] for row in counts}
    return [
        {"asn": asn, "name": name, "baseline_prefix_count": prefix_counts.get(asn, 0)}
        for asn, name in TRACKED_ASNS.items()
    ]


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
