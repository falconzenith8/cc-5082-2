// Live race view: run built-in scenarios or drive a manual session.
// Lap/penalty and the trajectory position always come from the judge's actual
// computation; anomalies are surfaced through the diagnostic panels, and every
// penalty row can be expanded to the raw segment that produced it.
import { api } from "./api.js";
import { makeProjector, drawTrack, drawVehicles,
         drawHighlightedSegment } from "./iso.js";
import { vehicleColor, renderEligibility, renderPenaltyLedger,
         renderTimeRecords, renderEventStream, renderRanking,
         renderAdvisory, renderBoundedSummary, renderSegmentDetail }
  from "./panels.js";

const state = {
  track: null,
  snapshot: null,
  sessionId: null,
  timer: null,
  selectedSegment: null,
};

async function refreshTrackSelectors() {
  const { tracks } = await api.listTracks();
  const { scenarios } = await api.scenarios();
  for (const id of ["race-track", "replay-track", "replay-current-track"]) {
    const sel = document.getElementById(id);
    sel.innerHTML = "";
    const none = document.createElement("option");
    none.value = ""; none.textContent = "（使用记录内嵌配置）";
    sel.appendChild(none);
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

function renderCanvas() {
  if (!state.track || !state.snapshot) return;
  const canvas = document.getElementById("race-canvas");
  const ctx = canvas.getContext("2d");
  const project = makeProjector(canvas);
  drawTrack(ctx, project, state.track);

  for (const [vid, d] of Object.entries(state.snapshot.vehicles)) {
    drawVehicles(ctx, project,
      { [vid]: { state: d.state, color: vehicleColor(vid) } });
  }
  if (state.selectedSegment) {
    drawHighlightedSegment(ctx, project, state.selectedSegment);
  }
}

function renderPanels() {
  if (!state.snapshot) return;
  renderEligibility(document.getElementById("eligibility"), state.snapshot);
  renderPenaltyLedger(
    document.getElementById("penalty-ledger"), state.snapshot,
    state.sessionId ? onPenaltyClick : null,
  );
  renderTimeRecords(document.getElementById("time-records"), state.snapshot);
  renderRanking(document.getElementById("race-ranking"), state.snapshot);
  renderEventStream(document.getElementById("event-stream"), state.snapshot);

  // bounded diagnostic summary rides along in every snapshot. A live session
  // always judges under its own embedded config, so no version advisory applies.
  const firstVid = Object.keys(state.snapshot.vehicles)[0];
  const diag = state.snapshot.vehicles[firstVid]?.diagnostics;
  renderAdvisory(document.getElementById("race-advisory"), null);
  renderBoundedSummary(
    document.getElementById("race-diag-summary"), diag,
    state.sessionId ? ((idx) => showSegment(firstVid, idx)) : null,
  );
  renderSegmentDetail(
    document.getElementById("race-segment-detail"), state.selectedSegment);
}

function render() {
  renderCanvas();
  renderPanels();
}

async function onPenaltyClick(vid, penalty) {
  if (penalty.segment_index === null ||
      penalty.segment_index === undefined) return;
  await showSegment(vid, penalty.segment_index);
}

async function showSegment(vid, index) {
  if (!state.sessionId) {
    document.getElementById("race-segment-detail").textContent =
      "内置场景快照不保留在线会话；请新建手动会话以展开原始片段。";
    state.selectedSegment = null;
    renderCanvas();
    return;
  }
  try {
    const out = await api.sessionDiagnostics(state.sessionId, vid, index);
    state.selectedSegment = out.segment;
    render();
    if (out.recomputed) {
      const box = document.getElementById("race-segment-detail");
      const note = Object.assign(document.createElement("div"),
        { className: "muted-note",
          textContent: "该片段已按有界策略丢弃，此为从原始遥测按需重算的结果。" });
      box.prepend(note);
    }
  } catch (err) {
    const box = document.getElementById("race-segment-detail");
    box.innerHTML = "";
    box.appendChild(Object.assign(document.createElement("div"),
      { className: "muted-note bad-note",
        textContent: `片段 #${index} 无法展开：${err.message}` }));
  }
}

async function runScenario() {
  stopAuto();
  const trackId = document.getElementById("race-track").value;
  const scenario = document.getElementById("race-scenario").value;
  const { snapshot, replay } = await api.runScenario(trackId, scenario);
  state.track = replay.track;
  state.snapshot = snapshot;
  state.sessionId = null;
  state.selectedSegment = null;
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
  state.selectedSegment = null;
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
