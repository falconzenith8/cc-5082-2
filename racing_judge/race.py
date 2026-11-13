"""Race session orchestration: sim step -> judge feed -> replay capture."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional

from .geometry import Vec3
from .judge import VehicleJudge
from .model import Track
from .replay import Replay
from .results import rank_vehicles
from .simulator import Control, ScriptedController, Vehicle


class RaceSession:
    def __init__(self, track: Track, vehicle_ids: List[str],
                 replay_id: Optional[str] = None):
        self.track = track
        self.t = 0.0
        self.dt = 1.0 / track.rules.tick_hz
        self.vehicles: Dict[str, Vehicle] = {}
        self.judges: Dict[str, VehicleJudge] = {}
        self.controllers: Dict[str, ScriptedController] = {}
        self.telemetry: Dict[str, List[dict]] = {v: [] for v in vehicle_ids}
        self.resets: Dict[str, List[dict]] = {v: [] for v in vehicle_ids}
        self._finish_sample_index: Dict[str, int] = {}
        self.replay = Replay(
            replay_id=replay_id or f"race-{uuid.uuid4().hex[:10]}",
            track=track.to_dict(),
            vehicles=list(vehicle_ids),
            telemetry=self.telemetry,
            resets=self.resets,
            finish_sample_index=self._finish_sample_index,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        self._all_finished_announced = False
        for vid in vehicle_ids:
            self.add_vehicle(vid)

    def add_vehicle(self, vid: str) -> None:
        pos = Vec3.from_iter(self.track.start_position)
        self.vehicles[vid] = Vehicle(vid, pos, self.track.start_heading)
        self.judges[vid] = VehicleJudge(vid, self.track,
                                        record_diagnostics=True)

    def attach_controller(self, vid: str, controller: ScriptedController) -> None:
        self.controllers[vid] = controller

    def start(self) -> None:
        for judge in self.judges.values():
            judge.start(0.0)

    def step(self) -> dict:
        self.t += self.dt
        for vid, vehicle in self.vehicles.items():
            judge = self.judges[vid]
            ctrl = Control(0.0, 0.0)
            flag = {"connected": True, "reset": None}
            if vid in self.controllers:
                ctrl, flag = self.controllers[vid].control(
                    vehicle, self.t, self.dt
                )
            if flag.get("reset"):
                r = flag["reset"]
                rec_pos = Vec3.from_iter(
                    r.get("position", self.track.recovery.position)
                )
                rec_heading = r.get(
                    "heading", self.track.recovery.heading
                )
                vehicle.teleport(rec_pos, rec_heading)
                judge.reset_vehicle(self.t, r.get("reason", "manual"))
                self.resets[vid].append(
                    {"t": round(self.t, 6), "reason": r.get("reason", "manual"),
                     "position": rec_pos.to_list(), "heading": rec_heading}
                )
            else:
                vehicle.step(ctrl, self.dt)
            connected = flag.get("connected", True)
            judge.feed(self.t, vehicle.state.pos, connected=connected)
            if judge.finished and vid not in self._finish_sample_index:
                # The frame that flips finished is the last one the live
                # judge fully processes; every later feed() short-circuits.
                self._finish_sample_index[vid] = len(self.telemetry[vid])
            self.telemetry[vid].append({
                "t": round(self.t, 6),
                "pos": vehicle.state.pos.to_list(),
                "heading": round(vehicle.state.heading, 6),
                "speed": round(vehicle.state.speed, 4),
                "connected": connected,
            })
        return self.snapshot()

    def run_until_finish(self, max_seconds: float = 600.0) -> dict:
        self.start()
        while self.t < max_seconds:
            self.step()
            if all(j.finished for j in self.judges.values()):
                break
        return self.snapshot(final=True)

    def snapshot(self, final: bool = False) -> dict:
        # capture the rules actually in effect at snapshot time so an
        # independent replay re-judges under identical thresholds
        self.replay.track = self.track.to_dict()
        self.replay.track_fingerprint = self.track.config_fingerprint()
        ranking = rank_vehicles(list(self.judges.values()), self.track) if (
            final or self._all_finished_announced
            or any(j.finished for j in self.judges.values())
        ) else []
        if all(j.finished for j in self.judges.values()):
            self._all_finished_announced = True
        return {
            "t": round(self.t, 4),
            "vehicles": {
                vid: {
                    "summary": j.progress_summary(),
                    "penalties": j.penalty_ledger(),
                    "events": j.event_log(),
                    "evidence": j.evidence_log(),
                    "diagnostics": j.diagnostic_summary(),
                    "state": {
                        "pos": self.vehicles[vid].state.pos.to_list(),
                        "heading": self.vehicles[vid].state.heading,
                        "speed": self.vehicles[vid].state.speed,
                    },
                }
                for vid, j in self.judges.items()
            },
            "ranking": ranking,
            "all_finished": all(j.finished for j in self.judges.values()),
            "replay_id": self.replay.replay_id,
            "track_fingerprint": self.replay.track_fingerprint,
        }
