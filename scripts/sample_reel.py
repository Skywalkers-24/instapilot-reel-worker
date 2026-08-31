#!/usr/bin/env python3
"""
Standalone sample renderer — NO backend, NO network, NO publish.

Renders one full 20s reel (12s job content + 8s full-avatar "Follow
@trendyapaa" outro) locally so you can review the video before it goes live.
Uses a fake job reel + a local avatar from the worker's avatars/ folder.

Run from the worker repo root (so avatars/ and face_logos/ resolve):
    python scripts/sample_reel.py
Optional:
    python scripts/sample_reel.py --avatar avatar_av16_avatar_girls.jpg --out sample.mp4

Output: sample-reel.mp4 (720x1280, ~20s). No audio track is merged in this
sample (there's no download); the real pipeline merges the trending audio.
"""
from __future__ import annotations

import argparse
import os
import sys

# Import the render pipeline from the sibling publish script.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from publish_reel import (  # noqa: E402
    OUTRO_SECONDS,
    REEL_SECONDS,
    generate_local_content_cover,
    render_multi_scene_video,
)

SAMPLE_REEL = {
    "reel_id": 16,  # even -> content cover; odd -> avatar cover (both look fine)
    "title": "Senior Backend Engineer",
    "company": "Rubrik",
    "role": "Senior Backend Engineer",
    "location": "Bengaluru, India (Hybrid)",
    "experience_label": "2-4 Years Exp",
    "salary_text": "₹35-55 LPA",
    "skills": ["Go", "Kubernetes", "Distributed Systems", "PostgreSQL", "gRPC"],
    "badge_text": "Fresher friendly",
    "avatar_name": "",  # resolved from the local avatars/ folder
}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--avatar", default="avatar_av16_avatar_girls.jpg",
                    help="avatar filename in avatars/ to use for the outro")
    ap.add_argument("--out", default="sample-reel.mp4")
    args = ap.parse_args()

    reel = dict(SAMPLE_REEL)
    reel["avatar_name"] = args.avatar

    print(f"Content: {REEL_SECONDS}s + Outro: {OUTRO_SECONDS}s = {REEL_SECONDS + OUTRO_SECONDS}s total")
    print(f"Avatar: {reel['avatar_name']}")
    print("Generating job content cover...")
    content_img = generate_local_content_cover(reel)

    print(f"Rendering sample reel -> {args.out} ...")
    render_multi_scene_video(reel, content_img, args.out, audio_file=None)
    size = os.path.getsize(args.out)
    print(f"DONE: {args.out} ({size} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
