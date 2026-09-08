/**
 * Thin fetch wrapper around the CORS relay (see cloudflare-worker/fpl-relay.js)
 * for the endpoints the browser tool (public/build.html) needs. Mirrors the
 * endpoint set of src/fpl_forecast/api_client.py, minus element-summary
 * (deliberately not fetched client-side -- see fpl-model.js's module
 * docstring for why).
 */

export class RelayError extends Error {}

export function createRelayClient(relayBaseUrl) {
  const base = relayBaseUrl.replace(/\/$/, "");

  async function getJson(path) {
    let response;
    try {
      response = await fetch(`${base}${path}`);
    } catch (err) {
      throw new RelayError(
        `Couldn't reach the relay at ${base} -- is it deployed and is the URL right? (${err.message})`
      );
    }
    if (!response.ok) {
      throw new RelayError(`Relay/FPL request to ${path} failed: HTTP ${response.status}`);
    }
    return response.json();
  }

  return {
    getBootstrapStatic: () => getJson("/api/bootstrap-static/"),
    getFixtures: () => getJson("/api/fixtures/"),
    getEntry: (teamId) => getJson(`/api/entry/${encodeURIComponent(teamId)}/`),
    getEntryPicks: (teamId, gameweek) =>
      getJson(`/api/entry/${encodeURIComponent(teamId)}/event/${encodeURIComponent(gameweek)}/picks/`),
  };
}
