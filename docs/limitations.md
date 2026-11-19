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
  contains my ASN". That's wrong for hijack detection: a hijacker
  announcing your prefix has a path that does NOT contain your ASN at all,
  so path-based filtering would silently miss real hijacks. This monitor
  filters by prefix instead, which correctly surfaces an announcement of
  your address space regardless of who originates it.
- **RPKI coverage is inconsistent for Indian address space, and varies
  sharply by ASN.** `rpki_coverage.py` samples 30 baseline prefixes per
  tracked ASN. The latest weekly sample (2026-11-09) found real coverage
  from **50%** (Bharti Airtel Ltd's primary block, AS24560) up to **100%**
  (BSNL, ACT Fibernet, Vodafone Idea, RailTel), with the rest between 87%
  and 97%. Earlier samples had AS24560 anywhere from 40% to 63%. This means RPKI can
  strongly corroborate a hijack for some ASNs and barely help for others.
  Detection here is baseline (origin-ASN) comparison first, RPKI second.
  The coverage number itself is a 30-prefix sample per ASN, not exhaustive
  (checking all ~20k baseline prefixes would mean thousands of RIPEstat
  calls), so it is shown as "(sample of N)" on the dashboard rather than presented
  as a precise figure.
- **Verified precision, not recall, against real live traffic.** An earlier
  60-second run reported "59 messages, zero false positives", but that was
  hollow: the handler read `announcement["prefix"]` while RIS Live sends a
  `prefixes` list, so a swallowed `KeyError` skipped every announcement.
  After the fix, a ~15-minute run (~48k prefix updates) flagged real
  origin differences, nearly all of them RPKI-authorized customer
  sub-blocks (now `info`), plus a few no-ROA cases (`warning`). Airtel's
  ASNs announcing each other's space were false positives, now handled as
  siblings in `targets.py`: first AS24560 / AS9498, then a later run
  surfaced AS45609, Airtel's mobile network, which isn't tracked at all.
  Sibling groups are a hand-maintained list, so other multi-ASN companies
  may still need adding. It did NOT observe an actual real hijack live (none
  occurred in that window), so recall (does it actually catch a real
  hijack when one happens) is verified via unit tests against fabricated
  fixtures (`backend/detector/test_detection_logic.py`), not a real
  incident. Be precise about this distinction if presenting the project.
- **Baseline staleness is now partially, not fully, automated.**
  `.github/workflows/refresh-baseline.yml` rebuilds the baseline daily in
  CI and commits a summary (`docs/baseline-summary.json`), which keeps the
  *repo* honest about current counts and catches RIPEstat API breakage
  early. It does NOT update your own locally-running instance's SQLite DB;
  that still needs a manual `baseline.py` re-run (or your own scheduled
  task) to stay current for live detection.
- **No outbound alerting.** Events are written to SQLite and shown on the
  dashboard; there's no email/webhook/SMS alert. Deliberately out of scope:
  sending messages on your behalf needs explicit permission each time,
  and isn't needed for a demo/portfolio project.
- **Single point of collection, though it can now persist.**
  `run_monitor.bat` + Windows Task Scheduler (`docs/scheduling.md`) lets
  the monitor auto-restart and run continuously instead of only during a
  manual session, which is what makes the dashboard's "Live" badge mean
  something over time. It's still one machine with no redundant
  collectors, so if that machine is off, nothing is being watched.
- **`monitor_status` (messages-processed counter) requires a commit after
  every message, not just ones that flag an event.** This was a real bug
  found during verification: the counter silently stayed at 0 after a real
  60s run because the DB write was only committed inside the (rare)
  event-flagging branch. Fixed in `backend/detector/monitor.py`; caught by
  actually checking the dashboard against the DB, not by assuming the code
  was correct because it looked right.
- **The free Render deployment sleeps after 15 minutes of no HTTP traffic**,
  which kills the monitor's WebSocket connection too, since it runs inside
  the same process (`RUN_MONITOR_INLINE=1`, see `docs/deployment.md`), and
  takes about 50 seconds to wake. So live monitoring only runs while someone
  has the dashboard open. An uptime pinger is deliberately not used: Render's
  750 free instance hours a month are shared across every free service on
  the account, and one always-on service would use about 720 of them.
  Instead the dashboard paints saved data (the visitor's last session, or
  `docs/dashboard-snapshot.json`, saved daily by CI) while the server wakes,
  and says how old it is.
- **Render's free tier has no persistent disk add-on**, so every redeploy
  or restart gets a fresh filesystem, and the SQLite DB (baseline, events,
  RPKI coverage) resets. Two things now self-heal without any manual step:
  baseline (rebuilds automatically on boot if empty) and **RPKI coverage**
  (`backend/detector/rpki_coverage.py`'s `seed_from_committed_summary()`
  loads the last CI-committed `docs/rpki-coverage-summary.json` into the DB
  synchronously at boot, so real data is available within seconds of a restart
  rather than "not yet sampled", and with `RUN_RPKI_REFRESH_INLINE=1` set, a
  background thread re-samples live data to bring it fully current within
  ~5 minutes). Verified locally: wiped the RPKI table, restarted, real
  percentages were back within 3 seconds, and the preserved `checked_at`
  timestamp correctly matched the committed snapshot's real generation
  time rather than falsely claiming "just checked now". Accumulated
  **event history** still does not self-heal (there's no committed
  snapshot of past events to seed from, by design: events are live
  detections, not baseline/config data) and genuinely resets on redeploy.
- **The live deployment intermittently 404s on real endpoints.** Confirmed
  live at https://india-bgp-hijack-monitor.onrender.com on 2026-07-11:
  1 of 6 rapid requests to `/api/asns` returned 404 despite the route being
  correctly registered (confirmed via `/openapi.json`); a different request
  to `/api/events` also 404'd once and then succeeded on retry. The
  failures rotate across different endpoints rather than one being broken,
  and every failure self-resolved on the next request. Most likely cause:
  the free tier's limited shared CPU under contention between the request-
  handling event loop and the monitor's background WebSocket thread
  (`RUN_MONITOR_INLINE=1`), not a routing or code bug. The dashboard's
  15-second polling naturally retries and recovers from this, and a banner
  only appears after repeated failures.
- **Baseline drift used to be permanently broken on Render, and the root
  cause was Render-specific.** `backend/detector/baseline_drift.py`
  originally shelled out to local `git log`/`git show` to read prior
  `docs/baseline-summary.json` commits. That works fine locally but always
  reported "1 snapshot" on the live deployment no matter how many real
  commits existed on GitHub, confirmed by checking GitHub's commit history
  directly and comparing against what the deployed instance saw. Root
  cause: Render's deploy checkout is a shallow git clone, so local git
  commands on the deployed filesystem structurally cannot see history that
  exists on GitHub. The first fix queried the GitHub REST API instead, which
  worked until the history passed ~60 commits: a cold start (frequent on
  Render's free tier, which sleeps) needed one call per commit, so it hit
  GitHub's 60/hour unauthenticated limit every time and the dashboard showed
  "GitHub API unavailable". Now `refresh-baseline.yml` appends each
  snapshot to `docs/baseline-history.json`, and the endpoint reads that
  committed file: no network calls, no rate limit.
- **The scheduled (cron) GitHub Actions workflows have not been reliably
  firing on schedule.** `refresh-baseline.yml` (daily) and
  `refresh-rpki-coverage.yml` (weekly) both had their cron times offset a
  few minutes past the hour to avoid GitHub's documented on-the-hour
  scheduling delay, but the daily workflow still had not fired 8+ hours
  past its window when checked. The workflow file's content on GitHub was
  confirmed byte-correct (not a YAML or syntax bug), so this looks like a
  genuine GitHub Actions scheduler reliability limitation rather than a
  repo-level bug, and outside this project's control to force-fix. The
  `workflow_dispatch` manual trigger in the GitHub Actions UI remains a
  reliable fallback if a fresh snapshot is needed before the cron catches
  up.
- **Route-leak detection is a Tier-1 heuristic, not a relationship model.**
  `find_route_leak()` flags a path where a network outside a fixed list of
  transit-free (Tier-1) ASNs sits between two of them. That needs no
  external data and caught the 2019 Verizon leak in the archived data
  (`scripts/replay_incidents.py`), naming AS396531. It cannot see a leak
  between regional networks: the 2024 leak of 1.1.1.0/24 through AS262504
  was recorded by RIS but has no Tier-1 on both sides, so it was missed.
  The Tier-1 list is hand-maintained; a network wrongly on or off it would
  cause false leads or misses. Leak events are `warning`, never `critical`.
- **The captured history is a sample of time.** `capture.yml` watches for
  20 minutes four times a day (80 minutes a day). Daily counts describe
  watched time, which is why churn is normalised per hour watched. An
  incident that starts and ends between captures is missing from the
  history, though the live dashboard sees it if someone has it open.
- **The incident replay uses one or two collectors per incident** and a
  small hand-written baseline (the victim's block and origin from the cited
  write-up), not the full Indian baseline. It shows the detection functions
  work on real recorded data; it is not a recall rate for Indian space.
- **Weekly RPKI samples swing by chance.** Each week draws a fresh random
  30 prefixes per network, so a single week for Airtel's AS24560 has ranged
  from 37% to 67%. The dashboard pools all weeks (600 prefixes per network
  by November 2026) and shows a 95% Wilson interval instead.
