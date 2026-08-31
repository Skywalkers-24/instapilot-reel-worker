/**
 * Cloudflare Worker Cron for Instapilot Automations
 *
 * Schedules regular triggers to the backend:
 *   1. Reel Publishing: every 30 minutes ("* /30 * * * *") -> /api/cron/trigger-publish
 *   2. Job Scraping: 6:00 AM & 4:00 PM IST ("30 0,10 * * *") -> /api/cron/trigger-scrape-jobs
 *
 * Also handles HTTP requests for easy testing, manual triggers, and health checks via:
 *   - GET/POST /trigger (or /trigger-publish)
 *   - GET/POST /trigger-scrape-jobs (or /trigger?job=scrape)
 *   - GET/POST /__scheduled (Cloudflare scheduled testing endpoint)
 *   - GET /health or GET / (Diagnostics UI & live status)
 */

export default {
  /**
   * Scheduled cron handler
   */
  async scheduled(controller, env, ctx) {
    const startedAt = new Date().toISOString();
    const cronPattern = controller?.cron || "";
    console.log(`[cron] Scheduled trigger started at ${startedAt}, cron: ${cronPattern}`);

    // Determine event type based on cron expression:
    // "30 0,10 * * *" -> Scrape Jobs (6:00 AM & 4:00 PM IST)
    // "*/30 * * * *" -> Publish Reel (backend cadence-gated)
    const isJobScrape =
      cronPattern === "30 0,10 * * *" ||
      cronPattern === "30 10 * * *" ||
      cronPattern === "30 0 * * *";

    const eventType = isJobScrape ? "scrape-jobs" : "publish-reel";
    const endpoint = isJobScrape ? "/api/cron/trigger-scrape-jobs" : "/api/cron/trigger-publish";

    const triggerPromise = triggerBackend(env, endpoint, {
      source: "cron",
      event: eventType,
      cron: cronPattern,
      scheduledTime: controller?.scheduledTime,
    });

    ctx.waitUntil(triggerPromise);
    await triggerPromise;
  },

  /**
   * HTTP fetch handler for manual triggering and diagnostics
   */
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const path = url.pathname.toLowerCase();
    const isJobScrape =
      path === "/trigger-scrape-jobs" ||
      path === "/scrape-jobs" ||
      url.searchParams.get("job") === "scrape" ||
      url.searchParams.get("event") === "scrape-jobs";

    const isPublish =
      path === "/trigger" ||
      path === "/trigger-publish" ||
      path === "/publish" ||
      path === "/__scheduled" ||
      path === "/run" ||
      url.searchParams.has("trigger") ||
      url.searchParams.has("force") ||
      (request.method === "POST" && !isJobScrape);

    // Manual / test trigger invocation
    if (isJobScrape || isPublish) {
      const endpoint = isJobScrape ? "/api/cron/trigger-scrape-jobs" : "/api/cron/trigger-publish";
      const eventType = isJobScrape ? "scrape-jobs" : "publish-reel";

      try {
        const result = await triggerBackend(env, endpoint, {
          source: "http",
          event: eventType,
          path: url.pathname,
          method: request.method,
        });
        return new Response(JSON.stringify(result, null, 2), {
          status: result.ok ? 200 : 502,
          headers: {
            "content-type": "application/json; charset=utf-8",
            "cache-control": "no-store",
          },
        });
      } catch (err) {
        return new Response(
          JSON.stringify(
            {
              success: false,
              event: eventType,
              error: err.message || String(err),
              timestamp: new Date().toISOString(),
            },
            null,
            2,
          ),
          {
            status: 500,
            headers: {
              "content-type": "application/json; charset=utf-8",
              "cache-control": "no-store",
            },
          },
        );
      }
    }

    // Health / config check
    const backendUrl = getBackendUrl(env);
    const hasSecret = Boolean(env.CRON_SECRET);

    const info = {
      worker: "instapilot-reel-worker",
      status: "online",
      schedules: [
        { name: "Reel Publish", cron: "*/30 * * * *", frequency: "Every 30 min" },
        { name: "Job Scraping", cron: "30 0,10 * * *", frequency: "6:00 AM & 4:00 PM IST (00:30 & 10:30 UTC)" },
      ],
      configured: {
        backend_url: backendUrl ? `${backendUrl.slice(0, 30)}...` : null,
        cron_secret_set: hasSecret,
      },
      endpoints: {
        trigger_publish: `${url.origin}/trigger`,
        trigger_scrape_jobs: `${url.origin}/trigger-scrape-jobs`,
        scheduled_test: `${url.origin}/__scheduled`,
        health: `${url.origin}/health`,
      },


      timestamp: new Date().toISOString(),
    };

    if (path === "/health" || path === "/status" || request.headers.get("accept")?.includes("application/json")) {
      return new Response(JSON.stringify(info, null, 2), {
        headers: {
          "content-type": "application/json; charset=utf-8",
          "cache-control": "no-store",
        },
      });
    }

    // Human-friendly HTML dashboard
    const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Instapilot Cron Automation Worker</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #0f172a; color: #f8fafc; padding: 2rem; margin: 0; line-height: 1.5; }
    .card { max-width: 620px; margin: 2rem auto; background: #1e293b; border-radius: 12px; padding: 2rem; border: 1px solid #334155; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3); }
    h1 { margin-top: 0; font-size: 1.5rem; color: #38bdf8; display: flex; align-items: center; justify-content: space-between; }
    .badge { display: inline-block; padding: 0.25rem 0.6rem; border-radius: 9999px; font-size: 0.8rem; font-weight: 600; background: #059669; color: white; }
    .badge.warn { background: #d97706; }
    .stat-row { display: flex; justify-content: space-between; padding: 0.6rem 0; border-bottom: 1px solid #334155; font-size: 0.95rem; }
    .btn-group { display: flex; flex-direction: column; gap: 0.75rem; margin-top: 1.5rem; }
    .btn { display: block; background: #0284c7; color: white; text-decoration: none; padding: 0.75rem 1.25rem; border-radius: 8px; font-weight: 600; text-align: center; }
    .btn:hover { background: #0369a1; }
    .btn.secondary { background: #475569; }
    .btn.secondary:hover { background: #334155; }
    code { background: #0f172a; padding: 0.2rem 0.4rem; border-radius: 4px; font-size: 0.85em; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Instapilot Cron Worker <span class="badge ${backendUrl && hasSecret ? "" : "warn"}">${backendUrl && hasSecret ? "Active" : "Config Missing"}</span></h1>
    <p style="color: #94a3b8; font-size: 0.95rem;">Cloudflare cron triggers manage automated Instagram publishing & job scraping.</p>
    
    <div class="stat-row"><span>1. Reel Publish:</span> <code>*/30 * * * *</code> (Every 30 min)</div>
    <div class="stat-row"><span>2. Job Scraping:</span> <code>30 0,10 * * *</code> (6:00 AM & 4:00 PM IST)</div>
    <div class="stat-row"><span>Backend URL:</span> <strong>${backendUrl ? "Configured" : "Not Set"}</strong></div>
    <div class="stat-row"><span>CRON_SECRET:</span> <strong>${hasSecret ? "Configured" : "Not Set"}</strong></div>
    
    <div class="btn-group">
      <a href="/trigger" class="btn">🚀 Trigger Reel Publish Now</a>
      <a href="/trigger-scrape-jobs" class="btn secondary">🔍 Trigger Job Scraping Now (6 AM & 4 PM)</a>
    </div>
  </div>
</body>
</html>`;

    return new Response(html, {
      headers: {
        "content-type": "text/html; charset=utf-8",
        "cache-control": "no-store",
      },
    });
  },
};

/**
 * Execute trigger HTTP request to backend
 */
async function triggerBackend(env, endpointPath, meta = {}) {
  const backendUrl = getBackendUrl(env);
  if (!backendUrl) {
    throw new Error("Neither BACKEND_URL nor BACKEND_PUBLIC_URL is configured in Cloudflare Worker environment");
  }

  const cronSecret = env.CRON_SECRET;
  if (!cronSecret) {
    throw new Error("CRON_SECRET is not configured in Cloudflare Worker environment");
  }

  const targetUrl = `${backendUrl}${endpointPath}`;
  const startTime = Date.now();

  let response;
  try {
    response = await fetch(targetUrl, {
      method: "POST",
      headers: {
        "X-Cron-Secret": cronSecret,
        "Authorization": `Bearer ${cronSecret}`,
        "User-Agent": "instapilot-reel-publish-cron/1.0",
        "Accept": "application/json",
      },
      signal: AbortSignal.timeout ? AbortSignal.timeout(20000) : undefined,
    });
  } catch (netErr) {
    const errorMsg = `Failed to connect to backend at ${targetUrl}: ${netErr.message}`;
    console.error(`[cron-error] ${errorMsg}`);
    throw new Error(errorMsg);
  }

  const latencyMs = Date.now() - startTime;
  const bodyText = await response.text();
  let parsedBody = null;
  try {
    parsedBody = JSON.parse(bodyText);
  } catch {
    parsedBody = bodyText.slice(0, 500);
  }

  const logPayload = {
    event: meta.event || "unknown",
    target: targetUrl,
    status: response.status,
    ok: response.ok,
    latency_ms: latencyMs,
    source: meta.source || "cron",
    body: parsedBody,
  };

  console.log(JSON.stringify(logPayload));

  if (!response.ok) {
    const detail = typeof parsedBody === "object" && parsedBody ? JSON.stringify(parsedBody) : bodyText.slice(0, 300);
    throw new Error(`${meta.event || "trigger"} failed: HTTP ${response.status} - ${detail}`);
  }

  return {
    success: true,
    event: meta.event,
    ok: response.ok,
    status: response.status,
    latency_ms: latencyMs,
    backend_response: parsedBody,
    timestamp: new Date().toISOString(),
  };
}

/**
 * Helper to resolve backend URL with automatic https prefix and trailing slash cleanup
 */
function getBackendUrl(env) {
  let raw = env.BACKEND_URL || env.BACKEND_PUBLIC_URL || env.RENDER_URL || env.API_URL || "";
  raw = String(raw).trim().replace(/\/+$/, "");
  if (!raw) return "";

  if (!raw.startsWith("http://") && !raw.startsWith("https://")) {
    raw = `https://${raw}`;
  }
  return raw;
}
