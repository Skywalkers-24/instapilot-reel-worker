"""Local, multithreaded headless scraper for Instagram audio reel-usage counts.

Goal: for each Instagram audio_id, visit its public audio page
(https://www.instagram.com/reels/audio/<id>/), capture how many reels use that
sound, and record an honest per-link STATUS. NO session id is used — this is a
best-effort anonymous scrape, so Instagram may serve a login wall for some/all
links. That case is reported as status="login_wall" rather than faked.

How the count is obtained (in priority order):
  1. The page's own XHR `POST /api/v1/clips/music/` JSON — the most reliable
     source. We read any of the known count fields in `metadata.music_info`.
  2. A regex sweep of the rendered HTML / embedded JSON for a
     "<N> reels|posts" style count as a fallback.

Concurrency: Playwright's sync API is NOT thread-safe across a shared instance,
so each worker THREAD launches its OWN Chromium (one browser per worker). We cap
the pool small (default 4) to stay light and avoid IG rate-blocks.

Output: results are written INCREMENTALLY as each audio finishes:
  - scripts/audio_reel_counts.jsonl   (one JSON line per audio, appended live)
  - scripts/audio_reel_counts.json    (final, sorted-by-count summary)

Usage:
  python scripts/scrape_audio_reel_counts.py                 # uses built-in list
  python scripts/scrape_audio_reel_counts.py --input ids.json --workers 6
  python scripts/scrape_audio_reel_counts.py --headful       # watch the browser
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("scrape_audio_counts")

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_HERE = Path(__file__).resolve().parent
JSONL_PATH = _HERE / "audio_reel_counts.jsonl"
SUMMARY_PATH = _HERE / "audio_reel_counts.json"

# Serializes appends to the JSONL file from multiple worker threads.
_write_lock = threading.Lock()

# Count fields seen in metadata.music_info across IG API variants. First hit wins.
_COUNT_FIELDS = (
    "clips_count",
    "reels_media_count",
    "formatted_clips_media_count",
    "media_count",
    "reel_count",
    "usage_count",
)

# Fallback: match "12.3K reels", "4,201 posts", "1.2M Reels" in raw HTML/JSON.
_HTML_COUNT_RE = re.compile(
    r"([\d][\d.,]*\s*[KMB]?)\s*(?:reels?|posts?|clips?)\b", re.IGNORECASE
)

# The built-in list from the user's message (the 9 "evergreen" original sounds).
_DEFAULT_ITEMS = [
    {"audio_id": "150937794278098", "title": "We are infinite - evergreen mix"},
    {"audio_id": "154946997661388", "title": "Into the wild x Evergreen"},
    {"audio_id": "166904146146892", "title": "robin williams + evergreen"},
    {"audio_id": "249938584292232", "title": "evergreen soni"},
    {"audio_id": "256386069697969", "title": "Evergreen mashup 3"},
    {"audio_id": "264804580032145", "title": "Evergreen Mashup 2024"},
    {"audio_id": "275192592046045", "title": "Evergreen"},
    {"audio_id": "277483254961503", "title": "laurentferrier.ch/evergreen"},
    {"audio_id": "285429911179193", "title": "Bollywood Evergreen"},
]


def _humanize_to_int(raw: str) -> int | None:
    """Turn '12.3K' / '4,201' / '1.2M' into an int. None if unparseable."""
    if raw is None:
        return None
    s = str(raw).strip().replace(",", "")
    if not s:
        return None
    mult = 1
    if s[-1] in "kKmMbB":
        mult = {"k": 1_000, "m": 1_000_000, "b": 1_000_000_000}[s[-1].lower()]
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return None


def _count_from_music_info(music_info: dict) -> tuple[int | None, str]:
    """Return (count, field_name) from a music_info dict, or (None, '')."""
    if not isinstance(music_info, dict):
        return None, ""
    # Some payloads nest the count one level down in music_asset_info too.
    candidates = [music_info, music_info.get("music_asset_info") or {}]
    for node in candidates:
        if not isinstance(node, dict):
            continue
        for field in _COUNT_FIELDS:
            if field in node and node[field] is not None:
                val = node[field]
                if isinstance(val, (int, float)):
                    return int(val), field
                parsed = _humanize_to_int(str(val))
                if parsed is not None:
                    return parsed, field
    return None, ""


def _parse_music_json(body: str) -> dict:
    """Parse a clips/music XHR body into {count, count_field, title, artist}."""
    if body.startswith("for (;;);"):
        body = body[len("for (;;);"):]
    try:
        data = json.loads(body)
    except Exception:
        return {}
    node = data.get("payload") if isinstance(data, dict) else None
    if not isinstance(node, dict):
        node = data if isinstance(data, dict) else {}
    metadata = node.get("metadata") or {}
    music_info = metadata.get("music_info") or {}
    asset = music_info.get("music_asset_info") or {}
    count, field = _count_from_music_info(music_info)
    out: dict = {}
    if asset.get("title"):
        out["scraped_title"] = (asset.get("title") or "").strip()
        out["scraped_artist"] = (asset.get("display_artist") or "").strip()
    if count is not None:
        out["reel_count"] = count
        out["count_field"] = field
    return out


# In-page JS: locate the reel/view count that renders next to the
# "View count icon" SVG and return its raw text (e.g. "183k"). Falls back to
# scanning for any "<num>[kmb] ... reels/posts" text node if the icon moves.
_COUNT_DOM_JS = r"""
() => {
  const numRe = /^[\d][\d.,]*\s*[KMB]?$/i;
  // 1. Anchor on the "View count icon" SVG, then read numeric text nearby.
  const icons = Array.from(document.querySelectorAll('svg[aria-label="View count icon"], svg title'));
  for (const node of icons) {
    let el = node.closest('div') || node.parentElement;
    // Walk up a few levels; the count span is a sibling within the container.
    for (let up = 0; el && up < 4; up++, el = el.parentElement) {
      const spans = el.querySelectorAll('span');
      for (const s of spans) {
        const t = (s.textContent || '').trim();
        if (numRe.test(t)) return t;
      }
    }
  }
  // 2. Fallback: any element whose text is like "12.3k reels" / "4,201 posts".
  const all = document.querySelectorAll('span, div');
  for (const s of all) {
    const t = (s.textContent || '').trim();
    const m = t.match(/^([\d][\d.,]*\s*[KMB]?)\s*(reels?|posts?|clips?)$/i);
    if (m) return m[1];
  }
  return '';
}
"""

# og:title carries "<artist> | Original audio on Instagram" or the song name.
_OG_TITLE_RE = re.compile(r'<meta property="og:title" content="([^"]+)"', re.IGNORECASE)


def _title_artist_from_html(html: str) -> tuple[str, str]:
    """Pull (title/artist) from the audio page's og:title. Anonymous-safe."""
    if not html:
        return "", ""
    m = _OG_TITLE_RE.search(html)
    if not m:
        return "", ""
    raw = m.group(1).replace(" on Instagram", "").strip()
    # Forms seen: "artist | Original audio", "Song • Artist", "Song by Artist".
    for sep in ("|", "•"):
        if sep in raw:
            a, b = raw.split(sep, 1)
            return b.strip(), a.strip()  # (title-ish, artist-ish); page is loose
    return raw, ""


