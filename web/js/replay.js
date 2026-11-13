// Replay viewer: rebuild a race from raw telemetry, scrub frame-by-frame and
// show the independent re-judge verification against the stored result.
// The diagnostic panels explain, from the raw segments, why a coarse replay
// loses laps or flags a legal fork; positions are only selected, never
// rewritten, and config drift is warned about rather than called cheating.
import { api } from "./api.js";
import { makeProjector, drawTrack, drawTrajectory, drawVehicles,
         drawEvidenceMarkers, drawHighlightedSegment } from "./iso.js";
import { vehicleColor, renderAdvisory, renderBoundedSummary,
         renderSegmentDetail, renderDivergenceList, renderStrideRuns }
  from "./panels.js";

const state = {
  replay: null,
  track: null,
  frame: 0,
  maxFrame: 0,
  timer: null,
  verify: null,
  diagnostics: null,
  selectedSegment: null,
  selectedOrder: null,
};

function primaryVehicle() {
  return state.replay.vehicles[0];
}

function render() {
  if (!state.track) return;
  const canvas = document.getElementById("replay-canvas");
  const ctx = canvas.getContext("2d");
  const project = makeProjector(canvas);
  drawTrack(ctx, project, state.track);

  for (const vid of state.replay.vehicles) {
    const samples = state.replay.telemetry[vid];
    const upto = Math.min(state.frame + 1, samples.length);
    drawTrajectory(ctx, project, samples, vehicleColor(vid), upto);
    if (upto > 0) {
      drawVehicles(ctx, project,
        { [vid]: { pos: samples[upto - 1].pos, color: vehicleColor(vid) } });
    }
  }

  // markers from independent verification evidence
  if (state.verify) {
    for (const entry of Object.values(state.verify.vehicles)) {
      const visible = entry.replayed.evidence
        .filter((e) => e.t <= currentTime());
      drawEvidenceMarkers(ctx, project, visible);
    }
  }
  if (state.selectedSegment) {
    drawHighlightedSegment(ctx, project, state.selectedSegment);
  }
  document.getElementById("replay-frame").textContent =
    `帧 ${state.frame}/${state.maxFrame} · t=${currentTime().toFixed(2)}s`;
}

function currentTime() {
  const vid = primaryVehicle();
  const samples = state.replay.telemetry[vid];
  return samples[Math.min(state.frame, samples.length - 1)]?.t ?? 0;
}

function currentTrackId() {
  return document.getElementById("replay-current-track").value || undefined;
}

function renderVerify() {
  const box = document.getElementById("replay-verify");
  box.innerHTML = "";
  if (!state.verify) return;
  const head = document.createElement("div");
  head.className = "ledger-row";
  head.textContent = state.verify.matches
    ? "回放核验一致 ✓"
    : "回放核验不一致 ✗";
  head.style.color = state.verify.matches ? "#37d399" : "#ff6b6b";
  box.appendChild(head);

  const cfg = state.verify.config;
  if (cfg && !cfg.config_match) {
    renderAdvisory(
      document.getElementById("replay-advisory"),
      cfg.config_advisory, cfg.config_changed_fields);
  } else {
    renderAdvisory(document.getElementById("replay-advisory"), null);
  }

  const evBox = document.getElementById("replay-evidence");
  evBox.innerHTML = "";
  for (const [vid, entry] of Object.entries(state.verify.vehicles)) {
    const r = entry.replayed;
    const summary = document.createElement("div");
    summary.className = "ev";
    summary.innerHTML =
      `<span class="t">${vid}</span><span>圈数 ${r.laps} · 有效穿越 ` +
      `${r.valid_crossings} · 判罚 ${r.penalty_s}s · 总 ${r.total_time_s}s</span>`;
    box.appendChild(summary);
    for (const e of [...r.evidence].reverse().slice(0, 40)) {
      const row = document.createElement("div");
      row.className = `ev ${e.valid ? "valid" : "invalid"}`;
      row.innerHTML =
        `<span class="t">${e.t.toFixed(2)}s</span><span>${e.zone_id} ` +
        `${e.direction > 0 ? "正向" : "逆向"} ` +
        `${e.valid ? "有效" : "无效:" + (e.invalidation || "越界")}</span>`;
      evBox.appendChild(row);
    }
  }
}

function renderDiagnostics() {
  if (!state.diagnostics) return;
  const report = state.diagnostics;
  renderAdvisory(
    document.getElementById("replay-advisory"),
    report.config_advisory, report.config_changed_fields);

  const v = report.vehicles[primaryVehicle()];
  renderStrideRuns(
    document.getElementById("replay-strides"), v.runs, v.reference);
  renderDivergenceList(
    document.getElementById("replay-divergences"), v.divergences,
    onDivergenceClick, state.selectedOrder);
  renderBoundedSummary(
    document.getElementById("replay-diag-summary"), v.summary,
    (idx) => showSegment(idx, 1));
  renderSegmentDetail(
    document.getElementById("replay-segment-detail"), state.selectedSegment);

  const catBox = document.getElementById("replay-strides");
  if (report.divergence_total > 0) {
    const note = document.createElement("div");
    note.className = "muted-note";
    note.textContent =
      `分歧合计 ${report.divergence_total}：` +
      Object.entries(report.divergence_categories)
        .map(([k, n]) => `${k} ×${n}`).join("，");
    catBox.appendChild(note);
  }
  const sampling = document.createElement("div");
  sampling.className = "muted-note";
  sampling.textContent = report.sampling_note;
  catBox.appendChild(sampling);
}

