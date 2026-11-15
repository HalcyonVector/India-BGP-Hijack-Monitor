# Deploying (free): Render (backend) + GitHub Pages (frontend)

## Why this combination

- **Render free Web Service** runs a real, long-running Python process with
  a working disk while the instance is up. That is required here because the
  monitor holds a continuous WebSocket connection and the SQLite DB needs
  to persist between requests. Vercel (serverless, ephemeral filesystem,
  no long-running processes) can't do either of those things for this app.
- **GitHub Pages** serves the static dashboard for free, permanently, from
  the same repo, along with the committed `docs/*.json` files.
- **The trade-off**: Render's free tier sleeps after 15 minutes with no HTTP
  traffic, which stops the monitor's WebSocket connection too. Instead of
  keeping it awake, the dashboard shows saved data while it wakes (see
  Part 4). This is a documented limitation; see
  [limitations.md](limitations.md).

---

## Part 1: Deploy the backend to Render

1. Go to [render.com](https://render.com) and sign up or log in (GitHub
   OAuth is the fastest option).
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
     Instance Type: **Free**. Then add the environment variables from
     step 5 manually under the service's **Environment** tab.
5. `render.yaml` already sets all required environment variables, but if
   you set the service up by hand, add them under **Environment**:
   - `RUN_MONITOR_INLINE` = `1` (starts the live monitor in a background
     thread inside the API process; see `backend/api.py`)
   - `RUN_RPKI_REFRESH_INLINE` = `1` (re-samples live RPKI coverage in a
     background thread on boot, on top of the instant seed from the
     committed summary; see "Redeploying after code changes" below)
   - `PYTHONUNBUFFERED` = `1` (so log output shows up in Render's log
     viewer immediately instead of being buffered for minutes at a time)

   **If you already created the service before an env var existed**:
   Blueprint config only applies automatically on initial creation.
   Updating `render.yaml` later does not change an already-running
   service's environment variables. Add the variable manually under the
   service's **Environment** tab, then **Manual Deploy** → **Deploy latest
   commit** (or push any commit) to pick it up.
6. Click **Create Web Service** (or confirm the Blueprint). The first deploy
   takes a few minutes; watch the **Logs** tab. On first boot the API
   process builds the baseline itself (an empty DB triggers
   `build_baseline()` automatically, see `backend/api.py`), so the first
   deploy is slower than later ones.
7. Once live, note your service URL. Render gives you something like
   `https://india-bgp-hijack-monitor.onrender.com`.
8. Verify it directly: open `https://YOUR-URL.onrender.com/api/status` in
   a browser. You should see real JSON with `baseline_prefix_count` around
   20,000+. If it's `0`, check the Logs tab for a RIPEstat error.

---

## Part 2: Point the frontend at your Render URL

1. In `frontend/index.html`, find this line near the top of the
   `<script>` block:
   ```js
   const DEPLOYED_API_URL = 'https://REPLACE-WITH-YOUR-RENDER-URL.onrender.com';
   ```
2. Replace the placeholder with your actual Render URL from Part 1, step 7
   (no trailing slash). Do the same for `API` in `scripts/snapshot_api.py`.
3. Commit and push:
   ```bash
   git add frontend/index.html scripts/snapshot_api.py
   git commit -m "Point deployed frontend at the live Render API"
   git push
   ```

---

## Part 3: Deploy the frontend to GitHub Pages

1. On GitHub, open the `India-BGP-Hijack-Monitor` repo → **Settings** →
   **Pages** (left sidebar, under "Code and automation").
2. Under **Build and deployment** → **Source**, choose **Deploy from a
   branch**.
3. Under **Branch**, choose `main` and folder `/ (root)`, then **Save**.
4. Wait a minute or two, then your dashboard is live at:
   ```
   https://<your-github-username>.github.io/India-BGP-Hijack-Monitor/frontend/
   ```
   GitHub Pages serves the whole repo as static files when the source is
   the root, so `frontend/` is in the URL because `index.html` lives in that
   folder, and the dashboard can also fetch `../docs/dashboard-snapshot.json`.
5. Open that URL and confirm the dashboard loads and shows real data. It
   calls your Render backend cross-origin, which works because the API
   already has `allow_origins=["*"]` set.

---

## Part 4: How the dashboard handles Render's sleep

Render's free tier sleeps after 15 minutes of no HTTP traffic and takes
about 50 seconds to wake. Since the monitor's WebSocket connection lives in
that same process, live monitoring only runs while someone has the
dashboard open (it polls every 15 seconds, which keeps the instance awake).

An external uptime pinger could keep it awake all the time, but that is not
used here: Render's 750 free instance hours a month are shared by every free
service on the account, and one always-on service uses about 720 of them.
Instead:

1. **First paint from saved data.** The dashboard draws the visitor's last
   cached responses (localStorage), or `docs/dashboard-snapshot.json` from
   GitHub Pages, straight away, with a note saying how old it is.
2. **Wake counter.** The badge counts the seconds while the server wakes,
   then live data replaces the saved data automatically.
3. **Daily snapshot.** The `Daily baseline refresh` workflow runs
   `scripts/snapshot_api.py`, which wakes the instance once a day (about 15
   minutes of free hours) and commits a fresh snapshot.

Right after waking, the badge reads "Server up, connecting to the BGP feed"
for a minute or so until the monitor receives its first update from RIS Live.

---

## Redeploying after code changes

Render auto-redeploys on every push to `main` by default (configurable
under the service's **Settings** → **Build & Deploy**). Just `git push` as
usual.

**Caveat**: Render's free tier has no persistent disk, so a redeploy,
restart or wake from sleep gets a fresh filesystem. Three things self-heal
automatically, no manual step required:
- **Baseline** rebuilds itself on boot if the table is empty.
- **RPKI coverage** is seeded instantly from the committed
  `docs/rpki-coverage-summary.json` (real percentages within seconds of
  boot), then refreshed with live data in the background if
  `RUN_RPKI_REFRESH_INLINE=1` is set.
- **Baseline drift** is read from the committed `docs/baseline-history.json`,
  so it needs no network calls at all.

**Event history does not self-heal.** There's no committed record of past
detections to seed from (events are live findings, not baseline or config
data), so accumulated events reset on restart. This is deliberate: seeding
fake "historical" events from a static file would be misleading for a
security-monitoring tool. Documented in [limitations.md](limitations.md).
