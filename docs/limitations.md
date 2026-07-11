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
- **RPKI coverage is inconsistent for Indian address space.** Spot-checked
  live: some Airtel blocks return RPKI status "unknown" (no ROA published),
  not "valid" or "invalid" — meaning RPKI can corroborate a hijack when a
  ROA exists, but can't be relied on alone. Detection here is baseline
  (origin-ASN) comparison first, RPKI second.
- **Verified precision, not recall, against real live traffic.** A 60-second
  live run against real BGP traffic processed 59 real messages and flagged
  zero false positives — confirms the detector doesn't cry wolf on normal
  announcements. It did NOT observe an actual real hijack live (none
  occurred in that window), so recall (does it actually catch a real
  hijack when one happens) is verified via 5 unit tests against fabricated
  fixtures (`backend/detector/test_detection_logic.py`), not a real
  incident. Be precise about this distinction if presenting the project.
- **Baseline goes stale without re-running `baseline.py`.** ISPs add and
  retire prefixes over time; a stale baseline will eventually flag
  legitimate new announcements as false positives, or miss retired-prefix
  hijacks. Re-run periodically (daily is reasonable) — not automated yet.
- **No outbound alerting.** Events are written to SQLite and shown on the
  dashboard; there's no email/webhook/SMS alert. Deliberately out of scope
  — sending messages on your behalf needs explicit permission each time,
  and isn't needed for a demo/portfolio project.
- **Single point of collection.** The monitor runs on one machine; if it's
  off, nothing is being watched. A production tool would run redundant
  collectors. Fine for a demo, worth naming explicitly as a gap.
- **`monitor_status` (messages-processed counter) requires a commit after
  every message, not just ones that flag an event** — this was a real bug
  found during verification: the counter silently stayed at 0 after a real
  60s run because the DB write was only committed inside the (rare)
  event-flagging branch. Fixed in `backend/detector/monitor.py`; caught by
  actually checking the dashboard against the DB, not by assuming the code
  was correct because it looked right.
