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
Backend auth is pinned to the exact prepare-bundle.yml feature-branch workflow.

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

## Remaining production step

Deploy backend branch, verify routes, then enable content_mode=bundle via the
admin settings API. Confirm existing external triggers reach the backend.
Activation is reversible via content_mode=jobs. No production setting was changed
while finalizing this code, and no additional live reel was posted.
