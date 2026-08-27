#!/usr/bin/env python3
"""
InstaPilot Reel Worker (GitHub Actions).

Holds NO app code and NO secrets except CRON_SECRET + BACKEND_URL. The Instagram
token stays on the backend; the runner only drives the slow steps and calls thin
backend endpoints. Flow:

  1. Ask the backend for the next reel        (POST /api/cron/next-reel)
  2. Download BOTH frames:
       - avatar_cover_url  → frame 1 (poster: full avatar, shown before playback)
       - content_cover_url → frame 2 (designed content card: company/role/CTA)
  3. Render a 1080x1920 / 30s MP4 on the runner: ~3s avatar intro then content
  4. Upload the MP4 to a GitHub Release        (public URL, no bandwidth cost)
  5. Prune old release assets (keep newest 5)
  6. Drive the Instagram publish via THIN backend endpoints (token stays server-side):
       POST /api/cron/ig/create-container      → container_id
       POST /api/cron/ig/container-status (loop, with retries here on the runner)
       POST /api/cron/ig/publish               → media_id
  7. Report the outcome                        (POST /api/cron/mark-published)

Env:
  BACKEND_URL       base URL of the backend (required)
  CRON_SECRET       shared secret (matches backend CRON_SECRET)
  GITHUB_TOKEN      auto-provided by Actions (for release upload/prune)
  GITHUB_REPOSITORY auto-provided by Actions (owner/repo)
  RELEASE_TAG       release tag to store assets under (default: reel-media)
  KEEP_ASSETS       how many MP4s to keep (default: 5)
  INTRO_SECONDS     avatar intro duration (default: 3)
  REEL_SECONDS      total reel duration (default: 30)
  POLL_MAX          max container-status checks (default: 30)
  POLL_INTERVAL     seconds between status checks (default: 10)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

BACKEND_URL = os.environ["BACKEND_URL"].rstrip("/")
CRON_SECRET = os.environ["CRON_SECRET"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ["GITHUB_REPOSITORY"]  # owner/repo
RELEASE_TAG = os.getenv("RELEASE_TAG", "reel-media")
KEEP_ASSETS = int(os.getenv("KEEP_ASSETS", "5"))
INTRO_SECONDS = float(os.getenv("INTRO_SECONDS", "3"))
REEL_SECONDS = float(os.getenv("REEL_SECONDS", "30"))
# Keep Instagram API calls low: wait a bit before the first check (containers
# rarely finish instantly), then poll a modest number of times. ~12 checks x 8s
# ≈ up to ~100s, plenty for a short still-image reel, without hammering the API.
POLL_MAX = int(os.getenv("POLL_MAX", "12"))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "8"))
POLL_FIRST_DELAY = float(os.getenv("POLL_FIRST_DELAY", "15"))

GH_API = "https://api.github.com"


def _http(method: str, url: str, *, headers=None, data=None, timeout=60):
    req = urllib.request.Request(url, method=method, data=data)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def backend_post(path: str, payload: dict | None):
    body = json.dumps(payload).encode() if payload is not None else b"{}"
    status, raw = _http(
        "POST", f"{BACKEND_URL}{path}",
        headers={"Content-Type": "application/json", "X-Cron-Secret": CRON_SECRET},
        data=body,
    )
    try:
        parsed = json.loads(raw or b"{}")
    except Exception:
        parsed = {"raw": raw.decode(errors="replace")}
    return status, parsed


def gh_api(method: str, path: str, *, data=None):
    status, raw = _http(
        method, f"{GH_API}{path}",
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
        },
        data=json.dumps(data).encode() if data is not None else None,
    )
    try:
        return status, json.loads(raw or b"{}")
    except Exception:
        return status, {}


def ensure_release() -> int:
    """Return the release id for RELEASE_TAG, creating it if needed."""
    status, rel = gh_api("GET", f"/repos/{GITHUB_REPO}/releases/tags/{RELEASE_TAG}")
    if status == 200 and rel.get("id"):
        return rel["id"]
    status, rel = gh_api(
        "POST", f"/repos/{GITHUB_REPO}/releases",
        data={"tag_name": RELEASE_TAG, "name": "Reel Media",
              "body": "Auto-generated reel videos (auto-pruned).", "prerelease": True},
    )
    if status not in (200, 201):
        raise RuntimeError(f"Failed to create release: {status} {rel}")
    return rel["id"]


def upload_asset(release_id: int, file_path: str, asset_name: str) -> str:
    # Delete an existing asset with the same name first (uploads can't overwrite).
    _, rel = gh_api("GET", f"/repos/{GITHUB_REPO}/releases/{release_id}")
    for a in rel.get("assets", []):
        if a.get("name") == asset_name:
            gh_api("DELETE", f"/repos/{GITHUB_REPO}/releases/assets/{a['id']}")

    with open(file_path, "rb") as f:
        content = f.read()
    upload_url = f"https://uploads.github.com/repos/{GITHUB_REPO}/releases/{release_id}/assets?name={asset_name}"
    status, raw = _http(
        "POST", upload_url,
        headers={
            "Authorization": f"Bearer {GITHUB_TOKEN}",
            "Content-Type": "video/mp4",
        },
        data=content, timeout=180,
    )
    if status not in (200, 201):
        raise RuntimeError(f"Asset upload failed: {status} {raw[:300]!r}")
    return json.loads(raw)["browser_download_url"]


def prune_assets(release_id: int, keep: int) -> None:
    _, rel = gh_api("GET", f"/repos/{GITHUB_REPO}/releases/{release_id}")
    assets = sorted(rel.get("assets", []), key=lambda a: a.get("created_at", ""), reverse=True)
    for a in assets[keep:]:
        gh_api("DELETE", f"/repos/{GITHUB_REPO}/releases/assets/{a['id']}")
        print(f"Pruned old asset: {a['name']}")


def download(url: str, dest: str) -> bool:
    """Download an image to `dest`. Returns True on success (>=500 bytes)."""
    status, raw = _http("GET", url, timeout=120)
    if status != 200 or len(raw) < 500:
        print(f"  download failed: {url} status={status} bytes={len(raw)}")
        return False
    with open(dest, "wb") as f:
        f.write(raw)
    return True


def render_video(content_path: str, out_path: str, total: float = REEL_SECONDS, fps: int = 30) -> None:
    """Render the reel MP4 on the runner from the CONTENT card only.

    Playback starts on the content image immediately (no avatar intro). The
    full-avatar image is set separately as the reel's grid COVER via the Graph
    API `cover_url` — so the grid shows the avatar while the video plays content.
    """
    vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", content_path,
        "-f", "lavfi", "-t", f"{total:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-map", "0:v", "-map", "1:a",
        "-vf", vf,
        "-c:v", "libx264", "-tune", "stillimage",
        "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", "-r", str(fps), "-g", str(fps * 2),
        "-t", f"{total:.3f}", "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
        "-movflags", "+faststart", "-shortest",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def publish_via_backend(reel_id: int, video_url: str, cover_url: str = "") -> tuple[str, str, str]:
    """Drive the Instagram publish through THIN backend endpoints.

    The backend makes each single Graph API call (token stays server-side); the
    runner owns the create → poll → publish loop and its retries. `cover_url`
    (the full-avatar image) becomes the reel's grid thumbnail. Returns
    (status, media_id, message).
    """
    # 1. Create the REELS container (one backend call).
    payload = {"reel_id": reel_id, "video_url": video_url}
    if cover_url:
        payload["cover_url"] = cover_url
    st, data = backend_post("/api/cron/ig/create-container", payload)
    if st != 200:
        return "ERROR", "", f"create-container HTTP {st}: {data}"
    status = data.get("status", "")
    if status in ("DRY_RUN",):
        return "DRY_RUN", "", data.get("message", "Dry run.")
    if status not in ("CREATED",):
        return status or "ERROR", "", data.get("message", "container create failed")
    container_id = data.get("container_id", "")
    if not container_id:
        return "ERROR", "", "no container_id returned"
    print(f"  container created: {container_id}")

    # 2. Poll status until FINISHED (retries live here on the runner).
    #    Wait once up-front so the container has time to process — this avoids a
    #    burst of early "IN_PROGRESS" checks and keeps total IG calls low.
    time.sleep(POLL_FIRST_DELAY)
    for attempt in range(POLL_MAX):
        st, data = backend_post("/api/cron/ig/container-status", {"container_id": container_id})
        if st == 200:
            code = data.get("status_code", "UNKNOWN")
            print(f"  container status: {code} (attempt {attempt+1})")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                return code, "", f"container processing {code}"
        else:
            print(f"  status check HTTP {st} (attempt {attempt+1}); retrying")
        # Wait between checks (skip the wait after the final attempt).
        if attempt < POLL_MAX - 1:
            time.sleep(POLL_INTERVAL)
    else:
        return "IN_PROGRESS", "", "Container did not finish within polling window."

    # 3. Publish the finished container (one backend call).
    st, data = backend_post("/api/cron/ig/publish", {"reel_id": reel_id, "container_id": container_id})
    if st != 200:
        return "ERROR", "", f"publish HTTP {st}: {data}"
    return data.get("status", "ERROR"), data.get("media_id", ""), data.get("message", "")


def main() -> int:
    # 0. Build the next reel (cadence-gated, fast, no publish). SKIPPED_CADENCE /
    #    NO_JOB just mean "nothing to post right now" — not an error.
    st, build = backend_post("/api/cron/auto-post", {})
    if st == 200:
        print(f"auto-post: {build.get('status')} reel_id={build.get('reel_id')}")
    else:
        print(f"auto-post HTTP {st}: {build}")

    # 1. Ask the backend for the next reel to publish.
    status, data = backend_post("/api/cron/next-reel", None)
    if status != 200:
        print(f"next-reel failed: {status} {data}")
        return 1
    reel = data.get("reel")
    if not reel:
        print("No reel to publish. Done.")
        return 0

    reel_id = reel["reel_id"]
    print(f"Next reel: #{reel_id} — {reel.get('title')!r}")

    # 2. Download the CONTENT card (played from frame 1) + the AVATAR (grid cover).
    content_url = reel.get("content_cover_url")
    avatar_url = reel.get("avatar_cover_url") or reel.get("cover_url")
    if not content_url or not download(content_url, "content.jpg"):
        print("Content frame download failed — cannot render.")
        return 1
    have_cover = bool(avatar_url) and download(avatar_url, "avatar.jpg")
    print(f"Downloaded (content=yes, avatar_cover={'yes' if have_cover else 'no'}).")

    # 3. Render the MP4 from the content card (playback starts on content).
    out = f"reel-{reel_id}.mp4"
    render_video("content.jpg", out)
    size = os.path.getsize(out)
    print(f"Rendered {out} ({size} bytes)")

    # 4. Upload to a GitHub Release (public URL; timestamp busts CDN cache).
    release_id = ensure_release()
    asset_name = f"reel-{reel_id}-{int(time.time())}.mp4"
    video_url = upload_asset(release_id, out, asset_name)
    print(f"Uploaded: {video_url}")
    cover_public_url = avatar_url if have_cover else ""

    # 5. Prune to newest KEEP_ASSETS.
    prune_assets(release_id, KEEP_ASSETS)

    # 6. Publish via the thin backend endpoints (token stays server-side).
    #    The avatar image is passed as the grid cover_url.
    print("Publishing to Instagram via backend endpoints...")
    ig_status, media_id, msg = publish_via_backend(reel_id, video_url, cover_url=cover_public_url)
    print(f"IG result: status={ig_status} media_id={media_id} msg={msg}")

    # 7. Report the outcome back to the backend (marks job POSTED, first comment).
    backend_post("/api/cron/mark-published", {
        "reel_id": reel_id, "video_url": video_url,
        "media_id": media_id, "status": ig_status, "error": msg,
    })

    if ig_status in ("PUBLISHED", "DRY_RUN", "CONTAINER_READY"):
        print("Done — reel published.")
        return 0
    print(f"Publish did not succeed: {msg}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
