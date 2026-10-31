"""
One-off: rebuild docs/baseline-history.json from the git history of
docs/baseline-summary.json. Run from a full (non-shallow) clone. After this,
refresh-baseline.yml appends one entry per run, so it never needs re-running.
"""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = "docs/baseline-summary.json"
HISTORY = ROOT / "docs" / "baseline-history.json"


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout


entries = []
for sha in reversed(git("log", "--format=%H", "--", SUMMARY).split()):
    try:
        s = json.loads(git("show", f"{sha}:{SUMMARY}"))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        continue
    entries.append({
        "generated_at_utc": s.get("generated_at_utc"),
        "total_prefixes": s.get("total_prefixes"),
        "per_asn": s.get("per_asn", {}),
    })

HISTORY.write_text(json.dumps(entries, separators=(",", ":")) + "\n", encoding="utf-8")
print(f"wrote {len(entries)} snapshots to {HISTORY.relative_to(ROOT)}")
