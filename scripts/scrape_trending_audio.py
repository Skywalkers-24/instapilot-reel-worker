#!/usr/bin/env python3
"""
Scrape today's trending Instagram Reels audio (runs on the GitHub runner).

The Snaplytics trending page is a Next.js app that INLINES the trending list in
its server-rendered HTML (RSC stream) — each song object already carries the
real Instagram `audioId` plus title/artist. So we just fetch the HTML and regex
out those objects; no headless browser needed.

Then we POST them to the backend at /api/cron/ingest-trending-audio, which
stores them in the trending_audio table. The publish flow later picks a RANDOM
available track to attach to a reel.

Env:
  BACKEND_URL   base URL of the backend (required)
  CRON_SECRET   shared secret (matches backend CRON_SECRET)
  TRENDING_URL  source page (default: Snaplytics trending songs)
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request

BACKEND_URL = os.environ["BACKEND_URL"].rstrip("/")
CRON_SECRET = os.environ["CRON_SECRET"]
TRENDING_URL = os.getenv("TRENDING_URL", "https://instagram-trending.snaplytics.io/")

# The Next.js RSC stream double-escapes quotes, so fields look like:
#   \"audioId\":\"956972566750887\",\"title\":\"Golden Embers\",\"artist\":\"Oldies Playing\"
# We match the audioId (digits) and pull title/artist near it. \\+ tolerates the
# escaped backslash-quote form; the same patterns also work if quotes are plain.
_AID = re.compile(r'\\*"audioId\\*"\s*:\s*\\*"(?P<aid>\d+)\\*"')
_TITLE = re.compile(r'\\*"title\\*"\s*:\s*\\*"(?P<v>(?:[^"\\]|\\.)*?)\\*"')
_ARTIST = re.compile(r'\\*"artist\\*"\s*:\s*\\*"(?P<v>(?:[^"\\]|\\.)*?)\\*"')
# Each song object also inlines a direct CDN audio stream URL (an .mp4 with an
# AAC audio track). The runner downloads this, skips the first 10s and merges it
# into the reel video. The URL body has no literal quotes; it ends at the next
# escaped closing quote (tolerating the double-escaped \" form).
_AUDIOURL = re.compile(r'\\*"audioUrl\\*"\s*:\s*\\*"(?P<v>(?:\\.|[^"])*?)\\+"')


def _unescape(s: str) -> str:
    # The RSC stream double-escapes; best-effort unescape of common sequences.
    return (
        s.replace('\\"', '"').replace("\\\\", "\\").replace("\\u0026", "&").replace("\\/", "/")
    )


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; InstaPilotAudioBot/1.0)"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read().decode("utf-8", errors="replace")


def parse_songs(html: str) -> list[dict]:
    songs: list[dict] = []
    seen: set[str] = set()
    for m in _AID.finditer(html):
        aid = m.group("aid")
        if aid in seen:
            continue
        seen.add(aid)
        # title/artist/audioUrl follow the audioId within the same object window.
        # audioUrl sits after the (long) coverArt URL, so use a wide window.
        window = html[m.end():m.end() + 400]
        url_window = html[m.end():m.end() + 2000]
        tm = _TITLE.search(window)
        am = _ARTIST.search(window)
        um = _AUDIOURL.search(url_window)
        songs.append({
            "audio_id": aid,
            "title": _unescape(tm.group("v")) if tm else "",
            "artist": _unescape(am.group("v")) if am else "",
            "audio_url": _unescape(um.group("v")) if um else "",
        })
    return songs


def post_songs(songs: list[dict]) -> tuple[int, dict]:
    data = json.dumps({"songs": songs}).encode()
    req = urllib.request.Request(
        f"{BACKEND_URL}/api/cron/ingest-trending-audio", method="POST", data=data,
        headers={"Content-Type": "application/json", "X-Cron-Secret": CRON_SECRET},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode(errors="replace")[:300]}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)[:300]}


def main() -> int:
    print(f"Fetching trending audio from {TRENDING_URL}")
    try:
        html = fetch_html(TRENDING_URL)
    except Exception as e:  # noqa: BLE001
        print(f"Fetch failed: {e}")
        return 1

    songs = parse_songs(html)[:30]
    print(f"Parsed {len(songs)} trending songs (strictly capped to top 30).")
    for s in songs:
        has_url = "url" if s.get("audio_url") else "no-url"
        print(f"  - {s['audio_id']}: {s['title']} — {s['artist']} [{has_url}]")
    if not songs:
        print("No songs parsed — the page structure may have changed.")
        return 1

    status, resp = post_songs(songs)
    print(f"ingest-trending-audio HTTP {status}: {resp}")
    return 0 if status == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
