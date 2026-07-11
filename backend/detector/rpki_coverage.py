"""
Sample-based RPKI ROA coverage per tracked ASN.

Checking all ~20k baseline prefixes individually against RIPEstat's
rpki-validation endpoint isn't practical (thousands of calls). Instead this
takes a random sample of up to SAMPLE_SIZE prefixes per ASN and reports what
fraction have a ROA at all (status 'valid' or 'invalid', as opposed to
'unknown' -- no ROA published). This is an estimate, not exhaustive, and is
reported as such everywhere it's surfaced.
"""
import random
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from backend.detector.targets import TRACKED_ASNS
from db import store

RPKI_URL = "https://stat.ripe.net/data/rpki-validation/data.json"
SAMPLE_SIZE = 30


def check_rpki(asn, prefix):
    try:
        resp = requests.get(RPKI_URL, params={"resource": asn, "prefix": prefix}, timeout=10)
        resp.raise_for_status()
        return resp.json().get("data", {}).get("status")
    except requests.RequestException:
        return None


def compute_coverage_for_asn(conn, asn, sample_size=SAMPLE_SIZE):
    rows = conn.execute(
        "SELECT prefix FROM baseline_prefixes WHERE asn = ?", (asn,)
    ).fetchall()
    if not rows:
        return 0, 0
    prefixes = [r["prefix"] for r in rows]
    sample = random.sample(prefixes, min(sample_size, len(prefixes)))

    covered = 0
    for prefix in sample:
        status = check_rpki(asn, prefix)
        if status in ("valid", "invalid"):
            covered += 1
        time.sleep(0.3)  # be polite to the free API
    return len(sample), covered


def compute_all():
    store.init_db()
    conn = store.get_connection()
    try:
        for asn, name in TRACKED_ASNS.items():
            sample_size, covered = compute_coverage_for_asn(conn, asn)
            store.set_rpki_coverage(conn, asn, sample_size, covered)
            conn.commit()
            pct = (covered / sample_size * 100) if sample_size else 0
            print(f"  AS{asn} ({name}): {covered}/{sample_size} sampled prefixes have a ROA ({pct:.0f}%)")
    finally:
        conn.close()


if __name__ == "__main__":
    compute_all()
