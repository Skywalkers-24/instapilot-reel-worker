#!/usr/bin/env python3
"""
Full-Universe Job Scraper (GitHub Actions Worker / Local Runner).

Features:
  - Crawls all companies from the master company directory (TOP_N = 2000).
  - Strictly filters tech roles for 0–3 years experience max (via filters.py).
  - Inspects title and complete JD text for required qualifications.
  - Scrapes direct genuine ATS endpoints (Greenhouse, Workday, Lever, SmartRecruiters, Ashby, Jobvite, etc.).
  - Deduplicates by canonical URL and job external ID.
  - Maintains structured detailed logs per company and source:
      Company -> Source -> Endpoints checked -> Found -> Accepted -> Rejected with exact reasons -> Retries/Errors.
  - Streams batches to backend which caps DB to 500 jobs max.
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter

import httpx

from filters import validate_strict_early_career
from providers import USER_AGENT, provider_for

BACKEND_URL = os.environ.get("BACKEND_URL", "http://localhost:8000").rstrip("/")
CRON_SECRET = os.environ.get("CRON_SECRET", "dev-secret")
TOP_N = int(os.getenv("TOP_N", "2000"))
MAX_JOBS_PER_COMPANY = int(os.getenv("MAX_JOBS_PER_COMPANY", "5"))
INGEST_BATCH = int(os.getenv("INGEST_BATCH", "40"))
HTTP_TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "25"))


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
        print(f"Companies fetch failed: HTTP {status} {data}")
        return []
    return data.get("companies", [])


def _iso(value) -> str | None:
    if not value:
        return None
    try:
        return value.isoformat()
    except AttributeError:
        return str(value)


def scrape_company(client: httpx.Client, company: dict) -> tuple[list[dict], dict]:
    """Scrape one company's genuine ATS sources with strict 0-3y validation and detailed logging."""
    name = str(company.get("name") or "Unknown")
    website = str(company.get("website") or "")
    source_urls = company.get("source_urls") or []
    
    stats = {
        "company": name,
        "sources_checked": 0,
        "jobs_found": 0,
        "jobs_accepted": 0,
        "jobs_rejected": 0,
        "rejection_reasons": Counter(),
        "errors": [],
    }
    
    rows: list[dict] = []
    per_company = 0

    if not source_urls:
        stats["errors"].append("no_source_urls_configured")
        return rows, stats

    for source_url in source_urls[:3]:
        stats["sources_checked"] += 1
        candidates = []
        try:
            provider = provider_for(source_url, client)
            candidates = provider.collect(source_url)
        except Exception as exc:  # noqa: BLE001
            err_msg = f"{source_url} -> {str(exc)[:140]}"
            stats["errors"].append(err_msg)
            print(f"  [ERROR] {name}: {err_msg}")
            continue

        stats["jobs_found"] += len(candidates)

        for cand in candidates:
            title = str(cand.title or "").strip()
            loc = str(cand.location or "").strip()
            cntry = str(cand.country or "").strip()
            desc = str(cand.description or "").strip()

            # Strict 0-3 Years Early-Career Tech Filter
            is_ok, reason, min_yoe, max_yoe, exp_lbl = validate_strict_early_career(
                title=title,
                location=loc,
                country=cntry,
                description=desc,
            )

            if not is_ok:
                stats["jobs_rejected"] += 1
                # Group reason categories for clean logging
                category_reason = reason.split(":")[0] if ":" in reason else reason
                stats["rejection_reasons"][category_reason] += 1
                continue

            external_id = str(cand.external_id or "").strip()
            apply_url = str(cand.canonical_url or cand.apply_url or "").strip()
            if not (external_id and apply_url):
                stats["jobs_rejected"] += 1
                stats["rejection_reasons"]["missing_id_or_apply_url"] += 1
                continue

            if per_company >= MAX_JOBS_PER_COMPANY:
                stats["rejection_reasons"]["max_jobs_per_company_cap"] += 1
                continue

            rows.append({
                "company": name,
                "website": website,
                "source_url": source_url,
                "external_id": external_id,
                "title": title,
                "location": loc,
                "country": cntry,
                "department": cand.department or "",
                "workplace_type": cand.workplace_type or "",
                "employment_type": cand.employment_type or "full_time",
                "salary_text": cand.salary_text or "",
                "experience_label": exp_lbl,
                "experience_min": min_yoe,
                "experience_max": max_yoe,
                "apply_url": apply_url,
                "canonical_url": cand.canonical_url or apply_url,
                "description": desc[:10000],
                "posted_at": _iso(cand.posted_at),
                "expires_at": _iso(cand.expires_at),
            })
            stats["jobs_accepted"] += 1
            per_company += 1

    return rows, stats


