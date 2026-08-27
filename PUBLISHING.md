# Reliable reel publishing (every ~10 min)

## Why this exists
GitHub Actions `schedule:` (cron) is **best-effort**. For short intervals like
`*/10`, GitHub frequently delays runs 15–60+ min or **skips them entirely** under
load. That's why the `Publish Reel` workflow's cron never fired on its own — every
past run was a manual `workflow_dispatch`.

## How it works now
The `Publish Reel` workflow (`.github/workflows/publish-reel.yml`) has three triggers:
- `schedule` `*/10` — best-effort backup (may or may not fire).
- `repository_dispatch` type `publish-reel` — the reliable path, fired via API.
- `workflow_dispatch` — the manual "Run workflow" button.

Each run: builds the next reel (backend, cadence-gated) → renders the MP4 → publishes
via the backend's thin Instagram endpoints. The **backend cadence gate**
(`post_interval_minutes`, default 10) is authoritative — extra/overlapping triggers
just return `SKIPPED_CADENCE`, so triggering more often than needed is safe.

## The reliable trigger: an external cron → backend → repository_dispatch
The backend exposes `POST|GET /api/cron/trigger-publish` (gated by `X-Cron-Secret`).
It fires the `repository_dispatch` on this repo, which starts a Publish Reel run.

Point a **dependable free cron service** at it every 10 minutes:

- **cron-job.org** (recommended, free):
  - URL: `https://<your-backend>/api/cron/trigger-publish`
  - Method: `POST`
  - Header: `X-Cron-Secret: <your CRON_SECRET>`
  - Schedule: every 10 minutes
- Alternatives: EasyCron, UptimeRobot (as an HTTP monitor with a custom header),
  or a Vercel Cron (Pro plan — Hobby caps crons at once/day, too infrequent here).

### One-time backend setup (Vercel env vars)
`/api/cron/trigger-publish` needs a GitHub token to fire the dispatch:
- `GITHUB_DISPATCH_TOKEN` — a fine-grained PAT with **Actions: read/write** (and
  **Contents: read**) scoped to this worker repo. Set it in the backend's Vercel
  Environment Variables.
- `CRON_SECRET` — already set; must match what the external cron sends.

Once `GITHUB_DISPATCH_TOKEN` is set and the external cron is running, reels publish
reliably every ~10 minutes regardless of GitHub's own scheduler.

## Manual test
```bash
# Fire one publish run immediately (uses your gh auth):
gh api -X POST /repos/Skywalkers-24/instapilot-reel-worker/dispatches -f event_type=publish-reel

# Or via the backend endpoint (what the external cron calls):
curl -X POST "https://<your-backend>/api/cron/trigger-publish" -H "X-Cron-Secret: <CRON_SECRET>"
```
