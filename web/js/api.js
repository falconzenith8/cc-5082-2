// Thin JSON wrapper around the stdlib HTTP API.
async function request(method, path, body) {
  const opts = { method, headers: { "Content-Type": "application/json" } };
  if (body !== undefined) opts.body = JSON.stringify(body);
  const res = await fetch(path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

export const api = {
  listTracks: () => request("GET", "/api/tracks"),
  getTrack: (id) => request("GET", `/api/tracks/${id}`),
  saveTrack: (id, def) => request("PUT", `/api/tracks/${id}`, def),
  scenarios: () => request("GET", "/api/scenarios"),
  runScenario: (track_id, scenario) =>
    request("POST", "/api/races/run", { track_id, scenario }),
  createSession: (track_id, vehicles) =>
    request("POST", "/api/sessions", { track_id, vehicles }),
  getSession: (sid) => request("GET", `/api/sessions/${sid}`),
  step: (sid, controls) =>
    request("POST", `/api/sessions/${sid}/step`, { controls }),
  reset: (sid, vehicle_id, reason) =>
    request("POST", `/api/sessions/${sid}/reset`, { vehicle_id, reason }),
  verify: (replay) => request("POST", "/api/replays/verify", replay),
};
