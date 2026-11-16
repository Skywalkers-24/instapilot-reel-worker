# Next-agent handoff

## Current user decision

Use on-demand processing in Actions. Bulk reprocessing and bulk uploading were
explicitly cancelled. No batch runner is active. Do not restart it by default.

## Where to start

Read the workspace InstaPilot skill and architecture, then bundle-pipeline.md.
Both backend and worker changes are on feature/trendyapa-bundle-reels. Final
code is pushed for deployment; production activation still requires verification.
Do not assume production aliases follow a feature-branch preview deployment.

## Commands

Worker checks (use a Python environment with requirements installed):
`python -m unittest discover -s tests -v`

On-demand entry point:
`scripts/run_bundle.py` (requires an Actions OIDC environment; not a local login).
Backend auth is pinned to the exact publish-reel.yml feature-branch workflow.

Local audit/review:
`scripts/inventory_bundles.py` and `scripts/prepare_bundle_review.py`.
The user review is in bundle-output/review-20/index.html. Revised fixed marks
are in slots 03,04,18; slots 01,13 are excluded. Originals and previous-pass
renders are preserved locally. These outputs are ignored by Git.

## Processing logs and checkpoints

Actions stores *-report.json and selection.json artifacts for 14 days. Reports
contain findings, final regions or skip reasons. Backend bundle_sources stores
source identity/status/media ID permanently; settings keys bundle_run_* record
per-run phase and bundle_publish_gate records the claimed IST slot. Unknown
publish outcomes must be reconciled with Instagram before changing their state.

The completed read-only inventory and per-video CSV/JSON are in
bundle-output/inventory/. No API calls publish anything during inventory.
The two-video bulk smoke test checkpoint and its media are in
bundle-output/library-v1/. Historical batch/benchmark scripts were moved to
bundle-output/experiments/ after cancellation and are not part of active code.
Two smoke-test assets were uploaded before uploads were deferred; they were
not published to Instagram and are unused by the final on-demand flow.

## Credential boundary

No passwords, tokens or login values belong in this handoff, source, artifacts
or logs. Root .env is only an operator control file. Backend settings hold Meta
credentials. The public worker receives no Meta, database or admin credentials.
Existing shared CRON_SECRET in legacy orchestration remains architecture drift;
the new worker contract uses OIDC.

## Current production schedule

Backend production is on the feature code and `content_mode=bundle` is active.
The bundle gate allows 13:00, 18:00, 20:00 and 22:00 IST, with a maximum of
four published reels per IST date and no workflow force bypass. Backend
deployment commit `df154c7` is live on the production alias.

Worker branch commit `4852781` configures Cloudflare for those four times
(07:30, 12:30, 14:30 and 16:30 UTC), while preserving the job scrape schedule.
The schedule is deployed to the live Worker and its health page reports the four
publishing times plus the two existing job-scrape times. Cloudflare version
`5d483cfc-87e1-4c2a-a498-e4db2939c026` is active. Activation is reversible by
setting `content_mode=jobs`; preserve all ledger rows.

## Explicit logo-update repost

The user subsequently requested one repost of the earlier miniature reel with
the updated wordmark. It was published as Instagram media 18655260460031132:
https://www.instagram.com/reel/DfxaWHRu1fI/ . Original media 17981142956921915
and its source ledger record were preserved. The explicit exception is audited
under manual_repost_17981142956921915_logo_v2; do not repeat it automatically.
Five duplicate ZIP copies were deleted from the workspace root at the user's
request. The five worker bundles and all local review files remain intact.
