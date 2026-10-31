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
MAX_ATTEMPTS = 4


def fetch_announced_prefixes(asn):
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = requests.get(RIPESTAT_URL, params={"resource": f"AS{asn}"}, timeout=45)
            resp.raise_for_status()
            data = resp.json()
            prefixes = data.get("data", {}).get("prefixes", [])
            return [p["prefix"] for p in prefixes]
        except requests.exceptions.RequestException as e:
            if attempt == MAX_ATTEMPTS:
                raise
            backoff = 2 ** attempt
            print(f"  AS{asn}: {e} (attempt {attempt}/{MAX_ATTEMPTS}, retrying in {backoff}s)")
            time.sleep(backoff)


def build_baseline():
    store.init_db()
    conn = store.get_connection()
    total = 0
    failed = []
    try:
        for asn, name in TRACKED_ASNS.items():
            try:
                prefixes = fetch_announced_prefixes(asn)
            except requests.exceptions.RequestException as e:
                print(f"  AS{asn} ({name}): FAILED after {MAX_ATTEMPTS} attempts -- {e}")
                failed.append(asn)
                continue
            store.replace_baseline(conn, asn, prefixes)
            conn.commit()
            print(f"  AS{asn} ({name}): {len(prefixes)} prefixes")
            total += len(prefixes)
            time.sleep(0.5)  # be polite to the free API
    finally:
        conn.close()
    return total, failed


if __name__ == "__main__":
    n, failed = build_baseline()
    print(f"Baseline built: {n} total prefixes across {len(TRACKED_ASNS) - len(failed)} tracked ASNs.")
    if failed:
        print(f"Failed to fetch {len(failed)} ASN(s) after retries: {failed}")
        sys.exit(1)
