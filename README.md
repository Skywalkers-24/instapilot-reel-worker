# InstaPilot Reel Worker

The public worker handles media compute; the backend owns publishing policy,
credentials and permanent posting history.

## Bundle mode

`publish-reel.yml` runs `scripts/run_bundle.py` on demand. The backend checks
an IST posting slot before the worker downloads a ZIP, selects an unused source,
processes one reel and uploads the output. Backend-owned Instagram calls then
publish it and commit the source identity. No bulk preparation is required.
The backend rotates the preferred bundle across days and slots for variety.

See [bundle pipeline](docs/bundle-pipeline.md) for deployment/activation and
[agent handoff](docs/HANDOFF.md) for current state, logs and rollback.

## Jobs mode

The existing job publisher, scrapers and Cloudflare wakeup remain available.
The persisted backend cadence gate is authoritative (50-minute default).
Activating bundle mode pauses automatic job selection; switching back to jobs
restores the existing path. Retain all source ledger records when switching.

## Credentials

New bundle publishing uses GitHub OIDC. Actions uses its ephemeral GitHub token
for release media and the existing BACKEND_URL configuration. Meta, database and
admin credentials stay in the backend. Legacy orchestration still uses the shared
CRON_SECRET; the new workflow does not introduce another use of it.

## Local tools

`prepare_bundle_review.py` makes side-by-side samples. `inventory_bundles.py`
audits archives without publishing. `python -m unittest discover -s tests -v`
runs processing/orchestration checks. Local bundles, renders and review packs
are Git-ignored; source ZIPs live in the draft bundle-sources-v1 release.
