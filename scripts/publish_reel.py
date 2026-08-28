#!/usr/bin/env python3
"""
InstaPilot Reel Worker (GitHub Actions).

Upgraded Multi-Scene Engine:
1. Asks backend for next reel with job details.
2. Downloads content card & avatar face logo.
3. Renders 20-second dynamic multi-scene video (Hook -> Content Card -> Spaced Details -> CTA).
4. Uploads to GitHub Releases and publishes live to Instagram via Graph API.
"""
from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import urllib.error
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageFilter

BACKEND_URL = os.environ["BACKEND_URL"].rstrip("/")
CRON_SECRET = os.environ["CRON_SECRET"]
GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
GITHUB_REPO = os.environ["GITHUB_REPOSITORY"]  # owner/repo
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


def upload_asset(release_id: int, file_path: str, asset_name: str) -> str:
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


def resolve_local_face_logo(avatar_name: str) -> Image.Image:
    logos_dir = Path("face_logos")
    if logos_dir.exists():
        stem = Path(avatar_name).stem.replace("avatar_", "").replace("face_", "")
        for f in logos_dir.glob("*.png"):
            if stem and stem in f.stem:
                try:
                    return Image.open(f).convert("RGBA")
                except Exception:
                    pass
        first = sorted(logos_dir.glob("*.png"))
        if first:
            try:
                return Image.open(first[0]).convert("RGBA")
            except Exception:
                pass
    circle = Image.new("RGBA", (200, 200), (0, 0, 0, 0))
    d = ImageDraw.Draw(circle)
    d.ellipse((0, 0, 200, 200), fill=(0, 217, 255, 200))
    return circle