async function onDivergenceClick(d) {
  state.selectedOrder = d.order;
  // the coarse segment at the first diverging stride shows the actual miss
  const stride = d.first_diverging_stride;
  const per = (d.per_stride || []).find((p) => p.stride === stride);
  const coarseIndex = per ? per.coarse_segment_index : null;
  const index = coarseIndex ?? d.reference_segment_index;
  await showSegment(index, stride);
  renderDivergenceList(
    document.getElementById("replay-divergences"),
    state.diagnostics.vehicles[primaryVehicle()].divergences,
    onDivergenceClick, state.selectedOrder);
}

async function showSegment(index, stride = 1) {
  if (index === null || index === undefined) return;
  try {
    const out = await api.replaySegment(
      state.replay, primaryVehicle(), index, stride);
    state.selectedSegment = out.segment;
    render();
    renderSegmentDetail(
      document.getElementById("replay-segment-detail"), out.segment);
  } catch (err) {
    const box = document.getElementById("replay-segment-detail");
    box.innerHTML = "";
    box.appendChild(Object.assign(document.createElement("div"),
      { className: "muted-note bad-note",
        textContent: `片段 #${index} 无法重算：${err.message}` }));
  }
}

async function loadReplay() {
  stop();
  const trackId = document.getElementById("replay-track").value;
  const scenario = document.getElementById("replay-scenario").value;
  const { replay } = await api.runScenario(trackId, scenario);
  await adoptReplay(replay);
}

async function loadDiagnosticCase() {
  stop();
  const sel = document.getElementById("replay-case");
  if (!sel.value) return;
  const replay = await api.diagnosticCase(sel.value);
  await adoptReplay(replay);
}

async function adoptReplay(replay) {
  state.replay = replay;
  state.track = replay.track;
  state.diagnostics = null;
  state.selectedSegment = null;
  state.selectedOrder = null;
  state.verify = await api.verify(replay, currentTrackId());
  state.maxFrame = Math.max(
    ...replay.vehicles.map((v) => replay.telemetry[v].length - 1));
  state.frame = 0;
  const scrub = document.getElementById("replay-scrub");
  scrub.max = state.maxFrame;
  scrub.value = 0;
  document.getElementById("replay-divergences").innerHTML = "";
  document.getElementById("replay-diag-summary").innerHTML = "";
  document.getElementById("replay-segment-detail").innerHTML = "";
  document.getElementById("replay-strides").innerHTML = "";
  renderVerify();
  render();
}

async function runDiagnostics() {
  if (!state.replay) return;
  const opts = { strides: [1, 2, 4, 8] };
  const trackId = currentTrackId();
  if (trackId) opts.current_track_id = trackId;
  state.diagnostics = await api.replayDiagnostics(state.replay, opts);
  state.selectedSegment = null;
  state.selectedOrder = null;
  renderDiagnostics();
  render();
}

async function refreshCaseSelect() {
  const sel = document.getElementById("replay-case");
  sel.innerHTML = "";
  const none = document.createElement("option");
  none.value = ""; none.textContent = "（选择已提交失败样例）";
  sel.appendChild(none);
  try {
    const { cases } = await api.diagnosticCases();
    for (const c of cases) {
      const opt = document.createElement("option");
      opt.value = c.replay_id;
      opt.textContent = `${c.replay_id}（${c.description || c.expected_category}）`;
      sel.appendChild(opt);
    }
  } catch {
    // diagnostic samples are optional in standalone deployments
  }
}

function stop() {
  if (state.timer) {
    clearInterval(state.timer);
    state.timer = null;
  }
}

function play() {
  stop();
  state.timer = setInterval(() => {
    if (state.frame >= state.maxFrame) { stop(); return; }
    state.frame += 1;
    document.getElementById("replay-scrub").value = state.frame;
    render();
  }, 40);
}

export async function initReplayView() {
  await refreshCaseSelect();
  document.getElementById("btn-load-replay")
    .addEventListener("click", loadReplay);
  document.getElementById("btn-load-case")
    .addEventListener("click", loadDiagnosticCase);
  document.getElementById("btn-run-diagnostics")
    .addEventListener("click", runDiagnostics);
  document.getElementById("replay-current-track")
    .addEventListener("change", async () => {
      if (state.replay) {
        state.verify = await api.verify(state.replay, currentTrackId());
        renderVerify();
      }
    });
  document.getElementById("btn-play").addEventListener("click", play);
  document.getElementById("btn-pause").addEventListener("click", stop);
  document.getElementById("replay-scrub").addEventListener("input", (e) => {
    stop();
    state.frame = parseInt(e.target.value, 10);
    render();
  });
}
