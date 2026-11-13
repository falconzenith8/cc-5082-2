"""Segment-to-checkpoint diagnostics for race verification.

The recorder captures, for every processed trajectory segment, the raw
ingredients of a checkpoint decision:

* intersection parameters per zone (signed plane distances, sides, ``t``);
* the direction sign and polygon inclusion of every plane hit;
* the candidate successor set before and after the segment;
* recovery markers (vehicle resets) and gap / long-segment flags.

Records are bounded: long races keep every anomalous segment plus rolling
per-lap summaries, and any dropped segment can always be recomputed on demand
from the raw telemetry.  The analyzer replays the *same* path at several
sampling intervals and classifies a disagreement as a geometric crossing miss
or a state-inheritance error; anything it cannot explain stays ``unknown``
rather than being attributed to the player.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import TYPE_CHECKING, Dict, List, Optional, Sequence, Tuple

from .geometry import Vec3
from .model import Track

if TYPE_CHECKING:
    from .judge import VehicleJudge
    from .replay import Replay

# Decision / anomaly reason codes (stable identifiers; the UI owns labels)
DEC_ACCEPT = "accept_forward"
DEC_REVERSE = "reverse_cross"
DEC_BACKSIDE = "backside_not_expected"
DEC_OUTSIDE_GATE = "outside_gate"
DEC_PLANE_MISS = "plane_miss_ignored"
DEC_START_REENTRY = "start_line_reentry"
DEC_GAP_CROSS = "gap_cross_invalidated"
DEC_LONG_SEGMENT = "long_segment"
DEC_TELEMETRY_GAP = "telemetry_gap"
DEC_RESET = "vehicle_reset"
DEC_RACE_START = "race_start"

# Divergence categories between the reference run and a coarser replay
GEOMETRIC_CROSS_MISSED = "geometric_cross_missed"
GEOMETRIC_POLYGON_MISS = "geometric_polygon_miss"
STATE_INHERITANCE_ERROR = "state_inheritance_error"
CONFIG_VERSION_MISMATCH = "config_version_mismatch"
UNKNOWN_CAUSE = "unknown"

# Bounded-retention defaults
DEFAULT_MAX_SEGMENTS = 4000
DEFAULT_MAX_FLAGGED = 400
TAIL_WINDOW = 80
HEAD_WINDOW = 20
MAX_DIVERGENCES = 200

# Reasons that mark a segment as worth retaining in full detail
FLAGGED_REASONS = {
    DEC_REVERSE, DEC_BACKSIDE, DEC_OUTSIDE_GATE, DEC_GAP_CROSS,
    DEC_LONG_SEGMENT, DEC_TELEMETRY_GAP, DEC_RESET,
}


@dataclass
class ZoneAnalysisRecord:
    zone_id: str
    crossed: bool
    direction: Optional[int]
    segment_t: Optional[float]
    hit: Optional[List[float]]
    inside: bool
    edge_distance: Optional[float]
    side_a: int
    side_b: int
    signed_a: float
    signed_b: float
    edge_contact: bool
    near_gate: bool
    route_relevant: bool


@dataclass
class DecisionRecord:
    reason: str
    zone_id: Optional[str]
    direction: Optional[int]
    detail: dict = field(default_factory=dict)
    penalty_index: Optional[int] = None


@dataclass
class SegmentDiagnostic:
    index: int
    t0: float
    t1: float
    p0: List[float]
    p1: List[float]
    length: float
    gap: bool
    gap_s: float
    allow_time_gap: bool
    lap: int
    expected_before: List[str]
    expected_after: List[str]
    passed_before: List[str]
    passed_after: List[str]
    start_opened: bool
    long_segment: bool
    zones: List[ZoneAnalysisRecord]
    decisions: List[DecisionRecord]
    penalties: List[dict]
    reset_reason: Optional[str] = None

    def is_flagged(self) -> bool:
        if self.long_segment or self.gap or self.reset_reason is not None:
            return True
        return any(d.reason in FLAGGED_REASONS for d in self.decisions)

    def to_dict(self) -> dict:
        out = asdict(self)
        out["flagged"] = self.is_flagged()
        return out


@dataclass
class LapSummary:
    lap: int
    segment_range: Tuple[int, int]
    time_range: Tuple[float, float]
    crossings: List[str]
    valid_crossings: int
    invalid_decisions: int
    penalty_seconds: float
    reason_counts: Dict[str, int]
    resets: int


@dataclass
class RecoveryMarker:
    t: float
    segment_index: int
    reason: str
    position: List[float]
    zone_id: Optional[str]


class DiagnosticRecorder:
    """Collects per-segment diagnostics on the live judging path."""

    def __init__(
        self,
        max_segments: int = DEFAULT_MAX_SEGMENTS,
        max_flagged: int = DEFAULT_MAX_FLAGGED,
    ):
        self.max_segments = max_segments
        self.max_flagged = max_flagged
        self.segments: List[SegmentDiagnostic] = []
        self.lap_summaries: List[LapSummary] = []
        self.recovery_markers: List[RecoveryMarker] = []
        self.total_segments = 0
        self.dropped_plain = 0
        self._open: Optional[dict] = None
        self._lap_penalty_start = 0
        self._lap_reset_count = 0
        self._attached_resets: set = set()

    # ------------------------------------------------------------- capture
    def begin_segment(
        self, index: int, t0: float, p0: Vec3, t1: float, p1: Vec3,
        gap: bool, gap_s: float, allow_time_gap: bool,
        lap: int, expected: set, passed: set, start_opened: bool,
    ) -> None:
        self._open = {
            "index": index,
            "t0": t0, "t1": t1,
            "p0": p0.to_list(), "p1": p1.to_list(),
            "length": p0.distance(p1),
            "gap": gap, "gap_s": gap_s,
            "allow_time_gap": allow_time_gap,
            "lap": lap,
            "expected_before": sorted(expected),
            "passed_before": sorted(passed),
            "start_opened": start_opened,
            "zones": [],
            "decisions": [],
            "long_segment": False,
            "penalty_start": None,
        }

    def record_zone(self, rec: ZoneAnalysisRecord) -> None:
        if self._open is not None:
            self._open["zones"].append(rec)

    def record_decision(self, dec: DecisionRecord) -> None:
        if self._open is not None:
            self._open["decisions"].append(dec)

    def mark_long_segment(self) -> None:
        if self._open is not None:
            self._open["long_segment"] = True

    def record_reset(self, t: float, segment_index: int, reason: str,
                     position: List[float], zone_id: Optional[str]) -> None:
        self.recovery_markers.append(
            RecoveryMarker(t, segment_index, reason, position, zone_id)
        )
        self._lap_reset_count += 1

    def end_segment(
        self, expected: set, passed: set, start_opened: bool,
        penalties: List[dict], penalty_start: int,
    ) -> Optional[SegmentDiagnostic]:
        if self._open is None:
            return None
        data = self._open
        self._open = None
        seg = SegmentDiagnostic(
            index=data["index"],
            t0=round(data["t0"], 6), t1=round(data["t1"], 6),
            p0=data["p0"], p1=data["p1"],
            length=round(data["length"], 4),
            gap=data["gap"], gap_s=round(data["gap_s"], 4),
            allow_time_gap=data["allow_time_gap"],
            lap=data["lap"],
            expected_before=data["expected_before"],
            expected_after=sorted(expected),
            passed_before=data["passed_before"],
            passed_after=sorted(passed),
            start_opened=start_opened,
            long_segment=data["long_segment"],
            zones=data["zones"],
            decisions=data["decisions"],
            penalties=penalties[penalty_start:],
        )
        self._store(seg)
        return seg

    def _store(self, seg: SegmentDiagnostic) -> None:
        self.total_segments += 1
        if seg.is_flagged():
            # flagged segments are never dropped while the flagged budget
            # holds; the oldest flagged segment is sacrificed only past it
            flagged = [s for s in self.segments if s.is_flagged()]
            if len(flagged) >= self.max_flagged:
                oldest = next(s for s in self.segments if s.is_flagged())
                self.segments.remove(oldest)
                self.dropped_plain += 1
            self.segments.append(seg)
            return
        if len(self.segments) >= self.max_segments:
            # prefer keeping head/tail context; drop an old plain middle segment
            head_last = min(HEAD_WINDOW, len(self.segments))
            tail_first = max(head_last, len(self.segments) - TAIL_WINDOW)
            dropped = False
            for i in range(head_last, tail_first):
                if not self.segments[i].is_flagged():
                    del self.segments[i]
                    self.dropped_plain += 1
                    dropped = True
                    break
            if not dropped:
                self.dropped_plain += 1
                return
        self.segments.append(seg)

    def close_lap(self, judge: VehicleJudge) -> None:
        lap_record = judge.laps[-1]
        segs = [s for s in self.segments if s.lap == lap_record.lap]
        reason_counts: Dict[str, int] = {}
        invalid = 0
        for s in segs:
            for d in s.decisions:
                reason_counts[d.reason] = reason_counts.get(d.reason, 0) + 1
                if d.reason != DEC_ACCEPT:
                    invalid += 1
        penalty_seconds = sum(
            p.seconds for p in judge.penalties[self._lap_penalty_start:]
        )
        self._lap_penalty_start = len(judge.penalties)
        if segs:
            rng = (segs[0].index, segs[-1].index)
            trng = (segs[0].t0, segs[-1].t1)
        else:
            rng = (-1, -1)
            trng = (0.0, 0.0)
        self.lap_summaries.append(LapSummary(
            lap=lap_record.lap,
            segment_range=rng,
            time_range=(round(trng[0], 4), round(trng[1], 4)),
            crossings=list(lap_record.crossings),
            valid_crossings=sum(
                1 for s in segs for d in s.decisions
                if d.reason == DEC_ACCEPT
            ),
            invalid_decisions=invalid,
            penalty_seconds=round(penalty_seconds, 4),
            reason_counts=reason_counts,
            resets=self._lap_reset_count,
        ))
        self._lap_reset_count = 0

    # --------------------------------------------------------------- output
    def retention(self) -> dict:
        return {
            "total_segments": self.total_segments,
            "retained_segments": len(self.segments),
            "dropped_plain_segments": self.dropped_plain,
            "max_segments": self.max_segments,
            "max_flagged": self.max_flagged,
            "recomputable": True,
        }

    def flagged_indices(self) -> List[int]:
        return sorted(s.index for s in self.segments if s.is_flagged())

    def reason_totals(self) -> Dict[str, int]:
        totals: Dict[str, int] = {}
        for s in self.segments:
            for d in s.decisions:
                totals[d.reason] = totals.get(d.reason, 0) + 1
        return totals

    def get_segment(self, index: int) -> Optional[SegmentDiagnostic]:
        for s in self.segments:
            if s.index == index:
                return s
        return None

    def bounded_summary(self) -> dict:
        return {
            "retention": self.retention(),
            "flagged_segment_indices": self.flagged_indices(),
            "reason_totals": self.reason_totals(),
            "lap_summaries": [asdict(x) for x in self.lap_summaries],
            "recovery_markers": [asdict(x) for x in self.recovery_markers],
        }


# --------------------------------------------------------------------- runs
def _resample_indices(n: int, stride: int, finish_index: Optional[int]) -> List[int]:
    last = n - 1 if finish_index is None else min(finish_index, n - 1)
    indices = list(range(0, last + 1, max(1, stride)))
    if not indices or indices[-1] != last:
        indices.append(last)
    return indices


def replay_at_stride(
    replay: Replay, vid: str, stride: int,
    track: Optional[Track] = None,
    max_segments: int = DEFAULT_MAX_SEGMENTS,
    max_flagged: int = DEFAULT_MAX_FLAGGED,
) -> Tuple["VehicleJudge", List[int]]:
    """Re-judge one vehicle's raw path keeping every stride-th sample.

    Samples are only selected, never rewritten or interpolated.  Time-gap
    handling is suspended so the comparison isolates geometry and state
    inheritance from the (expectedly larger) telemetry-gap penalties; the
    report states this explicitly.
    """
    from .judge import VehicleJudge  # lazy: avoid judge<->diagnostics cycle

    track = track or Track.from_dict(replay.track)
    judge = VehicleJudge(vid, track, record_diagnostics=True)
    judge.recorder = DiagnosticRecorder(
        max_segments=max_segments, max_flagged=max_flagged
    )
    samples = replay.telemetry[vid]
    resets = replay.resets.get(vid, [])
    finish_index = replay.finish_sample_index.get(vid)
    indices = _resample_indices(len(samples), stride, finish_index)
    reset_pos = 0
    judge.start(0.0)
    first = True
    for sample_index in indices:
        sample = samples[sample_index]
        t = sample["t"]
        while reset_pos < len(resets) and resets[reset_pos]["t"] <= t:
            r = resets[reset_pos]
            judge.reset_vehicle(r["t"], r.get("reason", "replay"))
            reset_pos += 1
        if first:
            judge._initialize_sides(Vec3.from_iter(sample["pos"]))
            first = False
        judge.feed(
            t,
            Vec3.from_iter(sample["pos"]),
            connected=sample.get("connected", True),
            force=True,
            allow_time_gap=True,
        )
    return judge, indices


def _accepted_events(judge: "VehicleJudge") -> List[dict]:
    """Accepted crossings in processing order with an ordinal per (lap, zone)
    so repeated crossings of the same gate are never collapsed."""
    out = []
    seen: Dict[tuple, int] = {}
    for s in judge.recorder.segments:
        for d in s.decisions:
            if d.reason != DEC_ACCEPT:
                continue
            key = (s.lap, d.zone_id)
            ordinal = seen.get(key, 0)
            seen[key] = ordinal + 1
            out.append({
                "t": s.t1, "zone_id": d.zone_id,
                "segment_index": s.index, "lap": s.lap,
                "ordinal": ordinal,
            })
    return out


def _segment_spanning(segments: Sequence[SegmentDiagnostic], t: float):
    for s in segments:
        if s.t0 <= t <= s.t1:
            return s
    # fall back to the nearest segment when t sits exactly on a resampled
    # boundary (two coarse segments share that timestamp)
    return min(segments, key=lambda s: min(abs(t - s.t0), abs(t - s.t1)),
               default=None)


def _zone_record(seg: SegmentDiagnostic, zone_id: str) -> Optional[ZoneAnalysisRecord]:
    for z in seg.zones:
        if z.zone_id == zone_id:
            return z
    return None


def classify_divergences(
    reference: "VehicleJudge",
    coarse: "VehicleJudge",
    stride: int,
) -> List[dict]:
    """Per-stride explanations of reference crossings the coarse run loses.

    Crossings are matched by processing order (both judges see the same raw
    path, so the nth accepted crossing refers to the same gate event); this
    stays correct when a lost closing crossing shifts the lap numbering of
    every later event.  A loss is geometric only when the coarse segment
    truly fails to cross the plane (or misses its polygon); a real in-gate
    crossing that the successor state rejects is a state-inheritance error.
    """
    coarse_segments = coarse.recorder.segments
    ref_events = _accepted_events(reference)
    coarse_events = _accepted_events(coarse)

    divergences: List[dict] = []
    for order, ev in enumerate(ref_events):
        if order < len(coarse_events):
            # same ordinal gate event survived in the coarse run
            match = coarse_events[order]
            if match["zone_id"] == ev["zone_id"]:
                continue
        coarse_seg = _segment_spanning(coarse_segments, ev["t"])
        ref_seg = reference.recorder.get_segment(ev["segment_index"])
        zrec = (
            _zone_record(coarse_seg, ev["zone_id"])
            if coarse_seg is not None else None
        )
        if zrec is None or not zrec.crossed:
            category = GEOMETRIC_CROSS_MISSED
            detail = ("the coarse segment never intersects the checkpoint "
                      "plane; the crossing exists only in dropped samples")
        elif not zrec.inside:
            category = GEOMETRIC_POLYGON_MISS
            detail = ("the plane is intersected but the hit lands outside "
                      "the gate polygon at this sampling interval")
        else:
            category = STATE_INHERITANCE_ERROR
            detail = ("geometry still crosses the gate inside the polygon, "
                      "but inherited successor state no longer expects it")
        divergences.append({
            "category": category,
            "reason": category,
            "zone_id": ev["zone_id"],
            "lap": ev["lap"],
            "ordinal": ev["ordinal"],
            "order": order,
            "t": ev["t"],
            "detail": detail,
            "reference_segment_index": ev["segment_index"],
            "coarse_stride": stride,
            "coarse_segment_index": coarse_seg.index if coarse_seg else None,
            "coarse_zone": asdict(zrec) if zrec else None,
            "expected_at_coarse_segment":
                coarse_seg.expected_before if coarse_seg else [],
            "direction_reference":
                next((d.direction for d in ref_seg.decisions
                      if d.zone_id == ev["zone_id"]), None)
                if ref_seg else None,
            "caused_by": None,
        })

    if reference.current_lap != coarse.current_lap and not divergences:
        divergences.append({
            "category": UNKNOWN_CAUSE,
            "reason": UNKNOWN_CAUSE,
            "zone_id": None,
            "lap": coarse.current_lap + 1,
            "ordinal": 0,
            "order": len(ref_events),
            "t": None,
            "detail": ("lap counts differ but every reference crossing has a "
                       "matching coarse crossing; no segment-level cause found"),
            "reference_segment_index": None,
            "coarse_stride": stride,
            "coarse_segment_index": None,
            "coarse_zone": None,
            "expected_at_coarse_segment": [],
            "direction_reference": None,
            "caused_by": None,
        })
    return divergences


def aggregate_divergences(
    reference: "VehicleJudge",
    stride_judges: Dict[int, "VehicleJudge"],
) -> List[dict]:
    """One divergence per reference crossing, resolved across all strides.

    ``first_diverging_stride`` is where the loss first appears; the category
    is taken at that stride.  State-inheritance losses inherit a ``caused_by``
    link to the earliest earlier geometric loss, which is the actual root
    cause even when several later checkpoints are rejected as shortcuts.
    """
    per_stride: Dict[int, List[dict]] = {
        stride: ([] if stride == 1
                 else classify_divergences(reference, judge, stride))
        for stride, judge in stride_judges.items()
    }
    merged: Dict[int, dict] = {}
    for stride in sorted(per_stride):
        for d in per_stride[stride]:
            key = d["order"]
            if key in merged:
                merged[key]["strides_affected"].append(stride)
                merged[key]["per_stride"].append({
                    "stride": stride,
                    "category": d["category"],
                    "coarse_segment_index": d["coarse_segment_index"],
                    "coarse_zone": d["coarse_zone"],
                    "expected": d["expected_at_coarse_segment"],
                })
                continue
            entry = dict(d)
            entry["first_diverging_stride"] = stride
            entry["strides_affected"] = [stride]
            entry["per_stride"] = [{
                "stride": stride,
                "category": d["category"],
                "coarse_segment_index": d["coarse_segment_index"],
                "coarse_zone": d["coarse_zone"],
                "expected": d["expected_at_coarse_segment"],
            }]
            merged[key] = entry
    ordered = sorted(merged.values(), key=lambda d: d["order"])
    geometric_roots = [
        d for d in ordered
        if d["category"] in (GEOMETRIC_CROSS_MISSED, GEOMETRIC_POLYGON_MISS)
        and d["t"] is not None
    ]
    for d in ordered:
        if d["category"] != STATE_INHERITANCE_ERROR:
            continue
        roots = [m for m in geometric_roots if m["t"] <= d["t"]]
        if roots:
            root = min(roots, key=lambda m: m["t"])
            d["caused_by"] = {
                "category": root["category"],
                "zone_id": root["zone_id"],
                "lap": root["lap"],
                "segment_index": root["reference_segment_index"],
                "t": root["t"],
            }
    return ordered


def _penalty_totals(judge: VehicleJudge) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for p in judge.penalty_ledger():
        out[p["source"]] = out.get(p["source"], 0) + 1
    return out


def analyze_replay(
    replay: Replay,
    current_track: Optional[Track] = None,
    strides: Sequence[int] = (1, 2, 4, 8),
) -> dict:
    """Full bounded diagnostic report for every vehicle in a replay."""
    embedded = Track.from_dict(replay.track)
    embedded_fp = embedded.config_fingerprint()
    config_match = True
    changed_fields: List[str] = []
    current_fp = embedded_fp
    if current_track is not None:
        current_fp = current_track.config_fingerprint()
        config_match = current_fp == embedded_fp
        changed_fields = current_track.config_diff(embedded)

    report: dict = {
        "replay_id": replay.replay_id,
        "track_fingerprint": embedded_fp,
        "current_track_fingerprint": current_fp,
        "config_match": config_match,
        "config_changed_fields": changed_fields,
        "config_advisory": None,
        "sampling_note": (
            "coarse replays select every Nth raw sample without rewriting "
            "positions; telemetry-gap timing penalties are suspended so the "
            "comparison isolates geometry vs. state inheritance"
        ),
        "vehicles": {},
    }
    if not config_match:
        report["config_advisory"] = (
            "checkpoint configuration changed after this record was saved; "
            "anomalies are version mismatches and must not be classified as "
            "player cheating"
        )

    all_divergences: List[dict] = []
    for vid in replay.vehicles:
        ref_judge, _ = replay_at_stride(replay, vid, 1, embedded)
        runs = []
        stride_judges: Dict[int, "VehicleJudge"] = {}
        for stride in strides:
            judge, indices = replay_at_stride(replay, vid, stride, embedded)
            stride_judges[stride] = judge
            runs.append({
                "stride": stride,
                "sample_count": len(indices),
                "laps": judge.current_lap,
                "finished": judge.finished,
                "valid_crossings": len(judge.valid_crossings()),
                "penalty_totals": _penalty_totals(judge),
                "retention": judge.recorder.retention(),
            })
        vehicle_divergences = aggregate_divergences(ref_judge, stride_judges)
        for d in vehicle_divergences:
            d["vehicle_id"] = vid
            if not config_match:
                d["advisory"] = report["config_advisory"]
        all_divergences.extend(vehicle_divergences)
        bounded = vehicle_divergences[:MAX_DIVERGENCES]
        report["vehicles"][vid] = {
            "reference": {
                "laps": ref_judge.current_lap,
                "finished": ref_judge.finished,
                "valid_crossings": len(ref_judge.valid_crossings()),
                "penalty_totals": _penalty_totals(ref_judge),
            },
            "runs": runs,
            "divergences": bounded,
            "divergences_truncated":
                len(vehicle_divergences) > MAX_DIVERGENCES,
            "summary": ref_judge.recorder.bounded_summary(),
        }
    report["divergence_total"] = len(all_divergences)
    report["divergence_categories"] = _category_counts(all_divergences)
    report["divergences_truncated"] = len(all_divergences) > (
        MAX_DIVERGENCES * max(1, len(replay.vehicles)))
    return report


def _category_counts(divergences: Sequence[dict]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for d in divergences:
        out[d["category"]] = out.get(d["category"], 0) + 1
    return out


def recompute_segment(
    replay: Replay, vid: str, segment_index: int, stride: int = 1,
) -> dict:
    """Re-derive a single segment on demand from the raw telemetry.

    Used for segments dropped from the bounded report.  The judge is rebuilt
    up to the requested index from untouched telemetry; no position is ever
    modified.
    """
    judge, indices = replay_at_stride(replay, vid, stride)
    total = judge.recorder.total_segments
    if segment_index < 0 or segment_index >= total:
        raise IndexError(
            f"segment {segment_index} out of range (0..{total - 1})"
        )
    seg = judge.recorder.get_segment(segment_index)
    if seg is None:
        raise LookupError(
            f"segment {segment_index} was not retained; rerun with a higher "
            "retention budget"
        )
    return {
        "vehicle_id": vid,
        "stride": stride,
        "total_segments": total,
        "segment": seg.to_dict(),
        "recovery_markers": [
            asdict(m) for m in judge.recorder.recovery_markers
            if abs(m.t - seg.t1) <= max(seg.t1 - seg.t0, 1e-9)
            or (seg.t0 <= m.t <= seg.t1)
        ],
    }


def live_report(judge: VehicleJudge,
                current_track: Optional[Track] = None) -> dict:
    """Bounded diagnostic view of a live (or freshly re-judged) vehicle."""
    if judge.recorder is None:
        raise ValueError("judge was created without diagnostic recording")
    embedded = judge.track
    embedded_fp = embedded.config_fingerprint()
    config_match = True
    changed_fields: List[str] = []
    current_fp = embedded_fp
    if current_track is not None:
        current_fp = current_track.config_fingerprint()
        config_match = current_fp == embedded_fp
        changed_fields = current_track.config_diff(embedded)
    return {
        "vehicle_id": judge.vehicle_id,
        "track_fingerprint": embedded_fp,
        "current_track_fingerprint": current_fp,
        "config_match": config_match,
        "config_changed_fields": changed_fields,
        "config_advisory": (
            "checkpoint configuration changed after this record was saved"
            if not config_match else None
        ),
        "summary": judge.recorder.bounded_summary(),
        "current_segment_index": max(0, judge.recorder.total_segments - 1),
        "progress": judge.progress_summary(),
    }
