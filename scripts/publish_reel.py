#!/usr/bin/env python3
"""
InstaPilot Reel Worker (GitHub Actions) — Professional Human-Editorial Engine.

Key Highlights:
1. Pure Editorial Typography & Verified Job Intelligence:
   - Eliminates robotic AI voiceovers and spammy fake batch scripts ("Stop scrolling!").
   - Displays 100% accurate job details (real experience, compensation, location, extracted tech stack).
2. Clean Audio & Native Instagram Trending Music:
   - Renders crisp, clean video designed for native Instagram trending audio overlay.
3. Multi-Scene Motion Design:
   - Scene 1: Verified Opening Hook & Role Alert
   - Scene 2: Content Card with subtle Ken Burns cinematic zoom
   - Scene 3: Accurate Role Specifications & Tech Stack
   - Scene 4: Clear Official Application CTA (Pinned Comment)
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont, ImageOps

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
CRON_SECRET = os.getenv("CRON_SECRET", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPOSITORY", "")  # owner/repo
RELEASE_TAG = os.getenv("RELEASE_TAG", "reel-media")
KEEP_ASSETS = int(os.getenv("KEEP_ASSETS", "5"))
# Job-content section stays 12s (capped). An OUTRO section (default 8s) is
# appended after it — a full-avatar "Follow @trendyapaa" close — so the whole
# reel runs CONTENT + OUTRO seconds (default 20s). The merged trending audio
# spans the full length.
REEL_SECONDS = min(float(os.getenv("REEL_SECONDS", "12.0")), 12.0)
OUTRO_SECONDS = min(float(os.getenv("OUTRO_SECONDS", "8.0")), 12.0)
TOTAL_SECONDS = REEL_SECONDS + OUTRO_SECONDS
FORCE_PUBLISH = os.getenv("FORCE_PUBLISH", "").strip().lower() in {"1", "true", "yes", "on"}

POLL_MAX = int(os.getenv("POLL_MAX", "8"))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "15"))
POLL_FIRST_DELAY = float(os.getenv("POLL_FIRST_DELAY", "30"))
IG_SESSIONID = os.getenv("IG_SESSIONID", "").strip()
AUDIO_SCRAPE_MAX = min(int(os.getenv("AUDIO_SCRAPE_MAX", "20")), 20)
AUDIO_SCRAPE_WORKERS = min(int(os.getenv("AUDIO_SCRAPE_WORKERS", "4")), 4)
STATE_PATH = Path(os.getenv("PUBLISH_STATE_PATH", ".publish_state.json"))

GH_API = "https://api.github.com"

# ─── Global Channel Branding Configuration ───────────────────────────────────
CHANNEL_HANDLE = "@trendyapaa"
CHANNEL_DISPLAY_NAME = "TrendyApaa Jobs"
CHANNEL_TAGLINE = "Verified Tech Roles • Official Company Careers"
CHANNEL_FOOTER_NOTE = "Direct Employer Applications • Zero Spam"
# ─────────────────────────────────────────────────────────────────────────────

W, H = 720, 1280
FPS = 24
TOTAL_FRAMES = int(FPS * TOTAL_SECONDS)

COLOR_BG_BASE = (12, 16, 26)
COLOR_CARD_BG = (22, 28, 44, 240)
COLOR_CARD_BORDER = (255, 255, 255, 50)
COLOR_ACCENT = (0, 217, 255)         # Neon Cyan
COLOR_ACCENT_ORANGE = (255, 106, 0)  # Warm Converting Orange
COLOR_TEXT_WHITE = (255, 255, 255)
COLOR_TEXT_MUTED = (160, 175, 200)
COLOR_TEXT_SUB = (140, 155, 180)


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


def _log(message: str) -> None:
    print(message, flush=True)


def _load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2), encoding="utf-8")


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


def upload_asset(release_id: int, file_path: str, asset_name: str, content_type: str = "video/mp4") -> str:
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
            "Content-Type": content_type,
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


# ─── Font and Rendering Utilities ───────────────────────────────────────────
def get_font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        p = Path(candidate)
        if p.exists():
            return ImageFont.truetype(str(p), size)
    return ImageFont.load_default()


def get_best_fit_font(
    draw: ImageDraw.ImageDraw,
    text: str,
    max_w: int,
    max_h: int,
    start_size: int = 46,
    min_size: int = 22,
    bold: bool = True,
    max_lines: int = 2,
) -> tuple[ImageFont.FreeTypeFont, list[str]]:
    for sz in range(start_size, min_size - 1, -2):
        fnt = get_font(sz, bold)
        words = text.split()
        lines: list[str] = []
        cur = ""
        for w in words:
            trial = f"{cur} {w}".strip()
            if draw.textbbox((0, 0), trial, font=fnt)[2] <= max_w:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)

        if len(lines) <= max_lines:
            total_h = sum(draw.textbbox((0, 0), l, font=fnt)[3] + 8 for l in lines)
            if total_h <= max_h:
                return fnt, lines

    fnt = get_font(min_size, bold)
    return fnt, lines[:max_lines]


FONT_HERO = get_font(44, True)
FONT_TITLE = get_font(32, True)
FONT_MED = get_font(26, True)
FONT_BODY = get_font(22)
FONT_SMALL = get_font(18)
FONT_BADGE = get_font(17, True)
FONT_CTA = get_font(25, True)


def rounded_rect(draw: ImageDraw.ImageDraw, box, radius: int, fill, outline=None, width: int = 1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def create_dynamic_background(frame: int, total_frames: int = TOTAL_FRAMES) -> Image.Image:
    t = frame / max(1, total_frames)
    base = Image.new("RGB", (W, H), COLOR_BG_BASE)
    draw = ImageDraw.Draw(base)

    for y in range(0, H, 8):
        yr = y / H
        color = (
            int(12 + 16 * yr),
            int(16 + 22 * yr),
            int(26 + 32 * (1 - yr)),
        )
        draw.rectangle((0, y, W, y + 8), fill=color)

    glow = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    blobs = [
        (int(W * 0.3 + math.sin(t * math.tau) * 100), int(H * 0.25 + math.cos(t * math.tau) * 120), 220, (0, 217, 255, 45)),
        (int(W * 0.75 + math.cos(t * math.tau * 0.8) * 110), int(H * 0.7 + math.sin(t * math.tau * 0.8) * 145), 250, (255, 106, 0, 40)),
        (int(W * 0.5 + math.sin(t * math.tau * 1.2) * 85), int(H * 0.5 + math.cos(t * math.tau * 1.2) * 100), 200, (146, 54, 255, 30)),
    ]
    for cx, cy, rad, col in blobs:
        gd.ellipse((cx - rad, cy - rad, cx + rad, cy + rad), fill=col)

    blurred_glow = glow.filter(ImageFilter.GaussianBlur(48))
    return Image.alpha_composite(base.convert("RGBA"), blurred_glow).convert("RGB")


# ─── Dynamic Palette System for Rich Visual Diversity ────────────────────────
THEME_PALETTES = [
    {  # 0: Deep Cyber Cyan (Modern Tech)
        "bg_top": (10, 15, 28), "bg_bot": (18, 28, 48),
        "card_bg": (20, 26, 42, 240), "accent": (0, 217, 255), "accent_sub": (255, 106, 0),
        "glow1": (0, 217, 255, 35), "glow2": (255, 106, 0, 25),
    },
    {  # 1: Electric Purple / Neon Violet (AI & Product)
        "bg_top": (16, 10, 30), "bg_bot": (32, 18, 56),
        "card_bg": (28, 20, 48, 240), "accent": (191, 90, 242), "accent_sub": (255, 59, 128),
        "glow1": (191, 90, 242, 35), "glow2": (0, 217, 255, 25),
    },
    {  # 2: Obsidian Gold / Amber (High CTC & Premium)
        "bg_top": (18, 14, 8), "bg_bot": (36, 26, 12),
        "card_bg": (32, 24, 14, 240), "accent": (255, 184, 0), "accent_sub": (255, 106, 0),
        "glow1": (255, 184, 0, 35), "glow2": (255, 75, 43, 25),
    },
    {  # 3: Emerald Dark (Fintech & Data)
        "bg_top": (8, 20, 18), "bg_bot": (14, 38, 32),
        "card_bg": (14, 34, 28, 240), "accent": (48, 209, 88), "accent_sub": (0, 217, 255),
        "glow1": (48, 209, 88, 35), "glow2": (0, 217, 255, 25),
    },
    {  # 4: Sunset Crimson (Urgent Drives & Off-Campus)
        "bg_top": (22, 10, 14), "bg_bot": (44, 16, 24),
        "card_bg": (36, 18, 26, 240), "accent": (255, 69, 58), "accent_sub": (255, 159, 10),
        "glow1": (255, 69, 58, 35), "glow2": (255, 159, 10, 25),
    },
]


def resolve_local_face_logo(avatar_name: str | None, reel_id: int | None = None) -> Image.Image:
    """Resolve face logo directly from local worker/face_logos/ directory with dynamic rotation."""
    logos_dir = Path("face_logos")
    if not logos_dir.exists():
        logos_dir = Path("worker/face_logos")
    if logos_dir.exists():
        all_final = sorted(logos_dir.glob("final_face_*.png"))
        if avatar_name:
            name_clean = Path(avatar_name).stem.lower().replace("avatar_", "").replace("face_", "")
            match = re.search(r"av(\d+)", name_clean)
            num_str = match.group(1) if match else None

            if num_str and all_final:
                target_prefix = f"final_face_av{int(num_str):02d}_"
                for f in all_final:
                    if f.stem.lower().startswith(target_prefix):
                        try:
                            return Image.open(f).convert("RGBA")
                        except Exception:
                            pass
                av_idx = int(num_str) % len(all_final)
                try:
                    return Image.open(all_final[av_idx]).convert("RGBA")
                except Exception:
                    pass

            for f in all_final:
                f_clean = f.stem.lower().replace("final_face_", "")
                if name_clean in f_clean or f_clean in name_clean:
                    try:
                        return Image.open(f).convert("RGBA")
                    except Exception:
                        pass

        if all_final:
            idx = (int(reel_id or 0) % len(all_final)) if reel_id else int(time.time()) % len(all_final)
            try:
                return Image.open(all_final[idx]).convert("RGBA")
            except Exception:
                pass

    circle = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(circle)
    d.ellipse((0, 0, 200, 200), fill=(0, 217, 255, 200))
    return circle


def get_theme_palette(reel_id: int | None) -> dict:
    idx = int(reel_id or 0) % len(THEME_PALETTES)
    return THEME_PALETTES[idx]


def resolve_local_avatar_file(avatar_name: str | None, reel_id: int | None = None) -> Path | None:
    avatars_dir = Path("avatars")
    if not avatars_dir.exists():
        avatars_dir = Path("worker/avatars")
    if not avatars_dir.exists():
        return None
    all_av = sorted(avatars_dir.glob("avatar_*"))
    if avatar_name:
        p = avatars_dir / avatar_name
        if p.exists():
            return p
        stem = Path(avatar_name).stem.lower().replace("avatar_", "")
        match = re.search(r"av(\d+)", stem)
        if match:
            num = int(match.group(1))
            for f in all_av:
                if f"av{num:02d}" in f.stem.lower() or f"av{num}" in f.stem.lower():
                    return f
        for f in all_av:
            if stem in f.stem.lower():
                return f

    if all_av:
        idx = (int(reel_id or 0) % len(all_av)) if reel_id else 0
        return all_av[idx]
    return None


def get_ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def _ensure_playwright_chromium() -> None:
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=180,
        )
    except Exception:
        pass


def _scrape_audio_candidates_on_runner(candidates: list[dict]) -> list[dict]:
    """Scrape candidate reel counts on the GitHub runner and return results."""
    candidates = [c for c in (candidates or []) if c.get("audio_id")][:AUDIO_SCRAPE_MAX]
    if not candidates:
        return []

    try:
        scripts_dir = Path(__file__).resolve().parent
        if str(scripts_dir) not in sys.path:
            sys.path.insert(0, str(scripts_dir))
        from scrape_audio_reel_counts import scrape_counts  # type: ignore
    except Exception as exc:
        print(f"  audio scrape unavailable on runner: {exc}")
        return []

    _ensure_playwright_chromium()
    _log(f"  scraping {len(candidates)} audio candidate(s) on GitHub runner...")
    for idx, c in enumerate(candidates, 1):
        aid = str(c.get("audio_id") or "")
        _log(
            f"  AUDIT audio_candidate[{idx}] "
            f"audio_id={aid} instagram_url=https://www.instagram.com/reels/audio/{aid}/ "
            f"title={c.get('title', '')!r} artist={c.get('display_artist', '')!r} "
            f"audio_type={c.get('audio_type', '')} has_download_url={bool(c.get('download_url'))}"
        )
    results = scrape_counts(
        [{"audio_id": c["audio_id"], "title": c.get("title", "")} for c in candidates],
        session_id=IG_SESSIONID,
        workers=min(AUDIO_SCRAPE_WORKERS, len(candidates)),
        headless=True,
        per_page_timeout_ms=15000,
        poll_ms=500,
    )
    by_id = {str(r.get("audio_id") or ""): r for r in results}
    ranked = []
    for c in candidates:
        row = {**c, **(by_id.get(str(c.get("audio_id") or "")) or {})}
        ranked.append(row)
    ranked.sort(key=lambda r: r.get("reel_count") if isinstance(r.get("reel_count"), int) else -1, reverse=True)
    for idx, row in enumerate(ranked, 1):
        count = row.get("reel_count")
        aid = str(row.get("audio_id") or "")
        _log(
            f"  AUDIT audio_scrape_result[{idx}] "
            f"audio_id={aid} instagram_url=https://www.instagram.com/reels/audio/{aid}/ "
            f"status={row.get('status', 'unknown')} reel_count={count if count is not None else '-'} "
            f"count_field={row.get('count_field', '')} elapsed_s={row.get('elapsed_s', '')} "
            f"title={row.get('title', '')!r} scraped_title={row.get('scraped_title', '')!r}"
        )
    return ranked


def _choose_audio_from_runner_scrape(reel: dict) -> dict:
    """Pick the highest-count scraped candidate; fall back to next-reel audio."""
    fallback = {
        "audio_id": reel.get("audio_id") or "",
        "audio_label": reel.get("audio_label") or "",
        "download_url": reel.get("audio_download_url") or "",
        "reel_count": None,
    }
    ranked = _scrape_audio_candidates_on_runner(reel.get("audio_candidates") or [])
    for row in ranked:
        if row.get("audio_id") and row.get("download_url") and isinstance(row.get("reel_count"), int):
            label = f"{row.get('title', '')} - {row.get('display_artist', '')}".strip(" -")
            _log(
                f"  AUDIT audio_selected audio_id={row['audio_id']} "
                f"instagram_url=https://www.instagram.com/reels/audio/{row['audio_id']}/ "
                f"reel_count={row['reel_count']} label={label or row['audio_id']!r}"
            )
            return {
                "audio_id": row["audio_id"],
                "audio_label": label or fallback["audio_label"],
                "download_url": row.get("download_url") or fallback["download_url"],
                "reel_count": row.get("reel_count"),
            }
    if ranked:
        row = ranked[0]
        if row.get("audio_id") and row.get("download_url"):
            label = f"{row.get('title', '')} - {row.get('display_artist', '')}".strip(" -")
            _log(
                f"  AUDIT audio_selected_no_count audio_id={row['audio_id']} "
                f"instagram_url=https://www.instagram.com/reels/audio/{row['audio_id']}/ "
                f"label={label or row['audio_id']!r}"
            )
            return {
                "audio_id": row["audio_id"],
                "audio_label": label or fallback["audio_label"],
                "download_url": row.get("download_url") or fallback["download_url"],
                "reel_count": row.get("reel_count"),
            }
    return fallback


def _download_audio(download_url: str, reel_id: int) -> Path | None:
    if not download_url:
        return None
    out = Path(f"audio-{reel_id}.mp4")
    try:
        req = urllib.request.Request(
            download_url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; InstaPilotWorker/1.0)"},
        )
        with urllib.request.urlopen(req, timeout=60) as resp, out.open("wb") as f:
            shutil.copyfileobj(resp, f)
        if out.exists() and out.stat().st_size > 1000:
            print(f"  downloaded audio for merge: {out} ({out.stat().st_size} bytes)")
            return out
    except Exception as exc:
        print(f"  audio download failed; publishing with attached IG audio only: {exc}")
    try:
        out.unlink(missing_ok=True)
    except Exception:
        pass
    return None


def _mix(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (
        int(c1[0] + (c2[0] - c1[0]) * t),
        int(c1[1] + (c2[1] - c1[1]) * t),
        int(c1[2] + (c2[2] - c1[2]) * t),
    )


def _vertical_gradient(w: int, h: int, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> Image.Image:
    ramp = Image.new("RGB", (1, h))
    px = ramp.load()
    for y in range(h):
        px[0, y] = _mix(top, bottom, y / max(1, h - 1))
    return ramp.resize((w, h))


def _radial_glow(
    w: int, h: int, color: tuple[int, int, int], center: tuple[int, int], radius: int, max_alpha: int = 80
) -> Image.Image:
    glow = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    gdraw = ImageDraw.Draw(glow)
    steps = 22
    for i in range(steps, 0, -1):
        r = int(radius * i / steps)
        a = int(max_alpha * (1 - i / steps))
        gdraw.ellipse(
            (center[0] - r, center[1] - int(r * 1.2), center[0] + r, center[1] + int(r * 1.2)),
            fill=(color[0], color[1], color[2], a),
        )
    return glow.filter(ImageFilter.GaussianBlur(48))


def _bottom_scrim(
    w: int, h: int, base: tuple[int, int, int], start_y: int, full_y: int, max_alpha: int = 255
) -> Image.Image:
    overlay = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    for y in range(start_y, h):
        if y >= full_y:
            a = max_alpha
        else:
            t = (y - start_y) / max(1, full_y - start_y)
            a = int(max_alpha * (t ** 1.5))
        odraw.line((0, y, w, y), fill=(base[0], base[1], base[2], a))
    return overlay


def _top_fade(w: int, base: tuple[int, int, int], start_y: int, fade_len: int = 140, max_alpha: int = 255) -> Image.Image:
    overlay = Image.new("RGBA", (w, fade_len), (0, 0, 0, 0))
    odraw = ImageDraw.Draw(overlay)
    for y in range(0, fade_len):
        t = y / max(1, fade_len)
        a = int(max_alpha * ((1 - t) ** 1.4))
        odraw.line((0, y, w, y), fill=(base[0], base[1], base[2], a))
    return overlay


def _draw_centered_text(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    text: str,
    font_size: int,
    fill: str | tuple[int, int, int],
    bold: bool = True,
) -> None:
    font = get_font(font_size, bold=bold)
    bbox = draw.textbbox((0, 0), text, font=font)
    x1, y1, x2, y2 = box
    tx = x1 + ((x2 - x1) - (bbox[2] - bbox[0])) // 2 - bbox[0]
    ty = y1 + ((y2 - y1) - (bbox[3] - bbox[1])) // 2 - bbox[1]
    draw.text((tx, ty), text, font=font, fill=fill)


def _draw_cover_details(
    draw: ImageDraw.ImageDraw,
    details: list[tuple[str, str]],
    x: int,
    y: int,
    w: int,
    accent: tuple[int, int, int] | str,
    card_bg: tuple[int, int, int, int] | str,
    card_border: tuple[int, int, int, int] | str,
    text_primary: tuple[int, int, int] | str,
    text_muted: tuple[int, int, int] | str,
    row_h: int = 84,
    gap: int = 12,
    max_rows: int = 3,
) -> int:
    bottom = y
    for index, (label, value) in enumerate(details[:max_rows]):
        top = y + (index * (row_h + gap))
        bottom = top + row_h
        draw.rounded_rectangle((x, top, x + w, bottom), radius=18, fill=card_bg, outline=card_border, width=2)
        draw.rounded_rectangle((x, top, x + 8, bottom), radius=4, fill=accent)
        draw.text((x + 28, top + int(row_h * 0.16)), label.upper(), font=get_font(18, bold=True), fill=text_muted)
        value_text = str(value or "Verified").upper()
        value_fnt, value_lines = get_best_fit_font(draw, value_text, w - 56, 44, start_size=32, min_size=20, bold=True, max_lines=1)
        draw.text((x + 28, top + int(row_h * 0.44)), value_lines[0], font=value_fnt, fill=accent if index == 0 else text_primary)
    return bottom


def generate_local_content_cover(reel_info: dict) -> Image.Image:
    """Generate professional 1080x1920 HD content cover with clean editorial typography."""
    CW, CH = 1080, 1920
    SAFE_MARGIN_X = 64
    CONTENT_WIDTH = CW - SAFE_MARGIN_X * 2

    reel_id = reel_info.get("reel_id")
    palette = get_theme_palette(reel_id)

    bg_rgb = palette["bg_top"]
    accent_rgb = palette["accent"]
    card_bg_rgb = palette["card_bg"]
    card_border_rgb = (255, 255, 255, 40)
    text_primary_rgb = (255, 255, 255)
    text_muted_rgb = (148, 163, 184)

    company = (reel_info.get("company") or "Top Tech Company").strip()
    role = (reel_info.get("role") or reel_info.get("title") or "Software Engineer").strip()
    location = (reel_info.get("location") or "India (Hybrid / Remote)").strip()
    exp = (reel_info.get("experience_label") or "0-3 Years Exp").strip()
    package = (reel_info.get("salary_text") or "Competitive CTC").strip()
    avatar_name = reel_info.get("avatar_name") or ""

    comp_lower = company.lower()
    loc_lower = location.lower()
    if any(m in comp_lower for m in ["google", "microsoft", "amazon", "apple", "meta", "adobe", "rubrik", "cisco", "oracle", "nvidia"]):
        series_badge = "TIER-1 TECH OPENING"
    elif any(r in loc_lower for r in ["remote", "work from home", "anywhere"]):
        series_badge = "100% REMOTE TECH JOB"
    elif any(s in package for s in ["20", "25", "30", "35", "40", "45", "50", "55", "60", "LPA"]):
        series_badge = "HIGH-PAYING TECH DRIVE"
    elif any(f in exp.lower() for f in ["0-", "fresher", "intern", "entry"]):
        series_badge = "EARLY CAREER / FRESHER DRIVE"
    else:
        series_badge = "VERIFIED CAREERS OPENING"

    badge_text = series_badge

    top_bg = _mix(bg_rgb, (255, 255, 255), 0.07)
    base = _vertical_gradient(CW, CH, top_bg, bg_rgb).convert("RGBA")
    glow = _radial_glow(CW, CH, accent_rgb, center=(CW // 2, 1230), radius=560, max_alpha=64)
    img = Image.alpha_composite(base, glow).convert("RGB")
    draw = ImageDraw.Draw(img)

    draw.rectangle((0, 0, CW, 10), fill=accent_rgb)

    HERO_TOP = 812
    region_h = CH - HERO_TOP
    avatar_file = resolve_local_avatar_file(avatar_name, reel_id)
    if avatar_file and avatar_file.exists():
        try:
            av_src = Image.open(avatar_file)
            cover = ImageOps.fit(
                av_src.convert("RGB"), (CW, region_h),
                method=Image.Resampling.LANCZOS, centering=(0.5, 0.28),
            )
            img.paste(cover, (0, HERO_TOP))
            fade = _top_fade(CW, bg_rgb, start_y=0, fade_len=140, max_alpha=240)
            img.paste(fade, (0, HERO_TOP), mask=fade)
            draw = ImageDraw.Draw(img)
        except Exception as e:
            print(f"Avatar stage error: {e}")

    top_y = 78
    badge_font = get_font(26, bold=True)
    bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pw, ph = tw + 22 * 2, th + 10 * 2
    draw.rounded_rectangle((SAFE_MARGIN_X, top_y, SAFE_MARGIN_X + pw, top_y + ph), radius=18, fill=accent_rgb)
    draw.text((SAFE_MARGIN_X + 22, top_y + 10 - bbox[1]), badge_text, font=badge_font, fill=bg_rgb)

    logo_size = 158
    logo_box = (CW - SAFE_MARGIN_X - logo_size, top_y - 4, CW - SAFE_MARGIN_X, top_y - 4 + logo_size)
    draw.rounded_rectangle(logo_box, radius=28, fill="#ffffff", outline=card_border_rgb, width=2)
    face_logo = resolve_local_face_logo(avatar_name, reel_id)
    face_in_box = face_logo.resize((120, 120), Image.Resampling.LANCZOS)
    img.paste(face_in_box, (logo_box[0] + (logo_size - 120) // 2, logo_box[1] + (logo_size - 120) // 2), face_in_box)

    company_fnt, company_lines = get_best_fit_font(draw, company.upper(), CW - SAFE_MARGIN_X * 2 - logo_size - 40, 80, start_size=58, min_size=32, bold=True, max_lines=1)
    draw.text((SAFE_MARGIN_X, top_y + 86), company_lines[0], font=company_fnt, fill=text_primary_rgb)
    draw.text((SAFE_MARGIN_X, top_y + 152), "OFFICIAL CAREERS OPENING", font=get_font(22, bold=True), fill=accent_rgb)

    title_y = 280
    role_lines, role_fnt = [], get_font(40, True)
    for sz in range(54, 33, -2):
        fnt = get_font(sz, True)
        words = role.upper().split()
        cur, lines = "", []
        for w in words:
            trial = f"{cur} {w}".strip()
            if draw.textbbox((0, 0), trial, font=fnt)[2] <= CONTENT_WIDTH:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = w
        if cur:
            lines.append(cur)
        if len(lines) <= 2:
            role_lines, role_fnt = lines, fnt
            break
    if not role_lines:
        role_lines, role_fnt = [role.upper()], get_font(36, True)

    for line in role_lines[:2]:
        draw.text((SAFE_MARGIN_X, title_y), line, font=role_fnt, fill=text_primary_rgb)
        title_y += draw.textbbox((0, 0), line, font=role_fnt)[3] + 8
    title_y += 8
    draw.rounded_rectangle((SAFE_MARGIN_X, title_y, SAFE_MARGIN_X + 180, title_y + 8), radius=4, fill=accent_rgb)

    details_y = title_y + 24
    details = [
        ("EXPERIENCE", exp),
        ("LOCATION", location),
        ("PACKAGE", package),
    ]
    _draw_cover_details(
        draw, details, SAFE_MARGIN_X, details_y, CONTENT_WIDTH,
        accent_rgb, card_bg_rgb, card_border_rgb, text_primary_rgb, text_muted_rgb,
        row_h=84, gap=12, max_rows=3,
    )

    scrim = _bottom_scrim(CW, CH, bg_rgb, start_y=1430, full_y=1878, max_alpha=252)
    img = Image.alpha_composite(img.convert("RGBA"), scrim).convert("RGB")
    draw = ImageDraw.Draw(img)

    pill_top, pill_h = 1560, 84
    pill_box = (SAFE_MARGIN_X, pill_top, SAFE_MARGIN_X + CONTENT_WIDTH, pill_top + pill_h)
    draw.rounded_rectangle(pill_box, radius=20, fill=card_bg_rgb, outline=card_border_rgb, width=2)
    seg = CONTENT_WIDTH // 3
    _draw_centered_text(draw, (pill_box[0], pill_top, pill_box[0] + seg, pill_top + pill_h), exp.upper(), 23, accent_rgb)
    _draw_centered_text(draw, (pill_box[0] + seg, pill_top, pill_box[0] + 2 * seg, pill_top + pill_h), "OFFICIAL APPLY LINK", 23, text_primary_rgb)
    _draw_centered_text(draw, (pill_box[0] + 2 * seg, pill_top, pill_box[2], pill_top + pill_h), "VERIFIED OPENING", 23, text_muted_rgb)
    for k in (1, 2):
        dx = pill_box[0] + seg * k
        draw.line((dx, pill_top + 20, dx, pill_top + pill_h - 20), fill=card_border_rgb, width=2)

    cta_top, cta_h = 1676, 156
    draw.rounded_rectangle((SAFE_MARGIN_X, cta_top, SAFE_MARGIN_X + CONTENT_WIDTH, cta_top + cta_h), radius=28, fill=accent_rgb)
    cta_str = "APPLY LINK IN 1ST PINNED COMMENT  →"
    _draw_centered_text(draw, (SAFE_MARGIN_X, cta_top, SAFE_MARGIN_X + CONTENT_WIDTH, cta_top + cta_h), cta_str, 38, bg_rgb)

    font_src = get_font(14, bold=True)
    font_legal = get_font(12, bold=False)
    draw.text((SAFE_MARGIN_X, CH - 40), f"Source: {company} Careers Portal", font=font_src, fill=text_muted_rgb)
    legal_text = "Trademarks belong to their respective owners."
    l_w = draw.textbbox((0, 0), legal_text, font=font_legal)[2]
    draw.text((CW - SAFE_MARGIN_X - l_w, CH - 40), legal_text, font=font_legal, fill=(80, 100, 130))

    return img


def generate_local_avatar_cover(reel_info: dict) -> Image.Image:
    """Generate high-impact 1080x1920 HD Full Avatar cover with clean editorial branding."""
    CW, CH = 1080, 1920
    SAFE_MARGIN_X = 64
    CONTENT_WIDTH = CW - SAFE_MARGIN_X * 2

    reel_id = reel_info.get("reel_id")
    palette = get_theme_palette(reel_id)

    bg_rgb = palette["bg_top"]
    accent_rgb = palette["accent"]
    card_bg_rgb = palette["card_bg"]
    card_border_rgb = (255, 255, 255, 40)
    text_primary_rgb = (255, 255, 255)
    text_muted_rgb = (148, 163, 184)

    company = (reel_info.get("company") or "Top Tech Company").strip()
    role = (reel_info.get("role") or reel_info.get("title") or "Software Engineer").strip()
    location = (reel_info.get("location") or "India (Hybrid / Remote)").strip()
    exp = (reel_info.get("experience_label") or "0-3 Years Exp").strip()
    package = (reel_info.get("salary_text") or "Competitive CTC").strip()
    avatar_name = reel_info.get("avatar_name") or ""

    comp_lower = company.lower()
    loc_lower = location.lower()
    if any(m in comp_lower for m in ["google", "microsoft", "amazon", "apple", "meta", "adobe", "rubrik", "cisco", "oracle", "nvidia"]):
        series_badge = "TIER-1 TECH OPENING"
    elif any(r in loc_lower for r in ["remote", "work from home", "anywhere"]):
        series_badge = "100% REMOTE TECH JOB"
    elif any(s in package for s in ["20", "25", "30", "35", "40", "45", "50", "55", "60", "LPA"]):
        series_badge = "HIGH-PAYING TECH DRIVE"
    elif any(f in exp.lower() for f in ["0-", "fresher", "intern", "entry"]):
        series_badge = "EARLY CAREER / FRESHER DRIVE"
    else:
        series_badge = "VERIFIED CAREERS OPENING"

    badge_text = series_badge

    top_bg = _mix(bg_rgb, (255, 255, 255), 0.08)
    base = _vertical_gradient(CW, CH, top_bg, bg_rgb).convert("RGBA")
    glow = _radial_glow(CW, CH, accent_rgb, center=(CW // 2, 900), radius=620, max_alpha=70)
    img = Image.alpha_composite(base, glow).convert("RGB")

    # 1. Neon Accent Top Bar
    draw = ImageDraw.Draw(img)
    draw.rectangle((0, 0, CW, 10), fill=accent_rgb)

    # 2. Prominent Full Avatar Hero Stage
    avatar_file = resolve_local_avatar_file(avatar_name, reel_id)
    if avatar_file and avatar_file.exists():
        try:
            av_src = Image.open(avatar_file)
            has_alpha = av_src.mode == "RGBA" and av_src.getchannel("A").getextrema()[0] < 255
            if has_alpha:
                ratio = min((CW - 80) / av_src.width, 1300 / av_src.height)
                nw, nh = max(1, int(av_src.width * ratio)), max(1, int(av_src.height * ratio))
                fig = av_src.resize((nw, nh), Image.Resampling.LANCZOS)
                px = (CW - nw) // 2
                py = 280 + (1300 - nh) // 2
                shadow = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
                sh = fig.split()[3].point(lambda a: int(a * 0.45))
                shadow.paste((0, 0, 0, 255), (px, py + 16), mask=sh)
                shadow = shadow.filter(ImageFilter.GaussianBlur(30))
                base_rgba = Image.alpha_composite(img.convert("RGBA"), shadow)
                base_rgba.paste(fig, (px, py), mask=fig)
                img = base_rgba.convert("RGB")
            else:
                AV_TOP = 220
                AV_HEIGHT = 1280
                cover = ImageOps.fit(
                    av_src.convert("RGB"), (CW, AV_HEIGHT),
                    method=Image.Resampling.LANCZOS, centering=(0.5, 0.26),
                )
                img.paste(cover, (0, AV_TOP))
                top_fade = _top_fade(CW, bg_rgb, start_y=0, fade_len=160, max_alpha=255)
                img.paste(top_fade, (0, AV_TOP), mask=top_fade)
        except Exception as e:
            print(f"Full avatar stage error: {e}")

    # 3. Top Header Bar & Channel Identity
    draw = ImageDraw.Draw(img)
    top_y = 68
    face_logo = resolve_local_face_logo(avatar_name, reel_id)
    face_sm = face_logo.resize((84, 84), Image.Resampling.LANCZOS)
    draw.rounded_rectangle((SAFE_MARGIN_X, top_y - 2, SAFE_MARGIN_X + 88, top_y + 86), radius=22, fill="#ffffff", outline=card_border_rgb, width=2)
    img.paste(face_sm, (SAFE_MARGIN_X + 2, top_y), face_sm)
    draw = ImageDraw.Draw(img)

    draw.text((SAFE_MARGIN_X + 104, top_y + 6), CHANNEL_DISPLAY_NAME, font=get_font(32, bold=True), fill=text_primary_rgb)
    draw.text((SAFE_MARGIN_X + 104, top_y + 46), CHANNEL_TAGLINE, font=get_font(20, bold=False), fill=text_muted_rgb)

    # 4. Top Series Badge Pill
    badge_font = get_font(24, bold=True)
    bbox = draw.textbbox((0, 0), badge_text, font=badge_font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    pw, ph = tw + 20 * 2, th + 8 * 2
    badge_x = CW - SAFE_MARGIN_X - pw
    draw.rounded_rectangle((badge_x, top_y + 12, badge_x + pw, top_y + 12 + ph), radius=16, fill=accent_rgb)
    draw.text((badge_x + 20, top_y + 12 + 8 - bbox[1]), badge_text, font=badge_font, fill=bg_rgb)

    # 5. Bottom Scrim & Hero Information Card
    scrim = _bottom_scrim(CW, CH, bg_rgb, start_y=1120, full_y=1640, max_alpha=255)
    img = Image.alpha_composite(img.convert("RGBA"), scrim).convert("RGB")
    draw = ImageDraw.Draw(img)

    # Company & Role Banner
    info_card_y = 1240
    draw.text((SAFE_MARGIN_X, info_card_y), company.upper(), font=get_font(52, bold=True), fill=accent_rgb)
    draw.text((SAFE_MARGIN_X, info_card_y + 68), "IS HIRING NOW", font=get_font(24, bold=True), fill=text_muted_rgb)

    role_fnt, role_lines = get_best_fit_font(draw, role.upper(), CONTENT_WIDTH, 110, start_size=42, min_size=28, bold=True, max_lines=2)
    role_y = info_card_y + 106
    for line in role_lines[:2]:
        draw.text((SAFE_MARGIN_X, role_y), line, font=role_fnt, fill=text_primary_rgb)
        role_y += draw.textbbox((0, 0), line, font=role_fnt)[3] + 8

    # 3-Segment Specs Pill
    pill_top, pill_h = 1530, 84
    pill_box = (SAFE_MARGIN_X, pill_top, SAFE_MARGIN_X + CONTENT_WIDTH, pill_top + pill_h)
    draw.rounded_rectangle(pill_box, radius=20, fill=card_bg_rgb, outline=card_border_rgb, width=2)
    seg = CONTENT_WIDTH // 3
    _draw_centered_text(draw, (pill_box[0], pill_top, pill_box[0] + seg, pill_top + pill_h), exp.upper(), 22, accent_rgb)
    _draw_centered_text(draw, (pill_box[0] + seg, pill_top, pill_box[0] + 2 * seg, pill_top + pill_h), "OFFICIAL APPLY LINK", 22, text_primary_rgb)
    _draw_centered_text(draw, (pill_box[0] + 2 * seg, pill_top, pill_box[2], pill_top + pill_h), "VERIFIED OPENING", 22, text_muted_rgb)
    for k in (1, 2):
        dx = pill_box[0] + seg * k
        draw.line((dx, pill_top + 18, dx, pill_top + pill_h - 18), fill=card_border_rgb, width=2)

    # Big Converting CTA Button
    cta_top, cta_h = 1646, 156
    draw.rounded_rectangle((SAFE_MARGIN_X, cta_top, SAFE_MARGIN_X + CONTENT_WIDTH, cta_top + cta_h), radius=28, fill=accent_rgb)
    cta_str = "APPLY LINK IN 1ST PINNED COMMENT  →"
    _draw_centered_text(draw, (SAFE_MARGIN_X, cta_top, SAFE_MARGIN_X + CONTENT_WIDTH, cta_top + cta_h), cta_str, 38, bg_rgb)

    # Footer source & legal notes
    font_src = get_font(14, bold=True)
    font_legal = get_font(12, bold=False)
    draw.text((SAFE_MARGIN_X, CH - 40), f"Source: {company} Careers Portal", font=font_src, fill=text_muted_rgb)
    legal_text = "Trademarks belong to their respective owners."
    l_w = draw.textbbox((0, 0), legal_text, font=font_legal)[2]
    draw.text((CW - SAFE_MARGIN_X - l_w, CH - 40), legal_text, font=font_legal, fill=(80, 100, 130))

    return img


def render_multi_scene_video(
    reel_info: dict,
    content_img_or_path: Image.Image | str,
    out_path: str,
    duration: float = REEL_SECONDS,
    audio_file: Path | str | None = None,
    outro_seconds: float = OUTRO_SECONDS,
) -> None:
    """
    Renders high-definition motion reel with authentic human-curated editorial scenes.
    Eliminates robotic AI voice and fake bot spam scripts.

    Layout: `duration` seconds of job-content scenes (capped 12s), followed by
    `outro_seconds` of a full-avatar "Follow @trendyapaa" close (2 scenes). The
    merged trending audio spans the full content+outro length.
    """
    # Job-content section stays capped at 12s; the outro is appended after it.
    content_seconds = min(float(duration or REEL_SECONDS), 12.0)
    outro_seconds = max(0.0, min(float(outro_seconds or 0.0), 12.0))
    total_seconds = content_seconds + outro_seconds
    ffmpeg_bin = get_ffmpeg_exe()
    company = (reel_info.get("company") or "Top Tech Company").strip()
    role = (reel_info.get("role") or reel_info.get("title") or "Software Engineer").strip()
    location = (reel_info.get("location") or "India (Hybrid / Remote)").strip()
    exp = (reel_info.get("experience_label") or "0-3 Years Exp").strip()
    package = (reel_info.get("salary_text") or "Competitive CTC").strip()
    skills = reel_info.get("skills") or ["Full Stack", "Problem Solving", "System Architecture", "Engineering"]
    avatar_name = reel_info.get("avatar_name") or ""
    reel_id = reel_info.get("reel_id")

    # Authoritative headline badge
    if any(f in exp.lower() for f in ["0-", "fresher", "intern", "entry"]):
        badge_text = "EARLY CAREER OPENING"
    elif any(s in package for s in ["20", "25", "30", "35", "40", "45", "50", "LPA"]):
        badge_text = "HIGH CTC TECH DRIVE"
    elif any(r in location.lower() for r in ["remote", "work from home"]):
        badge_text = "100% REMOTE TECH ROLE"
    else:
        badge_text = "VERIFIED HIRING ALERT"

    face_raw = resolve_local_face_logo(avatar_name, reel_id)
    face_sm = face_raw.resize((96, 96), Image.Resampling.LANCZOS)
    face_md = face_raw.resize((172, 172), Image.Resampling.LANCZOS)
    face_lg = face_raw.resize((252, 252), Image.Resampling.LANCZOS)

    if isinstance(content_img_or_path, Image.Image):
        content_img = content_img_or_path.convert("RGB")
    else:
        content_img = Image.open(content_img_or_path).convert("RGB")

    margin = 38
    inner_w = W - margin * 2
    card_y = 182
    card_h = 974

    pad = 14
    avail_w = inner_w - pad * 2
    avail_h = card_h - pad * 2
    orig_w, orig_h = content_img.size
    base_scale = min(avail_w / orig_w, avail_h / orig_h)
    fit_w = int(orig_w * base_scale)
    fit_h = int(orig_h * base_scale)
    fitted_cover = content_img.resize((fit_w, fit_h), Image.Resampling.LANCZOS)
    offset_x = margin + pad + (avail_w - fit_w) // 2
    offset_y = card_y + pad + (avail_h - fit_h) // 2

    content_frames = max(1, int(FPS * content_seconds))
    outro_frames = max(0, int(FPS * outro_seconds))
    total_frames = content_frames + outro_frames

    # Pre-build a CLEAN outro background once: the avatar photo on a themed
    # gradient, with the whole lower area left empty so the animated "Follow
    # @trendyapaa" panel sits on a clean surface. We deliberately do NOT reuse
    # generate_local_avatar_cover here — that cover bakes in the job card
    # (company/role, specs pills, "apply link" CTA) which would bleed through
    # behind the follow content.
    PANEL_TOP = H - 470  # everything below this is the clean follow panel
    outro_base = None
    if outro_frames > 0:
        try:
            palette = get_theme_palette(reel_id)
            bg_top = _mix(palette["bg_top"], (255, 255, 255), 0.06)
            base = _vertical_gradient(W, H, bg_top, palette["bg_top"]).convert("RGB")
            avatar_file = resolve_local_avatar_file(avatar_name, reel_id)
            if avatar_file and avatar_file.exists():
                # Fit the avatar into the region ABOVE the follow panel so it's
                # never covered awkwardly; center on the face.
                av_region_h = PANEL_TOP - 40
                cover = ImageOps.fit(
                    Image.open(avatar_file).convert("RGB"), (W, av_region_h),
                    method=Image.Resampling.LANCZOS, centering=(0.5, 0.30),
                )
                base.paste(cover, (0, 0))
                # Soft fade at the very top so the header sits cleanly.
                top_fade = _top_fade(W, palette["bg_top"], start_y=0, fade_len=150, max_alpha=235)
                base.paste(top_fade, (0, 0), mask=top_fade)
            outro_base = base
        except Exception as exc:
            print(f"  [OUTRO] background build failed: {exc}; outro will use plain background")

    def render_outro_frame(local_frame: int) -> Image.Image:
        """Full-avatar close with an animated 'Follow @trendyapaa' banner.

        Two sub-scenes across the outro: (1) a top 'FOLLOW FOR DAILY TECH JOBS'
        banner + big handle; (2) adds a pulsing 'Tap Follow' CTA pill. Rendered
        on top of the full-avatar hero still.
        """
        if outro_base is not None:
            img = outro_base.copy().convert("RGB")
        else:
            img = create_dynamic_background(content_frames + local_frame, total_frames)
        draw = ImageDraw.Draw(img)

        # Continue the top progress bar to 100% across the whole reel.
        progress = int((content_frames + local_frame + 1) / total_frames * inner_w)
        rounded_rect(draw, (margin, 46, W - margin, 54), 4, (40, 48, 64))
        rounded_rect(draw, (margin, 46, margin + progress, 54), 4, COLOR_ACCENT)

        half = max(1, outro_frames // 2)
        sub_scene = 1 if local_frame < half else 2

        # Solid, OPAQUE follow panel over the lower area — a clean surface so
        # nothing from the avatar/background bleeds through behind the text. A
        # short gradient blends the avatar into the panel's top edge.
        panel = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        pdraw = ImageDraw.Draw(panel)
        blend_h = 90
        for y in range(PANEL_TOP - blend_h, PANEL_TOP):
            a = int(255 * ((y - (PANEL_TOP - blend_h)) / blend_h))
            pdraw.line((0, y, W, y), fill=(12, 16, 26, a))
        pdraw.rectangle((0, PANEL_TOP, W, H), fill=(12, 16, 26, 255))
        img = Image.alpha_composite(img.convert("RGBA"), panel).convert("RGB")
        draw = ImageDraw.Draw(img)

        # Top pill: FOLLOW FOR DAILY TECH JOBS
        top_pill = "FOLLOW FOR DAILY TECH JOBS"
        tp_fnt = get_font(24, bold=True)
        tpw = draw.textbbox((0, 0), top_pill, font=tp_fnt)[2]
        pill_w = tpw + 56
        rounded_rect(draw, ((W - pill_w) // 2, 96, (W + pill_w) // 2, 150), 18, COLOR_ACCENT_ORANGE)
        draw.text(((W - tpw) // 2, 108), top_pill, font=tp_fnt, fill=COLOR_TEXT_WHITE)

        # ── Follow panel contents (all laid out inside the clean panel) ──
        # Face logo sits centered at the panel's top edge.
        face_d = 150
        face_x = (W - face_d) // 2
        face_y = PANEL_TOP - face_d // 2
        face_circ = face_lg.resize((face_d, face_d), Image.Resampling.LANCZOS)
        img.paste(face_circ, (face_x, face_y), face_circ)
        draw.ellipse((face_x, face_y, face_x + face_d, face_y + face_d), outline=COLOR_ACCENT, width=5)

        # Big handle "Follow @trendyapaa".
        handle_y = face_y + face_d + 22
        follow_line = f"Follow {CHANNEL_HANDLE}"
        fl_fnt = get_font(46, bold=True)
        flw = draw.textbbox((0, 0), follow_line, font=fl_fnt)[2]
        draw.text(((W - flw) // 2, handle_y), follow_line, font=fl_fnt, fill=COLOR_TEXT_WHITE)

        sub = "Verified tech roles • Every single day"
        sub_fnt = get_font(22, bold=False)
        sw = draw.textbbox((0, 0), sub, font=sub_fnt)[2]
        draw.text(((W - sw) // 2, handle_y + 58), sub, font=sub_fnt, fill=COLOR_ACCENT)

        # Sub-scene 2: pulsing "TAP FOLLOW" CTA pill (well below the subline).
        if sub_scene == 2:
            pulse = 0.5 + 0.5 * math.sin((local_frame - half) / max(1, half) * math.tau)
            cta = "TAP  FOLLOW  →"
            cta_fnt = get_font(30, bold=True)
            cw = draw.textbbox((0, 0), cta, font=cta_fnt)[2]
            cpw = cw + 72
            cta_h = 64
            cy = handle_y + 110
            col = _mix(COLOR_ACCENT_ORANGE, (255, 255, 255), 0.25 * pulse)
            rounded_rect(draw, ((W - cpw) // 2, cy, (W + cpw) // 2, cy + cta_h), 22, col)
            draw.text(((W - cw) // 2, cy + 16), cta, font=cta_fnt, fill=COLOR_TEXT_WHITE)

        return img

    def draw_frame(frame: int, scene_name: str) -> Image.Image:
        img = create_dynamic_background(frame, total_frames)
        draw = ImageDraw.Draw(img)

        # 1. Top progress bar
        progress = int((frame + 1) / total_frames * inner_w)
        rounded_rect(draw, (margin, 46, W - margin, 54), 4, (40, 48, 64))
        rounded_rect(draw, (margin, 46, margin + progress, 54), 4, COLOR_ACCENT)

        # 2. Header Bar
        img.paste(face_sm, (margin, 70), face_sm)
        draw.text((margin + 112, 78), CHANNEL_DISPLAY_NAME, font=FONT_MED, fill=COLOR_TEXT_WHITE)
        draw.text((margin + 112, 112), CHANNEL_TAGLINE, font=FONT_SMALL, fill=COLOR_TEXT_MUTED)

        if scene_name == "intro_hook":
            card_surface = Image.new("RGBA", (inner_w, card_h), COLOR_CARD_BG)
            card_draw = ImageDraw.Draw(card_surface)
            card_draw.rounded_rectangle((0, 0, inner_w, card_h), radius=32, fill=COLOR_CARD_BG, outline=COLOR_CARD_BORDER, width=2)
            img.paste(card_surface, (margin, card_y), card_surface)

            av_x = (W - 252) // 2
            av_y = card_y + 48
            img.paste(face_lg, (av_x, av_y), face_lg)
            draw.ellipse((av_x, av_y, av_x + 252, av_y + 252), outline=COLOR_ACCENT_ORANGE, width=4)

            pill_w = max(240, draw.textbbox((0, 0), badge_text, font=FONT_BADGE)[2] + 48)
            rounded_rect(draw, ((W - pill_w) // 2, card_y + 336, ((W + pill_w) // 2), card_y + 378), 16, COLOR_ACCENT_ORANGE)
            draw.text(((W - draw.textbbox((0, 0), badge_text, font=FONT_BADGE)[2]) // 2, card_y + 346), badge_text, font=FONT_BADGE, fill=COLOR_TEXT_WHITE)

            intro_text = "Verified Career Opening"
            draw.text(((W - draw.textbbox((0, 0), intro_text, font=FONT_TITLE)[2]) // 2, card_y + 410), intro_text, font=FONT_TITLE, fill=COLOR_TEXT_WHITE)

            comp_text = f"{company} is hiring"
            comp_fnt, comp_lines = get_best_fit_font(draw, comp_text, inner_w - 70, 94, start_size=46, min_size=30, bold=True, max_lines=1)
            tw = draw.textbbox((0, 0), comp_lines[0], font=comp_fnt)[2]
            draw.text(((W - tw) // 2, card_y + 474), comp_lines[0], font=comp_fnt, fill=COLOR_ACCENT)

            role_fnt, role_lines = get_best_fit_font(draw, role, inner_w - 70, 146, start_size=38, min_size=24, bold=True, max_lines=2)
            y_role = card_y + 548
            for r_line in role_lines:
                rtw = draw.textbbox((0, 0), r_line, font=role_fnt)[2]
                draw.text(((W - rtw) // 2, y_role), r_line, font=role_fnt, fill=COLOR_TEXT_WHITE)
                y_role += draw.textbbox((0, 0), r_line, font=role_fnt)[3] + 10

            rounded_rect(draw, (margin + 32, card_y + 780, W - margin - 32, card_y + 886), 20, (30, 38, 58), outline=COLOR_ACCENT, width=1)
            apply_hint = "Official direct application link in pinned comment"
            draw.text(((W - draw.textbbox((0, 0), apply_hint, font=FONT_BODY)[2]) // 2, card_y + 816), apply_hint, font=FONT_BODY, fill=COLOR_TEXT_WHITE)

        elif scene_name == "content_card":
            card_surface = Image.new("RGBA", (inner_w, card_h), (20, 25, 38, 245))
            card_draw = ImageDraw.Draw(card_surface)
            card_draw.rounded_rectangle((0, 0, inner_w, card_h), radius=32, fill=(20, 25, 38, 245), outline=COLOR_CARD_BORDER, width=2)
            img.paste(card_surface, (margin, card_y), card_surface)

            img.paste(fitted_cover, (offset_x, offset_y))
            draw.rounded_rectangle((margin + pad, card_y + pad, margin + pad + avail_w, card_y + pad + avail_h), radius=22, outline=(255, 255, 255, 50), width=1)

        elif scene_name == "role_details":
            card_surface = Image.new("RGBA", (inner_w, card_h), COLOR_CARD_BG)
            card_draw = ImageDraw.Draw(card_surface)
            card_draw.rounded_rectangle((0, 0, inner_w, card_h), radius=32, fill=COLOR_CARD_BG, outline=COLOR_CARD_BORDER, width=2)
            img.paste(card_surface, (margin, card_y), card_surface)

            # 1. Header Pill Banner (y: 24..78, h=54)
            header_w = inner_w - 48
            rounded_rect(draw, (margin + 24, card_y + 24, margin + 24 + header_w, card_y + 78), 16, COLOR_ACCENT)
            draw.text(((W - draw.textbbox((0, 0), "VERIFIED ROLE SPECIFICATIONS", font=FONT_BADGE)[2]) // 2, card_y + 36), "VERIFIED ROLE SPECIFICATIONS", font=FONT_BADGE, fill=(12, 16, 26))

            # 2. Company & Role Hero Card (y: 96..242, h=146)
            hero_y = card_y + 96
            hero_h = 146
            rounded_rect(draw, (margin + 24, hero_y, W - margin - 24, hero_y + hero_h), 20, (26, 34, 54), outline=COLOR_ACCENT, width=2)
            draw.text((margin + 46, hero_y + 18), "COMPANY & POSITION", font=FONT_SMALL, fill=COLOR_TEXT_SUB)
            comp_fnt, comp_lines = get_best_fit_font(draw, company.upper(), inner_w - 90, 44, start_size=30, min_size=22, bold=True, max_lines=1)
            draw.text((margin + 46, hero_y + 48), comp_lines[0], font=comp_fnt, fill=COLOR_TEXT_WHITE)
            role_fnt, role_lines = get_best_fit_font(draw, role, inner_w - 90, 44, start_size=26, min_size=18, bold=True, max_lines=1)
            draw.text((margin + 46, hero_y + 94), role_lines[0], font=role_fnt, fill=COLOR_ACCENT)

            # 3. Major Spec Cards (y: 258..650, 3 cards x 120h + 16 gap)
            specs = [
                ("EXPERIENCE & ELIGIBILITY", exp, (76, 217, 100)),
                ("LOCATION / WORK MODE", location, COLOR_TEXT_WHITE),
                ("PACKAGE / COMPENSATION", package, (255, 214, 10)),
            ]

            spec_y = card_y + 258
            spec_h = 120
            for label, val, val_col in specs:
                rounded_rect(draw, (margin + 24, spec_y, W - margin - 24, spec_y + spec_h), 18, (26, 34, 54))
                draw.text((margin + 46, spec_y + 18), label, font=FONT_SMALL, fill=COLOR_TEXT_SUB)
                val_fnt, val_lines = get_best_fit_font(draw, val, inner_w - 90, 56, start_size=32, min_size=22, bold=True, max_lines=1)
                draw.text((margin + 46, spec_y + 54), val_lines[0], font=val_fnt, fill=val_col)
                spec_y += spec_h + 16

            # 4. Tech Stack Banner (y: 666..786, h=120)
            skills_y = card_y + 666
            skills_h = 120
            rounded_rect(draw, (margin + 24, skills_y, W - margin - 24, skills_y + skills_h), 18, (20, 28, 44), outline=COLOR_ACCENT, width=1)
            draw.text((margin + 46, skills_y + 18), "REQUIRED TECH STACK", font=FONT_SMALL, fill=COLOR_TEXT_SUB)
            skill_str = " • ".join(skills[:5])
            sk_fnt, sk_lines = get_best_fit_font(draw, skill_str, inner_w - 90, 52, start_size=24, min_size=16, bold=True, max_lines=1)
            draw.text((margin + 46, skills_y + 54), sk_lines[0], font=sk_fnt, fill=COLOR_ACCENT)

            # 5. Bottom Action Button (y: 806..920, h=114)
            btn_y = card_y + 806
            btn_h = 114
            rounded_rect(draw, (margin + 24, btn_y, W - margin - 24, btn_y + btn_h), 22, COLOR_ACCENT_ORANGE)
            save_note = "SAVE THIS POST TO REVIEW BEFORE APPLYING  →"
            save_fnt, save_lines = get_best_fit_font(draw, save_note, inner_w - 70, 50, start_size=23, min_size=16, bold=True, max_lines=1)
            draw.text(((W - draw.textbbox((0, 0), save_lines[0], font=save_fnt)[2]) // 2, btn_y + 40), save_lines[0], font=save_fnt, fill=COLOR_TEXT_WHITE)

        else:
            card_surface = Image.new("RGBA", (inner_w, card_h), COLOR_CARD_BG)
            card_draw = ImageDraw.Draw(card_surface)
            card_draw.rounded_rectangle((0, 0, inner_w, card_h), radius=32, fill=COLOR_CARD_BG, outline=COLOR_CARD_BORDER, width=2)
            img.paste(card_surface, (margin, card_y), card_surface)

            # 1. Avatar Header
            av_x = (W - 200) // 2
            av_y = card_y + 32
            face_cta = face_raw.resize((200, 200), Image.Resampling.LANCZOS)
            img.paste(face_cta, (av_x, av_y), face_cta)
            draw.ellipse((av_x, av_y, av_x + 200, av_y + 200), outline=COLOR_ACCENT, width=4)

            # 2. Title & Subtitle
            draw.text(((W - draw.textbbox((0, 0), "HOW TO APPLY", font=FONT_HERO)[2]) // 2, card_y + 248), "HOW TO APPLY", font=FONT_HERO, fill=COLOR_TEXT_WHITE)
            sub_cta = "Direct Official Application in 3 Steps"
            draw.text(((W - draw.textbbox((0, 0), sub_cta, font=FONT_SMALL)[2]) // 2, card_y + 308), sub_cta, font=FONT_SMALL, fill=COLOR_ACCENT)

            # 3. Three Spacious Instruction Steps (y: 348..796, 3 cards x 136h + 16 gap)
            step_cards = [
                ("STEP 1", "1st Pinned Comment on this Reel", "Direct official link to employer career portal", True),
                ("STEP 2", "Verified Official Application", "Zero third-party forms • Official ATS drive", False),
                ("STEP 3", f"Follow {CHANNEL_HANDLE} for Daily Roles", "Hand-curated top engineering & tech hiring drives", False),
            ]

            step_y = card_y + 348
            step_h = 136
            for badge, heading, desc, is_highlight in step_cards:
                bg_col = (20, 36, 60) if is_highlight else (26, 34, 52)
                border_col = COLOR_ACCENT if is_highlight else (255, 255, 255, 30)
                rounded_rect(draw, (margin + 24, step_y, W - margin - 24, step_y + step_h), 20, bg_col, outline=border_col, width=2 if is_highlight else 1)

                # Step Badge
                rounded_rect(draw, (margin + 44, step_y + 18, margin + 130, step_y + 48), 10, COLOR_ACCENT if is_highlight else (45, 58, 85))
                draw.text((margin + 56, step_y + 23), badge, font=FONT_SMALL, fill=(12, 16, 26) if is_highlight else COLOR_TEXT_WHITE)

                # Head & Desc
                head_col = COLOR_ACCENT if is_highlight else COLOR_TEXT_WHITE
                draw.text((margin + 144, step_y + 21), heading, font=get_font(22, bold=True), fill=head_col)
                draw.text((margin + 46, step_y + 72), desc, font=FONT_BODY, fill=(230, 240, 255) if is_highlight else COLOR_TEXT_MUTED)

                step_y += step_h + 16

            # 4. Large Converting Bottom Button (y: 818..926, h=108)
            cta_btn_y = card_y + 818
            cta_btn_h = 108
            rounded_rect(draw, (margin + 24, cta_btn_y, W - margin - 24, cta_btn_y + cta_btn_h), 26, COLOR_ACCENT_ORANGE)
            cta_str = "TAP SAVE TO APPLY LATER  →"
            draw.text(((W - draw.textbbox((0, 0), cta_str, font=FONT_CTA)[2]) // 2, cta_btn_y + 36), cta_str, font=FONT_CTA, fill=COLOR_TEXT_WHITE)

        draw.text((margin + 10, H - 72), CHANNEL_HANDLE, font=FONT_MED, fill=COLOR_TEXT_WHITE)
        draw.text((margin + 10, H - 38), CHANNEL_FOOTER_NOTE, font=FONT_SMALL, fill=COLOR_TEXT_MUTED)

        return img

    with tempfile.TemporaryDirectory(prefix="runner_multi_reel_") as tmp:
        frame_dir = Path(tmp)

        for i in range(total_frames):
            if i < content_frames:
                # Job-content scenes mapped by ABSOLUTE seconds within the 12s
                # content window (so appending the outro never rescales them).
                cur_sec = i / FPS
                rel_t = cur_sec / max(1.0, content_seconds)
                if rel_t < 0.22:
                    scene_name = "intro_hook"
                elif rel_t < 0.48:
                    scene_name = "content_card"
                elif rel_t < 0.78:
                    scene_name = "role_details"
                else:
                    scene_name = "cta_action"
                f_img = draw_frame(i, scene_name=scene_name)
            else:
                # Outro: full-avatar "Follow @trendyapaa" close (2 sub-scenes).
                f_img = render_outro_frame(i - content_frames)
            f_img.save(frame_dir / f"frame_{i:04d}.png", "PNG")

        tmp_out = frame_dir / "out.mp4"
        if audio_file and os.path.exists(str(audio_file)) and os.path.getsize(str(audio_file)) > 1000:
            # Skip the first 10s of the track (intros), span the full reel, fade
            # out the last 1s.
            fade_start = max(0.0, total_seconds - 1.0)
            audio_inputs = [
                "-ss", "10",
                "-t", str(total_seconds),
                "-i", str(audio_file),
                "-af", f"afade=t=out:st={fade_start:.1f}:d=1.0",
            ]
        else:
            audio_inputs = [
                "-f", "lavfi",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
            ]

        cmd = [
            ffmpeg_bin,
            "-y",
            "-framerate", str(FPS),
            "-i", str(frame_dir / "frame_%04d.png"),
            *audio_inputs,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "22",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "128k",
            "-t", str(total_seconds),
            "-r", str(FPS),
            "-movflags", "+faststart",
            str(tmp_out),
        ]
        print(f"  [FFMPEG LOG] Rendering clean human-editorial reel: {' '.join(cmd)}")
        res = subprocess.run(cmd, capture_output=True, text=True)
        if res.returncode != 0:
            print(f"  [FFMPEG ERROR] Code {res.returncode}: {res.stderr[-500:]}")
            raise RuntimeError(f"FFmpeg failed: {res.stderr}")
        shutil.copyfile(tmp_out, out_path)
        out_sz = os.path.getsize(out_path)
        print(f"  [FFMPEG SUCCESS] Rendered MP4 output: {out_path} ({out_sz} bytes)")


def publish_via_backend(reel_id: int, video_url: str, cover_url: str = "", audio_id: str = "", audio_label: str = "") -> tuple[str, str, str]:
    payload = {"reel_id": reel_id, "video_url": video_url}
    if cover_url:
        payload["cover_url"] = cover_url
    if audio_id:
        payload["audio_id"] = audio_id
    if audio_label:
        payload["audio_label"] = audio_label
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
    audio_label = data.get("audio") or ""
    audio_id_used = data.get("audio_id") or ""
    if audio_id_used:
        _log(f"  audio attached: {audio_label or audio_id_used} (id={audio_id_used})")
    else:
        _log("  no audio attached (none available / not applicable)")
    _log(f"  container created: {container_id}")

    _log(
        f"  waiting {POLL_FIRST_DELAY:.0f}s before first Instagram processing status check "
        f"(then up to {POLL_MAX} checks every {POLL_INTERVAL:.0f}s)"
    )
    time.sleep(POLL_FIRST_DELAY)
    for attempt in range(POLL_MAX):
        st, data = backend_post("/api/cron/ig/container-status", {"container_id": container_id})
        if st == 200:
            code = data.get("status_code", "UNKNOWN")
            _log(f"  container status: {code} (attempt {attempt+1})")
            if code == "FINISHED":
                break
            if code in ("ERROR", "EXPIRED"):
                return code, "", f"container processing {code}"
        else:
            _log(f"  status check HTTP {st} (attempt {attempt+1}); retrying")
        if attempt < POLL_MAX - 1:
            time.sleep(POLL_INTERVAL)
    else:
        return "IN_PROGRESS", "", "Container did not finish within polling window."

    st, data = backend_post("/api/cron/ig/publish", {"reel_id": reel_id, "container_id": container_id})
    if st != 200:
        return "ERROR", "", f"publish HTTP {st}: {data}"
    return data.get("status", "ERROR"), data.get("media_id", ""), data.get("message", "")


def stage_prepare() -> int:
    st, build = backend_post("/api/cron/auto-post", {"force": FORCE_PUBLISH})
    if st == 200:
        _log(f"auto-post: {build.get('status')} reel_id={build.get('reel_id')}")
        if build.get("message") or build.get("reason"):
            _log(f"auto-post message: {build.get('message') or build.get('reason')}")
    else:
        _log(f"auto-post HTTP {st}: {build}")
    if isinstance(build, dict) and build.get("status") == "SKIPPED_CADENCE":
        _log(f"Cadence gate active: {build.get('reason')}")
        _save_state({"skip": True, "reason": build.get("reason")})
        return 0

    status, data = backend_post("/api/cron/next-reel", {"force": FORCE_PUBLISH})
    if status != 200:
        _log(f"next-reel failed: {status} {data}")
        return 1
    reel = data.get("reel")
    if not reel:
        if data.get("message"):
            _log(f"next-reel message: {data.get('message')}")
        _log("No reel to publish. Done.")
        _save_state({"skip": True, "reason": data.get("message") or "no reel"})
        return 0

    _log(f"Next reel: #{reel['reel_id']} - {reel.get('title')!r}")
    _log(f"Audio candidates from backend: {len(reel.get('audio_candidates') or [])}")
    _save_state({"skip": False, "reel": reel})
    return 0


def stage_scrape_audio() -> int:
    state = _load_state()
    if state.get("skip"):
        _log(f"Skipping audio scrape: {state.get('reason', 'no reel')}")
        return 0
    reel = state.get("reel") or {}
    reel_id = reel.get("reel_id")
    chosen_audio = _choose_audio_from_runner_scrape(reel)
    audio_id = chosen_audio.get("audio_id") or ""
    audio_label = chosen_audio.get("audio_label") or ""
    audio_file_path = _download_audio(chosen_audio.get("download_url") or "", reel_id)
    if audio_id:
        _log(f"  audio to attach: {audio_label or audio_id}")
    state["audio"] = {
        **chosen_audio,
        "audio_id": audio_id,
        "audio_label": audio_label,
        "audio_file_path": str(audio_file_path or ""),
    }
    _save_state(state)
    return 0


def stage_render() -> int:
    state = _load_state()
    if state.get("skip"):
        _log(f"Skipping render: {state.get('reason', 'no reel')}")
        return 0
    reel = state.get("reel") or {}
    audio = state.get("audio") or {}
    reel_id = reel["reel_id"]
    audio_file_path = audio.get("audio_file_path") or None

    _log(f"Generating unique 1080x1920 job content card locally on worker for reel #{reel_id}...")
    content_img = generate_local_content_cover(reel)

    use_avatar_cover = (int(reel_id or 0) % 2 == 1)
    if use_avatar_cover:
        _log(f"Generating unique 1080x1920 FULL AVATAR cover for reel #{reel_id} (alternating thumbnail mode)...")
        cover_img = generate_local_avatar_cover(reel)
    else:
        _log(f"Generating unique 1080x1920 SCENE 1 / CONTENT cover for reel #{reel_id} (alternating thumbnail mode)...")
        cover_img = content_img

    cover_file = f"cover-{reel_id}.jpg"
    cover_img.save(cover_file, "JPEG", quality=95)

    out = f"reel-{reel_id}.mp4"
    _log("Rendering clean human-editorial multi-scene dynamic reel...")
    render_multi_scene_video(reel, content_img, out, audio_file=audio_file_path)
    size = os.path.getsize(out)
    _log(f"Rendered {out} ({size} bytes)")
    state["render"] = {"video_file": out, "cover_file": cover_file, "video_size": size}
    _save_state(state)
    return 0


def stage_upload() -> int:
    state = _load_state()
    if state.get("skip"):
        _log(f"Skipping upload: {state.get('reason', 'no reel')}")
        return 0
    reel = state.get("reel") or {}
    render = state.get("render") or {}
    reel_id = reel["reel_id"]
    out = render.get("video_file") or f"reel-{reel_id}.mp4"
    cover_file = render.get("cover_file") or f"cover-{reel_id}.jpg"

    release_id = ensure_release()
    ts = int(time.time())
    asset_name = f"reel-{reel_id}-{ts}.mp4"
    video_url = upload_asset(release_id, out, asset_name, content_type="video/mp4")
    _log(f"Uploaded video: {video_url}")

    cover_asset_name = f"cover-{reel_id}-{ts}.jpg"
    cover_public_url = upload_asset(release_id, cover_file, cover_asset_name, content_type="image/jpeg")
    _log(f"Uploaded unique cover: {cover_public_url}")

    prune_assets(release_id, KEEP_ASSETS)
    state["upload"] = {"video_url": video_url, "cover_public_url": cover_public_url}
    _save_state(state)
    return 0


def stage_publish() -> int:
    state = _load_state()
    if state.get("skip"):
        _log(f"Nothing to publish: {state.get('reason', 'no reel')}")
        return 0
    reel = state.get("reel") or {}
    audio = state.get("audio") or {}
    upload = state.get("upload") or {}
    reel_id = reel["reel_id"]
    video_url = upload.get("video_url") or ""
    cover_public_url = upload.get("cover_public_url") or ""
    audio_id = audio.get("audio_id") or ""
    audio_label = audio.get("audio_label") or ""

    _log("Publishing to Instagram via backend endpoints...")
    ig_status, media_id, msg = publish_via_backend(
        reel_id, video_url, cover_url=cover_public_url,
        audio_id=audio_id, audio_label=audio_label,
    )
    _log(f"IG result: status={ig_status} media_id={media_id} msg={msg}")

    _st, mark = backend_post("/api/cron/mark-published", {
        "reel_id": reel_id, "video_url": video_url,
        "media_id": media_id, "status": ig_status, "error": msg,
    })
    if isinstance(mark, dict) and mark.get("media_audio_type"):
        _log(f"  media_audio_type: {mark.get('media_audio_type')}")

    if ig_status in ("PUBLISHED", "DRY_RUN", "CONTAINER_READY"):
        _log("Done - clean human-editorial reel published.")
        return 0
    _log(f"Publish did not succeed: {msg}")
    return 1


def main() -> int:
    stage = ""
    if "--stage" in sys.argv:
        idx = sys.argv.index("--stage")
        stage = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
    stages = {
        "prepare": stage_prepare,
        "scrape-audio": stage_scrape_audio,
        "render": stage_render,
        "upload": stage_upload,
        "publish": stage_publish,
    }
    if stage:
        fn = stages.get(stage)
        if not fn:
            _log(f"Unknown stage: {stage}")
            return 2
        return fn()

    for fn in stages.values():
        code = fn()
        if code != 0:
            return code
    return 0
if __name__ == "__main__":
    sys.exit(main())
