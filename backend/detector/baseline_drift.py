"""
Reads the git history of docs/baseline-summary.json (committed daily by
.github/workflows/refresh-baseline.yml) and reports per-ASN prefix count
drift between snapshots. This is the "is this data going stale, and how
fast" signal -- currently collected by CI but otherwise unused.

Needs at least 2 historical commits of the summary file to show real drift;
with only 1 (a fresh repo) it reports that plainly instead of fabricating
a trend from nothing.
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

SUMMARY_PATH = "docs/baseline-summary.json"


def _run_git(args):
    result = subprocess.run(["git"] + args, capture_output=True, text=True, cwd=Path(__file__).parent.parent.parent)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr}")
    return result.stdout


def get_historical_snapshots():
    """Return [(commit_hash, commit_date, summary_dict), ...] oldest first."""
    log = _run_git(["log", "--format=%H|%cI", "--", SUMMARY_PATH]).strip().splitlines()
    snapshots = []
    for line in reversed(log):  # oldest first
        commit_hash, date = line.split("|", 1)
        try:
            content = _run_git(["show", f"{commit_hash}:{SUMMARY_PATH}"])
            snapshots.append((commit_hash[:8], date, json.loads(content)))
        except (RuntimeError, json.JSONDecodeError):
            continue
    return snapshots


def compute_drift():
    snapshots = get_historical_snapshots()
    if len(snapshots) < 2:
        return {
            "status": "insufficient_history",
            "message": f"Only {len(snapshots)} snapshot(s) of {SUMMARY_PATH} in git history -- "
                       f"drift needs at least 2. refresh-baseline.yml will add one per day.",
            "snapshots": len(snapshots),
        }

    oldest_hash, oldest_date, oldest = snapshots[0]
    newest_hash, newest_date, newest = snapshots[-1]
    oldest_per_asn = oldest.get("per_asn", {})
    newest_per_asn = newest.get("per_asn", {})

    drift = []
    for asn in sorted(set(oldest_per_asn) | set(newest_per_asn), key=int):
        before = oldest_per_asn.get(asn, 0)
        after = newest_per_asn.get(asn, 0)
        if before != after:
            drift.append({"asn": int(asn), "before": before, "after": after, "delta": after - before})

    return {
        "status": "ok",
        "snapshots": len(snapshots),
        "compared": {"from": f"{oldest_date} ({oldest_hash})", "to": f"{newest_date} ({newest_hash})"},
        "total_before": oldest.get("total_prefixes"),
        "total_after": newest.get("total_prefixes"),
        "per_asn_drift": drift,
    }


def print_report(result):
    if result["status"] == "insufficient_history":
        print(result["message"])
        return
    print(f"Comparing {result['compared']['from']} -> {result['compared']['to']} "
          f"({result['snapshots']} snapshots total)")
    print(f"Total prefixes: {result['total_before']} -> {result['total_after']}")
    if not result["per_asn_drift"]:
        print("No per-ASN prefix count changes in this window.")
    for d in result["per_asn_drift"]:
        sign = "+" if d["delta"] > 0 else ""
        print(f"  AS{d['asn']}: {d['before']} -> {d['after']} ({sign}{d['delta']})")


if __name__ == "__main__":
    print_report(compute_drift())
