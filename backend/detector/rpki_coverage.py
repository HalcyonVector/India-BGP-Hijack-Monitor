"""
Sample-based RPKI ROA coverage per tracked ASN.

Checking all ~20k baseline prefixes individually against RIPEstat's
rpki-validation endpoint isn't practical (thousands of calls). Instead this
takes a random sample of up to SAMPLE_SIZE prefixes per ASN and reports what
fraction have a ROA at all (status 'valid' or 'invalid', as opposed to
'unknown' -- no ROA published). This is an estimate, not exhaustive, and is
reported as such everywhere it's surfaced.

On a host with no persistent disk (Render's free tier), this table resets
on every restart. Two mechanisms keep it populated without a manual re-run:
1. seed_from_committed_summary() loads docs/rpki-coverage-summary.json
   (committed weekly by .github/workflows/refresh-rpki-coverage.yml) into
   the DB immediately on boot -- no network calls, no delay, at most a
   week stale.
2. backend/api.py's startup hook then kicks off compute_all() in a
   background thread to refresh with fully live data within ~5 minutes,
   the same pattern already used for the inline monitor.
"""
import calendar
import json
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
COMMITTED_SUMMARY_PATH = Path(__file__).parent.parent.parent / "docs" / "rpki-coverage-summary.json"


def seed_from_committed_summary():
    """
    Load the last CI-committed RPKI coverage summary into the DB, if the
    table is currently empty and the summary file exists. Fast (one file
    read, no network) -- meant to run synchronously at API startup so the
    dashboard never shows "not yet sampled" right after a fresh boot.
    Returns the number of ASNs seeded (0 if nothing to do).
    """
    if not COMMITTED_SUMMARY_PATH.exists():
        return 0

    conn = store.get_connection()
    try:
        already_have = conn.execute("SELECT COUNT(*) c FROM rpki_coverage").fetchone()["c"]
        if already_have > 0:
            return 0  # already populated (e.g. compute_all() already ran this boot)

        summary = json.loads(COMMITTED_SUMMARY_PATH.read_text())
        generated_at = summary.get("generated_at_utc")
        checked_at = None
        if generated_at:
            try:
                checked_at = calendar.timegm(time.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ"))
            except ValueError:
                checked_at = None  # falls back to "now" in set_rpki_coverage -- still correct, just less precise

        seeded = 0
        for asn_str, row in summary.get("per_asn", {}).items():
            store.set_rpki_coverage(conn, int(asn_str), row["sample_size"], row["covered_count"],
                                     checked_at=checked_at)
            seeded += 1
        conn.commit()
        return seeded
    finally:
        conn.close()


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
