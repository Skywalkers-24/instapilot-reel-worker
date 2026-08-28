/**
 * Cloudflare Worker Cron for Instapilot Reel Publishing
 *
 * Schedules regular trigger calls to the backend's `/api/cron/trigger-publish` endpoint.
 * Also handles HTTP requests for easy testing, manual triggers, and health checks via:
 *   - Scheduled cron event (every 15 min by default)
 *   - GET/POST /__scheduled (Cloudflare scheduled testing endpoint)
 *   - GET/POST /trigger (Manual trigger endpoint)
 *   - GET /health or GET / (Diagnostics and config status)
 */

export default {
  /**
   * Scheduled cron handler
   */
  async scheduled(controller, env, ctx) {
    const startedAt = new Date().toISOString();
    console.log(`[cron] Scheduled trigger started at ${startedAt}, cron: ${controller?.cron || "unknown"}`);

    // ctx.waitUntil ensures the promise resolves even if the worker begins shutting down
    const triggerPromise = triggerPublish(env, {
      source: "cron",
      cron: controller?.cron,
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
    const isTrigger =
      path === "/__scheduled" ||
      path === "/trigger" ||
      path === "/publish" ||
      path === "/run" ||
      url.searchParams.has("trigger") ||
      url.searchParams.has("force") ||
      request.method === "POST";

    // Manual or simulated trigger
    if (isTrigger) {
      try {
        const result = await triggerPublish(env, {
          source: "http",
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
      worker: "instapilot-reel-publish-cron",
      status: "online",
      cron_schedule: "*/15 * * * *",
      configured: {
        backend_url: backendUrl ? `${backendUrl.slice(0, 30)}...` : null,
        cron_secret_set: hasSecret,
      },
      endpoints: {
        manual_trigger: `${url.origin}/trigger`,
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

    // Human-friendly HTML landing page
    const html = `<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Instapilot Cron Worker</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #0f172a; color: #f8fafc; padding: 2rem; margin: 0; line-height: 1.5; }
    .card { max-width: 600px; margin: 2rem auto; background: #1e293b; border-radius: 12px; padding: 2rem; border: 1px solid #334155; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.3); }
    h1 { margin-top: 0; font-size: 1.5rem; color: #38bdf8; display: flex; align-items: center; gap: 0.5rem; }
    .badge { display: inline-block; padding: 0.25rem 0.6rem; border-radius: 9999px; font-size: 0.8rem; font-weight: 600; background: #059669; color: white; }
    .badge.warn { background: #d97706; }
    .stat-row { display: flex; justify-content: space-between; padding: 0.6rem 0; border-bottom: 1px solid #334155; }
    .btn { display: inline-block; background: #0284c7; color: white; text-decoration: none; padding: 0.75rem 1.25rem; border-radius: 8px; font-weight: 600; text-align: center; margin-top: 1.5rem; width: calc(100% - 2.5rem); }
    .btn:hover { background: #0369a1; }
    code { background: #0f172a; padding: 0.2rem 0.4rem; border-radius: 4px; font-size: 0.85em; }
  </style>
</head>
<body>
  <div class="card">
    <h1>Instapilot Cron Worker <span class="badge ${backendUrl && hasSecret ? "" : "warn"}">${backendUrl && hasSecret ? "Active" : "Config Missing"}</span></h1>
    <p>Cloudflare cron trigger is active on schedule <code>*/15 * * * *</code> (every 15 minutes).</p>
    <div class="stat-row"><span>Backend URL:</span> <strong>${backendUrl ? "Configured" : "Not Set"}</strong></div>
    <div class="stat-row"><span>CRON_SECRET:</span> <strong>${hasSecret ? "Configured" : "Not Set"}</strong></div>
    <a href="/trigger" class="btn">Trigger Reel Publish Now</a>
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
 * Execute the trigger request to the backend
 */
async function triggerPublish(env, meta = {}) {
  const backendUrl = getBackendUrl(env);
  if (!backendUrl) {
    throw new Error("Neither BACKEND_URL nor BACKEND_PUBLIC_URL is configured in Cloudflare Worker environment");
  }

  const cronSecret = env.CRON_SECRET;
  if (!cronSecret) {
    throw new Error("CRON_SECRET is not configured in Cloudflare Worker environment");
  }

  const targetUrl = `${backendUrl}/api/cron/trigger-publish`;
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
    cron: "publish-reel",
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
    throw new Error(`trigger-publish failed: HTTP ${response.status} - ${detail}`);
  }

  return {
    success: true,
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
