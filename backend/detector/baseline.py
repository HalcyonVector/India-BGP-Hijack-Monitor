"""
Builds the 'expected' baseline: which prefixes each tracked Indian ASN
currently announces, via RIPEstat's free announced-prefixes endpoint.
Re-run periodically (e.g. daily) to keep the baseline current -- ISPs add/
retire prefixes over time, and a stale baseline would generate false
'hijack' alerts for legitimate new announcements.
"""
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from backend.detector.targets import TRACKED_ASNS
from db import store

RIPESTAT_URL = "https://stat.ripe.net/data/announced-prefixes/data.json"


def fetch_announced_prefixes(asn):
    resp = requests.get(RIPESTAT_URL, params={"resource": f"AS{asn}"}, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    prefixes = data.get("data", {}).get("prefixes", [])
    return [p["prefix"] for p in prefixes]


def build_baseline():
    store.init_db()
    conn = store.get_connection()
    total = 0
    try:
        for asn, name in TRACKED_ASNS.items():
            prefixes = fetch_announced_prefixes(asn)
            store.replace_baseline(conn, asn, prefixes)
            conn.commit()
            print(f"  AS{asn} ({name}): {len(prefixes)} prefixes")
            total += len(prefixes)
            time.sleep(0.5)  # be polite to the free API
    finally:
        conn.close()
    return total


if __name__ == "__main__":
    n = build_baseline()
    print(f"Baseline built: {n} total prefixes across {len(TRACKED_ASNS)} tracked ASNs.")
