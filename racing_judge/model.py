"""Track, race and penalty-rule configuration model."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Dict, List, Optional, Sequence

from .geometry import Vec3, Zone


@dataclass
class ZoneSpec:
    id: str
    name: str
    points: List[List[float]]
    normal: List[float]
    thickness: float = 0.6
    successors: List[str] = field(default_factory=list)


@dataclass
class RecoverySpec:
    zone_id: Optional[str] = None
    position: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    heading: float = 0.0


@dataclass
class PenaltyPolicy:
    backside_seconds: float = 2.0
    reverse_cross_seconds: float = 3.0
    wrong_order_seconds: float = 5.0
    edge_jitter_seconds: float = 1.0
    jitter_sample_limit: int = 4
    long_segment_seconds: float = 4.0
    gap_seconds: float = 6.0
    reset_seconds: float = 2.0
    outside_gate_seconds: float = 2.0


@dataclass
class RaceRules:
    total_laps: int = 3
    start_zone: str = "start"
    lap_policy: str = "start_return"  # or "all_zones"
    tick_hz: float = 30.0
    max_segment_length: float = 12.0
    gap_threshold_s: float = 0.25
    tie_policy: str = "finish_event"  # finish_event | min_valid_time | shared_rank
    finish_window_s: float = 0.05


@dataclass
class Track:
    track_id: str
    name: str
    zones: List[ZoneSpec]
    start_position: List[float]
    start_heading: float
    recovery: RecoverySpec
    rules: RaceRules
    penalty_policy: PenaltyPolicy

    @classmethod
    def from_dict(cls, data: dict) -> "Track":
        recovery = RecoverySpec(**data.get("recovery", {}))
        rules = RaceRules(**data.get("rules", {}))
        penalty = PenaltyPolicy(**data.get("penalty_policy", {}))
        zones = [ZoneSpec(**z) for z in data["zones"]]
        return cls(
            track_id=data["track_id"],
            name=data["name"],
            zones=zones,
            start_position=data["start_position"],
            start_heading=data["start_heading"],
            recovery=recovery,
            rules=rules,
            penalty_policy=penalty,
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def load(cls, path: str) -> "Track":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False, indent=2)

    def build_zones(self) -> Dict[str, Zone]:
        out: Dict[str, Zone] = {}
        for spec in self.zones:
            out[spec.id] = Zone(
                zone_id=spec.id,
                name=spec.name,
                points=[Vec3.from_iter(p) for p in spec.points],
                normal=Vec3.from_iter(spec.normal),
                thickness=spec.thickness,
                successors=spec.successors,
            )
        self.validate(out)
        return out

    def validate(self, zones: Optional[Dict[str, Zone]] = None) -> None:
        if zones is None:
            zones = self.build_zones_unchecked()
        ids = {z.id for z in zones.values()}
        if self.rules.start_zone not in ids:
            raise ValueError(f"start_zone {self.rules.start_zone!r} missing")
        for spec in self.zones:
            for succ in spec.successors:
                if succ not in ids:
                    raise ValueError(
                        f"zone {spec.id}: unknown successor {succ!r}"
                    )
        # reachability from start (BFS over successor edges)
        seen = {self.rules.start_zone}
        stack = [self.rules.start_zone]
        while stack:
            cur = stack.pop()
            for succ in next(z.successors for z in zones.values() if z.id == cur):
                if succ not in seen:
                    seen.add(succ)
                    stack.append(succ)
        unreachable = ids - seen
        if unreachable:
            raise ValueError(f"unreachable zones from start: {sorted(unreachable)}")
        # start must be reachable from some zone (closed course)
        has_loop = any(
            self.rules.start_zone in z.successors for z in zones.values()
        )
        if not has_loop:
            raise ValueError("course is not closed: no zone leads back to start")
        if self.rules.tie_policy not in (
            "finish_event", "min_valid_time", "shared_rank"
        ):
            raise ValueError(f"unknown tie_policy {self.rules.tie_policy}")
        if self.rules.lap_policy not in ("start_return", "all_zones"):
            raise ValueError(f"unknown lap_policy {self.rules.lap_policy}")

    def build_zones_unchecked(self) -> Dict[str, Zone]:
        out: Dict[str, Zone] = {}
        for spec in self.zones:
            out[spec.id] = Zone(
                zone_id=spec.id,
                name=spec.name,
                points=[Vec3.from_iter(p) for p in spec.points],
                normal=Vec3.from_iter(spec.normal),
                thickness=spec.thickness,
                successors=spec.successors,
            )
        return out