def _count_from_html(html: str) -> tuple[int | None, str]:
    """Best-effort: pull a reel/post count out of rendered HTML or inline JSON."""
    if not html:
        return None, ""
    # Prefer an explicit JSON-ish "clips_count": 1234 first.
    for field in _COUNT_FIELDS:
        m = re.search(rf'"{field}"\s*:\s*"?([\d.,KMB]+)"?', html, re.IGNORECASE)
        if m:
            parsed = _humanize_to_int(m.group(1))
            if parsed is not None:
                return parsed, f"html:{field}"
    m = _HTML_COUNT_RE.search(html)
    if m:
        parsed = _humanize_to_int(m.group(1))
        if parsed is not None:
            return parsed, "html:regex"
    return None, ""


def _classify_status(html: str, got_count: bool) -> str:
    """Decide a human-readable status for the link.

    NOTE: a logged-out audio page STILL renders (og:title, PolarisClipsAudioRoute
    payload) — it just doesn't include the reel COUNT, which is fetched by a
    client-side XHR that needs JS+session. So the real logged-out outcome is
    "page loaded but no count", NOT a login wall. We only call it login_wall when
    the page is genuinely the login screen (no audio route + a login form).
    """
    low = (html or "").lower()
    if got_count:
        return "count_found"
    if not html:
        return "no_response"
    # The real audio page always carries these markers even when logged out.
    page_loaded = ("polarisclipsaudioroute" in low
                   or "original audio on instagram" in low
                   or "reels/audio/" in low
                   or 'og:title' in low)
    if page_loaded:
        return "page_no_count"  # loaded fine, but count needs a session
    if "page not found" in low or "sorry, this page" in low:
        return "not_found"
    if "loginform" in low or ("login" in low and "accounts/login" in low):
        return "login_wall"
    return "no_count_in_page"


