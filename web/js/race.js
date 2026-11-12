// Live race view: run built-in scenarios or drive a manual session.
import { api } from "./api.js";
import { makeProjector, drawTrack, drawVehicles, drawTrajectory,
         drawEvidenceMarkers } from "./iso.js";
import { vehicleColor, renderEligibility, renderPenaltyLedger,
         renderTimeRecords, renderEventStream, renderRanking } from "./panels.js";

const state = {
  track: null,
  snapshot: null,
  sessionId: null,
  timer: null,
};

async function refreshTrackSelectors() {
  const { tracks } = await api.listTracks();
  const { scenarios } = await api.scenarios();
  for (const id of ["race-track", "replay-track"]) {
    const sel = document.getElementById(id);
    sel.innerHTML = "";
    for (const t of tracks) {
      const opt = document.createElement("option");
      opt.value = t; opt.textContent = t;
      sel.appendChild(opt);
    }
  }
  const sc = document.getElementById("race-scenario");
  sc.innerHTML = "";
  for (const name of scenarios) {
    const opt = document.createElement("option");
    opt.value = name; opt.textContent = name;
    sc.appendChild(opt);
  }
  const rsc = document.getElementById("replay-scenario");
  rsc.innerHTML = "";
  for (const name of scenarios) {
    const opt = document.createElement("option");
    opt.value = name; opt.textContent = name;
    rsc.appendChild(opt);
  }
}

function render() {
  if (!state.track || !state.snapshot) return;
  const canvas = document.getElementById("race-canvas");
  const ctx = canvas.getContext("2d");
  const project = makeProjector(canvas);
  drawTrack(ctx, project, state.track);

  for (const [vid, d] of Object.entries(state.snapshot.vehicles)) {
    drawVehicles(ctx, project,
      { [vid]: { state: d.state, color: vehicleColor(vid) } });
  }
  renderEligibility(document.getElementById("eligibility"), state.snapshot);
  renderPenaltyLedger(document.getElementById("penalty-ledger"), state.snapshot);
  renderTimeRecords(document.getElementById("time-records"), state.snapshot);
  renderEventStream(document.getElementById("event-stream"), state.snapshot);
}

async function runScenario() {
  stopAuto();
  const trackId = document.getElementById("race-track").value;
  const scenario = document.getElementById("race-scenario").value;
  const { snapshot, replay } = await api.runScenario(trackId, scenario);
  state.track = replay.track;
  state.snapshot = snapshot;
  state.sessionId = null;
  document.getElementById("btn-step").disabled = true;
  document.getElementById("btn-reset").disabled = true;
  render();
}

async function newSession() {
  stopAuto();
  const trackId = document.getElementById("race-track").value;
  const { session_id, snapshot } = await api.createSession(trackId, ["V1"]);
  state.sessionId = session_id;
  state.track = await api.getTrack(trackId);
  state.snapshot = snapshot;
  document.getElementById("btn-step").disabled = false;
  document.getElementById("btn-reset").disabled = false;
  render();
}

async function step() {
  if (!state.sessionId) return;
  // simple forward control each tick; steering kept neutral
  state.snapshot = await api.step(state.sessionId,
    { V1: { throttle: 1.0, steer: 0.0 } });
  render();
  if (state.snapshot.all_finished) stopAuto();
}

async function resetVehicle() {
  if (!state.sessionId) return;
  state.snapshot = await api.reset(state.sessionId, "V1", "manual_reset");
  render();
}

function stopAuto() {
  if (state.timer) {
    clearInterval(state.timer);
    state.timer = null;
  }
}

export async function initRaceView() {
  await refreshTrackSelectors();
  document.getElementById("btn-run-scenario")
    .addEventListener("click", runScenario);
  document.getElementById("btn-new-session")
    .addEventListener("click", newSession);
  document.getElementById("btn-step").addEventListener("click", step);
  document.getElementById("btn-reset").addEventListener("click", resetVehicle);
  await runScenario();
}
