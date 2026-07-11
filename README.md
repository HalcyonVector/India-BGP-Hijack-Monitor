# India BGP Hijack/Leak Monitor

Watches real-time BGP route announcements for 9 major Indian networks (Airtel, Jio, BSNL, ACT Fibernet, Vodafone Idea, Tata Communications, Sify, RailTel). Flags a prefix if it gets announced by an ASN that doesn't normally own it, cross-checks RPKI where a ROA exists, and serves the result through a FastAPI backend and a dashboard. Built on free, keyless public data — RIPE RIS Live and RIPEstat.

---

## Disclaimer

Educational/portfolio project, not a production security tool. Detection coverage is partial (the ~8 largest blocks per tracked ASN, not full coverage — see Known Limitations). Verified for precision against real BGP traffic (zero false positives in a live run) and for logic correctness against fabricated hijack fixtures (5/5 unit tests) — not against a real hijack, since none occurred during testing. No credentials, no paid APIs, no outbound alerting anywhere in this project.

---

## Features

### Core Detection

- Real-time subscription to RIPE RIS Live, filtered by prefix, not AS path — a hijacker's path never contains the victim's ASN, so path filtering would miss real hijacks
- Baseline comparison: each tracked ASN's currently-announced prefixes (RIPEstat) act as ground truth; a live announcement by a different origin ASN gets flagged
- More-specific detection: `moreSpecific: true` subscriptions catch a /24 hijacked out of a normally /16 block, not just exact-prefix mismatches
- RPKI cross-check on every flagged event where a ROA exists; an explicit "invalid" result escalates severity to critical
- Observed hijacker ASN is resolved to its holder org name (RIPEstat as-overview) on every event, not left as a bare number
- `baseline_drift.py` / `/api/baseline-drift` reads the git history of the daily-committed baseline summary and reports real per-ASN prefix count changes over time

### Backend & API

- FastAPI, read-only (`/api/status`, `/api/asns`, `/api/events`), CORS-enabled
- SQLite: `baseline_prefixes`, `events`, `monitor_status` — no external database
- Events tagged `info` / `warning` / `critical`

### Frontend

- Stat tiles (baseline prefixes, tracked ASNs, messages processed, events flagged), auto-refresh every 15s
- Live/not-running status badge based on time since the last processed message
- Tracked-ASN grid, filterable event table

### RPKI Coverage

