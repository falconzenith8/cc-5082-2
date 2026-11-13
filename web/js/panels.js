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

export function renderPenaltyLedger(container, snapshot, onPenaltyClick) {
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
      const canDrill = onPenaltyClick &&
        p.segment_index !== null && p.segment_index !== undefined;
      if (canDrill) {
        row.classList.add("clickable");
        row.title = "点击查看产生该判罚的原始轨迹片段";
      }
      row.appendChild(el("span", null, `${p.t.toFixed(2)}s ${p.source}`));
      row.appendChild(el("span", null,
        `+${p.seconds}s${p.zone_id ? " · " + p.zone_id : ""}` +
        `${canDrill ? ` · 片段#${p.segment_index} ›` : ""}`));
      if (canDrill) {
        row.addEventListener("click", () => onPenaltyClick(vid, p));
      }
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

// ------------------------------------------------------- diagnostic labels
const REASON_LABELS = {
  accept_forward: "正向有效穿越",
  reverse_cross: "逆向穿越",
  backside_not_expected: "背面穿越（非当前期望）",
  outside_gate: "交点在检查点多边形外",
  plane_miss_ignored: "平面外擦过（忽略）",
  start_line_reentry: "起终点重复穿越",
  gap_cross_invalidated: "断连期间穿越（无效）",
  long_segment: "长片段（疑似采样跨跃）",
  telemetry_gap: "遥测断连",
  vehicle_reset: "车辆重置（恢复标记）",
  race_start: "比赛开始",
};

const CATEGORY_LABELS = {
  geometric_cross_missed: "几何穿越遗漏",
  geometric_polygon_miss: "几何交点落在闸门外",
  state_inheritance_error: "状态继承错误",
  config_version_mismatch: "配置版本不匹配",
  unknown: "未知原因",
};

const CATEGORY_CLASSES = {
  geometric_cross_missed: "cat-geo",
  geometric_polygon_miss: "cat-geo",
  state_inheritance_error: "cat-state",
  config_version_mismatch: "cat-config",
  unknown: "cat-unknown",
};

export function reasonLabel(code) {
  return REASON_LABELS[code] || code;
}

export function categoryLabel(code) {
  return CATEGORY_LABELS[code] || code;
}

function fmtNum(v, digits = 3) {
  if (v === null || v === undefined) return "—";
  return Number(v).toFixed(digits);
}

function fmtPoint(p) {
  if (!p) return "—";
  return `(${p.map((c) => fmtNum(c, 2)).join(", ")})`;
}

function sideLabel(s) {
  return s > 0 ? "+1" : s < 0 ? "-1" : "0";
}

function dirLabel(d) {
  if (d === null || d === undefined) return "—";
  return d > 0 ? "正向" : d < 0 ? "逆向" : "贴平面";
}

export function renderAdvisory(container, advisory, changedFields) {
  container.innerHTML = "";
  if (!advisory) {
    container.style.display = "none";
    return;
  }
  container.style.display = "block";
  const banner = el("div", "advisory-banner");
  banner.appendChild(el("strong", null, "配置版本不匹配"));
  if (changedFields && changedFields.length) {
    banner.appendChild(el("span", null,
      `变化字段：${changedFields.join("、")}`));
  }
  banner.appendChild(el("span", null, advisory));
  banner.appendChild(el("span", null,
    "异常按配置漂移提示，不会判定为玩家作弊；轨迹保持原始数据不被改写。"));
  container.appendChild(banner);
}

// Per-zone intersection parameters, direction signs and successor sets of the
// raw segment that actually drove a decision.
export function renderSegmentDetail(container, seg, opts = {}) {
  container.innerHTML = "";
  if (!seg) {
    container.appendChild(el("div", "muted-note", "暂无片段数据"));
    return;
  }
  const head = el("div", "segment-head");
  head.appendChild(el("strong", null, `原始片段 #${seg.index}`));
  const flags = [];
  if (seg.flagged) flags.push("已标记");
  if (seg.long_segment) flags.push("长片段");
  if (seg.gap) flags.push(`断连 ${fmtNum(seg.gap_s, 2)}s`);
  if (seg.allow_time_gap) flags.push("间隔罚时已挂起");
  if (seg.reset_reason) flags.push(`重置:${seg.reset_reason}`);
  if (flags.length) head.appendChild(el("span", "tag fork", flags.join(" · ")));
  container.appendChild(head);

  const meta = el("div", "segment-meta");
  const metaRows = [
    ["时间", `${fmtNum(seg.t0, 3)}s → ${fmtNum(seg.t1, 3)}s`],
    ["第", `${seg.lap} 圈`],
    ["长度", `${fmtNum(seg.length, 2)} m`],
    ["起点 p0", fmtPoint(seg.p0)],
    ["终点 p1", fmtPoint(seg.p1)],
  ];
  for (const [k, v] of metaRows) {
    const r = el("div", "kv-row");
    r.appendChild(el("span", "kv-key", k));
    r.appendChild(el("span", "kv-val mono", v));
    meta.appendChild(r);
  }
  container.appendChild(meta);

  const succ = el("div", "segment-succ");
  const succRows = [
    ["片段前期望后继", seg.expected_before],
    ["片段后期望后继", seg.expected_after],
    ["片段前本圈已过", seg.passed_before],
    ["片段后本圈已过", seg.passed_after],
  ];
  for (const [k, list] of succRows) {
    const r = el("div", "kv-row");
    r.appendChild(el("span", "kv-key", k));
    r.appendChild(el("span", "kv-val", list.length ? list.join(", ") : "—"));
    succ.appendChild(r);
  }
  {
    const r = el("div", "kv-row");
    r.appendChild(el("span", "kv-key", "起点开放"));
    r.appendChild(el("span", "kv-val", seg.start_opened ? "是" : "否"));
    succ.appendChild(r);
  }
  container.appendChild(succ);

  const zHead = el("h3", null, "逐检查点求交参数");
  container.appendChild(zHead);
  for (const z of seg.zones) {
    const card = el("div", `zone-card${z.route_relevant ? " relevant" : ""}`);
    const title = el("div", "zone-card-title");
    title.appendChild(el("span", "mono", z.zone_id));
    const ztags = [];
    if (z.crossed) ztags.push(`穿越·${dirLabel(z.direction)}`);
    else ztags.push("未穿越");
    if (z.inside) ztags.push("多边形内");
    if (z.near_gate) ztags.push("贴近闸门");
    if (z.edge_contact) ztags.push("边缘接触");
    title.appendChild(el("span", "tag idle", ztags.join(" · ")));
    card.appendChild(title);

    const grid = el("div", "zone-grid");
    const cells = [
      ["符号距离 d(p0)", fmtNum(z.signed_a)],
      ["符号距离 d(p1)", fmtNum(z.signed_b)],
      ["侧别 s(p0)/s(p1)", `${sideLabel(z.side_a)} / ${sideLabel(z.side_b)}`],
      ["交点参数 t", fmtNum(z.segment_t, 4)],
      ["交点 hit", fmtPoint(z.hit)],
      ["多边形内", z.inside ? "是" : "否"],
      ["离边缘距离", fmtNum(z.edge_distance)],
      ["路径相关", z.route_relevant ? "是" : "否"],
    ];
    for (const [k, v] of cells) {
      const c = el("div", "zone-cell");
      c.appendChild(el("div", "cell-key", k));
      c.appendChild(el("div", "cell-val mono", String(v)));
      grid.appendChild(c);
    }
    card.appendChild(grid);
    container.appendChild(card);
  }

  if (seg.decisions.length) {
    container.appendChild(el("h3", null, "判定决策"));
    for (const d of seg.decisions) {
      const r = el("div", `decision-row dec-${d.reason}`);
      r.appendChild(el("span", "mono", reasonLabel(d.reason)));
      r.appendChild(el("span", null,
        `${d.zone_id || "—"} · ${dirLabel(d.direction)}` +
        `${d.penalty_index !== null && d.penalty_index !== undefined
          ? ` · 判罚#${d.penalty_index}` : ""}`));
      container.appendChild(r);
    }
  }

  if (seg.penalties && seg.penalties.length) {
    container.appendChild(el("h3", null, "本片段产生的判罚"));
    for (const p of seg.penalties) {
      const r = el("div", `decision-row pen-${p.source}`);
      r.appendChild(el("span", null, reasonLabel(p.source) || p.source));
      r.appendChild(el("span", "mono",
        `+${p.seconds}s${p.zone_id ? " · " + p.zone_id : ""}`));
      container.appendChild(r);
    }
  }
}

export function renderBoundedSummary(container, summary, onSegmentClick) {
  container.innerHTML = "";
  if (!summary) return;
  const ret = summary.retention;
  const retRow = el("div", "retention-row");
  retRow.appendChild(el("span", null,
    `片段 ${ret.retained_segments}/${ret.total_segments} 保留` +
    `${ret.dropped_plain_segments ? `（丢弃 ${ret.dropped_plain_segments} 个普通片段）` : ""}`));
  retRow.appendChild(el("span", "muted-note",
    "有界保留 · 丢弃片段可按需从原始遥测重算"));
  container.appendChild(retRow);

  if (Object.keys(summary.reason_totals).length) {
    const r = el("div", "reason-totals");
    for (const [reason, n] of Object.entries(summary.reason_totals)) {
      const pill = el("span", "succ-pill", `${reasonLabel(reason)} ×${n}`);
      r.appendChild(pill);
    }
    container.appendChild(r);
  }

  if (summary.lap_summaries && summary.lap_summaries.length) {
    container.appendChild(el("h3", null, "分圈摘要（长比赛始终保留）"));
    for (const lap of summary.lap_summaries) {
      const row = el("div", "lap-summary-row");
      row.appendChild(el("span", "mono",
        `第${lap.lap}圈 · 片段[${lap.segment_range[0]}–${lap.segment_range[1]}]`));
      row.appendChild(el("span", null,
        `有效穿越 ${lap.valid_crossings} · 无效决策 ${lap.invalid_decisions}` +
        ` · 判罚 ${fmtNum(lap.penalty_seconds, 2)}s` +
        `${lap.resets ? ` · 重置 ${lap.resets}` : ""}`));
      container.appendChild(row);
    }
  }

  if (summary.recovery_markers && summary.recovery_markers.length) {
    container.appendChild(el("h3", null, "恢复标记"));
    for (const m of summary.recovery_markers) {
      const row = el("div", "recovery-row");
      row.style.cursor = onSegmentClick ? "pointer" : "default";
      row.appendChild(el("span", "mono",
        `${fmtNum(m.t, 2)}s · 片段#${m.segment_index}`));
      row.appendChild(el("span", null,
        `${m.reason}${m.zone_id ? " · " + m.zone_id : ""} · ${fmtPoint(m.position)}`));
      if (onSegmentClick) {
        row.addEventListener("click", () => onSegmentClick(m.segment_index));
      }
      container.appendChild(row);
    }
  }

  if (summary.flagged_segment_indices &&
      summary.flagged_segment_indices.length) {
    container.appendChild(el("h3", null, "已标记片段（点击展开原始数据）"));
    const chips = el("div", "flag-chips");
    for (const idx of summary.flagged_segment_indices) {
      const chip = el("button", "secondary chip", `#${idx}`);
      if (onSegmentClick) {
        chip.addEventListener("click", () => onSegmentClick(idx));
      }
      chips.appendChild(chip);
    }
    container.appendChild(chips);
  }
}

export function renderDivergenceList(container, divergences, onDivergenceClick,
                                     selectedOrder) {
  container.innerHTML = "";
  if (!divergences || !divergences.length) {
    container.appendChild(el("div", "muted-note good-note",
      "所有采样间隔判定一致，未发现分歧。"));
    return;
  }
  for (const d of divergences) {
    const row = el("div",
      `divergence-row ${CATEGORY_CLASSES[d.category] || ""}` +
      `${d.order === selectedOrder ? " selected" : ""}`);
    row.style.cursor = onDivergenceClick ? "pointer" : "default";
    const top = el("div", "divergence-top");
    top.appendChild(el("span", `tag ${CATEGORY_CLASSES[d.category] || "idle"}`,
      categoryLabel(d.category)));
    top.appendChild(el("span", "mono",
      `${d.zone_id || "—"} · 第${d.lap}圈` +
      `${d.t !== null && d.t !== undefined ? " · " + fmtNum(d.t, 2) + "s" : ""}`));
    top.appendChild(el("span", "muted-note",
      `首次分歧间隔 ×${d.first_diverging_stride}` +
      ` · 影响 [${d.strides_affected.join(",")}]`));
    row.appendChild(top);
    row.appendChild(el("div", "divergence-detail", d.detail));
    if (d.caused_by) {
      const link = el("div", "caused-by");
      link.textContent =
        `根因 → ${categoryLabel(d.caused_by.category)} @ ${d.caused_by.zone_id}` +
        `（片段#${d.caused_by.segment_index}）`;
      row.appendChild(link);
    }
    if (d.advisory) {
      row.appendChild(el("div", "advisory-inline", d.advisory));
    }
    if (onDivergenceClick) {
      row.addEventListener("click", () => onDivergenceClick(d));
    }
    container.appendChild(row);
  }
}

export function renderStrideRuns(container, runs, reference) {
  container.innerHTML = "";
  if (reference) {
    const ref = el("div", "stride-row stride-reference");
    ref.appendChild(el("span", "mono", "参考 ×1（全采样）"));
    ref.appendChild(el("span", null,
      `圈数 ${reference.laps} · 有效穿越 ${reference.valid_crossings}`));
    container.appendChild(ref);
  }
  for (const run of runs || []) {
    const row = el("div", "stride-row");
    row.appendChild(el("span", "mono", `采样间隔 ×${run.stride}`));
    row.appendChild(el("span", null,
      `${run.sample_count} 样本 · 圈数 ${run.laps} · 有效穿越 ` +
      `${run.valid_crossings}${run.finished ? " · 完赛" : " · 未完赛"}`));
    container.appendChild(row);
  }
}
