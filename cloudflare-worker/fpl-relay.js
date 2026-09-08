/**
 * Minimal CORS relay for the official Fantasy Premier League API.
 *
 * The FPL API (fantasy.premierleague.com/api/...) doesn't send the CORS
 * headers a browser requires before letting JavaScript on a different
 * origin (like a GitHub Pages site) read the response -- server-to-server
 * calls (the CLI, the GitHub Actions workflows) aren't affected, only
 * in-browser fetch() calls. This Worker's only job is: forward an allowed
 * request to the real API, and add the one header that unblocks it.
 *
 * It does NOT run any of the tool's scoring/optimizer logic -- that all
 * still happens in the visitor's own browser (see public/js/fpl-model.js,
 * public/js/fpl-optimizer.js, public/build.html). This relay never sees
 * or stores a squad, a team ID's picks, or anything computed from them; it
 * only ever proxies bytes through to FPL's public endpoints and back.
 *
 * Deploy: see the README "Browser tool" section for the free Cloudflare
 * dashboard steps (no command line required). Update ALLOWED_ORIGIN below
 * to your actual GitHub Pages URL before deploying, or leave it as "*" if
 * you don't mind other sites also being able to use your relay (it still
 * only ever talks to the public FPL API either way, so the exposure is
 * limited to "who's allowed to make FPL API calls without hitting CORS",
 * not any private data of yours).
 */

const ALLOWED_ORIGIN = "*";

// Only these path prefixes are relayed -- an explicit allowlist, not an
// open proxy, so this can't be used to fetch arbitrary third-party URLs.
const ALLOWED_PREFIXES = [
  "/api/bootstrap-static/",
  "/api/fixtures/",
  "/api/fixtures",
  "/api/entry/",
  "/api/event/",
  "/api/event-status/",
  "/api/team/set-piece-notes/",
];

const FPL_ORIGIN = "https://fantasy.premierleague.com";

function corsHeaders() {
  return {
    "Access-Control-Allow-Origin": ALLOWED_ORIGIN,
    "Access-Control-Allow-Methods": "GET, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Access-Control-Max-Age": "86400",
  };
}

function isAllowed(pathname) {
  return ALLOWED_PREFIXES.some((prefix) => pathname.startsWith(prefix));
}

export default {
  async fetch(request) {
    const url = new URL(request.url);

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: corsHeaders() });
    }

    if (request.method !== "GET") {
      return new Response("Only GET is supported.", { status: 405, headers: corsHeaders() });
    }

    // Path after this Worker's own origin becomes the FPL API path, e.g.
    // https://your-relay.workers.dev/api/bootstrap-static/
    //   -> https://fantasy.premierleague.com/api/bootstrap-static/
    if (!isAllowed(url.pathname)) {
      return new Response("This path isn't relayed. See ALLOWED_PREFIXES in fpl-relay.js.", {
        status: 403,
        headers: corsHeaders(),
      });
    }

    const upstreamUrl = FPL_ORIGIN + url.pathname + url.search;

    let upstreamResponse;
    try {
      upstreamResponse = await fetch(upstreamUrl, {
        headers: { Accept: "application/json" },
        cf: { cacheTtl: 60, cacheEverything: true },
      });
    } catch (err) {
      return new Response(`Could not reach the FPL API: ${err.message}`, {
        status: 502,
        headers: corsHeaders(),
      });
    }

    const body = await upstreamResponse.arrayBuffer();
    return new Response(body, {
      status: upstreamResponse.status,
      headers: {
        ...corsHeaders(),
        "Content-Type": upstreamResponse.headers.get("Content-Type") || "application/json",
      },
    });
  },
};
