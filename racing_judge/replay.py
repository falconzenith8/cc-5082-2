"""Persistable race replays and independent replay verification."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from .geometry import Vec3
from .judge import VehicleJudge
from .model import Track
from .results import rank_vehicles


@dataclass
class Replay:
    replay_id: str
    track: dict
    vehicles: List[str]
    telemetry: Dict[str, List[dict]]
    resets: Dict[str, List[dict]] = field(default_factory=dict)
    finish_sample_index: Dict[str, int] = field(default_factory=dict)
    created_at: str = ""
    track_fingerprint: str = ""

    def to_dict(self) -> dict:
        return {
            "replay_id": self.replay_id,
            "created_at": self.created_at,
            "track": self.track,
            "vehicles": self.vehicles,
            "telemetry": self.telemetry,
            "resets": self.resets,
            "finish_sample_index": self.finish_sample_index,
            "track_fingerprint": self.track_fingerprint,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Replay":
        return cls(
            replay_id=data["replay_id"],
            created_at=data.get("created_at", ""),
            track=data["track"],
            vehicles=data["vehicles"],
            telemetry=data["telemetry"],
            resets=data.get("resets", {}),
            finish_sample_index=data.get("finish_sample_index", {}),
            track_fingerprint=data.get("track_fingerprint", ""),
        )

    def save(self, directory: str) -> str:
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{self.replay_id}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh, ensure_ascii=False)
        return path

    @classmethod
    def load(cls, path: str) -> "Replay":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))


def replay_judge(replay: Replay) -> Dict[str, VehicleJudge]:
    """Rebuild judges deterministically from raw telemetry."""
    track = Track.from_dict(replay.track)
    judges: Dict[str, VehicleJudge] = {
        vid: VehicleJudge(vid, track) for vid in replay.vehicles
    }
    reset_index = {vid: 0 for vid in replay.vehicles}
    for vid in replay.vehicles:
        judge = judges[vid]
        resets = replay.resets.get(vid, [])
        finish_index = replay.finish_sample_index.get(vid)
        first = True
        for sample_index, sample in enumerate(replay.telemetry[vid]):
            # include the frame that triggers completion, skip later frames
            # (the live judge ignores all post-finish telemetry)
            if finish_index is not None and sample_index > finish_index:
                break
            t = sample["t"]
            while (
                reset_index[vid] < len(resets)
                and resets[reset_index[vid]]["t"] <= t
            ):
                r = resets[reset_index[vid]]
                judge.reset_vehicle(r["t"], r.get("reason", "replay"))
                reset_index[vid] += 1
            if first:
                judge.start(0.0)
                first_pos = Vec3.from_iter(sample["pos"])
                judge._initialize_sides(first_pos)
                first = False
            # force processes the exact same frame range as live even if the
            # replay judge flips finished on an earlier frame
            judge.feed(
                t,
                Vec3.from_iter(sample["pos"]),
                connected=sample.get("connected", True),
                force=True,
            )
    return judges


def verify_replay(
    replay: Replay,
    original: Optional[Dict[str, dict]] = None,
    current_track: Optional[Track] = None,
) -> dict:
    """Re-judge the replay and compare laps/evidence with stored summaries.

    When ``current_track`` differs from the configuration embedded in the
    replay, the report carries an explicit version-mismatch advisory: such
    anomalies are configuration drift, never a player-cheating conclusion.
    """
    judges = replay_judge(replay)
    embedded = Track.from_dict(replay.track)
    track = current_track or embedded
    ranking = rank_vehicles(list(judges.values()), track)
    embedded_fp = embedded.config_fingerprint()
    stored_fp = replay.track_fingerprint or embedded_fp
    current_fp = track.config_fingerprint()
    config_match = (
        stored_fp == current_fp and embedded_fp == current_fp
    )
    config_report = {
        "track_fingerprint": embedded_fp,
        "stored_fingerprint": stored_fp,
        "current_track_fingerprint": current_fp,
        "config_match": config_match,
        "config_changed_fields": track.config_diff(embedded),
        "config_advisory": None,
    }
    if not config_match:
        config_report["config_advisory"] = (
            "checkpoint configuration changed after this record was saved; "
            "anomalies are version mismatches, not player cheating"
        )
    report = {
        "matches": True, "vehicles": {}, "ranking": ranking,
        "config": config_report,
    }
    for vid, judge in judges.items():
        summary = judge.progress_summary()
        evidence = judge.evidence_log()
        entry = {
            "replayed": {
                "laps": judge.current_lap,
                "valid_crossings": summary["valid_crossings"],
                "penalty_s": summary["penalty_s"],
                "valid_driving_s": summary["valid_driving_s"],
                "total_time_s": summary["total_time_s"],
                "finished": summary["finished"],
                "evidence": evidence,
            }
        }
        if original and vid in original:
            ref = original[vid]
            checks = {
                "laps": judge.current_lap == ref.get("laps"),
                "valid_crossings":
                    summary["valid_crossings"] == ref.get("valid_crossings"),
                "penalty_s":
                    abs(summary["penalty_s"] - ref.get("penalty_s", 0)) < 1e-6,
                "total_time_s":
                    abs(summary["total_time_s"] - ref.get("total_time_s", 0))
                    < 1e-6,
            }
            entry["checks"] = checks
            if not all(checks.values()):
                report["matches"] = False
        report["vehicles"][vid] = entry
    return report
