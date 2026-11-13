"""Authoritative race judge: continuous trajectory vs directed zones.

The judge never looks at "distance to start" or wall-clock alone. Every
progress decision is an intersection of a continuous movement segment with an
oriented checkpoint slab, checked against the declared successor graph.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence

from .diagnostics import (
    DEC_ACCEPT,
    DEC_BACKSIDE,
    DEC_GAP_CROSS,
    DEC_LONG_SEGMENT,
    DEC_OUTSIDE_GATE,
    DEC_PLANE_MISS,
    DEC_RACE_START,
    DEC_RESET,
    DEC_REVERSE,
    DEC_START_REENTRY,
    DEC_TELEMETRY_GAP,
    DecisionRecord,
    DiagnosticRecorder,
    ZoneAnalysisRecord,
)
from .geometry import Vec3, Zone
from .model import PenaltyPolicy, RaceRules, Track

# Event kinds ---------------------------------------------------------------
E_FORWARD = "forward_cross"
E_REVERSE = "reverse_cross"
E_BACKSIDE = "backside_cross"
E_EDGE_GRAZE = "edge_graze"
E_EDGE_JITTER = "edge_jitter"
E_OUTSIDE_GATE = "outside_gate"
E_LONG_SEGMENT = "long_segment"
E_GAP = "telemetry_gap"
E_GAP_CROSS = "gap_cross_invalidated"
E_LAP = "lap_completed"
E_FINISH = "race_finish"
E_RESET = "vehicle_reset"
E_WRONG_ORDER = "wrong_order"
E_START = "race_start"


@dataclass
class Event:
    t: float
    kind: str
    zone_id: Optional[str]
    detail: dict = field(default_factory=dict)


@dataclass
class Penalty:
    t: float
    source: str
    seconds: float
    zone_id: Optional[str]
    detail: dict = field(default_factory=dict)
    segment_index: Optional[int] = None


@dataclass
class CrossingEvidence:
    t: float
    zone_id: str
    direction: int
    valid: bool
    segment_t: float
    hit: List[float]
    side_a: int
    side_b: int
    inside: bool
    lap_at_event: int
    invalidation: Optional[str] = None
    segment_index: Optional[int] = None


@dataclass
class LapRecord:
    lap: int
    start_t: float
    finish_t: Optional[float] = None
    crossings: List[str] = field(default_factory=list)


@dataclass
class _ZoneRuntime:
    side: int = 0
    in_slab: bool = False
    jitter_count: int = 0


class VehicleJudge:
    def __init__(self, vehicle_id: str, track: Track,
                 record_diagnostics: bool = False):
        self.vehicle_id = vehicle_id
        self.track = track
        self.rules: RaceRules = track.rules
        self.policy: PenaltyPolicy = track.penalty_policy
        self.zones: Dict[str, Zone] = track.build_zones()
        self.runtime: Dict[str, _ZoneRuntime] = {
            zid: _ZoneRuntime() for zid in self.zones
        }
        self.events: List[Event] = []
        self.penalties: List[Penalty] = []
        self.evidence: List[CrossingEvidence] = []
        self.laps: List[LapRecord] = []
        self.elapsed_s: float = 0.0
        self.penalty_s: float = 0.0
        self.valid_driving_s: float = 0.0
        self.gap_s: float = 0.0
        self.finished: bool = False
        self.finish_t: Optional[float] = None
        self.finish_order: Optional[int] = None
        self.finish_rank: Optional[int] = None
        self.current_lap: int = 0
        self.expected: set = set()
        self.passed_this_lap: set = set()
        self._start_opened = False
        self._last_sample: Optional[tuple] = None  # (t, pos, connected)
        self._started = False
        # segment diagnostics (bounded recorder; every dropped segment can be
        # recomputed on demand from the raw telemetry)
        self.recorder: Optional[DiagnosticRecorder] = (
            DiagnosticRecorder() if record_diagnostics else None
        )
        self._segment_index = -1
        self._active_segment: Optional[int] = None
        self._active_penalty_start = 0

    # ------------------------------------------------------------------ input
    def start(self, t: float) -> None:
        if self._started:
            return
        self._started = True
        self.current_lap = 0
        self.laps.append(LapRecord(lap=1, start_t=t))
        start_zone = self.zones[self.rules.start_zone]
        self.runtime[self.rules.start_zone].side = (
            start_zone.plane.side(Vec3.from_iter(self.track.start_position))
        )
        # the start line itself is the first legal crossing; afterwards its
        # declared successors become eligible
        self.expected = {self.rules.start_zone}
        self.passed_this_lap = set()
        self.events.append(Event(t, E_START, self.rules.start_zone))

    def reset_vehicle(self, t: float, reason: str = "manual") -> None:
        """Return to declared recovery point; legal progress is retained."""
        rec = self.track.recovery
        pos = Vec3.from_iter(rec.position)
        self.penalty_s += self.policy.reset_seconds
        self.penalties.append(
            Penalty(t, "reset", self.policy.reset_seconds, rec.zone_id,
                    {"reason": reason})
        )
        self.events.append(
            Event(t, E_RESET, rec.zone_id, {"reason": reason})
        )
        # Recompute settled side state from recovery position; progress kept.
        for zid, zone in self.zones.items():
            rt = self.runtime[zid]
            rt.side = zone.plane.side(pos)
            rt.in_slab = zone.in_slab(pos)
            rt.jitter_count = 0
        self._last_sample = (t, pos, True)
        if self.recorder is not None:
            self.recorder.record_reset(
                t, self._segment_index, reason,
                pos.to_list(), rec.zone_id,
            )

    def feed(
        self,
        t: float,
        pos: Vec3,
        connected: bool = True,
        force: bool = False,
        allow_time_gap: bool = False,
    ) -> None:
        # ``force`` is used by deterministic replay: it processes exactly the
        # same frame range as the live judge regardless of when completion
        # flips, relying on _complete_lap idempotency to avoid double counts.
        # ``allow_time_gap`` suspends time-jump gap penalties for diagnostic
        # multi-interval replays, which deliberately resample the same path.
        if self.finished and not force:
            return
        if not self._started:
            self.start(t)
        last = self._last_sample
        self._last_sample = (t, pos, connected)
        if last is None:
            self._initialize_sides(pos)
            return

        t0, p0, c0 = last
        dt = max(0.0, t - t0)
        self.elapsed_s = t

        # A real telemetry gap: missing samples (time jump) OR a disconnect
        # boundary.  Within a continuous disconnect stream the segment is
        # still geometrically processed (so a crossing is recorded) but its
        # intersections are invalidated rather than silently swallowed.
        time_gap = (not allow_time_gap) and dt > self.rules.gap_threshold_s
        disconnect_boundary = (c0 and not connected) or (not c0 and connected)
        is_gap = time_gap or disconnect_boundary
        if is_gap:
            self.gap_s += dt
            self.penalty_s += self.policy.gap_seconds
            self.penalties.append(
                Penalty(t, "gap", self.policy.gap_seconds, None,
                        {"gap_s": round(dt, 4)})
            )
            self.events.append(
                Event(t, E_GAP, None, {"gap_s": round(dt, 4)})
            )
            if time_gap:
                # no trustworthy geometry across a time jump
                self._begin_diagnostic_segment(t0, p0, t, pos, True, dt,
                                               allow_time_gap)
                self._record_simple_decision(DEC_TELEMETRY_GAP, None,
                                             {"gap_s": round(dt, 4)})
                self._process_segment(t0, p0, t, pos, gap=True)
                self._end_diagnostic_segment()
                self._initialize_sides(pos)
                return

        # crossing during a disconnected stream is judged but never valid
        segment_gap = is_gap or (not c0) or (not connected)
        self._begin_diagnostic_segment(t0, p0, t, pos, segment_gap, dt,
                                       allow_time_gap)
        if segment_gap:
            self._process_segment(t0, p0, t, pos, gap=True)
            self._end_diagnostic_segment()
            return

        self.valid_driving_s += dt
        self._process_segment(t0, p0, t, pos, gap=False)
        self._end_diagnostic_segment()

    # --------------------------------------------------- diagnostic capture
    def _begin_diagnostic_segment(self, t0, p0, t1, p1, gap, gap_s,
                                  allow_time_gap) -> None:
        if self.recorder is None:
            return
        self._segment_index += 1
        self._active_segment = self._segment_index
        self._active_penalty_start = len(self.penalties)
        self.recorder.begin_segment(
            self._segment_index, t0, p0, t1, p1, gap, gap_s,
            allow_time_gap, self.current_lap + 1,
            set(self.expected), set(self.passed_this_lap),
            self._start_opened,
        )
        # a reset between samples marks the segment that follows it, so the
        # recovery event is locatable from the segment timeline as well
        attached = self.recorder._attached_resets
        for marker in self.recorder.recovery_markers:
            if marker.t <= t0 + 1e-9 and id(marker) not in attached:
                self.recorder.record_decision(DecisionRecord(
                    reason=DEC_RESET, zone_id=marker.zone_id,
                    direction=None, detail={"reason": marker.reason},
                ))
                attached.add(id(marker))

    def _end_diagnostic_segment(self) -> None:
        if self.recorder is None or self._active_segment is None:
            return
        self.recorder.end_segment(
            self.expected, self.passed_this_lap, self._start_opened,
            self.penalty_ledger(), self._active_penalty_start,
        )
        # tag every penalty produced during this segment with its index so a
        # clicked penalty row can open the exact originating trajectory piece
        for pen in self.penalties[self._active_penalty_start:]:
            if pen.segment_index is None:
                pen.segment_index = self._active_segment
        self._active_segment = None

    def _record_zone_analysis(self, zid: str, zone: Zone, a,
                              p0: Vec3, p1: Vec3) -> None:
        if self.recorder is None or self._active_segment is None:
            return
        near_gate = False
        route_relevant = False
        if a.hit is not None:
            near_gate = (
                zone.in_plane_distance_to_gate(a.hit)
                <= zone.gate_radius() * 2.0
            )
            route_relevant = (
                zid in self.expected or zid == self.rules.start_zone
            ) and near_gate
        self.recorder.record_zone(ZoneAnalysisRecord(
            zone_id=zid,
            crossed=a.crossed,
            direction=a.direction,
            segment_t=a.t,
            hit=a.hit.to_list() if a.hit else None,
            inside=a.inside,
            edge_distance=a.edge_distance,
            side_a=a.side_a,
            side_b=a.side_b,
            signed_a=round(zone.signed_distance(p0), 6),
            signed_b=round(zone.signed_distance(p1), 6),
            edge_contact=a.edge_contact,
            near_gate=near_gate,
            route_relevant=route_relevant,
        ))

    def _record_simple_decision(self, reason: str, zone_id, detail=None,
                                direction=None) -> None:
        if self.recorder is None or self._active_segment is None:
            return
        self.recorder.record_decision(DecisionRecord(
            reason=reason, zone_id=zone_id, direction=direction,
            detail=detail or {},
        ))

    # ------------------------------------------------------------- internals
    def _initialize_sides(self, pos: Vec3) -> None:
        for zid, zone in self.zones.items():
            rt = self.runtime[zid]
            rt.side = zone.plane.side(pos)
            rt.in_slab = zone.in_slab(pos)

    def _segment_completes_lap(self, p0: Vec3, p1: Vec3) -> bool:
        """True if this segment's forward start-line crossing would close the
        final lap (used to avoid charging a segment penalty on the finishing
        frame, since no penalty is added once the race is complete)."""
        start_id = self.rules.start_zone
        if self.finished or not self._lap_ready_to_close():
            return False
        start_zone = self.zones[start_id]
        a = start_zone.analyze_segment(p0, p1)
        return bool(a.crossed and a.inside and a.direction > 0)

    def _process_segment(
        self, t0: float, p0: Vec3, t1: float, p1: Vec3, gap: bool
    ) -> None:
        seg_len = p0.distance(p1)
        if (
            not gap
            and not self._segment_completes_lap(p0, p1)
            and seg_len > self.rules.max_segment_length
        ):
            self.penalty_s += self.policy.long_segment_seconds
            self.penalties.append(
                Penalty(t1, "long_segment", self.policy.long_segment_seconds,
                        None, {"length": round(seg_len, 3)})
            )
            self.events.append(
                Event(t1, E_LONG_SEGMENT, None, {"length": round(seg_len, 3)})
            )
            self._record_simple_decision(
                DEC_LONG_SEGMENT, None, {"length": round(seg_len, 3)}
            )
            if self.recorder is not None and self._active_segment is not None:
                # the segment itself carries the flag for bounded retention
                open_seg = self.recorder._open
                if open_seg is not None:
                    open_seg["long_segment"] = True

        # deterministic zone order = declared configuration order
        analyses = []
        for spec in self.track.zones:
            zid = spec.id
            zone = self.zones[zid]
            analysis = zone.analyze_segment(p0, p1)
            analyses.append((zid, zone, analysis))
            self._record_zone_analysis(zid, zone, analysis, p0, p1)

        # crossings are processed strictly in geometric order along segment
        crossings = [
            (a.t, zid, zone, a)
            for zid, zone, a in analyses
            if a.crossed
        ]
        crossings.sort(key=lambda item: item[0])

        for seg_t, zid, zone, a in crossings:
            # A sample landing exactly on the plane is reported by both
            # adjacent segments. The previous segment's t=1 crossing already
            # counted it, so suppress this segment's duplicate t=0 landing.
            if a.t == 0.0 and self.runtime[zid].side == 0:
                continue
            t_hit = t0 + (t1 - t0) * seg_t
            self._handle_crossing(t_hit, zid, zone, a, gap)

        # slab / edge state updates + jitter accounting (non-crossing motion)
        crossed_ids = {zid for _, zid, _, _ in crossings}
        for zid, zone, a in analyses:
            rt = self.runtime[zid]
            now_in_slab = zone.in_slab(p1)
            if zid not in crossed_ids:
                if now_in_slab and a.edge_contact and rt.in_slab:
                    rt.jitter_count += 1
                    self.events.append(
                        Event(t1, E_EDGE_GRAZE, zid,
                              {"edge_distance": a.edge_distance})
                    )
                    if rt.jitter_count >= self.policy.jitter_sample_limit:
                        self.penalty_s += self.policy.edge_jitter_seconds
                        self.penalties.append(
                            Penalty(t1, "edge_jitter",
                                    self.policy.edge_jitter_seconds, zid,
                                    {"count": rt.jitter_count})
                        )
                        self.events.append(
                            Event(t1, E_EDGE_JITTER, zid,
                                  {"count": rt.jitter_count})
                        )
                        rt.jitter_count = 0
                elif not now_in_slab:
                    rt.jitter_count = 0
            rt.in_slab = now_in_slab
            rt.side = a.side_b

    def _handle_crossing(
        self, t: float, zid: str, zone: Zone, analysis, gap: bool
    ) -> None:
        rt = self.runtime[zid]
        direction = analysis.direction
        rt.side = analysis.side_b
        rt.jitter_count = 0

        invalidation = "telemetry_gap" if gap else None

        # A plane hit is only route-relevant when it lands near the gate.
        # A far-away hit (e.g. an arc crossing an expected gate's infinite
        # plane tens of meters from the polygon) is geometry noise, not a
        # driving offence; a near miss at the gate edge still penalizes.
        near_gate = (
            zone.in_plane_distance_to_gate(analysis.hit)
            <= zone.gate_radius() * 2.0
        )
        route_relevant = (zid in self.expected or zid == self.rules.start_zone)
        route_relevant = route_relevant and near_gate

        # crossing plane but missing the polygon -> never a progress event
        if not analysis.inside:
            self.evidence.append(self._evidence(t, zid, analysis, False,
                                                "outside_polygon"))
            if route_relevant:
                self.penalty_s += self.policy.outside_gate_seconds
                self.penalties.append(
                    Penalty(t, "outside_gate",
                            self.policy.outside_gate_seconds,
                            zid, {"edge_distance": analysis.edge_distance})
                )
                self.events.append(
                    Event(t, E_OUTSIDE_GATE, zid,
                          {"edge_distance": analysis.edge_distance,
                           "direction": direction})
                )
                self._record_simple_decision(
                    DEC_OUTSIDE_GATE, zid,
                    {"edge_distance": analysis.edge_distance}, direction)
            else:
                self.events.append(
                    Event(t, "plane_miss_ignored", zid,
                          {"edge_distance": analysis.edge_distance})
                )
                self._record_simple_decision(
                    DEC_PLANE_MISS, zid,
                    {"edge_distance": analysis.edge_distance}, direction)
            return

        if direction < 0:
            # reverse across the start/finish line is a line re-entry, not a
            # course violation: the car simply rolls back over the timing line
            if zid == self.rules.start_zone:
                self.events.append(
                    Event(t, "start_line_reentry", zid, {"direction": -1})
                )
                self.evidence.append(
                    self._evidence(t, zid, analysis, False, "start_line_reentry")
                )
                self._record_simple_decision(
                    DEC_START_REENTRY, zid, {"direction": -1}, direction)
                return
            # entered from the front / exited through the back
            source = "reverse_cross" if not gap else "gap_reverse_cross"
            secs = self.policy.reverse_cross_seconds
            self.penalty_s += secs
            self.penalties.append(Penalty(t, source, secs, zid))
            self.events.append(Event(t, E_REVERSE, zid,
                                     {"direction": direction, "gap": gap}))
            self.evidence.append(
                self._evidence(t, zid, analysis, False, invalidation or "reverse")
            )
            self._record_simple_decision(
                DEC_REVERSE, zid, {"gap": gap}, direction)
            return

        # forward plane crossing: verify successor / backside rule
        start_id = self.rules.start_zone
        if zid == start_id:
            if not self._start_opened:
                self._accept_forward(t, zid, zone, analysis)
                return
            if self._lap_ready_to_close():
                self._accept_forward(t, zid, zone, analysis)
                return
            # opened but no real progress yet -> harmless line re-entry
            self.events.append(
                Event(t, "start_line_reentry", zid, {"ignored": True})
            )
            self.evidence.append(
                self._evidence(t, zid, analysis, False, "start_line_reentry")
            )
            self._record_simple_decision(
                DEC_START_REENTRY, zid, {"ignored": True}, direction)
            return

        is_backside = zid not in self.expected
        if is_backside:
            secs = self.policy.backside_seconds
            self.penalty_s += secs
            self.penalties.append(
                Penalty(t, "backside_cross", secs, zid,
                        {"expected": sorted(self.expected)})
            )
            self.events.append(
                Event(t, E_BACKSIDE, zid,
                      {"expected": sorted(self.expected), "gap": gap})
            )
            self.evidence.append(
                self._evidence(t, zid, analysis, False,
                               invalidation or "backside_not_expected")
            )
            self._record_simple_decision(
                DEC_BACKSIDE, zid,
                {"expected": sorted(self.expected), "gap": gap}, direction)
            if gap:
                return
            # backside crossing also breaks ordering even without gap
            self.penalty_s += self.policy.wrong_order_seconds
            self.penalties.append(
                Penalty(t, "wrong_order", self.policy.wrong_order_seconds,
                        zid, {"expected": sorted(self.expected)})
            )
            self.events.append(Event(t, E_WRONG_ORDER, zid))
            return

        if gap:
            self.events.append(Event(t, E_GAP_CROSS, zid))
            self.evidence.append(
                self._evidence(t, zid, analysis, False, "telemetry_gap")
            )
            self._record_simple_decision(
                DEC_GAP_CROSS, zid, {"expected": sorted(self.expected)},
                direction)
            return

        self._accept_forward(t, zid, zone, analysis)

    def _accept_forward(
        self, t: float, zid: str, zone: Zone, analysis
    ) -> None:
        self.evidence.append(self._evidence(t, zid, analysis, True, None))
        self.events.append(Event(t, E_FORWARD, zid, {"lap": self.current_lap + 1}))
        self.laps[-1].crossings.append(zid)
        self._record_simple_decision(
            DEC_ACCEPT, zid,
            {"lap": self.current_lap + 1,
             "expected": sorted(self.expected)},
            analysis.direction,
        )

        start_id = self.rules.start_zone
        # only a start crossing after other zones were passed closes a lap
        if zid == start_id and self._lap_ready_to_close():
            self._complete_lap(t)
            return

        self.passed_this_lap.add(zid)
        if zid == start_id:
            self._start_opened = True
            # opening crossing of the lap: follow declared successors
            self.expected = set(zone.successors)
            return
        # expected = union of successors of every passed zone (merges forks)
        self.expected = self._successors_of(self.passed_this_lap)
        self.expected.discard(zid)
        if self.rules.lap_policy == "start_return":
            # the start/finish line remains a legal target at all times:
            # first crossing opens the lap, every later crossing closes it
            self.expected.add(start_id)

        if self.rules.lap_policy == "all_zones":
            remaining = set(self.zones) - self.passed_this_lap
            if not remaining:
                self._complete_lap(t)

    def _successors_of(self, zone_ids: set) -> set:
        out: set = set()
        for zid in zone_ids:
            out.update(self.zones[zid].successors)
        return out

    def _lap_ready_to_close(self) -> bool:
        """A start crossing closes the lap only after real progress was made.

        For start_return policy this means at least one non-start zone was
        crossed; for all_zones it means every zone has been visited.
        """
        if self.rules.lap_policy == "all_zones":
            return self.passed_this_lap >= set(self.zones) - {
                self.rules.start_zone
            }
        return bool(self.passed_this_lap)

    def _complete_lap(self, t: float) -> None:
        if self.finished:
            return
        lap = self.laps[-1]
        lap.finish_t = t
        self.current_lap += 1
        self.events.append(
            Event(t, E_LAP, self.rules.start_zone,
                  {"lap": self.current_lap, "crossings": list(lap.crossings)})
        )
        if self.recorder is not None:
            self.recorder.close_lap(self)
        if self.current_lap >= self.rules.total_laps:
            self.finished = True
            self.finish_t = t
            self.events.append(
                Event(t, E_FINISH, self.rules.start_zone,
                      {"laps": self.current_lap,
                       "valid_driving_s": self.valid_driving_s,
                       "penalty_s": self.penalty_s,
                       "total_s": self.total_time_s()})
            )
            return
        self.laps.append(LapRecord(lap=self.current_lap + 1, start_t=t))
        self.passed_this_lap = set()
        self._start_opened = False
        start_zone = self.zones[self.rules.start_zone]
        self.expected = set(start_zone.successors)

    def _evidence(self, t, zid, analysis, valid, invalidation):
        return CrossingEvidence(
            t=t,
            zone_id=zid,
            direction=analysis.direction,
            valid=valid,
            segment_t=analysis.t if analysis.t is not None else -1.0,
            hit=analysis.hit.to_list() if analysis.hit else [],
            side_a=analysis.side_a,
            side_b=analysis.side_b,
            inside=analysis.inside,
            lap_at_event=self.current_lap + 1,
            invalidation=invalidation,
            segment_index=self._active_segment,
        )

    # --------------------------------------------------------------- output
    def total_time_s(self) -> float:
        return self.valid_driving_s + self.penalty_s

    def valid_crossings(self) -> List[CrossingEvidence]:
        return [e for e in self.evidence if e.valid]

    def progress_summary(self) -> dict:
        return {
            "vehicle_id": self.vehicle_id,
            "lap": min(self.current_lap + 1, self.rules.total_laps),
            "total_laps": self.rules.total_laps,
            "passed_this_lap": sorted(self.passed_this_lap),
            "expected_next": sorted(self.expected),
            "valid_crossings": len(self.valid_crossings()),
            "finished": self.finished,
            "finish_t": self.finish_t,
            "elapsed_s": round(self.elapsed_s, 4),
            "valid_driving_s": round(self.valid_driving_s, 4),
            "penalty_s": round(self.penalty_s, 4),
            "gap_s": round(self.gap_s, 4),
            "total_time_s": round(self.total_time_s(), 4),
        }

    def penalty_ledger(self) -> List[dict]:
        return [
            {
                "t": round(p.t, 4),
                "source": p.source,
                "seconds": p.seconds,
                "zone_id": p.zone_id,
                "detail": p.detail,
                "segment_index": p.segment_index,
            }
            for p in self.penalties
        ]

    def event_log(self) -> List[dict]:
        return [
            {"t": round(e.t, 4), "kind": e.kind, "zone_id": e.zone_id,
             "detail": e.detail}
            for e in self.events
        ]

    def evidence_log(self) -> List[dict]:
        return [
            {
                "t": round(e.t, 4), "zone_id": e.zone_id,
                "direction": e.direction, "valid": e.valid,
                "segment_t": round(e.segment_t, 5), "hit": e.hit,
                "side_a": e.side_a, "side_b": e.side_b,
                "inside": e.inside, "lap": e.lap_at_event,
                "invalidation": e.invalidation,
                "segment_index": e.segment_index,
            }
            for e in self.evidence
        ]

    def diagnostic_summary(self) -> Optional[dict]:
        """Bounded per-segment diagnostic view (None when recording is off)."""
        if self.recorder is None:
            return None
        return self.recorder.bounded_summary()
