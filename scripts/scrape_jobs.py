#!/usr/bin/env python3
"""
Job scraper trigger (runs in GitHub Actions, no secrets except CRON_SECRET).

Calls the backend's POST /api/cron/scrape-jobs repeatedly — one batch of 5
companies per call — until the backend reports has_more=false. The backend
does the actual scraping + DB writes server-side (DATABASE_URL never touches
the runner). Top-200 companies, India fresher roles, real job IDs.

Env:
  BACKEND_URL   base URL of the backend (required)
  CRON_SECRET   shared secret (matches backend CRON_SECRET)
  TOP_N         company universe size (default 200)
  BATCH_SIZE    companies per batch (default 5)
  MAX_RETRIES   retries per batch on transient failure (default 2)
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
import urllib.error

BACKEND_URL = os.environ["BACKEND_URL"].rstrip("/")
CRON_SECRET = os.environ["CRON_SECRET"]
TOP_N = int(os.getenv("TOP_N", "200"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "5"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "2"))
MAX_BATCHES = (TOP_N + BATCH_SIZE - 1) // BATCH_SIZE


def post(path: str, payload: dict):
    data = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"{BACKEND_URL}{path}", method="POST", data=data,
        headers={"Content-Type": "application/json", "X-Cron-Secret": CRON_SECRET},
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": e.read().decode(errors="replace")[:300]}
    except Exception as e:
        return 0, {"error": str(e)[:300]}


def post_with_retry(path: str, payload: dict):
    """POST a batch, retrying transient failures with exponential backoff.

    Returns (status, data, attempts). A non-200 status after all retries is
    surfaced to the caller so the run can be marked degraded.
    """
    status, data = 0, {}
    for attempt in range(1, MAX_RETRIES + 2):  # 1 initial try + MAX_RETRIES
        status, data = post(path, payload)
        if status == 200:
            return status, data, attempt
        if attempt <= MAX_RETRIES:
            backoff = 5 * attempt
            print(f"    retry {attempt}/{MAX_RETRIES} after HTTP {status} (waiting {backoff}s)")
            time.sleep(backoff)
    return status, data, MAX_RETRIES + 1


def write_step_summary(lines: list[str]) -> None:
    """Append a Markdown summary to the GitHub Actions run (no-op locally)."""
    path = os.getenv("GITHUB_STEP_SUMMARY")
    if not path:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
    except OSError:
        pass


def main() -> int:
    total_saved = 0
    total_found = 0
    failed_batches: list[int] = []
    company_errors: list[str] = []
    completed = False
    print(f"Scraping top {TOP_N} companies in batches of {BATCH_SIZE}...")

    for batch_index in range(MAX_BATCHES):
        status, data, attempts = post_with_retry("/api/cron/scrape-jobs", {
            "batch_index": batch_index, "batch_size": BATCH_SIZE, "top_n": TOP_N,
        })
        if status != 200:
            print(f"  batch {batch_index}: FAILED after {attempts} attempt(s) — HTTP {status} {data}")
            failed_batches.append(batch_index)
            # Can't trust has_more from a failed batch; keep going through the
            # known company universe so one bad batch doesn't abort the run.
            time.sleep(5)
            continue

        saved = data.get("saved", 0)
        found = data.get("found", 0)
        total_saved += saved
        total_found += found
        names = ", ".join(data.get("companies_in_batch", []))
        print(f"  batch {batch_index}/{data.get('total_batches','?')}: {names} -> found={found} saved={saved}")

        if data.get("errors"):
            for e in data["errors"]:
                company_errors.append(str(e))
            for e in data["errors"][:3]:
                print(f"     ! {e}")

        if not data.get("has_more"):
            print("All batches processed.")
            completed = True
            break

        # Gentle pace to avoid hammering ATS APIs / backend
        time.sleep(3)

    print(f"DONE. Total found={total_found}, saved={total_saved}")
    if failed_batches:
        print(f"Degraded: {len(failed_batches)} batch(es) failed: {failed_batches}")

    # GitHub Actions run summary (visible on the job page)
    summary = [
        "## Scrape Jobs summary",
        "",
        f"- Total found: **{total_found}**",
        f"- Total saved: **{total_saved}**",
        f"- Batches failed: **{len(failed_batches)}**"
        + (f" ({failed_batches})" if failed_batches else ""),
        f"- Company-level errors: **{len(company_errors)}**",
        f"- Completed cleanly: **{completed}**",
    ]
    if company_errors:
        summary += ["", "<details><summary>Company errors</summary>", ""]
        summary += [f"- {e}" for e in company_errors[:50]]
        summary += ["", "</details>"]
    write_step_summary(summary)

    # Fail the workflow when batches errored or the run never completed, so a
    # degraded scrape shows up red instead of a silent green.
    if failed_batches or not completed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
