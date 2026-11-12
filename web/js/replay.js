// Replay viewer: rebuild a race from raw telemetry, scrub frame-by-frame and
// show the independent re-judge verification against the stored result.
import { api } from "./api.js";
import { makeProjector, drawTrack, drawTrajectory, drawVehicles,
         drawEvidenceMarkers } from "./iso.js";
import { vehicleColor } from "./panels.js";

const state = {
  replay: null,
  track: null,
  frame: 0,
  maxFrame: 0,
  timer: null,
  verify: null,
};

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
  document.getElementById("replay-frame").textContent =
    `帧 ${state.frame}/${state.maxFrame} · t=${currentTime().toFixed(2)}s`;
}

function currentTime() {
  const vid = state.replay.vehicles[0];
  const samples = state.replay.telemetry[vid];
  return samples[Math.min(state.frame, samples.length - 1)]?.t ?? 0;
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

async function loadReplay() {
  stop();
  const trackId = document.getElementById("replay-track").value;
  const scenario = document.getElementById("replay-scenario").value;
  const { replay } = await api.runScenario(trackId, scenario);
  state.replay = replay;
  state.track = replay.track;
  state.verify = await api.verify(replay);
  state.maxFrame = Math.max(
    ...replay.vehicles.map((v) => replay.telemetry[v].length - 1));
  state.frame = 0;
  const scrub = document.getElementById("replay-scrub");
  scrub.max = state.maxFrame;
  scrub.value = 0;
  renderVerify();
  render();
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
  document.getElementById("btn-load-replay")
    .addEventListener("click", loadReplay);
  document.getElementById("btn-play").addEventListener("click", play);
  document.getElementById("btn-pause").addEventListener("click", stop);
  document.getElementById("replay-scrub").addEventListener("input", (e) => {
    stop();
    state.frame = parseInt(e.target.value, 10);
    render();
  });
}
