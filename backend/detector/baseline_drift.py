"""
Reads the commit history of docs/baseline-summary.json (committed daily by
.github/workflows/refresh-baseline.yml, or manually) and reports per-ASN
prefix count drift between snapshots. This is the "is this data going
stale, and how fast" signal.

Needs at least 2 historical commits of the summary file to show real drift;
with only 1 it reports that plainly instead of fabricating a trend.

Uses the GitHub REST API instead of local `git log`/`git show`. Found via
live verification, not assumed: on Render, this always reported "1
snapshot" no matter how many real commits existed on GitHub, because
Render's deploy checkout doesn't carry full git history (shallow clone) --
local `git log` on the deployed filesystem genuinely can't see prior
commits. Querying GitHub's API directly works the same locally and
deployed, since it doesn't depend on the local clone's depth at all.
Results are cached in-memory for CACHE_TTL_SECONDS to stay well under
GitHub's 60/hour unauthenticated rate limit even if this endpoint is
polled frequently.
"""
import base64
import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

GITHUB_REPO = "HalcyonVector/India-BGP-Hijack-Monitor"
SUMMARY_PATH = "docs/baseline-summary.json"
GITHUB_API = f"https://api.github.com/repos/{GITHUB_REPO}"
CACHE_TTL_SECONDS = 600

_cache = {"result": None, "fetched_at": 0}


def _github_get(url, params=None):
    resp = requests.get(url, params=params, timeout=15,
                         headers={"Accept": "application/vnd.github+json"})
    resp.raise_for_status()
    return resp.json()


def get_historical_snapshots():
    """Return [(commit_sha_short, commit_date, summary_dict), ...] oldest first."""
    commits = _github_get(f"{GITHUB_API}/commits", params={"path": SUMMARY_PATH, "per_page": 100})
    snapshots = []
    for commit in reversed(commits):  # API returns newest first; we want oldest first
        sha = commit["sha"]
        date = commit["commit"]["committer"]["date"]
        try:
            file_data = _github_get(f"{GITHUB_API}/contents/{SUMMARY_PATH}", params={"ref": sha})
            content = base64.b64decode(file_data["content"]).decode("utf-8")
            snapshots.append((sha[:8], date, json.loads(content)))
        except (requests.RequestException, KeyError, json.JSONDecodeError):
            continue
    return snapshots


def compute_drift(use_cache=True):
    now = time.monotonic()
    if use_cache and _cache["result"] is not None and (now - _cache["fetched_at"]) < CACHE_TTL_SECONDS:
        return _cache["result"]

    result = _compute_drift_uncached()
    _cache["result"] = result
    _cache["fetched_at"] = now
    return result


def _compute_drift_uncached():
    snapshots = get_historical_snapshots()
    if len(snapshots) < 2:
        return {
            "status": "insufficient_history",
            "message": f"Only {len(snapshots)} snapshot(s) of {SUMMARY_PATH} on GitHub -- "
                       f"drift needs at least 2. refresh-baseline.yml adds one per day.",
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
        "total_history": [
            {"date": date, "total": s.get("total_prefixes")} for _, date, s in snapshots
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
