# Bundle pipeline branch

Source ZIPs are stored as GitHub Release assets under `bundle-sources-v1`;
`data/bundle_manifest.json` records archive hashes, sizes and MP4 counts.
Git stores the manifest and scripts. Draft release assets require authenticated
repository access. Actions can use its ephemeral GitHub token with contents read.

Selection: run `python scripts/select_bundle_clip.py --history history.json`.
The history must be a JSON list of reserved/published source SHA256 hashes.
The selector downloads missing archives, verifies checksums, skips identical
files already in history, and selects a decodable video longer than five seconds.
It preserves source attribution. Selection is not a publish or reservation.

Remaining implementation before enabling automation:
1. Backend queue with unique source hashes, atomic reservations, durable publish
   history and recovery of ambiguous Instagram responses without reposting.
2. Backend entertainment mode that pauses job posting and enforces three daily
   IST slots (initial experiment: 18:00, 20:00, 22:00), retaining the cadence gate.
3. FFmpeg enhancement and small bottom-right trendyapa overlay, with output probe.
4. Worker contract, workflow and backend deployment; integration verification.

Timing basis: Buffer's 2026 analysis of 9.6 million Instagram posts recommends
18:00–23:00 local time; it is not an India-only Reels experiment.
https://buffer.com/resources/when-is-the-best-time-to-post-on-instagram/

Rollback: disable entertainment mode, restore job mode, and retain all published
source hashes permanently. No production schedule has been changed by this branch.

## Automated processing

`Prepare Bundle Reel` is a manual Actions workflow on this branch. It accepts
an exclusion hash list, downloads sources using the ephemeral GitHub token,
verifies archive SHA256, screens twelve frames spanning each source using
Tesseract OCR, skips likely source marks, and continues until a clip renders.
OCR is heuristic: logos, moving marks and stylized text can be missed, and
ordinary edge text can cause false positives. This does not verify ownership.

`process_bundle_clip.py` preserves the entire source image, pads to 1080x1920,
limits duration to 30 seconds, applies modest contrast/sharpening, adds a small
bottom-right trendyapa mark, preserves available audio, and encodes H.264/AAC.
It checks output dimensions and duration before saving. Source marks are never
removed or covered deliberately. The workflow saves video and reports as
seven-day Actions artifacts. It does not publish to Instagram.

The workflow's exclusion list is an input, not a permanent publish ledger.
Scheduled publishing must not be enabled until the backend queue and atomic
reservation/publish recovery described above are implemented.
