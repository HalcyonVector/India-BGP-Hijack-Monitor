"""
Tests the FastAPI endpoints themselves (not just the detection logic --
see backend/detector/test_detection_logic.py for that). Runs against the
real local SQLite DB and baseline (same philosophy as the detection-logic
tests: real data, not mocks), so `python backend/detector/baseline.py`
must have been run first -- already true in CI (see .github/workflows/test.yml)
and for anyone who followed the Quick Start.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from fastapi.testclient import TestClient

from backend.api import app
from backend.detector.targets import TRACKED_ASNS


def run_tests():
    results = []
    with TestClient(app) as client:

        # /api/status
        r = client.get("/api/status")
        ok = r.status_code == 200
        data = r.json() if ok else {}
        ok = ok and all(k in data for k in
                         ("messages_seen", "total_events", "baseline_prefix_count", "tracked_asn_count"))
        ok = ok and data.get("tracked_asn_count") == len(TRACKED_ASNS)
        results.append(("GET /api/status has the expected shape", ok))

        # /api/asns
        r = client.get("/api/asns")
        ok = r.status_code == 200
        data = r.json() if ok else []
        ok = ok and len(data) == len(TRACKED_ASNS)
        ok = ok and all("asn" in row and "baseline_prefix_count" in row for row in data)
        results.append(("GET /api/asns returns all tracked ASNs with prefix counts", ok))

        # /api/events (no filter)
        r = client.get("/api/events")
        ok = r.status_code == 200 and isinstance(r.json(), list)
        results.append(("GET /api/events returns a list", ok))

        # /api/events with a severity filter
        r = client.get("/api/events", params={"severity": "critical"})
        ok = r.status_code == 200
        data = r.json() if ok else []
        ok = ok and all(row.get("severity") == "critical" for row in data)
        results.append(("GET /api/events?severity=critical only returns critical events", ok))

        # /api/events limit param is respected
        r = client.get("/api/events", params={"limit": 1})
        ok = r.status_code == 200 and len(r.json()) <= 1
        results.append(("GET /api/events?limit=1 returns at most 1 row", ok))

        # /api/events limit over the max is rejected, not silently truncated
        r = client.get("/api/events", params={"limit": 999999})
        ok = r.status_code == 422
        results.append(("GET /api/events?limit=999999 is rejected (max 500), not silently capped", ok))

        # /api/baseline-drift always returns a 'status' field, even with no history
        r = client.get("/api/baseline-drift")
        ok = r.status_code == 200 and "status" in r.json()
        results.append(("GET /api/baseline-drift always has a status field", ok))

    all_pass = True
    for name, ok in results:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        all_pass = all_pass and ok
    return all_pass


if __name__ == "__main__":
    ok = run_tests()
    print("ALL TESTS PASSED" if ok else "SOME TESTS FAILED")
    sys.exit(0 if ok else 1)
