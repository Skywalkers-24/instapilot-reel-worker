# Bundle pipeline branch

Source ZIPs are stored as GitHub Release assets under `bundle-sources-v1`;
`data/bundle_manifest.json` records archive hashes, sizes and MP4 counts.
Git stores the manifest and scripts. Draft release assets require authenticated
repository access. Actions can use its ephemeral GitHub token with contents read.

Selection: run `python scripts/select_bundle_clip.py --history history.json`.
The history must be a JSON list of reserved/published source SHA256 hashes.
The selector downloads missing archives, verifies checksums, skips identical
files already in history, and selects a decodable video longer than five seconds.
It records detected source marks. Selection is not a publish or reservation.

Remaining implementation before enabling automation:
1. Backend queue with unique source hashes, atomic reservations, durable publish
   history and recovery of ambiguous Instagram responses without reposting.
2. Backend entertainment mode that pauses job posting and enforces three daily
   IST slots (initial experiment: 18:00, 20:00, 22:00), retaining the cadence gate.
3. Verify deployment and schedule integration for the existing video renderer.
4. Worker contract, workflow and backend deployment; integration verification.

Timing basis: Buffer's 2026 analysis of 9.6 million Instagram posts recommends
18:00–23:00 local time; it is not an India-only Reels experiment.
https://buffer.com/resources/when-is-the-best-time-to-post-on-instagram/

Rollback: disable entertainment mode, restore job mode, and retain all published
source hashes permanently. No production schedule has been changed by this branch.

## Automated processing

`Prepare Bundle Reel` is a manual Actions workflow on this branch. It accepts
an exclusion hash list, downloads sources using the ephemeral GitHub token,
verifies archive SHA256, screens up to sixty frames from the rendered portion using
Tesseract OCR and masks detected regions before rendering.
OCR is heuristic: logos, moving marks and stylized text can be missed, and
ordinary edge text can cause false positives. This does not verify ownership.

`process_bundle_clip.py` preserves the entire source image, pads to 1080x1920,
limits duration to 30 seconds, applies modest contrast/sharpening, adds a small
bottom-right trendyapa mark, preserves available audio, and encodes H.264/AAC.
It checks output dimensions and duration before saving. Repeated small edge regions receive a local blend; captions are preserved. The workflow saves video and reports as
seven-day Actions artifacts. It does not publish to Instagram.

The workflow uses backend exclusion history and atomically reserves its selected source. The manual history argument remains available for local preparation.
Scheduled publishing must not be enabled until the backend queue and atomic
reservation/publish recovery described above are implemented.

## Permanent ledger backend

Backend migration `0023_bundle_sources` adds a permanent SHA256-keyed ledger.
Admin API `POST /api/bundle-sources/reserve` accepts source_sha256, archive,
member and reel_id. Duplicate reservations return 409. GET /api/bundle-sources
returns reserved and published identities. Confirmed worker publish callbacks
stamp media IDs and publication times. Reservations never expire automatically.
Deploy/migrate backend before consuming these APIs. The public worker must not
receive admin credentials; the narrow worker contract serves
exclusion history and performs reservations without exposing admin credentials.

The preparation workflow now retrieves history and reserves selections using
GitHub OIDC (audience instapilot-bundle-worker), restricted by the backend to
the exact feature-branch workflow and manual dispatch events. No admin or cron
secret is used for this new contract. BACKEND_URL uses the existing repository
configuration. Deploy the backend before running this workflow.

## Bounded watermark detection

Text masking requires high-confidence explicit handles or domains;
ordinary words, platform mentions and normal captions are preserved. Explicit small handles can be detected centrally too. A compact
four-point sparkle detector scans the bottom-right source corner separately
from OCR. Graphic detections must repeat at the same location in at least three distinct
samples. Text tracks require the same identity across at least three samples. Regions larger than 2% of the image or combined coverage above 4% are
rejected. FFmpeg delogo blends only those small rectangles instead of drawing
opaque black boxes; frames are fitted with padding, never cropped by default.

This is heuristic, not exhaustive logo recognition. Unrecognized graphics, low-contrast text and
large central marks can remain; blends may soften textured backgrounds. Reports
record raw findings and the final regions actually modified. The source hash
continues to identify the original clip independently of rendered variants.

Validation: 10 videos sampled at three timestamps across all five archives,
six detector regressions, and rendered checks from every bundle. Gemini samples
were detected in 9/12 and 12/12 sampled frames, respectively.

## Trendyapa overlay

The single-line white wordmark is drawn in one text operation on one baseline,
rendered at 3x resolution and downsampled for smooth edges. A subtle neutral dark
background improves readability. There is no icon or colored accent. The corners
are transparent and the mark stays small in the bottom-right safe area.

Run detector and branding regressions with
`python -m unittest discover -s tests -v` after installing worker dependencies.

## Moving text and additional graphics

The rendered portion is sampled at up to two frames per second, capped at 60
frames. Repeated creator text is grouped by identity, with small FFmpeg timeline
regions active only around each detection. It is never merged into a large
permanent rectangle along its path. Total simultaneous modified area remains
bounded at 4%. Fast jumps between samples may leave residual watermark pixels.
A compact YouTube badge detector requires both a red badge and white triangular
play glyph; arbitrary red rectangles are rejected. Unknown logo shapes are not
automatically erased. A synthetic eight-second moving-handle render verified
that timed blends leave the main caption readable. Ten unit tests passed.
