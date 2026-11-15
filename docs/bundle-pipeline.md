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
