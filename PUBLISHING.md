# Reliable reel publishing

## Why this exists

GitHub Actions `schedule:` is best-effort. For short intervals like 10 or 15
minutes, GitHub can delay runs or skip ticks entirely under load. That is why a
workflow can show a scheduled run much later than expected.

## How it works now

The `Publish Reel` workflow (`.github/workflows/publish-reel.yml`) has three
triggers:

- `schedule`: best-effort GitHub backup every 5 minutes
- `repository_dispatch` type `publish-reel`: the reliable path, fired via API
- `workflow_dispatch`: the manual "Run workflow" button

Each run builds the next reel, renders the MP4, publishes through the backend's
thin Instagram endpoints, and reports the outcome. The backend cadence gate
(`post_interval_minutes`, default 10) is authoritative. Triggering every 5
minutes is safe because extra runs return a skip when it is not time to post.

## Best setup: Cloudflare Worker cron -> backend -> repository_dispatch

The backend exposes `POST|GET /api/cron/trigger-publish`, gated by the
`X-Cron-Secret` header. It fires a `repository_dispatch` on this worker repo,
which starts a `Publish Reel` run.

This repo includes a Cloudflare Worker in `src/index.js` and `wrangler.toml`.
Deploy it from the `worker` repo and configure these Cloudflare variables:

- `BACKEND_URL`: your backend base URL, for example `https://<your-backend>`
- `CRON_SECRET`: the same secret configured on the backend

The Worker runs every 5 minutes and calls:

`POST {BACKEND_URL}/api/cron/trigger-publish`

with the `X-Cron-Secret` header. The backend then dispatches the GitHub Actions
`Publish Reel` workflow. The backend cadence gate keeps actual posting to the
configured interval.

Alternative services also work: cron-job.org, EasyCron, UptimeRobot with a
custom header, or a paid platform cron.

## Backend setup

`/api/cron/trigger-publish` needs:

- `GITHUB_DISPATCH_TOKEN`: a fine-grained PAT with Actions read/write and
  Contents read access scoped to this worker repo
- `CRON_SECRET`: the same secret sent by the external cron and configured in the
  worker repo's Actions secrets

Once these are set, the worker is kicked reliably every 5 minutes, and the
backend keeps actual Instagram posting to roughly every 10 minutes.

## Manual test

```bash
gh api -X POST /repos/Skywalkers-24/instapilot-reel-worker/dispatches -f event_type=publish-reel

curl -X POST "https://<your-backend>/api/cron/trigger-publish" -H "X-Cron-Secret: <CRON_SECRET>"
```
