// DOM panel renderers for eligibility, penalties, timing and evidence.

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined) node.textContent = text;
  return node;
}

const VEHICLE_COLORS = { V1: "#ff6b6b", VL: "#ff6b6b", VR: "#4dd0e1" };

export function vehicleColor(vid) {
  return VEHICLE_COLORS[vid] || "#ffd166";
}

export function renderEligibility(container, snapshot) {
  container.innerHTML = "";
  for (const [vid, d] of Object.entries(snapshot.vehicles)) {
    const s = d.summary;
    const wrap = el("div", null);
    wrap.appendChild(el("div", "elig-row",
      `${vid} · 第 ${s.lap}/${s.total_laps} 圈`));
    const expected = new Set(s.expected_next);
    const passed = new Set(s.passed_this_lap);
    for (const row of s.expected_next) {
      const tag = expected.has(row) ? "expected" : "idle";
      const r = el("div", "elig-row");
      r.appendChild(el("span", null, "下一合法区域"));
      r.appendChild(el("span", `tag ${tag}`, row));
      wrap.appendChild(r);
    }
    const pr = el("div", "elig-row");
    pr.appendChild(el("span", null, "本圈已通过"));
    pr.appendChild(el("span", null,
      passed.size ? [...passed].join(", ") : "—"));
    wrap.appendChild(pr);
    container.appendChild(wrap);
  }
}

export function renderPenaltyLedger(container, snapshot) {
  container.innerHTML = "";
  for (const [vid, d] of Object.entries(snapshot.vehicles)) {
    const wrap = el("div", null);
    wrap.appendChild(el("div", "ledger-row",
      `${vid} · 累计判罚 ${d.summary.penalty_s}s`));
    if (!d.penalties.length) {
      wrap.appendChild(el("div", "ledger-row", "无判罚"));
    }
    for (const p of d.penalties.slice(-12)) {
      const row = el("div", `ledger-row pen-${p.source}`);
      row.appendChild(el("span", null, `${p.t.toFixed(2)}s ${p.source}`));
      row.appendChild(el("span", null,
        `+${p.seconds}s${p.zone_id ? " · " + p.zone_id : ""}`));
      wrap.appendChild(row);
    }
    container.appendChild(wrap);
  }
}

export function renderTimeRecords(container, snapshot) {
  container.innerHTML = "";
  for (const [vid, d] of Object.entries(snapshot.vehicles)) {
    const s = d.summary;
    const wrap = el("div", null);
    const rows = [
      ["有效行驶时间", `${s.valid_driving_s}s`],
      ["判罚时间", `${s.penalty_s}s`],
      ["断连时长", `${s.gap_s}s`],
      ["总计（行驶+判罚）", `${s.total_time_s}s`],
      ["完成时刻", s.finish_t === null ? "未完赛" : `${s.finish_t}s`],
    ];
    wrap.appendChild(el("div", "time-row", vid));
    for (const [k, v] of rows) {
      const r = el("div", "time-row");
      r.appendChild(el("span", null, k));
      r.appendChild(el("span", null, v));
      wrap.appendChild(r);
    }
    container.appendChild(wrap);
  }
}

export function renderEventStream(container, snapshot) {
  container.innerHTML = "";
  for (const [vid, d] of Object.entries(snapshot.vehicles)) {
    const head = el("div", "ev", `车辆 ${vid}`);
    head.style.color = "#8a97a8";
    container.appendChild(head);
    for (const e of [...d.evidence].reverse().slice(0, 40)) {
      const row = el("div", `ev ${e.valid ? "valid" : "invalid"}`);
      row.appendChild(el("span", "t", `${e.t.toFixed(2)}s`));
      row.appendChild(el("span", null,
        `${e.zone_id} ${e.direction > 0 ? "正向" : "逆向"} ` +
        `${e.valid ? "有效推进" : "无效:" + (e.invalidation || "越界")}`));
      container.appendChild(row);
    }
  }
}

export function renderRanking(container, snapshot) {
  if (!snapshot.ranking || !snapshot.ranking.length) return;
  container.innerHTML = "";
  for (const r of snapshot.ranking) {
    const row = el("div", "ledger-row");
    row.appendChild(el("span", null,
      `#${r.rank ?? "-"} ${r.vehicle_id} [${r.status}]`));
    row.appendChild(el("span", null,
      `总 ${r.total_time_s}s (行驶 ${r.valid_driving_s}s + 罚 ${r.penalty_s}s)`));
    container.appendChild(row);
  }
}
