# Deploying (free): Render (backend) + GitHub Pages (frontend)

## Why this combination

- **Render free Web Service** runs a real, long-running Python process with
  a persistent disk while the instance is up — required here because the
  monitor holds a continuous WebSocket connection and the SQLite DB needs
  to persist between requests. Vercel (serverless, ephemeral filesystem,
  no long-running processes) can't do either of those things for this app.
- **GitHub Pages** serves the static dashboard for free, permanently, from
  the same repo.
- **The honest tradeoff**: Render's free tier sleeps after 15 minutes with
  no HTTP traffic, which would kill the monitor's WebSocket connection too.
  The fix is a free external uptime pinger (step 5) — a real workaround,
  not a guarantee. This is a documented limitation, not hidden. See
  [limitations.md](limitations.md).

---

## Part 1 — Deploy the backend to Render

1. Go to [render.com](https://render.com) and sign up / log in (free,
   GitHub OAuth is the fastest option).
2. Click **New +** → **Blueprint**.
3. Connect your GitHub account if prompted, then select the
   `India-BGP-Hijack-Monitor` repo.
4. Render will detect `render.yaml` in the repo root automatically and
   propose one service: `india-bgp-hijack-monitor` (free plan, Python
   runtime). Review it and click **Apply**.
   - If you'd rather set it up by hand instead of via Blueprint: **New +**
     → **Web Service** → connect the repo → Runtime: `Python 3` → Build
     Command: `pip install -r requirements.txt` → Start Command:
     `python -m uvicorn backend.api:app --host 0.0.0.0 --port $PORT` →
     Instance Type: **Free**. Then add the two environment variables from
     step 5 manually under the service's **Environment** tab.
5. `render.yaml` already sets both required environment variables, but if
   you set the service up by hand, add them under **Environment**:
   - `RUN_MONITOR_INLINE` = `1` (starts the live monitor in a background
     thread inside the API process — see `backend/api.py`)
   - `PYTHONUNBUFFERED` = `1` (so log output shows up in Render's log
     viewer immediately instead of being buffered — without this you'll
     see nothing in the logs for minutes at a time)
6. Click **Create Web Service** (or confirm the Blueprint). First deploy
   takes a few minutes — watch the **Logs** tab. On first boot the API
   process builds the baseline itself (empty DB triggers `build_baseline()`
   automatically, see `backend/api.py`), so the first deploy is slower
   than subsequent ones.
7. Once live, note your service URL — Render gives you something like
   `https://india-bgp-hijack-monitor.onrender.com`.
8. Verify it directly: open `https://YOUR-URL.onrender.com/api/status` in
   a browser. You should see real JSON with `baseline_prefix_count` around
   20,000+. If it's `0`, check the Logs tab for a RIPEstat error.

---

## Part 2 — Point the frontend at your Render URL

1. In `frontend/index.html`, find this line near the top of the
   `<script>` block:
   ```js
   const DEPLOYED_API_URL = 'https://REPLACE-WITH-YOUR-RENDER-URL.onrender.com';
   ```
2. Replace the placeholder with your actual Render URL from Part 1, step 7
   (no trailing slash).
3. Commit and push:
   ```bash
   git add frontend/index.html
   git commit -m "Point deployed frontend at the live Render API"
   git push
   ```

---

## Part 3 — Deploy the frontend to GitHub Pages

1. On GitHub, open the `India-BGP-Hijack-Monitor` repo → **Settings** →
   **Pages** (left sidebar, under "Code and automation").
2. Under **Build and deployment** → **Source**, choose **Deploy from a
   branch**.
3. Under **Branch**, choose `main` and folder `/ (root)`, then **Save**.
4. Wait a minute or two, then your dashboard is live at:
   ```
   https://<your-github-username>.github.io/India-BGP-Hijack-Monitor/frontend/
   ```
   (GitHub Pages serves the whole repo as static files when the source is
   set to root — `frontend/` in the URL is because `index.html` lives in
   that subfolder, not the repo root. GitHub automatically serves
   `index.html` for a directory path, so the trailing `/frontend/` with no
   filename works.)
5. Open that URL and confirm the dashboard loads and shows real data (it's
   now calling your Render backend cross-origin, which works because the
   API already has `allow_origins=["*"]` set).

---

## Part 4 — Keep the free Render instance awake (optional but recommended)

Render's free tier sleeps after 15 minutes of no HTTP traffic. Since the
monitor's WebSocket connection lives inside that same process, sleeping
kills live monitoring until the next request wakes it back up.

1. Sign up for a free account at [UptimeRobot](https://uptimerobot.com) or
   [cron-job.org](https://cron-job.org) (both have permanently free tiers
   for this use case).
2. Create a new monitor/cron job:
   - URL: `https://YOUR-RENDER-URL.onrender.com/api/status`
   - Interval: every 10-14 minutes (must be under Render's 15-minute
     sleep threshold)
3. This keeps the instance awake continuously, which is what makes the
   dashboard's "Live" badge meaningful on the deployed version, not just
   locally.

This is a known, common workaround for free-tier sleep behavior — not a
guarantee of 100% uptime. If you want guaranteed uptime, that requires a
paid tier, which is explicitly out of scope for this project.

---

## Redeploying after code changes

Render auto-redeploys on every push to `main` by default (configurable
under the service's **Settings** → **Build & Deploy**). Just `git push` as
usual.

**Caveat**: Render's free tier does not include a persistent disk add-on,
so a redeploy gets a fresh filesystem — your live baseline/events/RPKI
coverage data resets on every redeploy (the API rebuilds the baseline
automatically on boot if it's empty, so this self-heals, but accumulated
event history and RPKI coverage samples would need `rpki_coverage.py`
re-run manually, or you accept they reset). Documented in
[limitations.md](limitations.md).
