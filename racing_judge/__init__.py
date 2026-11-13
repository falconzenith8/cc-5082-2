"""Three-dimensional racing race judge package."""

from .diagnostics import (
    analyze_replay,
    live_report,
    recompute_segment,
)
from .geometry import Vec3, Zone
from .judge import VehicleJudge
from .model import Track
from .race import RaceSession
from .replay import Replay, replay_judge, verify_replay
from .results import rank_vehicles

__all__ = [
    "Vec3", "Zone", "VehicleJudge", "Track", "RaceSession",
    "Replay", "replay_judge", "verify_replay", "rank_vehicles",
    "analyze_replay", "live_report", "recompute_segment",
]
