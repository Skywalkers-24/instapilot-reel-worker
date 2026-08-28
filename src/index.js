export default {
  async scheduled(event, env, ctx) {
    ctx.waitUntil(triggerPublish(env));
  },

  async fetch() {
    return new Response("reel-publish-cron is running. Cron triggers do the work.", {
      headers: { "content-type": "text/plain; charset=utf-8" },
    });
  },
};

async function triggerPublish(env) {
  const backendUrl = requireEnv(env, "BACKEND_URL").replace(/\/+$/, "");
  const cronSecret = requireEnv(env, "CRON_SECRET");

  const response = await fetch(`${backendUrl}/api/cron/trigger-publish`, {
    method: "POST",
    headers: {
      "X-Cron-Secret": cronSecret,
      "User-Agent": "instapilot-reel-publish-cron/1.0",
    },
  });

  const body = await response.text();
  console.log(
    JSON.stringify({
      cron: "publish-reel",
      status: response.status,
      ok: response.ok,
      body: body.slice(0, 500),
    }),
  );

  if (!response.ok) {
    throw new Error(`trigger-publish failed: HTTP ${response.status} ${body.slice(0, 500)}`);
  }
}

function requireEnv(env, name) {
  const value = env[name];
  if (!value || typeof value !== "string") {
    throw new Error(`${name} is not configured`);
  }
  return value;
}
