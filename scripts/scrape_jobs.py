#!/usr/bin/env python3
"""
Job scraper — runs ON THE GITHUB RUNNER (heavy work, no time limit).

Unlike the old version (which asked the backend to scrape, hitting Vercel's
~10s function limit), this does ALL the ATS scraping here on the runner and
only sends clean rows to the backend to persist. The backend never makes slow
outbound scraping calls.

Flow:
  1. GET  {BACKEND_URL}/api/cron/companies?top_n=N   -> ranked companies + ATS URLs
  2. For each company: scrape its ATS on the runner (providers.py) and filter
     to India + fresher-target engineering roles (filters.py)
  3. POST {BACKEND_URL}/api/cron/ingest-jobs in batches -> backend scores,
     dedups, writes, and (on the final batch) prunes to the 500-job cap

No DB credentials ever touch the runner — only CRON_SECRET + BACKEND_URL.

Env:
  BACKEND_URL         base URL of the backend (required)
  CRON_SECRET         shared secret (matches backend CRON_SECRET)
  TOP_N               company universe size (default 200)
  MAX_JOBS_PER_COMPANY per-company cap sent to backend (default 3)
  INGEST_BATCH        rows per ingest POST (default 40)
  HTTP_TIMEOUT        per-request timeout seconds (default 20)
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

import httpx

# scrape_jobs.py runs as `python scripts/scrape_jobs.py`, so `scripts/` is on
# sys.path[0] and these sibling modules import directly.
from filters import india_ok, is_target_fresher_title
from providers import USER_AGENT, provider_for

BACKEND_URL = os.environ["BACKEND_URL"].rstrip("/")
CRON_SECRET = os.environ["CRON_SECRET"]
TOP_N = int(os.getenv("TOP_N", "200"))
MAX_JOBS_PER_COMPANY = int(os.getenv("MAX_JOBS_PER_COMPANY", "3"))
INGEST_BATCH = int(os.getenv("INGEST_BATCH", "40"))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "20"))


def _backend(method: str, path: str, payload: dict | None = None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(
        f"{BACKEND_URL}{path}", method=method, data=data,
        headers={"Content-Type": "application/json", "X-Cron-Secret": CRON_SECRET},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode(errors="replace")[:300]}
    except Exception as e:  # noqa: BLE001
        return 0, {"error": str(e)[:300]}


def fetch_companies() -> list[dict]:
    status, data = _backend("GET", f"/api/cron/companies?top_n={TOP_N}")
    if status != 200:
        print(f"companies fetch failed: HTTP {status} {data}")
        return []
    return data.get("companies", [])


def _iso(value) -> str | None:
    if not value:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def scrape_company(client: httpx.Client, company: dict) -> list[dict]:
    """Scrape one company's ATS sources on the runner; return clean India+fresher rows."""
    name = str(company.get("name") or "")
    website = str(company.get("website") or "")
    rows: list[dict] = []
    per_company = 0
    for source_url in (company.get("source_urls") or [])[:2]:
        if per_company >= MAX_JOBS_PER_COMPANY:
            break
        try:
            provider = provider_for(source_url, client)
            candidates = provider.collect(source_url)
        except Exception as exc:  # noqa: BLE001 — one bad source shouldn't kill the company
            print(f"  ! {name}: {source_url} -> {str(exc)[:120]}")
            continue
        for cand in candidates:
            if per_company >= MAX_JOBS_PER_COMPANY:
                break
            if not is_target_fresher_title(cand.title):
                continue
            if not india_ok(cand.location, cand.country):
                continue
            external_id = str(cand.external_id or "").strip()
            apply_url = cand.canonical_url or cand.apply_url
            if not (external_id and apply_url):
                continue
            rows.append({
                "company": name,
                "website": website,
                "source_url": source_url,
                "external_id": external_id,
                "title": cand.title,
                "location": cand.location,
                "country": cand.country,
                "department": cand.department,
                "workplace_type": cand.workplace_type,
                "employment_type": cand.employment_type,
                "salary_text": cand.salary_text,
                "apply_url": apply_url,
                "canonical_url": cand.canonical_url or apply_url,
                "description": (cand.description or "")[:8000],
                "posted_at": _iso(cand.posted_at),
                "expires_at": _iso(cand.expires_at),
            })
            per_company += 1
    return rows


def flush(batch: list[dict], is_last: bool) -> dict:
    status, data = _backend("POST", "/api/cron/ingest-jobs", {"jobs": batch, "is_last_batch": is_last})
    if status != 200:
        print(f"  ingest failed: HTTP {status} {data}")
        return {"saved": 0, "skipped": 0}
    print(f"  ingested batch of {len(batch)}: saved={data.get('saved')} skipped={data.get('skipped')} pruned={data.get('pruned_jobs', 0)}")
    return data


def write_step_summary(lines: list[str]) -> None:
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


def main() -> int:
    companies = fetch_companies()
    if not companies:
        print("No companies returned by backend; nothing to scrape.")
        return 1
    print(f"Scraping {len(companies)} companies on the runner (top_n={TOP_N})...")

    client = httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    total_rows = 0
    total_saved = 0
    total_skipped = 0
    batch: list[dict] = []
    try:
        for i, company in enumerate(companies):
            rows = scrape_company(client, company)
            total_rows += len(rows)
            if rows:
                print(f"  [{i+1}/{len(companies)}] {company.get('name')}: {len(rows)} row(s)")
            batch.extend(rows)
            if len(batch) >= INGEST_BATCH:
                res = flush(batch, is_last=False)
                total_saved += int(res.get("saved", 0) or 0)
                total_skipped += int(res.get("skipped", 0) or 0)
                batch = []
        # Final flush (always send, even if empty, so the backend prunes).
        res = flush(batch, is_last=True)
        total_saved += int(res.get("saved", 0) or 0)
        total_skipped += int(res.get("skipped", 0) or 0)
    finally:
        client.close()

    print(f"DONE. scraped_rows={total_rows} saved={total_saved} skipped={total_skipped}")
    write_step_summary([
        "## Scrape Jobs (runner-side) summary",
        "",
        f"- Companies scraped: **{len(companies)}**",
        f"- Rows scraped on runner: **{total_rows}**",
        f"- Saved to DB: **{total_saved}**",
        f"- Skipped (filtered/dupe): **{total_skipped}**",
    ])
    return 0


if __name__ == "__main__":
    sys.exit(main())
