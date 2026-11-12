"""Finish ordering based on completion events with explicit tie policies."""

from __future__ import annotations

from typing import List

from .judge import VehicleJudge
from .model import Track


def rank_vehicles(judges: List[VehicleJudge], track: Track) -> List[dict]:
    """Return ranking rows sorted by finish event then declared tie policy."""
    rules = track.rules
    finishers = [j for j in judges if j.finished]
    dnf = [j for j in judges if not j.finished]

    rows = []

    if rules.tie_policy == "finish_event":
        # strict event order, ties inside finish_window_s share a rank
        finishers.sort(key=lambda j: j.finish_t)
        for idx, j in enumerate(finishers):
            rank = idx + 1
            if idx > 0 and abs(j.finish_t - finishers[idx - 1].finish_t) <= rules.finish_window_s:
                rank = rows[-1]["rank"]
            j.finish_rank = rank
            j.finish_order = idx + 1
            rows.append(_row(j, rank, "finished"))

    elif rules.tie_policy == "min_valid_time":
        # finish event order first, but ties broken by pure driving time
        finishers.sort(key=lambda j: (j.finish_t, j.valid_driving_s))
        for idx, j in enumerate(finishers):
            j.finish_rank = idx + 1
            j.finish_order = idx + 1
            rows.append(_row(j, idx + 1, "finished"))

    elif rules.tie_policy == "shared_rank":
        # group by finish timestamp within window, identical rank, dense order
        finishers.sort(key=lambda j: j.finish_t)
        rank = 0
        last_t = None
        for idx, j in enumerate(finishers):
            if last_t is None or abs(j.finish_t - last_t) > rules.finish_window_s:
                rank += 1
                last_t = j.finish_t
            j.finish_rank = rank
            j.finish_order = idx + 1
            rows.append(_row(j, rank, "shared"))

    dnf.sort(key=lambda j: (-len(j.valid_crossings()), j.elapsed_s))
    next_rank = len(rows) + 1
    for idx, j in enumerate(dnf):
        j.finish_rank = None
        rows.append(_row(j, None, "dnf", dnf_index=idx + 1, dnf_rank=next_rank + idx))

    return rows


def _row(j: VehicleJudge, rank, status, dnf_index=None, dnf_rank=None) -> dict:
    return {
        "rank": rank,
        "status": status,
        "vehicle_id": j.vehicle_id,
        "laps": j.current_lap,
        "finish_t": round(j.finish_t, 4) if j.finish_t is not None else None,
        "valid_driving_s": round(j.valid_driving_s, 4),
        "penalty_s": round(j.penalty_s, 4),
        "total_time_s": round(j.total_time_s(), 4),
        "valid_crossings": len(j.valid_crossings()),
        "dnf_index": dnf_index,
        "dnf_rank": dnf_rank,
    }
