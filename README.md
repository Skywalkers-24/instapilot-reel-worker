# InstaPilot Reel Worker

Public repo → **unlimited free GitHub Actions minutes**. Renders reel videos with
FFmpeg on the runner, hosts them on a GitHub Release, and asks the backend to
publish them to Instagram. **No app code and no credentials live here** except a
shared `CRON_SECRET`.

## How it works

Every hour (or on manual trigger), `scripts/publish_reel.py`:

1. `POST {BACKEND_URL}/api/cron/next-reel` → gets the next reel's `cover_url` + caption
2. Downloads the cover image (rendered by the backend, Pillow — no FFmpeg needed there)
3. Renders a 1080×1920 / 30s MP4 from the cover (FFmpeg, on the runner — 7 GB RAM)
4. Uploads the MP4 to this repo's `reel-media` GitHub Release → public download URL
5. Prunes old release assets, keeping only the newest 5
6. `POST {BACKEND_URL}/api/cron/publish` with the video URL → backend posts to Instagram

Instagram fetches the video from the GitHub Release URL (no bandwidth cost, no R2, no Render).

## Required GitHub Secrets

Repo → Settings → Secrets and variables → Actions:

| Secret | Value |
|--------|-------|
| `BACKEND_URL` | `https://instapilot-india.vercel.app` |
| `CRON_SECRET` | any long random string (must match the backend's `CRON_SECRET`) |

`GITHUB_TOKEN` is provided automatically by Actions — no setup needed.

## Backend setup

Set the same `CRON_SECRET` as an environment variable on the backend (Vercel).
All other credentials (Supabase, Instagram token) stay on the backend only.

## Run manually

Actions tab → **Publish Reel** → **Run workflow**.
