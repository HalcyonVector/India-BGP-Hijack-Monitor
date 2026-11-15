"""
Save the deployed API's responses to docs/dashboard-snapshot.json.

GitHub Pages serves that file instantly, so the dashboard can show real
data on first paint while Render's free-tier instance wakes from sleep
(~50s). Run daily by refresh-baseline.yml. The first request waits for the
instance to wake; the rest return in well under a second.
"""
import json
import sys
import time
from pathlib import Path

import requests

API = "https://india-bgp-hijack-monitor.onrender.com"
OUT = Path(__file__).resolve().parent.parent / "docs" / "dashboard-snapshot.json"
ENDPOINTS = {
    "status": "/api/status",
    "asns": "/api/asns",
    "live": "/api/live",
    "events": "/api/events?limit=50",
    "drift": "/api/baseline-drift",
}


def main():
    snapshot = {"saved_at": time.time()}
    for key, path in ENDPOINTS.items():
        resp = requests.get(API + path, timeout=150)
        resp.raise_for_status()
        snapshot[key] = resp.json()
    # The dashboard never shows the raw BGP message; it's most of the bytes.
    for e in snapshot["events"]:
        e.pop("raw_message", None)
    OUT.write_text(json.dumps(snapshot, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"wrote {OUT.name}: {OUT.stat().st_size:,} bytes, "
          f"{len(snapshot['events'])} events, {len(snapshot['live']['feed'])} feed rows")


if __name__ == "__main__":
    try:
        main()
    except requests.RequestException as e:
        # Keep yesterday's snapshot rather than failing the baseline job.
        print(f"snapshot skipped: {e}", file=sys.stderr)
