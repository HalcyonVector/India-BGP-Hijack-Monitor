# Known limitations

- **Subscription coverage is partial by design.** The 9 tracked ASNs
  collectively own 20,737 baseline prefixes; subscribing to every single one
  individually over one WebSocket connection isn't practical. The monitor
  subscribes to the ~8 largest (least-specific) blocks per ASN (72 total),
  with `moreSpecific: true` so any more-specific sub-announcement within
  those blocks is still caught. A hijack of a prefix that falls entirely
  outside these 72 covering blocks would be missed. This is a documented
  scoping trade-off, not an oversight.
- **Filtering is prefix-based, not path-based, and that distinction matters.**
  An earlier draft of this project considered filtering RIS Live by "path
  contains my ASN" — that's wrong for hijack detection: a hijacker
  announcing your prefix has a path that does NOT contain your ASN at all,
  so path-based filtering would silently miss real hijacks. This monitor
  filters by prefix instead, which correctly surfaces an announcement of
  your address space regardless of who originates it.
- **RPKI coverage is inconsistent for Indian address space, and varies
  sharply by ASN.** `rpki_coverage.py` sampled 30 baseline prefixes per
  tracked ASN and found real coverage from **40%** (Bharti Airtel Ltd's
  primary block, AS24560) up to **100%** (Jio, BSNL, ACT Fibernet, Tata
  Communications) — confirmed live, not estimated. This means RPKI can
  strongly corroborate a hijack for some ASNs and barely help for others.
  Detection here is baseline (origin-ASN) comparison first, RPKI second.
  The coverage number itself is a 30-prefix sample per ASN, not exhaustive
  (checking all ~20k baseline prefixes would mean thousands of RIPEstat
  calls) — shown as "(sample of N)" on the dashboard rather than presented
  as a precise figure.
- **Verified precision, not recall, against real live traffic.** A 60-second
  live run against real BGP traffic processed 59 real messages and flagged
  zero false positives — confirms the detector doesn't cry wolf on normal
  announcements. It did NOT observe an actual real hijack live (none
  occurred in that window), so recall (does it actually catch a real
  hijack when one happens) is verified via 5 unit tests against fabricated
  fixtures (`backend/detector/test_detection_logic.py`), not a real
  incident. Be precise about this distinction if presenting the project.
- **Baseline staleness is now partially, not fully, automated.**
  `.github/workflows/refresh-baseline.yml` rebuilds the baseline daily in
  CI and commits a summary (`docs/baseline-summary.json`), which keeps the
  *repo* honest about current counts and catches RIPEstat API breakage
  early. It does NOT update your own locally-running instance's SQLite DB
  — that still needs a manual `baseline.py` re-run (or your own scheduled
  task) to stay current for live detection.
- **No outbound alerting.** Events are written to SQLite and shown on the
  dashboard; there's no email/webhook/SMS alert. Deliberately out of scope
  — sending messages on your behalf needs explicit permission each time,
  and isn't needed for a demo/portfolio project.
- **Single point of collection, though it can now persist.**
  `run_monitor.bat` + Windows Task Scheduler (`docs/scheduling.md`) lets
  the monitor auto-restart and run continuously instead of only during a
  manual session, which is what makes the dashboard's "Live" badge mean
  something over time. It's still one machine with no redundant
  collectors — if that machine is off, nothing is being watched.
- **`monitor_status` (messages-processed counter) requires a commit after
  every message, not just ones that flag an event** — this was a real bug
  found during verification: the counter silently stayed at 0 after a real
  60s run because the DB write was only committed inside the (rare)
  event-flagging branch. Fixed in `backend/detector/monitor.py`; caught by
  actually checking the dashboard against the DB, not by assuming the code
  was correct because it looked right.
- **The free Render deployment sleeps after 15 minutes of no HTTP traffic**,
  which kills the monitor's WebSocket connection too, since it runs inside
  the same process (`RUN_MONITOR_INLINE=1`, see `docs/deployment.md`). The
  documented workaround is a free external uptime pinger (UptimeRobot /
  cron-job.org) hitting `/api/status` every 10-14 minutes — a real
  mitigation, not a guarantee of continuous uptime. Genuine 24/7 uptime
  needs a paid tier, which is out of scope for this project.
- **Render's free tier has no persistent disk add-on**, so every redeploy
  or restart gets a fresh filesystem — the SQLite DB (baseline, events,
  RPKI coverage) resets. Two things now self-heal without any manual step:
  baseline (rebuilds automatically on boot if empty) and **RPKI coverage**
  (`backend/detector/rpki_coverage.py`'s `seed_from_committed_summary()`
  loads the last CI-committed `docs/rpki-coverage-summary.json` into the DB
  synchronously at boot — real data available within seconds of a restart,
  not "not yet sampled" — and with `RUN_RPKI_REFRESH_INLINE=1` set, a
  background thread re-samples live data to bring it fully current within
  ~5 minutes). Verified locally: wiped the RPKI table, restarted, real
  percentages were back within 3 seconds, and the preserved `checked_at`
  timestamp correctly matched the committed snapshot's real generation
  time rather than falsely claiming "just checked now". Accumulated
  **event history** still does not self-heal (there's no committed
  snapshot of past events to seed from, by design — events are live
  detections, not baseline/config data) and genuinely resets on redeploy.
- **The live deployment intermittently 404s on real endpoints** — confirmed
  live at https://india-bgp-hijack-monitor.onrender.com on 2026-07-11:
  1 of 6 rapid requests to `/api/asns` returned 404 despite the route being
  correctly registered (confirmed via `/openapi.json`); a different request
  to `/api/events` also 404'd once and then succeeded on retry. The
  failures rotate across different endpoints rather than one being broken,
  and every failure self-resolved on the next request. Most likely cause:
  the free tier's limited shared CPU under contention between the request-
  handling event loop and the monitor's background WebSocket thread
  (`RUN_MONITOR_INLINE=1`) — not a routing or code bug. The dashboard's
  15-second polling naturally retries and recovers from this; a hard
  refresh always works. If this matters for your use case, moving the
  monitor to a separate paid worker (decoupling it from request handling)
  would remove the contention, but that's out of scope for a free deploy.
