#!/usr/bin/env python3
"""
InstaPilot Reel Worker (GitHub Actions).

Holds NO app code and NO secrets except CRON_SECRET + BACKEND_URL. It:
  1. Asks the backend for the next reel to publish   (POST /api/cron/next-reel)
  2. Downloads that reel's cover image               (public cover_url)
  3. Renders a 1080x1920 / 30s MP4 from the cover     (FFmpeg on the runner)
  4. Uploads the MP4 to a GitHub Release              (public URL, no bandwidth cost)
  5. Prunes old release assets, keeping only the 5 newest
  6. Tells the backend the public video URL           (POST /api/cron/publish)
     The backend performs the Instagram publish (token stays server-side).

Env:
  BACKEND_URL       base URL of the backend (required)
  CRON_SECRET       shared secret (matches backend CRON_SECRET)
  GITHUB_TOKEN      auto-provided by Actions (for release upload/prune)
  GITHUB_REPOSITORY auto-provided by Actions (owner/repo)
  RELEASE_TAG       release tag to store assets under (default: reel-media)
  KEEP_ASSETS       how many MP4s to keep (default: 5)
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error
import urllib.parse

BACKEND_URL = os.environ["BACKEND_URL"].rstrip("/")
CRON_SECRET = os.environ["CRON_SECRET"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ["GITHUB_REPOSITORY"]  # owner/repo
RELEASE_TAG = os.getenv("RELEASE_TAG", "reel-media")
KEEP_ASSETS = int(os.getenv("KEEP_ASSETS", "5"))

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


def download(url: str, dest: str) -> None:
    status, raw = _http("GET", url, timeout=120)
    if status != 200 or len(raw) < 500:
        raise RuntimeError(f"Cover download failed: status={status} bytes={len(raw)}")
    with open(dest, "wb") as f:
        f.write(raw)


def fetch_trending_audio_id(base: str, user_id: str, token: str) -> str:
    """Get a trending, third-party-authorized audio_id (empty string if none)."""
    try:
        qs = urllib.parse.urlencode({"audio_type": "music", "user_id": user_id, "access_token": token})
        status, raw = _http("GET", f"{base}/ig_audio?{qs}", timeout=30)
        if status != 200:
            print(f"  trending audio unavailable (status={status})")
            return ""
        for item in json.loads(raw).get("audio", []):
            aid = item.get("audio_id")
            if aid:
                print(f"  trending audio: {item.get('title','?')} by {item.get('display_artist','?')} ({aid})")
                return str(aid)
    except Exception as e:
        print(f"  trending audio fetch error: {e}")
    return ""


def instagram_publish_reel(creds: dict, video_url: str, caption: str) -> tuple[str, str, str]:
    """Publish a Reel via the Instagram Graph API. Runs here (no time limit).

    Returns (status, media_id, message). Credentials are used in memory only.
    Attaches trending music when available (falls back to no audio otherwise).
    """
    if creds.get("mock_mode"):
        return "PUBLISHED", "mock-media-id", "Mock publish."
    if creds.get("dry_run"):
        return "DRY_RUN", "", "Dry run: no publish attempted."

    user_id = creds.get("user_id", "")
    token = creds.get("access_token", "")
    ver = creds.get("api_version", "v24.0")
    if not (user_id and token):
        return "NOT_CONFIGURED", "", "Instagram user id / token not configured."

    base = f"https://graph.facebook.com/{ver}"

    # Attach trending music if we can get an authorized track.
    audio_id = fetch_trending_audio_id(base, user_id, token)

    # 1. Create the REELS container
    container_params = {
        "media_type": "REELS", "video_url": video_url,
        "caption": caption, "access_token": token,
    }
    if audio_id:
        container_params["audio_configuration"] = json.dumps({
            "audio_id": audio_id, "audio_volume": 100, "video_volume": 0,
        })
    body = urllib.parse.urlencode(container_params).encode()
    status, raw = _http("POST", f"{base}/{user_id}/media",
                        headers={"Content-Type": "application/x-www-form-urlencoded"},
                        data=body, timeout=60)
    if status != 200:
        return "ERROR", "", f"container create failed: {raw[:300]!r}"
    container_id = json.loads(raw)["id"]

    # 2. Poll until the container finishes processing (worker has no time limit)
    for _ in range(30):  # up to ~5 min
        time.sleep(10)
        s2, r2 = _http("GET", f"{base}/{container_id}?fields=status_code&access_token={token}", timeout=30)
        if s2 != 200:
            return "ERROR", "", f"status poll failed: {r2[:200]!r}"
        code = json.loads(r2).get("status_code")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            return code, "", f"container processing {code}"
    else:
        return "IN_PROGRESS", "", "Container did not finish in time."

    # 3. Publish
    pub_body = urllib.parse.urlencode({"creation_id": container_id, "access_token": token}).encode()
    s3, r3 = _http("POST", f"{base}/{user_id}/media_publish",
                   headers={"Content-Type": "application/x-www-form-urlencoded"},
                   data=pub_body, timeout=60)
    if s3 != 200:
        return "ERROR", "", f"publish failed: {r3[:300]!r}"
    return "PUBLISHED", json.loads(r3).get("id", ""), "Published."


def render_video(cover_path: str, out_path: str, duration: int = 30, fps: int = 30) -> None:
    cmd = [
        "ffmpeg", "-y",
        "-loop", "1", "-i", cover_path,
        "-f", "lavfi", "-t", str(duration), "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
        "-map", "0:v", "-map", "1:a",
        "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1",
        "-c:v", "libx264", "-tune", "stillimage",
        "-profile:v", "high", "-level", "4.1", "-pix_fmt", "yuv420p",
        "-preset", "veryfast", "-r", str(fps), "-g", str(fps * 2),
        "-t", str(duration), "-crf", "23",
        "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
        "-movflags", "+faststart", "-shortest",
        out_path,
    ]
    subprocess.run(cmd, check=True, capture_output=True, text=True)


def main() -> int:
    # 1. Ask backend for the next reel
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

    # 2. Download cover
    download(reel["cover_url"], "cover.jpg")
    print("Cover downloaded.")

    # 3. Render MP4
    out = f"reel-{reel_id}.mp4"
    render_video("cover.jpg", out)
    size = os.path.getsize(out)
    print(f"Rendered {out} ({size} bytes)")

    # 4. Upload to GitHub Release (bust cache with a timestamp suffix in the name)
    release_id = ensure_release()
    asset_name = f"reel-{reel_id}-{int(time.time())}.mp4"
    video_url = upload_asset(release_id, out, asset_name)
    print(f"Uploaded: {video_url}")

    # 5. Prune to newest KEEP_ASSETS
    prune_assets(release_id, KEEP_ASSETS)

    # 6. Fetch IG credentials (in memory only) and publish from here — the
    # runner has no time limit, unlike Vercel's 10s functions.
    status, creds = backend_post("/api/cron/ig-credentials", None)
    if status != 200:
        print(f"ig-credentials failed: {status} {creds}")
        return 1

    caption = reel.get("caption", "")
    print("Publishing to Instagram (from runner)...")
    ig_status, media_id, msg = instagram_publish_reel(creds, video_url, caption)
    print(f"IG result: status={ig_status} media_id={media_id} msg={msg}")

    # 7. Report outcome back to the backend (updates DB, marks job POSTED)
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