def render_multi_scene_video(
    reel_info: dict,
    content_img_path: str,
    out_path: str,
) -> None:
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

    content_img = Image.open(content_img_path).convert("RGB")

    def draw_frame(frame: int) -> Image.Image:
        img = create_dynamic_background(frame)
        draw = ImageDraw.Draw(img)
        scene_name = get_scene(frame)

        margin = 38
        inner_w = W - margin * 2

        progress = int((frame + 1) / TOTAL_FRAMES * inner_w)
        rounded_rect(draw, (margin, 46, W - margin, 54), 4, (40, 48, 64))
        rounded_rect(draw, (margin, 46, margin + progress, 54), 4, COLOR_ACCENT)

        img.paste(face_sm, (margin, 70), face_sm)
        draw.text((margin + 112, 78), CHANNEL_DISPLAY_NAME, font=FONT_MED, fill=COLOR_TEXT_WHITE)
        draw.text((margin + 112, 112), CHANNEL_TAGLINE, font=FONT_SMALL, fill=COLOR_TEXT_MUTED)

        card_y = 182
        card_h = 974

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
            draw.text(((W - draw.textbbox((0, 0), "Check pinned comment for direct apply link", font=FONT_BODY)[2]) // 2, card_y + 816), "Check pinned comment for direct apply link", font=FONT_BODY, fill=COLOR_TEXT_WHITE)

        elif scene_name == "content_card":
            card_surface = Image.new("RGBA", (inner_w, card_h), (20, 25, 38, 245))
            card_draw = ImageDraw.Draw(card_surface)
            card_draw.rounded_rectangle((0, 0, inner_w, card_h), radius=32, fill=(20, 25, 38, 245), outline=COLOR_CARD_BORDER, width=2)
            img.paste(card_surface, (margin, card_y), card_surface)

            pad = 14
            avail_w = inner_w - pad * 2
            avail_h = card_h - pad * 2
            orig_w, orig_h = content_img.size
            scale = min(avail_w / orig_w, avail_h / orig_h)
            new_w = int(orig_w * scale)
            new_h = int(orig_h * scale)

            scaled_cover = content_img.resize((new_w, new_h), Image.Resampling.LANCZOS)
            offset_x = margin + pad + (avail_w - new_w) // 2
            offset_y = card_y + pad + (avail_h - new_h) // 2

            img.paste(scaled_cover, (offset_x, offset_y))
            draw.rounded_rectangle((offset_x, offset_y, offset_x + new_w, offset_y + new_h), radius=22, outline=(255, 255, 255, 50), width=1)

        elif scene_name == "role_details":
            card_surface = Image.new("RGBA", (inner_w, card_h), COLOR_CARD_BG)
            card_draw = ImageDraw.Draw(card_surface)
            card_draw.rounded_rectangle((0, 0, inner_w, card_h), radius=32, fill=COLOR_CARD_BG, outline=COLOR_CARD_BORDER, width=2)
            img.paste(card_surface, (margin, card_y), card_surface)

            draw.text((margin + 32, card_y + 30), "JOB HIGHLIGHTS & DETAILS", font=FONT_MED, fill=COLOR_ACCENT)

            specs = [
                ("Company", company, COLOR_TEXT_WHITE),
                ("Target Role", role, COLOR_ACCENT),
                ("Experience", exp, (76, 217, 100)),
                ("Location", location, COLOR_TEXT_WHITE),
                ("Package", package, (255, 214, 10)),
            ]

            y_pos = card_y + 84
            for label, val, val_col in specs:
                val_fnt, val_lines = get_best_fit_font(draw, val, inner_w - 90, 64, start_size=26, min_size=18, bold=True, max_lines=2)
                row_h = 106 if len(val_lines) <= 1 else 130
                
                rounded_rect(draw, (margin + 24, y_pos, W - margin - 24, y_pos + row_h), 18, (30, 38, 58))
                draw.text((margin + 46, y_pos + 16), label.upper(), font=FONT_SMALL, fill=COLOR_TEXT_SUB)
                
                y_text = y_pos + 48
                for vl in val_lines:
                    draw.text((margin + 46, y_text), vl, font=val_fnt, fill=val_col)
                    y_text += draw.textbbox((0, 0), vl, font=val_fnt)[3] + 6
                    
                y_pos += row_h + 16

            draw.text((margin + 32, y_pos + 6), "KEY SKILLS & STACK:", font=FONT_SMALL, fill=COLOR_TEXT_MUTED)
            skill_str = " • ".join(skills)
            rounded_rect(draw, (margin + 24, y_pos + 36, W - margin - 24, y_pos + 102), 16, (20, 26, 40), outline=COLOR_ACCENT, width=1)
            sk_fnt, sk_lines = get_best_fit_font(draw, skill_str, inner_w - 80, 48, start_size=20, min_size=16, bold=False, max_lines=1)
            draw.text((margin + 40, y_pos + 56), sk_lines[0], font=sk_fnt, fill=COLOR_ACCENT)

        else:
            card_surface = Image.new("RGBA", (inner_w, card_h), COLOR_CARD_BG)
            card_draw = ImageDraw.Draw(card_surface)
            card_draw.rounded_rectangle((0, 0, inner_w, card_h), radius=32, fill=COLOR_CARD_BG, outline=COLOR_CARD_BORDER, width=2)
            img.paste(card_surface, (margin, card_y), card_surface)

            av_x = (W - 172) // 2
            av_y = card_y + 48
            img.paste(face_md, (av_x, av_y), face_md)
            draw.ellipse((av_x, av_y, av_x + 172, av_y + 172), outline=COLOR_ACCENT, width=4)

            draw.text(((W - draw.textbbox((0, 0), "HOW TO APPLY", font=FONT_HERO)[2]) // 2, card_y + 254), "HOW TO APPLY", font=FONT_HERO, fill=COLOR_TEXT_WHITE)

            rounded_rect(draw, (margin + 26, card_y + 348, W - margin - 26, card_y + 494), 22, COLOR_ACCENT_ORANGE)
            draw.text((margin + 52, card_y + 380), "Direct Apply Link Pinned!", font=FONT_TITLE, fill=COLOR_TEXT_WHITE)
            draw.text((margin + 52, card_y + 436), "Check the 1st pinned comment below", font=FONT_BODY, fill=(255, 240, 230))

            rounded_rect(draw, (margin + 26, card_y + 534, W - margin - 26, card_y + 694), 22, (30, 38, 58))
            draw.text((margin + 52, card_y + 566), "Save & Share with a Friend", font=FONT_MED, fill=COLOR_ACCENT)
            draw.text((margin + 52, card_y + 616), f"Follow {CHANNEL_HANDLE} for daily verified jobs", font=FONT_BODY, fill=COLOR_TEXT_MUTED)

            rounded_rect(draw, (margin + 48, card_y + 760, W - margin - 48, card_y + 854), 26, (18, 24, 38), outline=COLOR_ACCENT_ORANGE, width=3)
            draw.text(((W - draw.textbbox((0, 0), "TAP SAVE NOW", font=FONT_CTA)[2]) // 2, card_y + 790), "TAP SAVE NOW", font=FONT_CTA, fill=COLOR_ACCENT_ORANGE)

        draw.text((margin + 10, H - 72), CHANNEL_HANDLE, font=FONT_MED, fill=COLOR_TEXT_WHITE)
        draw.text((margin + 10, H - 38), CHANNEL_FOOTER_NOTE, font=FONT_SMALL, fill=COLOR_TEXT_MUTED)

        return img

    with tempfile.TemporaryDirectory(prefix="runner_multi_reel_") as tmp:
        frame_dir = Path(tmp)
        for i in range(TOTAL_FRAMES):
            f_img = draw_frame(i)
            f_img.save(frame_dir / f"frame_{i:04d}.png", "PNG")

        tmp_out = frame_dir / "out.mp4"
        cmd = [
            "ffmpeg",
            "-y",
            "-framerate",
            str(FPS),
            "-i",
            str(frame_dir / "frame_%04d.png"),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=48000",
            "-shortest",
            "-c:v",
            "libx264",
            "-preset",
            "fast",
            "-crf",
            "22",
            "-pix_fmt",
            "yuv420p",
            "-r",
            str(FPS),
            "-movflags",
            "+faststart",
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
    st, build = backend_post("/api/cron/auto-post", {})
    if st == 200:
        print(f"auto-post: {build.get('status')} reel_id={build.get('reel_id')}")
    else:
        print(f"auto-post HTTP {st}: {build}")

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

    content_url = reel.get("content_cover_url")
    avatar_url = reel.get("avatar_cover_url") or reel.get("cover_url")
    if not content_url or not download(content_url, "content.jpg"):
        print("Content frame download failed — cannot render.")
        return 1
    have_cover = bool(avatar_url) and download(avatar_url, "avatar.jpg")
    print(f"Downloaded (content=yes, avatar_cover={'yes' if have_cover else 'no'}).")

    out = f"reel-{reel_id}.mp4"
    print("Rendering upgraded 20-second multi-scene dynamic reel...")
    render_multi_scene_video(reel, "content.jpg", out)
    size = os.path.getsize(out)
    print(f"Rendered {out} ({size} bytes)")

    release_id = ensure_release()
    asset_name = f"reel-{reel_id}-{int(time.time())}.mp4"
    video_url = upload_asset(release_id, out, asset_name)
    print(f"Uploaded: {video_url}")
    cover_public_url = avatar_url if have_cover else ""

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
