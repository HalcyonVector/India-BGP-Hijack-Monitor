# Running the monitor as a background service

`monitor.py` holds a persistent WebSocket connection to RIS Live — it's not
a periodic script, so "scheduling" it means keeping it running continuously,
not running it on a timer. `run_monitor.bat` wraps it in a restart loop (if
the WebSocket drops or the process crashes, it restarts after 10s) and logs
to `logs\monitor.log`.

## Windows Task Scheduler setup

1. Task Scheduler → Create Task
2. General: run whether the user is logged on or not, if you want it to
   survive logoff. Name it e.g. "BGP Monitor".
3. Trigger: **At startup** (or **At log on**) — not a repeating interval,
   since this is one long-running process, not a short script.
4. Action: Start a program → `D:\Projects\India-BGP-Hijack-Monitor\run_monitor.bat`
5. Settings: "If the task is already running" → **Do not start a new
   instance** (prevents duplicate WebSocket connections).
6. Save, then run it once manually to confirm it starts and writes to
   `logs\monitor.log`.

This is what makes the dashboard's "Live" badge mean something over time
instead of only during a manual demo session — `/api/status`'s
`last_message_at` only stays recent while something is actually consuming
the RIS Live stream.

## Verifying it's actually running

```bash
curl http://127.0.0.1:8000/api/status
```

Check `last_message_at` is within the last couple of minutes. Or just watch
the dashboard's status badge at the top of the page.
