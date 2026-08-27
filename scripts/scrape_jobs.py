#!/usr/bin/env python3
"""
Job scraper trigger (runs in GitHub Actions, no secrets except CRON_SECRET).

Calls the backend's POST /api/cron/scrape-jobs repeatedly — one batch of 5
companies per call — until the backend reports has_more=false. The backend
does the actual scraping + DB writes server-side (DATABASE_URL never touches
the runner). Top-200 companies, India fresher roles, real job IDs.

Env:
  BACKEND_URL   e.g. https://<your-backend-host>
  CRON_SECRET   shared secret (matches backend CRON_SECRET)
  TOP_N         company universe size (default 200)
  BATCH_SIZE    companies per batch (default 5)
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


def main() -> int:
    total_saved = 0
    total_found = 0
    print(f"Scraping top {TOP_N} companies in batches of {BATCH_SIZE}...")

    for batch_index in range(MAX_BATCHES):
        status, data = post("/api/cron/scrape-jobs", {
            "batch_index": batch_index, "batch_size": BATCH_SIZE, "top_n": TOP_N,
        })
        if status != 200:
            print(f"  batch {batch_index}: HTTP {status} {data}")
            # transient backend hiccup — brief pause and continue to next batch
            time.sleep(5)
            continue

        saved = data.get("saved", 0)
        found = data.get("found", 0)
        total_saved += saved
        total_found += found
        names = ", ".join(data.get("companies_in_batch", []))
        print(f"  batch {batch_index}/{data.get('total_batches','?')}: {names} -> found={found} saved={saved}")

        if data.get("errors"):
            for e in data["errors"][:3]:
                print(f"     ! {e}")

        if not data.get("has_more"):
            print("All batches processed.")
            break

        # Gentle pace to avoid hammering ATS APIs / backend
        time.sleep(3)

    print(f"DONE. Total found={total_found}, saved={total_saved}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
