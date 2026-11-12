// Track editor: load a definition, select zones on the canvas, edit their
// successors / normal / thickness, and validate + save through the API.
import { api } from "./api.js";
import { makeProjector, drawTrack } from "./iso.js";

const state = { track: null, selected: null };

function setStatus(text, ok) {
  const node = document.getElementById("editor-status");
  node.textContent = text;
  node.className = "status " + (ok ? "ok" : "err");
}

function renderCanvas() {
  if (!state.track) return;
  const canvas = document.getElementById("editor-canvas");
  const ctx = canvas.getContext("2d");
  const project = makeProjector(canvas);
  drawTrack(ctx, project, state.track);
  if (state.selected) {
    const zone = state.track.zones.find((z) => z.id === state.selected);
    ctx.strokeStyle = "#ffd166";
    ctx.lineWidth = 3;
    ctx.beginPath();
    zone.points.map((p) => project(p)).forEach((pt, i) =>
      i ? ctx.lineTo(pt.x, pt.y) : ctx.moveTo(pt.x, pt.y));
    ctx.closePath();
    ctx.stroke();
  }
}

function renderZoneList() {
  const list = document.getElementById("zone-list");
  list.innerHTML = "";
  for (const z of state.track.zones) {
    const row = document.createElement("div");
    row.className = "zone-row" + (z.id === state.selected ? " selected" : "");
    const name = document.createElement("span");
    name.textContent = `${z.id} → [${z.successors.join(", ")}]`;
    row.appendChild(name);
    row.addEventListener("click", () => {
      state.selected = z.id;
      renderCanvas();
      renderZoneList();
      renderZoneEditor();
    });
    list.appendChild(row);
  }
}

function renderZoneEditor() {
  const box = document.getElementById("zone-editor");
  box.innerHTML = "";
  if (!state.selected) {
    box.textContent = "点击左侧区域进行编辑";
    return;
  }
  const zone = state.track.zones.find((z) => z.id === state.selected);
  const allIds = state.track.zones.map((z) => z.id);

  const thicknessLabel = document.createElement("label");
  thicknessLabel.textContent = "板厚 thickness";
  const thickness = document.createElement("input");
  thickness.type = "number";
  thickness.step = "0.1";
  thickness.value = zone.thickness;
  thickness.addEventListener("change", () => {
    zone.thickness = parseFloat(thickness.value) || 0.6;
  });
  box.append(thicknessLabel, thickness);

  const normalLabel = document.createElement("label");
  normalLabel.textContent = "有向法线 normal (x,y,z)";
  const normal = document.createElement("input");
  normal.value = zone.normal.join(",");
  normal.addEventListener("change", () => {
    zone.normal = normal.value.split(",").map((v) => parseFloat(v.trim()));
  });
  box.append(normalLabel, normal);

  const succLabel = document.createElement("label");
  succLabel.textContent = "合法后继 successors";
  box.appendChild(succLabel);
  for (const id of allIds) {
    if (id === zone.id) continue;
    const wrap = document.createElement("label");
    wrap.className = "chk";
    const chk = document.createElement("input");
    chk.type = "checkbox";
    chk.checked = zone.successors.includes(id);
    chk.addEventListener("change", () => {
      const set = new Set(zone.successors);
      chk.checked ? set.add(id) : set.delete(id);
      zone.successors = allIds.filter((x) => set.has(x));
      renderZoneList();
    });
    wrap.append(chk, document.createTextNode(id));
    box.appendChild(wrap);
  }
}

async function load() {
  const id = document.getElementById("editor-track").value;
  state.track = await api.getTrack(id);
  state.selected = state.track.zones[0]?.id ?? null;
  setStatus(`已加载 ${id}`, true);
  renderCanvas();
  renderZoneList();
  renderZoneEditor();
}

async function save() {
  if (!state.track) return;
  const id = document.getElementById("editor-track").value;
  try {
    const res = await api.saveTrack(id, state.track);
    setStatus(`校验通过，已保存 ${res.zones} 个区域`, true);
  } catch (err) {
    setStatus("保存失败: " + err.message, false);
  }
}

function canvasPick(event) {
  if (!state.track) return;
  const canvas = document.getElementById("editor-canvas");
  const rect = canvas.getBoundingClientRect();
  const mx = (event.clientX - rect.left) * (canvas.width / rect.width);
  const my = (event.clientY - rect.top) * (canvas.height / rect.height);
  const project = makeProjector(canvas);
  let best = null;
  let bestDist = 24;
  for (const z of state.track.zones) {
    const c = z.points.reduce((a, p) => [a[0] + p[0] / z.points.length,
                                         a[1] + p[1] / z.points.length,
                                         a[2] + p[2] / z.points.length], [0, 0, 0]);
    const pt = project(c);
    const d = Math.hypot(pt.x - mx, pt.y - my);
    if (d < bestDist) { bestDist = d; best = z.id; }
  }
  if (best) {
    state.selected = best;
    renderCanvas();
    renderZoneList();
    renderZoneEditor();
  }
}

export async function initEditorView() {
  const { tracks } = await api.listTracks();
  const sel = document.getElementById("editor-track");
  sel.innerHTML = "";
  for (const t of tracks) {
    const opt = document.createElement("option");
    opt.value = t; opt.textContent = t;
    sel.appendChild(opt);
  }
  document.getElementById("btn-load-track").addEventListener("click", load);
  document.getElementById("btn-save-track").addEventListener("click", save);
  document.getElementById("editor-canvas").addEventListener("click", canvasPick);
  await load();
}