- `rpki_coverage.py` samples up to 30 baseline prefixes per ASN and reports what fraction have a ROA at all — real numbers range from 40% (Airtel's primary block) to 100% (Jio, BSNL, ACT, Tata Communications)
- Shown per-ASN on the dashboard as "X% ROA-covered (sample of N)" — explicitly labeled as a sample, not exhaustive

### Automation

- `.github/workflows/test.yml` — CI runs the detection-logic tests on every push
- `.github/workflows/refresh-baseline.yml` — daily cron job rebuilds the baseline against live RIPEstat, re-runs the tests against it, and commits a summary (`docs/baseline-summary.json`) back to the repo
- `.github/workflows/refresh-rpki-coverage.yml` — weekly cron job re-samples RPKI coverage and commits `docs/rpki-coverage-summary.json`
- `run_monitor.bat` — restart-on-crash wrapper so the monitor can run as a persistent Windows background service (see `docs/scheduling.md`) instead of only during a manual demo session

### Verification

- `test_detection_logic.py` — 5 fixtures against real Airtel/Jio blocks with synthetic wrong-origin scenarios
- Monitor has been run against real BGP traffic and confirmed to process real messages with zero false positives

---

## Tech Stack

| Layer | Technology | Details |
|-------|-----------|---------|
| Backend | FastAPI + Uvicorn | Read-only REST API, OpenAPI docs at `/docs` |
| Real-time data | RIPE RIS Live | Free WebSocket BGP firehose, server-side prefix filtering |
| Baseline data | RIPEstat API | Free, keyless |
| WebSocket client | websocket-client | |
| HTTP client | requests | |
| Database | SQLite (`sqlite3`) | |
| Frontend | Vanilla HTML/CSS/JS | No framework |
| CI | GitHub Actions | Runs `test_detection_logic.py` on push |

---

## Prerequisites

- Python 3.11+
- No API keys, no accounts, no paid services

---

## Quick Start

```bash
pip install -r requirements.txt

python db/store.py                          # create schema
python backend/detector/baseline.py         # build the real baseline (~21k prefixes, 9 ASNs)
python backend/detector/test_detection_logic.py   # verify detection logic
python backend/detector/monitor.py          # run the live monitor (Ctrl+C to stop)
```

In separate terminals:

```bash
python -m uvicorn backend.api:app --port 8000
python -m http.server 8090 --directory frontend   # then open http://localhost:8090
```

| Service | URL |
|---------|-----|
| Dashboard | http://localhost:8090 |
| API | http://localhost:8000 |
| API Docs | http://localhost:8000/docs |

---

## How Detection Works

1. **Baseline** — fetch each tracked ASN's currently-announced prefixes from RIPEstat.
2. **Live monitor** — subscribe to RIS Live filtered by prefix (the ~8 largest blocks per ASN, `moreSpecific: true`).
3. **Compare** — observed origin ASN vs. the baseline's expected owner. Mismatch → event, with an RPKI check where a ROA exists.
4. **Serve** — FastAPI reads SQLite; the dashboard polls every 15s.

---

## Project Structure

```
India-BGP-Hijack-Monitor/
├── README.md
├── LICENSE
├── requirements.txt
├── .gitignore
├── run_monitor.bat                        # Restart-on-crash wrapper for a persistent monitor
├── .github/workflows/
│   ├── test.yml                           # CI: runs detection tests on push
│   └── refresh-baseline.yml               # CI: daily baseline rebuild + summary commit
│
├── backend/
│   ├── api.py                             # FastAPI: status, asns, events, baseline-drift
│   └── detector/
│       ├── targets.py                     # 9 tracked Indian ASNs
│       ├── baseline.py                    # Fetches announced prefixes per ASN
│       ├── monitor.py                     # RIS Live listener, flags origin mismatches
│       ├── rpki_coverage.py               # Sample-based RPKI ROA coverage per ASN
│       ├── baseline_drift.py              # Per-ASN prefix drift from git history
│       └── test_detection_logic.py        # Unit tests against fabricated fixtures
│
├── db/
│   ├── schema.sql
│   └── store.py
│
├── frontend/
│   └── index.html
│
└── docs/
    ├── limitations.md
    ├── scheduling.md                      # Running the monitor as a background service
    ├── baseline-summary.json              # Committed daily by refresh-baseline.yml
    └── rpki-coverage-summary.json         # Committed weekly by refresh-rpki-coverage.yml
```

---

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| GET | `/api/status` | Messages processed, total events, baseline size, tracked ASN count, last message time |
| GET | `/api/asns` | Tracked ASNs with per-ASN baseline prefix counts and RPKI coverage |
| GET | `/api/events` | Recent events, optional `?severity=` and `?limit=` (default 50, max 500) |
| GET | `/api/baseline-drift` | Per-ASN prefix count drift between the oldest and newest committed baseline summary |

---

## Configuration

No environment variables or API keys. `backend/detector/targets.py` holds the tracked ASN list as a plain dict.

---

## Architecture

```
                    ┌──────────────────────────────┐
                    │   Dashboard (Port 8090)      │
                    │   Vanilla HTML/CSS/JS         │
                    │   Polls API every 15s         │
                    └──────────┬────────────────────┘
                               │ REST (fetch)
                    ┌──────────▼────────────────────┐
                    │   FastAPI (Port 8000)         │
                    │   /api/status /asns /events   │
                    └──────────┬────────────────────┘
                               │
                    ┌──────────▼────────────────────┐
                    │   SQLite                       │
                    │   baseline_prefixes / events /│
                    │   monitor_status               │
                    └──────────▲────────────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                 │
   ┌──────────▼────────┐ ┌────▼─────────┐ ┌─────▼──────────┐
   │ baseline.py         │ │ monitor.py   │ │ RPKI check      │
   │ RIPEstat            │ │ RIS Live WS  │ │ (RIPEstat)      │
   │ announced-prefixes  │ │ prefix-filtered│ │ on flagged events│
   └──────────────────────┘ └──────────────┘ └─────────────────┘
```

---

## Data Sources

| Source | Type | API Key? | What It Provides |
|--------|------|----------|-------------------|
| RIPE RIS Live | Real-time BGP stream | No | Live BGP UPDATE messages, server-side prefix filtering |
| RIPEstat announced-prefixes | Baseline | No | Currently-announced prefixes per ASN |
| RIPEstat rpki-validation | Corroboration | No | ROA validation status (valid/invalid/unknown) |
| RIPEstat as-overview | Attribution | No | Holder org name for an ASN (used to name the observed hijacker) |

---

## Testing

```bash
python backend/detector/test_detection_logic.py
```

5 fixture-based checks: legitimate exact match, legitimate sub-prefix, fabricated exact-prefix hijack, fabricated sub-prefix hijack, unrelated prefix correctly ignored. No live traffic or API keys required. Also runs in CI on every push.

---

## Available Scripts

| Script | Command | Description |
|--------|---------|--------------|
| Init schema | `python db/store.py` | Create the SQLite schema |
| Build baseline | `python backend/detector/baseline.py` | Fetch current announced prefixes |
| Run tests | `python backend/detector/test_detection_logic.py` | Verify detection logic |
| RPKI coverage | `python backend/detector/rpki_coverage.py` | Sample RPKI ROA coverage per ASN (~5 min) |
| Baseline drift | `python backend/detector/baseline_drift.py` | Per-ASN prefix count drift from git history |
| Live monitor (timed) | `python backend/detector/monitor.py --max-seconds 60` | Watch real BGP traffic for 60s |
| Live monitor (persistent) | `run_monitor.bat` | Run indefinitely with auto-restart (see `docs/scheduling.md`) |
| Backend | `python -m uvicorn backend.api:app --port 8000` | Start the API server |
| Frontend | `python -m http.server 8090 --directory frontend` | Serve the dashboard |

---

## Troubleshooting

**Dashboard shows no data:** confirm the backend is reachable — `curl http://127.0.0.1:8000/api/status`. If that fails, start it with `python -m uvicorn backend.api:app --port 8000`.

**`baseline.py` returns 0 prefixes for an ASN:** re-run the script; if it persists, check the ASN directly at `https://stat.ripe.net/AS<number>`.

**Monitor connects but never flags anything:** expected — hijacks are rare. Use `test_detection_logic.py` to confirm the logic itself works.

**Port already in use:** run on a different port and update the `API` constant in `frontend/index.html`:

```bash
python -m uvicorn backend.api:app --port 8001
python -m http.server 8091 --directory frontend
```

---

## Known Limitations

Full list in [docs/limitations.md](docs/limitations.md):

- Subscription coverage is the ~8 largest blocks per ASN, not full coverage
- RPKI coverage varies sharply by ASN (40%-100% in a 30-sample check) and the coverage stat itself is a sample, not exhaustive
- Verified for precision, not recall against a real incident (recall verified via fixtures instead)
- Baseline auto-refreshes daily via CI, but that only updates the repo's summary — you still need to re-run `baseline.py` locally for your own running instance
- No outbound alerting — dashboard only
- The monitor can now run persistently (`run_monitor.bat` + Task Scheduler), but it's still a single point of collection — one machine, no redundancy

---

## Project Stats

| Metric | Count |
|--------|-------|
| Tracked ASNs | 9 |
| Baseline prefixes | 20,737 |
| API endpoints | 3 |
| Detection logic unit tests | 5 |
| Backend Python files | 7 |
| Frontend files | 1 |
| CI workflows | 2 |
| RPKI coverage range (sampled) | 40%-100% |

---

## Author

Independent project — no personal attribution.

---

## License

MIT License — see [LICENSE](./LICENSE).
