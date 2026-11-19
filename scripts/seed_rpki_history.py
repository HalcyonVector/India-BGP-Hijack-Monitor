"""
One-off: rebuild docs/history/rpki-coverage.json from the git history of
docs/rpki-coverage-summary.json. Run from a full (non-shallow) clone. After
this, refresh-rpki-coverage.yml appends one entry per weekly run.
"""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SUMMARY = "docs/rpki-coverage-summary.json"
HISTORY = ROOT / "docs" / "history" / "rpki-coverage.json"


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, check=True,
                          capture_output=True, text=True).stdout


entries = {}
for sha in reversed(git("log", "--format=%H", "--", SUMMARY).split()):
    try:
        s = json.loads(git("show", f"{sha}:{SUMMARY}"))
    except (subprocess.CalledProcessError, json.JSONDecodeError):
        continue
    when = s.get("generated_at_utc")
    # Counts, not just percentages: each week is a fresh random sample, so
    # the dashboard pools weeks and shows confidence intervals from these.
    per_asn = {asn: [v["sample_size"], v["covered_count"]] for asn, v in (s.get("per_asn") or {}).items()
               if v.get("sample_size")}
    if when and per_asn:
        entries[when] = {"generated_at_utc": when, "sampled": per_asn}

HISTORY.parent.mkdir(parents=True, exist_ok=True)
rows = [entries[k] for k in sorted(entries)]
HISTORY.write_text(json.dumps(rows, separators=(",", ":")) + "\n", encoding="utf-8")
print(f"wrote {len(rows)} weekly samples to {HISTORY.relative_to(ROOT)}")
