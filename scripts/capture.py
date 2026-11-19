"""
Run the live monitor for a fixed window (in CI, four times a day) and fold
what it saw into committed history files, so the analysis rests on a growing
dataset instead of whatever the free Render instance saw since it last woke.

Writes:
  docs/history/monitor-daily.json  one row per UTC day: minutes watched,
                                   messages, prefix updates per network,
                                   distinct flags by severity and type
  docs/history/flags.json          every distinct flag (prefix + origin, or
                                   leaker for route leaks) with first/last
                                   seen, RPKI status and how many runs saw it
  docs/dashboard-snapshot.json     the API's responses at the end of the
                                   run: the dashboard's first paint while
                                   Render wakes

Usage: python scripts/capture.py --minutes 20   (baseline must be built first)
"""
import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.detector.monitor import run_monitor  # noqa: E402
from backend.detector.targets import TRACKED_ASNS  # noqa: E402
from db import store  # noqa: E402

HISTORY = ROOT / "docs" / "history"
DAILY = HISTORY / "monitor-daily.json"
FLAGS = HISTORY / "flags.json"
SNAPSHOT = ROOT / "docs" / "dashboard-snapshot.json"
DAILY_KEEP_DAYS = 365
FLAGS_KEEP_DAYS = 90
SEVERITIES = ("critical", "warning", "info")


def read_json(path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, separators=(",", ":")) + "\n", encoding="utf-8")


def flag_key(e):
    if e["event_type"] == "possible_route_leak":
        return f"leak|{e['expected_asn']}|{e['observed_origin_asn']}"
    return f"{e['event_type']}|{e['prefix']}|{e['observed_origin_asn']}"


def utc_day(ts):
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def update_daily(start, end, messages, activity, events):
    days = read_json(DAILY, [])
    day = utc_day(start)
    entry = next((d for d in days if d["date"] == day), None)
    if entry is None:
        for d in days:
            d.pop("_keys", None)  # only today's keys are needed to count distinct flags
        entry = {"date": day, "runs": 0, "minutes": 0, "messages": 0,
                 "updates": {}, "flags": {s: 0 for s in SEVERITIES}, "types": {}, "_keys": []}
        days.append(entry)
    entry["runs"] += 1
    entry["minutes"] = round(entry["minutes"] + (end - start) / 60, 1)
    entry["messages"] += messages
    for asn, (a, w) in activity.items():
        prev = entry["updates"].get(str(asn), [0, 0])
        entry["updates"][str(asn)] = [prev[0] + a, prev[1] + w]
    seen = set(entry["_keys"])
    for e in events:
        k = flag_key(e)
        if k in seen:
            continue
        seen.add(k)
        entry["flags"][e["severity"]] = entry["flags"].get(e["severity"], 0) + 1
        entry["types"][e["event_type"]] = entry["types"].get(e["event_type"], 0) + 1
    entry["_keys"] = sorted(seen)
    days = sorted(days, key=lambda d: d["date"])[-DAILY_KEEP_DAYS:]
    write_json(DAILY, days)
    return entry


def update_flags(end, events):
    data = read_json(FLAGS, {"entries": []})
    by_key = {e["key"]: e for e in data["entries"]}
    run_keys = set()
    for e in sorted(events, key=lambda x: x["timestamp"]):
        k = flag_key(e)
        row = by_key.get(k)
        if row is None:
            row = by_key[k] = {"key": k, "type": e["event_type"], "prefix": e["prefix"],
                               "expected_asn": e["expected_asn"], "observed_asn": e["observed_origin_asn"],
                               "first_seen": e["timestamp"], "runs_seen": 0}
        row.update(observed_org=e["observed_origin_org"] or row.get("observed_org"),
                   rpki_status=e["rpki_status"], severity=e["severity"],
                   last_seen=e["timestamp"], as_path=json.loads(e["as_path"]) if e["as_path"] else None)
        if k not in run_keys:
            row["runs_seen"] += 1
            run_keys.add(k)
    cutoff = end - FLAGS_KEEP_DAYS * 86400
    entries = sorted((r for r in by_key.values() if r["last_seen"] >= cutoff),
                     key=lambda r: r["last_seen"], reverse=True)
    write_json(FLAGS, {"updated_at": end, "keep_days": FLAGS_KEEP_DAYS, "entries": entries})
    return len(entries)


def write_snapshot(end):
    from fastapi.testclient import TestClient
    from backend.api import app

    snapshot = {"saved_at": end, "source": "ci-capture"}
    with TestClient(app) as client:
        for key, path in {"status": "/api/status", "asns": "/api/asns", "live": "/api/live",
                          "events": "/api/events?limit=50", "drift": "/api/baseline-drift"}.items():
            resp = client.get(path)
            resp.raise_for_status()
            snapshot[key] = resp.json()
    for e in snapshot["events"]:
        e.pop("raw_message", None)  # never shown, and most of the bytes
    write_json(SNAPSHOT, snapshot)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--minutes", type=float, default=20)
    args = parser.parse_args()

    start = time.time()
    state = run_monitor(max_seconds=int(args.minutes * 60))
    end = time.time()
    if state["count"] == 0:
        # Don't record an empty run as if nothing happened, or replace a good snapshot.
        sys.exit("no messages received: RIS Live unreachable from this runner? Nothing written.")

    conn = store.get_connection()
    activity = {r["asn"]: (r["a"], r["w"]) for r in conn.execute(
        "SELECT asn, SUM(announcements) a, SUM(withdrawals) w FROM activity_minutely "
        "WHERE minute >= ? GROUP BY asn", (int(start // 60 * 60),)) if r["asn"] in TRACKED_ASNS}
    events = [dict(r) for r in conn.execute(
        "SELECT * FROM events WHERE timestamp >= ? ORDER BY timestamp", (start - 300,))]
    conn.close()

    day = update_daily(start, end, state["count"], activity, events)
    kept = update_flags(end, events)
    write_snapshot(end)
    print(f"captured {state['count']} messages, {len(events)} events in {(end - start) / 60:.1f} min; "
          f"today so far: {day['runs']} runs, {sum(day['flags'].values())} distinct flags; "
          f"{kept} flags kept in {FLAGS.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
