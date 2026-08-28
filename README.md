# InstaPilot Reel Worker

Public repo -> unlimited free GitHub Actions minutes. This worker renders reel
videos with FFmpeg on the runner, hosts them on a GitHub Release, and asks the
backend to publish them to Instagram. No app code and no credentials live here
except a shared `CRON_SECRET`.

## How it works

Every trigger (scheduled backup, external dispatch, or manual run),
`scripts/publish_reel.py`:

1. `POST {BACKEND_URL}/api/cron/auto-post` to build the next cadence-eligible reel
2. `POST {BACKEND_URL}/api/cron/next-reel` to get the next reel's `cover_url` and caption
3. Downloads the cover image rendered by the backend
4. Renders a 1080x1920 / 30s MP4 from the cover with FFmpeg
5. Uploads the MP4 to this repo's `reel-media` GitHub Release for a public download URL
6. Prunes old release assets, keeping only the newest 5
7. Publishes through the backend's thin Instagram endpoints

Instagram fetches the video from the GitHub Release URL, so there is no separate
media bucket or bandwidth bill.

## Scheduling

The workflow has three triggers:

- `schedule`: best-effort GitHub backup every 15 minutes, offset away from busy minute boundaries
- `repository_dispatch`: reliable trigger fired by the backend's `/api/cron/trigger-publish`
- `workflow_dispatch`: manual "Run workflow" button

The backend setting `post_interval_minutes` is authoritative and defaults to 15.
Missed or manual extra runs cannot over-post.

## Cloudflare Cron Worker

This repo also includes a Cloudflare Worker cron app:

- `src/index.js`: scheduled handler that calls the backend trigger endpoint
- `wrangler.toml`: cron schedule, currently every 15 minutes

Configure these Cloudflare variables:

| Variable | Value |
|----------|-------|
| `BACKEND_URL` | your backend base URL, e.g. `https://<your-backend-host>` |
| `CRON_SECRET` | the same secret configured on the backend |

The Cloudflare Worker calls `POST {BACKEND_URL}/api/cron/trigger-publish`, and
the backend fires the `repository_dispatch` event for the GitHub Actions worker.

## Required GitHub Secrets

Repo -> Settings -> Secrets and variables -> Actions:

| Secret | Value |
|--------|-------|
| `BACKEND_URL` | your backend's base URL, e.g. `https://<your-backend-host>` |
| `CRON_SECRET` | any long random string, matching the backend's `CRON_SECRET` |

`GITHUB_TOKEN` is provided automatically by Actions.

## Backend setup

Set the same `CRON_SECRET` as an environment variable on the backend. Set
`GITHUB_DISPATCH_TOKEN` on the backend if you want reliable external cron ->
backend -> repository_dispatch triggering. All Instagram credentials stay on the
backend only.

## Run manually

Actions tab -> **Publish Reel** -> **Run workflow**.