def scrape_one(item: dict, *, headless: bool, per_page_timeout_ms: int,
               poll_ms: int, session_id: str = "") -> dict:
    """Scrape a single audio id in its OWN Chromium instance (thread-safe).

    If `session_id` is given, it's injected as the Instagram `sessionid` cookie
    so the audio page loads (logged-out visitors get a login wall and no count).
    """
    from playwright.sync_api import sync_playwright

    aid = str(item.get("audio_id") or "").strip()
    title = item.get("title") or ""
    url = f"https://www.instagram.com/reels/audio/{aid}/"
    started = time.time()

    result: dict = {
        "audio_id": aid,
        "title": title,
        "url": url,
        "reel_count": None,
        "raw_count_text": "",
        "count_field": "",
        "status": "error",
        "http_status": None,
        "scraped_title": "",
        "scraped_artist": "",
        "elapsed_s": 0.0,
        "error": "",
    }

    capture: dict = {"info": {}}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        try:
            ctx = browser.new_context(user_agent=_UA, viewport={"width": 1280, "height": 900},
                                      locale="en-US")
            if session_id:
                ctx.add_cookies([{
                    "name": "sessionid", "value": session_id,
                    "domain": ".instagram.com", "path": "/",
                    "httpOnly": True, "secure": True,
                }])
            # Block heavy assets; keep documents + XHR/fetch (that's where the count is).
            ctx.route(
                "**/*",
                lambda route: route.abort()
                if route.request.resource_type in ("image", "media", "font")
                else route.continue_(),
            )
            page = ctx.new_page()

            def on_response(resp):
                try:
                    if "/api/v1/clips/music/" in resp.url and resp.request.method == "POST":
                        parsed = _parse_music_json(resp.text())
                        if parsed:
                            capture["info"].update(parsed)
                except Exception:
                    pass

            page.on("response", on_response)

            try:
                nav = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                result["http_status"] = getattr(nav, "status", None)

                # The reel/view count renders into the DOM as the text span next
                # to the "View count icon" SVG (e.g. "183k"). It appears AFTER JS
                # runs, so poll for that element rather than reading static HTML.
                dom_count_raw = ""
                waited = 0
                while waited < per_page_timeout_ms:
                    # 1. Prefer the count XHR if it arrived.
                    if capture["info"].get("reel_count") is not None:
                        break
                    # 2. Look for the on-page view-count element.
                    try:
                        dom_count_raw = page.evaluate(_COUNT_DOM_JS) or ""
                    except Exception:
                        dom_count_raw = ""
                    if dom_count_raw:
                        break
                    page.wait_for_timeout(poll_ms)
                    waited += poll_ms

                html = ""
                try:
                    html = page.content()
                except Exception:
                    html = ""

                info = capture["info"]
                if info.get("reel_count") is not None:
                    result["reel_count"] = info["reel_count"]
                    result["count_field"] = info.get("count_field", "")
                elif dom_count_raw:
                    parsed = _humanize_to_int(dom_count_raw)
                    if parsed is not None:
                        result["reel_count"] = parsed
                        result["count_field"] = "dom:view_count"
                        result["raw_count_text"] = dom_count_raw
                else:
                    # Last-ditch fallback: sweep the static HTML.
                    c, field = _count_from_html(html)
                    if c is not None:
                        result["reel_count"] = c
                        result["count_field"] = field
                # Title/artist: XHR value first, else parse og:title (anon-safe).
                result["scraped_title"] = info.get("scraped_title", "")
                result["scraped_artist"] = info.get("scraped_artist", "")
                if not result["scraped_title"]:
                    t, a = _title_artist_from_html(html)
                    result["scraped_title"] = t
                    result["scraped_artist"] = a
                result["status"] = _classify_status(html, result["reel_count"] is not None)
            except Exception as exc:
                result["error"] = str(exc)[:300]
                result["status"] = "error"
        finally:
            browser.close()

    result["elapsed_s"] = round(time.time() - started, 1)
    return result


def scrape_counts(
    items: list[dict],
    *,
    session_id: str = "",
    workers: int = 4,
    headless: bool = True,
    per_page_timeout_ms: int = 15000,
    poll_ms: int = 500,
) -> list[dict]:
    """Reusable batch API: scrape reel-usage counts for many audios in parallel.

    `items` is a list of {"audio_id": str, "title": str}. Fans out one Chromium
    per worker thread (Playwright sync is not thread-safe across a shared
    instance, so each `scrape_one` owns its own browser). Returns the list of
    per-audio result dicts (same shape as `scrape_one`), unsorted. This is the
    importable entry point the backend uses at publish time — the CLI `main()`
    is just a thin wrapper around it.
    """
    if not items:
        return []
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        futures = {
            pool.submit(
                scrape_one, it, headless=headless,
                per_page_timeout_ms=per_page_timeout_ms, poll_ms=poll_ms,
                session_id=session_id,
            ): it
            for it in items
        }
        for fut in as_completed(futures):
            it = futures[fut]
            try:
                results.append(fut.result())
            except Exception as exc:
                results.append({
                    "audio_id": it.get("audio_id", ""), "title": it.get("title", ""),
                    "reel_count": None, "status": "error", "error": str(exc)[:300],
                })
    return results