def flush(batch: list[dict], is_last: bool) -> dict:
    status, data = _backend("POST", "/api/cron/ingest-jobs", {"jobs": batch, "is_last_batch": is_last})
    if status != 200:
        print(f"  [INGEST ERROR] HTTP {status}: {data}")
        return {"saved": 0, "skipped": 0}
    print(f"  [INGEST FLUSH] Batch of {len(batch)} -> Saved: {data.get('saved', 0)} | Skipped: {data.get('skipped', 0)} | DB Pruned to 500 Cap: {data.get('pruned_jobs', 0)}")
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
    print(f"=== Starting Crawl Across {len(companies)} Companies (Strict 0-3y Tech Roles) ===")

    client = httpx.Client(timeout=HTTP_TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    total_found = 0
    total_accepted = 0
    total_rejected = 0
    total_saved = 0
    total_skipped = 0
    global_rejection_reasons = Counter()
    batch: list[dict] = []
    start_time = time.time()

    try:
        for i, company in enumerate(companies):
            c_name = company.get("name", "Unknown")
            rows, stats = scrape_company(client, company)
            
            total_found += stats["jobs_found"]
            total_accepted += stats["jobs_accepted"]
            total_rejected += stats["jobs_rejected"]
            global_rejection_reasons.update(stats["rejection_reasons"])
            
            reasons_summary = ", ".join(f"{k}:{v}" for k, v in stats["rejection_reasons"].most_common(2))
            reasons_str = f" | Rejections: [{reasons_summary}]" if reasons_summary else ""
            err_str = f" | Errors: {len(stats['errors'])}" if stats["errors"] else ""
            
            print(f"[{i+1}/{len(companies)}] {c_name:<26} -> Checked: {stats['sources_checked']} src | Found: {stats['jobs_found']:>2} | Accepted: {stats['jobs_accepted']:>2} (0-3y){reasons_str}{err_str}")

            batch.extend(rows)
            if len(batch) >= INGEST_BATCH:
                res = flush(batch, is_last=False)
                total_saved += int(res.get("saved", 0) or 0)
                total_skipped += int(res.get("skipped", 0) or 0)
                batch = []

        # Final flush (always send, even if empty, so backend enforces 500 cap retention)
        res = flush(batch, is_last=True)
        total_saved += int(res.get("saved", 0) or 0)
        total_skipped += int(res.get("skipped", 0) or 0)
    finally:
        client.close()

    elapsed = time.time() - start_time
    print("\n" + "=" * 65)
    print(f"SCRAPE RUN COMPLETED in {elapsed:.1f}s")
    print(f"  Companies Processed: {len(companies)}")
    print(f"  Total Jobs Scanned:  {total_found}")
    print(f"  Strict 0-3y Accepted: {total_accepted}")
    print(f"  Jobs Rejected:       {total_rejected}")
    print(f"  Saved to DB:         {total_saved}")
    print(f"  Top Rejection Reasons:")
    for reason, count in global_rejection_reasons.most_common(5):
        print(f"    - {reason}: {count}")
    print("=" * 65)

    write_step_summary([
        "## Scrape Jobs (0–3 Years Strict Tech Roles) Summary",
        "",
        f"- **Companies Checked:** {len(companies)}",
        f"- **Total Jobs Scanned:** {total_found}",
        f"- **Strict 0–3y Accepted:** {total_accepted}",
        f"- **Saved to DB:** {total_saved}",
        f"- **Filtered / Duplicates:** {total_skipped}",
        f"- **Elapsed Time:** {elapsed:.1f}s",
    ])
    return 0


if __name__ == "__main__":
    sys.exit(main())
