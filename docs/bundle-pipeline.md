# On-demand bundle reels

The backend decides eligibility; the public worker selects and processes one
unused source only when eligible. Bulk preparation was cancelled by the user.

## Runtime

1. Existing Cloudflare/cron wakeups call backend `/api/cron/trigger-publish`.
2. In `content_mode=bundle`, the backend dispatches `prepare-bundle.yml` on
   `feature/trendyapa-bundle-reels`. In jobs mode, existing dispatch is preserved.
3. Actions obtains short-lived GitHub OIDC identity and calls
   `/api/bundle-worker/publish/next`. Backend enforces 18:00, 20:00 and 22:00 IST
   hourly windows, one claim per slot, at most three account posts per day,
   and the persisted cadence gate (50-minute default).
4. The selector downloads verified ZIP release assets, skips ledger hashes and
   reviewed exclusions, and considers at most 30 duration-valid candidates.
5. The worker scans the rendered portion (up to 60 samples), skips moving or
   ambiguous marks, blends bounded stationary marks, preserves captions/audio,
   fits to 1080x1920, caps at 30 seconds, and adds the white single-line wordmark.
6. It uploads one processed artifact to a `bundle-media-v1-*` release. It never
   replaces existing assets. Backend `/register` checks the media URL and bounds,
   locks the slot gate, creates a permanent unique source claim, and associates
   it with the signed GitHub workflow run.
7. Backend `/create`, `/status` and `/publish` own every Instagram request.
   PUBLISHED source records and InstagramPost rows are committed together.
   Ambiguous results retain their claim; they are never automatically reposted.

## Deployment and activation

Deploy the backend branch first; confirm the new publishing routes exist. Push
this worker branch before activation. Keep Meta/admin/database secrets on the
backend. Actions uses only the existing BACKEND_URL setting, GitHub's ephemeral
job token for release assets, and OIDC for backend calls. No new shared cron
secret use is introduced in the worker.

Admin-only `POST /api/settings/update` with
`{"settings":{"content_mode":"bundle"}}` activates this path. Jobs mode is the
default. Active bundle mode pauses legacy auto-post/next-reel job selection.
Rollback uses `{"settings":{"content_mode":"jobs"}}`; preserve all ledger rows.
The existing external wakeup must remain active; GitHub scheduled workflows
normally run only from the default branch, so this branch uses backend dispatch.
No deployment or activation is performed by a Git push alone.

## Watermarks and reports

`watermark_templates.json` contains compact edge signatures for reviewed AF and
itzzgoru styles. Matching searches a corner area across multiple scales and
requires repeated positions. `source_watermark_profiles.json` provides a bounded
fallback for reviewed source hashes. `source_exclusions.json` blocks review
videos 01 and 13. Other moving handles or known graphics are skipped when detected.

Ambiguous sparkle detections or a mask area over 4% cause a skip. Large or
unrecognized marks can still escape detection; this is not universal logo
recognition. Exact SHA256 identity prevents exact-file reposts, not perceptual
variants. Actions retains per-candidate JSON reports for 14 days.

## Local review and audit

- `prepare_bundle_review.py` prepares a local comparison gallery without posting.
- `inventory_bundles.py` audits metadata, first-frame decoding and exact duplicates.
- `bundle-output/review-20/` contains the existing user review pack (Git-ignored).
- `bundle-output/inventory/` contains the full audit (Git-ignored).

The 2026-11-15 audit found 1,737 video entries, 65 at or below five seconds,
27 exact duplicates among duration-valid files, and 1,645 unique duration-valid
first-frame-decodable videos. Subtract two known exclusions and one published
source: 1,642 preliminary candidates. Final watermark eligibility is evaluated
on demand. This is not a full-stream corruption or perceptual-duplicate audit.

Timing baseline: https://buffer.com/resources/when-is-the-best-time-to-post-on-instagram/
The underlying study covers Instagram posts generally, not India-only Reels.
