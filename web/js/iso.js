// Zero-dependency Canvas 2D renderer. World coords are (x=east, y=up,
// z=north); an isometric projection maps them onto the 2D canvas.

const SCALE = 6.2;

export function makeProjector(canvas) {
  const cx = canvas.width / 2;
  const cy = canvas.height / 2 + 30;
  // iso: east -> down-right, north -> up-right, up -> straight up
  return function project(p) {
    return {
      x: cx + (p[0] - p[2]) * SCALE * 0.866,
      y: cy + (p[0] + p[2]) * SCALE * 0.5 - (p[1] || 0) * SCALE,
    };
  };
}

function zoneColor(zid) {
  if (zid.startsWith("fork")) return "#c08cff";
  if (zid.startsWith("merge")) return "#4dd0e1";
  if (zid === "start") return "#37d399";
  return "#4da3ff";
}

export function drawTrack(ctx, project, track) {
  ctx.clearRect(0, 0, ctx.canvas.width, ctx.canvas.height);
  // ground grid
  ctx.strokeStyle = "#18202b";
  ctx.lineWidth = 1;
  for (let g = -80; g <= 80; g += 10) {
    const a = project([g, 0, -80]);
    const b = project([g, 0, 80]);
    const c = project([-80, 0, g]);
    const d = project([80, 0, g]);
    ctx.beginPath();
    ctx.moveTo(a.x, a.y); ctx.lineTo(b.x, b.y);
    ctx.moveTo(c.x, c.y); ctx.lineTo(d.x, d.y);
    ctx.stroke();
  }

  // zones as translucent slabs with a normal arrow
  for (const z of track.zones) {
    const poly = z.points.map(project);
    ctx.beginPath();
    poly.forEach((pt, i) => (i ? ctx.lineTo(pt.x, pt.y) : ctx.moveTo(pt.x, pt.y)));
    ctx.closePath();
    ctx.fillStyle = zoneColor(z.id) + "33";
    ctx.fill();
    ctx.strokeStyle = zoneColor(z.id);
    ctx.lineWidth = 2;
    ctx.stroke();

    // label + normal direction
    const avg = poly.reduce((acc, p) => ({ x: acc.x + p.x / poly.length,
                                           y: acc.y + p.y / poly.length }),
                            { x: 0, y: 0 });
    ctx.fillStyle = "#e6ebf2";
    ctx.font = "11px monospace";
    ctx.textAlign = "center";
    ctx.fillText(z.id, avg.x, avg.y - 4);

    const center = z.points.reduce((a, p) => [a[0] + p[0] / z.points.length,
                                              a[1] + p[1] / z.points.length,
                                              a[2] + p[2] / z.points.length],
                                   [0, 0, 0]);
    const n = z.normal;
    const p0 = project(center);
    const p1 = project([center[0] + n[0] * 4, center[1], center[2] + n[2] * 4]);
    ctx.strokeStyle = "#ffd166";
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.moveTo(p0.x, p0.y);
    ctx.lineTo(p1.x, p1.y);
    ctx.stroke();
  }
}

export function drawVehicles(ctx, project, vehicles) {
  for (const [vid, v] of Object.entries(vehicles)) {
    const pos = v.state ? v.state.pos : v.pos;
    const pt = project(pos);
    const color = v.color || "#ff6b6b";
    ctx.beginPath();
    ctx.arc(pt.x, pt.y, 6, 0, Math.PI * 2);
    ctx.fillStyle = color;
    ctx.fill();
    ctx.strokeStyle = "#fff";
    ctx.lineWidth = 1.5;
    ctx.stroke();
    ctx.fillStyle = "#fff";
    ctx.font = "bold 11px monospace";
    ctx.textAlign = "left";
    ctx.fillText(vid, pt.x + 9, pt.y + 4);
  }
}

export function drawTrajectory(ctx, project, samples, color = "#ff6b6b",
                               upto = samples.length) {
  if (upto < 2) return;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.6;
  ctx.beginPath();
  for (let i = 0; i < upto; i++) {
    const pt = project(samples[i].pos);
    i ? ctx.lineTo(pt.x, pt.y) : ctx.moveTo(pt.x, pt.y);
  }
  ctx.stroke();
}

export function drawEvidenceMarkers(ctx, project, evidence) {
  for (const e of evidence) {
    const pt = project(e.hit);
    ctx.beginPath();
    ctx.arc(pt.x, pt.y, e.valid ? 3.5 : 2.5, 0, Math.PI * 2);
    ctx.fillStyle = e.valid ? "#37d399" : "#ff6b6b";
    ctx.fill();
  }
}