def _append_jsonl(result: dict) -> None:
    line = json.dumps(result, ensure_ascii=False)
    with _write_lock:
        with JSONL_PATH.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()


def load_items(input_path: str | None) -> list[dict]:
    if not input_path:
        return list(_DEFAULT_ITEMS)
    p = Path(input_path)
    data = json.loads(p.read_text(encoding="utf-8"))
    # Accept either a bare list or the {"audio": [...]} shape.
    items = data.get("audio", data) if isinstance(data, dict) else data
    out = []
    for it in items:
        if isinstance(it, dict) and it.get("audio_id"):
            out.append({"audio_id": str(it["audio_id"]), "title": it.get("title", "")})
        elif isinstance(it, str):
            out.append({"audio_id": it, "title": ""})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default="", help="JSON file of ids ({'audio':[...]} or a list). Default: built-in evergreen list")
    ap.add_argument("--workers", type=int, default=4, help="parallel Chromium workers (default 4)")
    ap.add_argument("--headful", action="store_true", help="show the browser (debug)")
    ap.add_argument("--page-timeout-ms", type=int, default=12000, help="max wait per page for the count XHR")
    ap.add_argument("--poll-ms", type=int, default=500)
    ap.add_argument("--session", default="", help="Instagram sessionid cookie. If omitted, tries IG_SESSIONID env then the DB setting.")
    args = ap.parse_args()

    items = load_items(args.input or None)
    if not items:
        print("No audio ids to scrape.")
        return

    # Resolve a sessionid: explicit flag > env > DB setting. Anonymous (empty)
    # is allowed but Instagram will login-wall the pages.
    session_id = (args.session or "").strip()
    if not session_id:
        try:
            from app.services.instagram_audio_scraper import get_session_id
            try:
                from app.database import SessionLocal
                _db = SessionLocal()
                try:
                    session_id = get_session_id(_db)
                finally:
                    _db.close()
            except Exception:
                session_id = get_session_id(None)
        except Exception:
            session_id = ""
    print("Session cookie:", "provided (logged-in scrape)" if session_id else "NONE (anonymous - pages will likely be login-walled)")

    # Fresh run: truncate the live JSONL so it only holds this run's results.
    JSONL_PATH.write_text("", encoding="utf-8")

    print(f"Scraping {len(items)} audio id(s) with {args.workers} worker(s), no session id (anonymous).")
    print(f"Live results -> {JSONL_PATH}")

    headless = not args.headful
    results = scrape_counts(
        items, session_id=session_id, workers=args.workers, headless=headless,
        per_page_timeout_ms=args.page_timeout_ms, poll_ms=args.poll_ms,
    )
    for res in results:
        _append_jsonl(res)
        cnt = res.get("reel_count")
        print(f"  [{str(res.get('status')):>16}] {str(res.get('audio_id')):>18}  "
              f"count={cnt if cnt is not None else '-':>10}  "
              f"{(res.get('title') or res.get('scraped_title') or '')[:40]}")

    # Final summary: sort by count desc (Nones last), write pretty JSON.
    def sort_key(r):
        c = r.get("reel_count")
        return (0 if c is None else 1, c or 0)

    results.sort(key=sort_key, reverse=True)
    SUMMARY_PATH.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")

    found = [r for r in results if r.get("reel_count") is not None]
    print(f"\nDone. {len(found)}/{len(results)} links returned a usable count.")
    print(f"Summary (sorted by reel count) -> {SUMMARY_PATH}")
    if found:
        print("\nMost-used audio:")
        for r in found[:10]:
            print(f"  {r['reel_count']:>10}  {r['audio_id']}  {(r.get('title') or r.get('scraped_title') or '')[:45]}")
    walled = sum(1 for r in results if r.get("status") == "login_wall")
    if walled:
        print(f"\nNote: {walled} link(s) were login-walled (anonymous scrape). "
              f"Re-run with a session cookie for those if you need their counts.")


if __name__ == "__main__":
    main()
