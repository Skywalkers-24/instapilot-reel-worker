#!/usr/bin/env python3
"""
InstaPilot Reel Worker (GitHub Actions).

Upgraded Multi-Scene Engine:
1. Asks backend for next reel with job details.
2. Uses local avatar assets and local curated face logos directly (no network dependency).
3. Downloads the designed content card.
4. Renders 20-second dynamic multi-scene video (Hook -> Content Card -> Spaced Details -> CTA).
5. Uploads to GitHub Releases and publishes live to Instagram via Graph API.
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
import urllib.request
import urllib.error
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

BACKEND_URL = os.getenv("BACKEND_URL", "http://localhost:8000").rstrip("/")
CRON_SECRET = os.getenv("CRON_SECRET", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_REPO = os.getenv("GITHUB_REPOSITORY", "")  # owner/repo
RELEASE_TAG = os.getenv("RELEASE_TAG", "reel-media")
KEEP_ASSETS = int(os.getenv("KEEP_ASSETS", "5"))
REEL_SECONDS = float(os.getenv("REEL_SECONDS", "20"))

POLL_MAX = int(os.getenv("POLL_MAX", "12"))
POLL_INTERVAL = float(os.getenv("POLL_INTERVAL", "8"))
POLL_FIRST_DELAY = float(os.getenv("POLL_FIRST_DELAY", "15"))

GH_API = "https://api.github.com"

# ─── Global Channel Branding Configuration ───────────────────────────────────
CHANNEL_HANDLE = "@trendyapaa"
CHANNEL_DISPLAY_NAME = "TrendyApaa Jobs"
CHANNEL_TAGLINE = "Verified Tech Openings • Official Apply"
CHANNEL_FOOTER_NOTE = "Zero Spam • Verified Official Career Links"
# ─────────────────────────────────────────────────────────────────────────────

W, H = 720, 1280
FPS = 24
TOTAL_FRAMES = int(FPS * REEL_SECONDS)

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


def download(url: str, dest: str) -> bool:
    status, raw = _http("GET", url, timeout=120)
    if status != 200 or len(raw) < 500:
        print(f"  download failed: {url} status={status} bytes={len(raw)}")
        return False
    with open(dest, "wb") as f:
        f.write(raw)
    return True


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


FONT_HERO = get_font(46, True)
FONT_TITLE = get_font(34, True)
FONT_MED = get_font(26, True)
FONT_BODY = get_font(22)
FONT_SMALL = get_font(18)
FONT_BADGE = get_font(17, True)
FONT_CTA = get_font(25, True)


def rounded_rect(draw: ImageDraw.ImageDraw, box, radius: int, fill, outline=None, width: int = 1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def create_dynamic_background(frame: int) -> Image.Image:
    t = frame / TOTAL_FRAMES
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


def get_scene(frame: int) -> str:
    sec = frame / FPS
    if sec < 4.0:
        return "intro_hook"
    if sec < 9.0:
        return "content_card"
    if sec < 15.0:
        return "role_details"
    return "cta_action"


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
    if logos_dir.exists():
        all_final = sorted(logos_dir.glob("final_face_*.png"))
        if avatar_name:
            name_clean = Path(avatar_name).stem.lower().replace("avatar_", "").replace("face_", "")
            match = re.search(r"av(\d+)", name_clean)
            num_str = match.group(1) if match else None
            
            # 1. Exact match by number (final_face_avXX_*.png)
            if num_str:
                target_prefix = f"final_face_av{int(num_str):02d}_"
                for f in all_final:
                    if f.stem.lower().startswith(target_prefix):
                        try:
                            return Image.open(f).convert("RGBA")
                        except Exception:
                            pass

            # 2. Match by clean slug
            for f in all_final:
                f_clean = f.stem.lower().replace("final_face_", "")
                if name_clean in f_clean or f_clean in name_clean:
                    try:
                        return Image.open(f).convert("RGBA")
                    except Exception:
                        pass

        # 3. Dynamic Rotation across 90 face logos using reel_id
        if all_final:
            idx = (int(reel_id or 0) % len(all_final)) if reel_id else int(time.time()) % len(all_final)
            try:
                return Image.open(all_final[idx]).convert("RGBA")
            except Exception:
                pass

    # Safety circle fallback
    circle = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(circle)
    d.ellipse((0, 0, 200, 200), fill=(0, 217, 255, 200))
    return circle


def get_theme_palette(reel_id: int | None) -> dict:
    """Pick rotating aesthetic color palette based on reel_id."""
    idx = int(reel_id or 0) % len(THEME_PALETTES)
    return THEME_PALETTES[idx]



def resolve_local_avatar_file(avatar_name: str | None, reel_id: int | None = None) -> Path | None:
    """Resolve full avatar image directly from worker/avatars/ directory with dynamic rotation."""
    avatars_dir = Path("avatars")
    if not avatars_dir.exists():
        return None
    all_av = sorted(avatars_dir.glob("avatar_*"))
    if avatar_name:
        p = avatars_dir / avatar_name
        if p.exists():
            return p
        stem = Path(avatar_name).stem.lower().replace("avatar_", "")
        for f in all_av:
            if stem in f.stem.lower():
                return f

    if all_av:
        idx = (int(reel_id or 0) % len(all_av)) if reel_id else 0
        return all_av[idx]
    return None



def clean_spoken_text(text: str) -> str:
    """Clean technical titles/locations for smooth human TTS pronunciation (no IDs or symbols)."""
    import re
    # Remove requisition codes, brackets, IDs, e.g. (Req-12345), [2024], (m/f/d)
    text = re.sub(r"[\(\[\{][^\)\]\}]*[\)\]\}]", "", text)
    # Remove trailing hashes, IDs or numbers like #1245 or ID: 994
    text = re.sub(r"(?:req|id|job id|ref)[\s:#\-_0-9]+", "", text, flags=re.IGNORECASE)
    # Clean slash locations like "Bangalore / Hyderabad" -> "Bangalore or Hyderabad"
    text = re.sub(r"\s*/\s*", " or ", text)
    # Replace common acronyms for natural speech
    text = re.sub(r"\bSDE\b", "S D E", text, flags=re.IGNORECASE)
    text = re.sub(r"\bSWE\b", "Software Engineer", text, flags=re.IGNORECASE)
    text = re.sub(r"\bQA\b", "Q A", text, flags=re.IGNORECASE)
    text = re.sub(r"\bCTC\b", "C T C", text, flags=re.IGNORECASE)
    # Remove extra punctuation and whitespace
    text = re.sub(r"[^\w\s,\.\-]", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def pick_young_indian_voice(avatar_name: str | None = None) -> str:
    """Pick young 20-25 age Indian voice: ~80% soft female, ~20% energetic male."""
    import random

    female_pool = ["en-IN-NeerjaNeural", "en-IN-KavyaNeural"]
    male_pool = ["en-IN-PrabhatNeural"]

    if avatar_name:
        av_lower = avatar_name.lower()
        if "female" in av_lower or "girl" in av_lower:
            return random.choice(female_pool)
        if "male" in av_lower or "boy" in av_lower:
            return random.choice(male_pool)

    # Weighted rotation: 80% female, 20% male
    return random.choices(
        population=[random.choice(female_pool), random.choice(male_pool)],
        weights=[0.80, 0.20],
        k=1,
    )[0]


def generate_voiceover(reel_info: dict, out_audio_path: str) -> bool:
    """Generate punchy, natural young Indian voiceover with follow/share CTA."""
    import random

    raw_company = reel_info.get("company") or "Top Tech Company"
    raw_role = reel_info.get("role") or reel_info.get("title") or "Software Engineer"
    raw_location = reel_info.get("location") or "India"
    avatar_name = reel_info.get("avatar_name")

    company = clean_spoken_text(raw_company)
    role = clean_spoken_text(raw_role)
    location = clean_spoken_text(raw_location)

    # Simple, punchy, human script asking to follow and share
    script_text = (
        f"Stop scrolling! {company} is hiring for {role}. "
        f"Freshers and 2024 to 2026 batches can apply. Location is {location}. "
        f"Direct apply link is pinned in the first comment! "
        f"Save this reel, share it with your friends, and follow TrendyApaa for daily verified tech jobs."
    )

    voice_name = os.getenv("TTS_VOICE") or pick_young_indian_voice(avatar_name)

    # 1. Try python edge_tts module
    try:
        import asyncio
        import edge_tts

        async def _synth():
            communicate = edge_tts.Communicate(script_text, voice_name, rate="+4%")
            await communicate.save(out_audio_path)

        asyncio.run(_synth())
        if os.path.exists(out_audio_path) and os.path.getsize(out_audio_path) > 1000:
            print(f"  Edge-TTS voiceover ({voice_name}) generated: {out_audio_path}")
            return True
    except Exception as e:
        print(f"  Edge-TTS python fallback: {e}")

    # 2. Try edge-tts CLI
    try:
        cmd = [
            "edge-tts",
            "--voice", voice_name,
            "--rate", "+4%",
            "--text", script_text,
            "--write-media", out_audio_path,
        ]
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
        if res.returncode == 0 and os.path.exists(out_audio_path) and os.path.getsize(out_audio_path) > 1000:
            print(f"  Edge-TTS CLI voiceover ({voice_name}) generated: {out_audio_path}")
            return True
    except Exception:
        pass

    return False



def get_ffmpeg_exe() -> str:
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        return "ffmpeg"


def generate_local_content_cover(reel_info: dict) -> Image.Image:
    """Generate the exact 1080x1920 job cover template with full-bleed lower avatar and rich card layout."""
    CW, CH = 1080, 1920
    reel_id = reel_info.get("reel_id")
    palette = get_theme_palette(reel_id)

    bg_top = palette["bg_top"]
    bg_bot = palette["bg_bot"]
    accent = palette["accent"]
    accent_sub = palette["accent_sub"]

    img = Image.new("RGB", (CW, CH), bg_bot)
    draw = ImageDraw.Draw(img)

    company = (reel_info.get("company") or "Top Tech Company").strip()
    role = (reel_info.get("role") or reel_info.get("title") or "Software Engineer").strip()
    location = (reel_info.get("location") or "India (Hybrid / Remote)").strip()
    exp = (reel_info.get("experience_label") or "0-3 Years Exp").strip()
    package = (reel_info.get("salary_text") or "Competitive CTC").strip()
    badge_text = (reel_info.get("badge_text") or "HIRING NOW").upper()
    avatar_name = reel_info.get("avatar_name") or ""

    # 1. Smooth gradient background
    for y in range(0, CH, 16):
        yr = y / CH
        color = (
            int(bg_top[0] + (bg_bot[0] - bg_top[0]) * yr),
            int(bg_top[1] + (bg_bot[1] - bg_top[1]) * yr),
            int(bg_top[2] + (bg_bot[2] - bg_top[2]) * yr),
        )
        draw.rectangle((0, y, CW, y + 16), fill=color)

    # 2. Glowing ambient mesh
    glow = Image.new("RGBA", (CW, CH), (0, 0, 0, 0))
    gd = ImageDraw.Draw(glow)
    gd.ellipse((CW // 2 - 400, 700, CW // 2 + 400, 1500), fill=palette["glow1"])
    gd.ellipse((CW // 2 - 300, 1200, CW // 2 + 300, 1800), fill=palette["glow2"])
    blurred_glow = glow.filter(ImageFilter.GaussianBlur(64))
    img = Image.alpha_composite(img.convert("RGBA"), blurred_glow).convert("RGB")
    draw = ImageDraw.Draw(img)

    margin_x = 64
    content_w = CW - margin_x * 2

    # 3. Lower Stage: Full Avatar Illustration
    HERO_TOP = 840
    hero_h = CH - HERO_TOP
    avatar_file = resolve_local_avatar_file(avatar_name, reel_id)
    if avatar_file and avatar_file.exists():
        try:
            av_raw = Image.open(avatar_file).convert("RGB")
            # Fit avatar photo into lower hero region
            av_fitted = ImageOps.fit(av_raw, (content_w, hero_h - 40), method=Image.Resampling.LANCZOS, centering=(0.5, 0.25))
            av_masked = Image.new("RGBA", (content_w, hero_h - 40), (0, 0, 0, 0))
            av_masked.paste(av_fitted, (0, 0))
            # Put rounded mask on the avatar
            mask = Image.new("L", (content_w, hero_h - 40), 0)
            ImageDraw.Draw(mask).rounded_rectangle((0, 0, content_w - 1, hero_h - 41), radius=28, fill=255)
            av_masked.putalpha(mask)
            img.paste(av_masked, (margin_x, HERO_TOP), av_masked)
        except Exception:
            pass

    # 4. Top Header: Hiring Now pill badge + Company Name + White Logo Box
    top_y = 70
    badge_fnt = get_font(22, True)
    bw = draw.textbbox((0, 0), badge_text, font=badge_fnt)[2] + 44
    rounded_rect(draw, (margin_x, top_y, margin_x + bw, top_y + 48), 16, accent)
    draw.text((margin_x + 22, top_y + 12), badge_text, font=badge_fnt, fill=bg_top)

    # Top right white logo box with face/logo
    logo_size = 140
    logo_x = CW - margin_x - logo_size
    logo_y = top_y - 6
    rounded_rect(draw, (logo_x, logo_y, logo_x + logo_size, logo_y + logo_size), 26, (255, 255, 255), outline=COLOR_CARD_BORDER, width=2)
    face_logo = resolve_local_face_logo(avatar_name, reel_id)
    face_in_box = face_logo.resize((104, 104), Image.Resampling.LANCZOS)
    img.paste(face_in_box, (logo_x + (logo_size - 104) // 2, logo_y + (logo_size - 104) // 2), face_in_box)

    # Company name
    comp_fnt, comp_lines = get_best_fit_font(draw, company.upper(), content_w - logo_size - 60, 80, start_size=52, min_size=32, bold=True, max_lines=1)
    draw.text((margin_x, top_y + 72), comp_lines[0], font=comp_fnt, fill=COLOR_TEXT_WHITE)
    draw.text((margin_x, top_y + 132), "OFFICIAL CAREERS OPENING", font=get_font(20, True), fill=accent)

    # 5. Role Title
    title_y = top_y + 184
    role_fnt, role_lines = get_best_fit_font(draw, role.upper(), content_w, 160, start_size=46, min_size=28, bold=True, max_lines=3)
    ry = title_y
    for rl in role_lines:
        draw.text((margin_x, ry), rl, font=role_fnt, fill=COLOR_TEXT_WHITE)
        ry += draw.textbbox((0, 0), rl, font=role_fnt)[3] + 10

    # Accent underline
    rounded_rect(draw, (margin_x, ry + 4, margin_x + 180, ry + 12), 4, accent)

    # 6. Specs Rows with Accent Tabs
    specs_y = ry + 32
    specs = [
        ("EXPERIENCE", exp),
        ("LOCATION", location),
        ("PACKAGE", package),
    ]

    for label, val in specs:
        rounded_rect(draw, (margin_x, specs_y, margin_x + content_w, specs_y + 86), 18, palette["card_bg"], outline=COLOR_CARD_BORDER, width=1)
        rounded_rect(draw, (margin_x, specs_y, margin_x + 8, specs_y + 86), 4, accent)
        draw.text((margin_x + 24, specs_y + 14), label.upper(), font=get_font(17, True), fill=COLOR_TEXT_SUB)
        vfnt, vlines = get_best_fit_font(draw, val.upper(), content_w - 48, 44, start_size=24, min_size=18, bold=True, max_lines=1)
        draw.text((margin_x + 24, specs_y + 44), vlines[0], font=vfnt, fill=COLOR_TEXT_WHITE if label != "EXPERIENCE" else accent)
        specs_y += 98

    # 7. Lower Tags above button
    tag_y = CH - 240
    tag_str = "FRESHERS / 0-3 YOE • OFFICIAL APPLY LINK • TOP ROLE"
    rounded_rect(draw, (margin_x + 20, tag_y, CW - margin_x - 20, tag_y + 46), 14, (16, 22, 36, 230), outline=COLOR_CARD_BORDER, width=1)
    tfnt, tlines = get_best_fit_font(draw, tag_str, content_w - 60, 36, start_size=17, min_size=14, bold=True, max_lines=1)
    draw.text(((CW - draw.textbbox((0, 0), tlines[0], font=tfnt)[2]) // 2, tag_y + 12), tlines[0], font=tfnt, fill=COLOR_TEXT_WHITE)

    # 8. Big Cyan Apply CTA Button
    btn_y = CH - 175
    rounded_rect(draw, (margin_x, btn_y, margin_x + content_w, btn_y + 92), 24, accent)
    cta_str = "APPLY VIA LINK IN BIO →"
    btn_fnt = get_font(32, True)
    draw.text(((CW - draw.textbbox((0, 0), cta_str, font=btn_fnt)[2]) // 2, btn_y + 26), cta_str, font=btn_fnt, fill=bg_top)

    # 9. Small Disclaimer
    draw.text((margin_x + 10, CH - 60), "Source: Official Careers Page", font=get_font(15, False), fill=COLOR_TEXT_MUTED)

    return img




def render_multi_scene_video(
    reel_info: dict,
    content_img_or_path: Image.Image | str,
    out_path: str,
) -> None:
    ffmpeg_bin = get_ffmpeg_exe()
    company = reel_info.get("company") or "Top Tech Company"
    role = reel_info.get("role") or reel_info.get("title") or "Software Engineer"
    location = reel_info.get("location") or "India (Hybrid / Remote)"
    exp = reel_info.get("experience_label") or "0-3 Years Exp"
    package = reel_info.get("salary_text") or "Competitive CTC"
    skills = reel_info.get("skills") or ["Full Stack", "Problem Solving", "System Design", "Engineering"]
    badge_text = (reel_info.get("badge_text") or "OFFICIAL HIRING ALERT").upper()
    avatar_name = reel_info.get("avatar_name") or ""

    face_raw = resolve_local_face_logo(avatar_name)
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

    # ── Fast Pre-Render Cache for All 4 Scenes (Reduces rendering time by 10x) ──
    def build_base_scene(scene_name: str) -> Image.Image:
        base = Image.new("RGB", (W, H), COLOR_BG_BASE)
        draw = ImageDraw.Draw(base)

        # Background gradient
        for y in range(0, H, 16):
            yr = y / H
            color = (int(12 + 16 * yr), int(16 + 22 * yr), int(26 + 32 * (1 - yr)))
            draw.rectangle((0, y, W, y + 16), fill=color)

        # Header branding
        base.paste(face_sm, (margin, 70), face_sm)
        draw.text((margin + 112, 78), CHANNEL_DISPLAY_NAME, font=FONT_MED, fill=COLOR_TEXT_WHITE)
        draw.text((margin + 112, 112), CHANNEL_TAGLINE, font=FONT_SMALL, fill=COLOR_TEXT_MUTED)

        # Footer branding
        draw.text((margin + 10, H - 72), CHANNEL_HANDLE, font=FONT_MED, fill=COLOR_TEXT_WHITE)
        draw.text((margin + 10, H - 38), CHANNEL_FOOTER_NOTE, font=FONT_SMALL, fill=COLOR_TEXT_MUTED)

        card_surface = Image.new("RGBA", (inner_w, card_h), COLOR_CARD_BG)
        card_draw = ImageDraw.Draw(card_surface)
        card_draw.rounded_rectangle((0, 0, inner_w, card_h), radius=32, fill=COLOR_CARD_BG, outline=COLOR_CARD_BORDER, width=2)
        base.paste(card_surface, (margin, card_y), card_surface)

        if scene_name == "intro_hook":
            av_x = (W - 252) // 2
            av_y = card_y + 48
            base.paste(face_lg, (av_x, av_y), face_lg)
            draw.ellipse((av_x, av_y, av_x + 252, av_y + 252), outline=COLOR_ACCENT_ORANGE, width=4)

            pill_w = max(240, draw.textbbox((0, 0), badge_text, font=FONT_BADGE)[2] + 48)
            rounded_rect(draw, ((W - pill_w) // 2, card_y + 336, ((W + pill_w) // 2), card_y + 378), 16, COLOR_ACCENT_ORANGE)
            draw.text(((W - draw.textbbox((0, 0), badge_text, font=FONT_BADGE)[2]) // 2, card_y + 346), badge_text, font=FONT_BADGE, fill=COLOR_TEXT_WHITE)

            draw.text(((W - draw.textbbox((0, 0), "Stop scrolling!", font=FONT_TITLE)[2]) // 2, card_y + 410), "Stop scrolling!", font=FONT_TITLE, fill=COLOR_TEXT_WHITE)

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
            draw.text(((W - draw.textbbox((0, 0), "Check 1st pinned comment for direct apply link 👇", font=FONT_BODY)[2]) // 2, card_y + 816), "Check 1st pinned comment for direct apply link 👇", font=FONT_BODY, fill=COLOR_TEXT_WHITE)

        elif scene_name == "role_details":
            draw.text((margin + 32, card_y + 26), "OFFICIAL JOB SPECIFICATIONS", font=FONT_MED, fill=COLOR_ACCENT)

            specs = [
                ("Company", company, COLOR_TEXT_WHITE),
                ("Target Role", role, COLOR_ACCENT),
                ("Eligibility", f"{exp} • 2024/2025/2026 Batch", (76, 217, 100)),
                ("Location", location, COLOR_TEXT_WHITE),
                ("Package", package, (255, 214, 10)),
            ]

            y_pos = card_y + 76
            for label, val, val_col in specs:
                val_fnt, val_lines = get_best_fit_font(draw, val, inner_w - 90, 64, start_size=25, min_size=18, bold=True, max_lines=2)
                row_h = 100 if len(val_lines) <= 1 else 122
                
                rounded_rect(draw, (margin + 24, y_pos, W - margin - 24, y_pos + row_h), 18, (30, 38, 58))
                draw.text((margin + 46, y_pos + 14), label.upper(), font=FONT_SMALL, fill=COLOR_TEXT_SUB)
                
                y_text = y_pos + 44
                for vl in val_lines:
                    draw.text((margin + 46, y_text), vl, font=val_fnt, fill=val_col)
                    y_text += draw.textbbox((0, 0), vl, font=val_fnt)[3] + 6
                    
                y_pos += row_h + 12

            draw.text((margin + 32, y_pos + 4), "KEY SKILLS & TECH STACK:", font=FONT_SMALL, fill=COLOR_TEXT_MUTED)
            skill_str = " • ".join(skills[:5])
            rounded_rect(draw, (margin + 24, y_pos + 30, W - margin - 24, y_pos + 86), 16, (20, 26, 40), outline=COLOR_ACCENT, width=1)
            sk_fnt, sk_lines = get_best_fit_font(draw, skill_str, inner_w - 80, 44, start_size=19, min_size=15, bold=False, max_lines=1)
            draw.text((margin + 40, y_pos + 48), sk_lines[0], font=sk_fnt, fill=COLOR_ACCENT)

            rounded_rect(draw, (margin + 24, card_y + card_h - 96, W - margin - 24, card_y + card_h - 26), 18, (255, 106, 0, 40), outline=COLOR_ACCENT_ORANGE, width=2)
            draw.text(((W - draw.textbbox((0, 0), "💾 TAP SAVE — Apply from laptop tonight!", font=FONT_BODY)[2]) // 2, card_y + card_h - 70), "💾 TAP SAVE — Apply from laptop tonight!", font=FONT_BODY, fill=COLOR_TEXT_WHITE)

        elif scene_name == "cta_action":
            av_x = (W - 172) // 2
            av_y = card_y + 42
            base.paste(face_md, (av_x, av_y), face_md)
            draw.ellipse((av_x, av_y, av_x + 172, av_y + 172), outline=COLOR_ACCENT, width=4)

            draw.text(((W - draw.textbbox((0, 0), "HOW TO APPLY", font=FONT_HERO)[2]) // 2, card_y + 242), "HOW TO APPLY", font=FONT_HERO, fill=COLOR_TEXT_WHITE)

            rounded_rect(draw, (margin + 26, card_y + 332, W - margin - 26, card_y + 472), 22, COLOR_ACCENT_ORANGE)
            draw.text((margin + 52, card_y + 362), "Direct Apply Link Pinned!", font=FONT_TITLE, fill=COLOR_TEXT_WHITE)
            draw.text((margin + 52, card_y + 418), "Check the 1st pinned comment below 👇", font=FONT_BODY, fill=(255, 240, 230))

            rounded_rect(draw, (margin + 26, card_y + 508, W - margin - 26, card_y + 662), 22, (30, 38, 58))
            draw.text((margin + 52, card_y + 538), "Save & Share with a Friend ✈️", font=FONT_MED, fill=COLOR_ACCENT)
            draw.text((margin + 52, card_y + 588), f"Follow {CHANNEL_HANDLE} for daily verified tech drives", font=FONT_BODY, fill=COLOR_TEXT_MUTED)

            rounded_rect(draw, (margin + 48, card_y + 734, W - margin - 48, card_y + 834), 26, (18, 24, 38), outline=COLOR_ACCENT_ORANGE, width=3)
            draw.text(((W - draw.textbbox((0, 0), "TAP SAVE NOW 🚀", font=FONT_CTA)[2]) // 2, card_y + 766), "TAP SAVE NOW 🚀", font=FONT_CTA, fill=COLOR_ACCENT_ORANGE)

        return base

    cached_scenes = {
        "intro_hook": build_base_scene("intro_hook"),
        "content_card": build_base_scene("content_card"),
        "role_details": build_base_scene("role_details"),
        "cta_action": build_base_scene("cta_action"),
    }

    pad = 14
    avail_w = inner_w - pad * 2
    avail_h = card_h - pad * 2
    orig_w, orig_h = content_img.size
    base_scale = min(avail_w / orig_w, avail_h / orig_h)

    def draw_frame(frame: int) -> Image.Image:
        scene_name = get_scene(frame)
        base = cached_scenes[scene_name].copy()
        draw = ImageDraw.Draw(base)

        # Dynamic scene 2 Ken Burns Zoom
        if scene_name == "content_card":
            scene_progress = max(0.0, min(1.0, (frame - FPS * 4.0) / (FPS * 5.0)))
            scale = base_scale * (1.0 + 0.04 * scene_progress)
            new_w = int(orig_w * scale)
            new_h = int(orig_h * scale)

            scaled_cover = content_img.resize((new_w, new_h), Image.Resampling.BILINEAR)
            offset_x = margin + pad + (avail_w - new_w) // 2
            offset_y = card_y + pad + (avail_h - new_h) // 2

            crop_x1 = max(0, margin + pad - offset_x)
            crop_y1 = max(0, card_y + pad - offset_y)
            crop_x2 = min(new_w, margin + pad + avail_w - offset_x)
            crop_y2 = min(new_h, card_y + pad + avail_h - offset_y)
            
            if crop_x2 > crop_x1 and crop_y2 > crop_y1:
                cropped = scaled_cover.crop((crop_x1, crop_y1, crop_x2, crop_y2))
                base.paste(cropped, (max(offset_x, margin + pad), max(offset_y, card_y + pad)))

            draw.rounded_rectangle((margin + pad, card_y + pad, margin + pad + avail_w, card_y + pad + avail_h), radius=22, outline=(255, 255, 255, 50), width=1)

        # Progress bar
        progress = int((frame + 1) / TOTAL_FRAMES * inner_w)
        rounded_rect(draw, (margin, 46, W - margin, 54), 4, (40, 48, 64))
        rounded_rect(draw, (margin, 46, margin + progress, 54), 4, COLOR_ACCENT)

        return base

    with tempfile.TemporaryDirectory(prefix="runner_multi_reel_") as tmp:
        frame_dir = Path(tmp)
        for i in range(TOTAL_FRAMES):
            f_img = draw_frame(i)
            f_img.save(frame_dir / f"frame_{i:04d}.png", "PNG")

        voice_audio = frame_dir / "voiceover.mp3"
        has_voice = generate_voiceover(reel_info, str(voice_audio))

        tmp_out = frame_dir / "out.mp4"
        if has_voice:
            cmd = [
                ffmpeg_bin,
                "-y",
                "-framerate", str(FPS),
                "-i", str(frame_dir / "frame_%04d.png"),
                "-i", str(voice_audio),
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-af", f"apad,atrim=0:{REEL_SECONDS}",
                "-c:a", "aac",
                "-b:a", "128k",
                "-t", str(REEL_SECONDS),
                "-r", str(FPS),
                "-movflags", "+faststart",
                str(tmp_out),
            ]
        else:
            cmd = [
                ffmpeg_bin,
                "-y",
                "-framerate", str(FPS),
                "-i", str(frame_dir / "frame_%04d.png"),
                "-f", "lavfi",
                "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
                "-c:v", "libx264",
                "-preset", "veryfast",
                "-crf", "22",
                "-pix_fmt", "yuv420p",
                "-t", str(REEL_SECONDS),
                "-r", str(FPS),
                "-movflags", "+faststart",
                str(tmp_out),
            ]
        subprocess.run(cmd, check=True, capture_output=True, text=True)
        shutil.copyfile(tmp_out, out_path)





def publish_via_backend(reel_id: int, video_url: str, cover_url: str = "") -> tuple[str, str, str]:
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
    audio_label = data.get("audio") or ""
    audio_id_used = data.get("audio_id") or ""
    if audio_id_used:
        print(f"  audio attached: {audio_label or audio_id_used} (id={audio_id_used})")
    else:
        print("  no audio attached (none available / not applicable)")
    print(f"  container created: {container_id}")

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
        if attempt < POLL_MAX - 1:
            time.sleep(POLL_INTERVAL)
    else:
        return "IN_PROGRESS", "", "Container did not finish within polling window."

    st, data = backend_post("/api/cron/ig/publish", {"reel_id": reel_id, "container_id": container_id})
    if st != 200:
        return "ERROR", "", f"publish HTTP {st}: {data}"
    return data.get("status", "ERROR"), data.get("media_id", ""), data.get("message", "")


def main() -> int:
    st, build = backend_post("/api/cron/auto-post", {"force": True})
    if st == 200:
        print(f"auto-post: {build.get('status')} reel_id={build.get('reel_id')}")
    else:
        print(f"auto-post HTTP {st}: {build}")

    status, data = backend_post("/api/cron/next-reel", {"force": True})
    if status != 200:
        print(f"next-reel failed: {status} {data}")
        return 1
    reel = data.get("reel")
    if not reel:
        print("No reel to publish. Done.")
        return 0

    reel_id = reel["reel_id"]
    print(f"Next reel: #{reel_id} — {reel.get('title')!r}")

    # 100% local generation on worker with rotating face logos and palettes!
    print(f"Generating unique 1080x1920 job content card locally on worker for reel #{reel_id}...")
    content_img = generate_local_content_cover(reel)
    cover_file = f"cover-{reel_id}.jpg"
    content_img.save(cover_file, "JPEG", quality=95)

    out = f"reel-{reel_id}.mp4"
    print("Rendering upgraded 20-second multi-scene dynamic reel with local face logo...")
    render_multi_scene_video(reel, content_img, out)
    size = os.path.getsize(out)
    print(f"Rendered {out} ({size} bytes)")

    release_id = ensure_release()
    ts = int(time.time())
    asset_name = f"reel-{reel_id}-{ts}.mp4"
    video_url = upload_asset(release_id, out, asset_name, content_type="video/mp4")
    print(f"Uploaded video: {video_url}")

    cover_asset_name = f"cover-{reel_id}-{ts}.jpg"
    cover_public_url = upload_asset(release_id, cover_file, cover_asset_name, content_type="image/jpeg")
    print(f"Uploaded unique cover: {cover_public_url}")

    prune_assets(release_id, KEEP_ASSETS)

    print("Publishing to Instagram via backend endpoints...")
    ig_status, media_id, msg = publish_via_backend(reel_id, video_url, cover_url=cover_public_url)
    print(f"IG result: status={ig_status} media_id={media_id} msg={msg}")


    _st, mark = backend_post("/api/cron/mark-published", {
        "reel_id": reel_id, "video_url": video_url,
        "media_id": media_id, "status": ig_status, "error": msg,
    })
    if isinstance(mark, dict) and mark.get("media_audio_type"):
        print(f"  media_audio_type: {mark.get('media_audio_type')}")

    if ig_status in ("PUBLISHED", "DRY_RUN", "CONTAINER_READY"):
        print("Done — multi-scene reel published.")
        return 0
    print(f"Publish did not succeed: {msg}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
