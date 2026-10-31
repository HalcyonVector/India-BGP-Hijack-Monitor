"""
Reports per-ASN prefix count drift between the oldest and newest baseline
snapshots in docs/baseline-history.json. This is the "is this data going
stale, and how fast" signal.

refresh-baseline.yml appends one snapshot per run (the same data it writes
to docs/baseline-summary.json) and commits it, so the deployed checkout
always carries the full history as a plain file. Needs at least 2 snapshots
to show real drift; with fewer it says so instead of fabricating a trend.

History of how this got here, found via live verification:
- Local `git log` never worked on Render: the deploy checkout is shallow,
  so it always saw 1 snapshot.
- Querying the GitHub REST API instead worked until the history grew. A
  cold start (frequent on Render's free tier, which sleeps) cost one call
  for the commit list plus one per commit, ~110 calls against the 60/hour
  unauthenticated limit, so it failed every time.
Reading a committed file needs no network and has no rate limit.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

HISTORY_PATH = Path(__file__).parent.parent.parent / "docs" / "baseline-history.json"

_cache = {"mtime": None, "result": None}


def load_snapshots():
    """Return [{generated_at_utc, total_prefixes, per_asn}, ...] oldest first."""
    try:
        snapshots = json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return []
    except json.JSONDecodeError as e:
        raise RuntimeError(f"{HISTORY_PATH.name} is not valid JSON: {e}") from e
    return sorted(snapshots, key=lambda s: s.get("generated_at_utc") or "")


def compute_drift(use_cache=True):
    try:
        mtime = HISTORY_PATH.stat().st_mtime
    except FileNotFoundError:
        mtime = None
    if use_cache and _cache["result"] is not None and _cache["mtime"] == mtime:
        return _cache["result"]
    result = _compute_drift_uncached()
    _cache.update(mtime=mtime, result=result)
    return result


def _compute_drift_uncached():
    snapshots = load_snapshots()
    if len(snapshots) < 2:
        return {
            "status": "insufficient_history",
            "message": f"Only {len(snapshots)} snapshot(s) in docs/{HISTORY_PATH.name} -- "
                       f"drift needs at least 2. refresh-baseline.yml adds one per day.",
            "snapshots": len(snapshots),
        }

    oldest, newest = snapshots[0], snapshots[-1]
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
        "compared": {"from": oldest.get("generated_at_utc"), "to": newest.get("generated_at_utc")},
        "total_before": oldest.get("total_prefixes"),
        "total_after": newest.get("total_prefixes"),
        "total_history": [
            {"date": s.get("generated_at_utc"), "total": s.get("total_prefixes")} for s in snapshots
        ],
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
    print_report(compute_drift(use_cache=False))
