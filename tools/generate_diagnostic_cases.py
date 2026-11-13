"""Generate bounded failure-case trajectories for the diagnostics suite.

The generator builds raw replay documents (positions are only ever written,
never rewritten to fit an expected verdict) for the two failure classes the
diagnostic view must explain, plus a configuration-version-mismatch case:

1. ``high_speed_missed_lap`` — a high-speed two-sample leap over the thin
   start/finish slab on the lap-closing frame.  The dense reference sees the
   crossing; coarse sampling tunnels past it -> geometric crossing miss ->
   missing lap.
2. ``legal_fork_flagged_shortcut`` — a legal fork branch whose coarse
   resampling first misses a checkpoint geometrically; the successor state
   inherited afterwards then rejects the still-geometric merge crossing as a
   backside "shortcut" -> state inheritance error, with the root cause linked.
3. ``config_version_mismatch`` — a clean record paired with an edited track
   fingerprint, so old records are warned about rather than called cheating.

Outputs land in ``data/diagnostics`` and are committed with the repository.
The script is independent of the runtime package directory: run it with
``python3 -I tools/generate_diagnostic_cases.py``.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from racing_judge.geometry import Vec3  # noqa: E402
from racing_judge.model import Track  # noqa: E402
from racing_judge.replay import Replay, replay_judge  # noqa: E402

HZ = 30.0
DT = 1.0 / HZ
OUT_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "diagnostics",
)
TRACK_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "tracks", "fork_loop.json",
)


# --------------------------------------------------------------------- paths
def _straight(points, spacing=2.0):
    """Densify a polyline; positions come from geometry, never from verdicts."""
    out = []
    for a, b in zip(points, points[1:]):
        d = a.distance(b)
        n = max(1, int(round(d / spacing)))
        for k in range(n):
            f = k / n
            out.append(Vec3(a.x + (b.x - a.x) * f,
                            a.y + (b.y - a.y) * f,
                            a.z + (b.z - a.z) * f))
    out.append(points[-1].copy())
    return out


def _left_branch_lap():
    """One clean left-branch loop as a dense polyline (same geometry as the
    built-in scenario, expressed independently so cases stay self-contained)."""
    import math
    r = 20.0
    side = -1
    z_north, z_south = 30.0, -30.0
    arc_cx = side * 24.0
    branch_x = side * 44.0
    raw = [Vec3(0, 0, z_south), Vec3(0, 0, z_north),
           Vec3(arc_cx, 0, z_north)]

    def arc(cx, cz, a0, a1, steps=8):
        return [
            Vec3(cx + r * math.cos(a0 + (a1 - a0) * k / steps), 0,
                 cz + r * math.sin(a0 + (a1 - a0) * k / steps))
            for k in range(1, steps + 1)
        ]

    raw += arc(arc_cx, z_north - r, math.pi / 2, math.pi)
    raw.append(Vec3(branch_x, 0, z_south + r))
    raw += arc(arc_cx, z_south + r, math.pi, 3 * math.pi / 2)
    raw.append(Vec3(0, 0, z_south))  # horizontal merge back to center
    dense = _straight(raw, spacing=2.5)
    return dense[:-1]


def _to_samples(points, speed=22.0, connected=True, t0=0.0):
    samples = []
    for k, p in enumerate(points):
        samples.append({
            "t": round(t0 + k * DT, 6),
            "pos": p.to_list(),
            "heading": 0.0,
            "speed": float(speed),
            "connected": connected,
        })
    return samples


def _build_replay(replay_id, track_dict, vid, samples, finish_index):
    return Replay(
        replay_id=replay_id,
        track=copy.deepcopy(track_dict),
        vehicles=[vid],
        telemetry={vid: samples},
        resets={vid: []},
        finish_sample_index={vid: finish_index},
        created_at=datetime.now(timezone.utc).isoformat(),
        track_fingerprint=Track.from_dict(track_dict).config_fingerprint(),
    )


def _three_lap_points():
    """Three completed laps as a self-contained dense polyline.

    The three closed loops already contain the opening start crossing and
    every checkpoint, but a static replay ends on the south merge instead of
    driving north across the timing line one last time.  That single closing
    crossing is appended; nothing is inserted in the middle, so intermediate
    sampling strides stay faithful to the reference.
    """
    lap = _left_branch_lap()
    points = []
    for _ in range(3):
        points.extend(p.copy() for p in lap)
    points.extend([Vec3(0, 0, -30), Vec3(0, 0, -26)])  # close final lap
    return points


# ------------------------------------------------------------- case builders
def case_high_speed_missed_lap(track: Track) -> Replay:
    """Dense legal laps, but the lap-2 closing frame is a wide high-speed leap
    whose two endpoints straddle the start plane far outside the slab."""
    points = _three_lap_points()

    # locate the segments that cross z=-28 (start plane); the second closing
    # crossing (after the opening one) ends lap 2
    plane_z = -28.0
    closing = [i for i in range(1, len(points))
               if (points[i - 1].z - plane_z) * (points[i].z - plane_z) < 0]
    assert len(closing) >= 4, f"need >= 4 start crossings, got {closing}"
    target = closing[2]  # lap-2 closing crossing (0=opening, 1=lap1, 2=lap2)

    # Splice a five-sample high-speed maneuver onto the stride-4 grid.  The
    # dense (stride-1) judge sees a real in-gate crossing on the middle
    # segment (hit x=1.0); stride 2 keeps an every-other segment whose hit
    # (x=5.5) is still inside the gate; stride 4 merges the whole maneuver
    # into one segment whose plane intersection lands at x=12.1, outside the
    # [-6, 6] gate polygon.  The geometric polygon miss is therefore the
    # root cause; successor state only diverges afterwards.  Positions are
    # emitted once and never rewritten to make the verdict fit.
    anchor_index = target - 1
    while anchor_index % 4 != 0:
        anchor_index -= 1
    maneuver = [
        Vec3(10.0, 0, plane_z - 3.0),   # grid anchor (kept at every stride)
        Vec3(-2.0, 0, plane_z - 1.0),   # stride-1 crossing starts here
        Vec3(4.0,  0, plane_z + 1.0),   # stride-2 crossing stays in-gate
        Vec3(12.0, 0, plane_z + 1.5),
        Vec3(15.0, 0, plane_z + 4.0),   # next grid anchor (index % 4 == 0)
    ]
    points = points[:anchor_index] + maneuver + points[target + 2:]
    samples = _to_samples(points, speed=64.0)
    finish_index = len(samples) - 1
    return _build_replay(
        "diag_high_speed_missed_lap", track.to_dict(), "V1",
        samples, finish_index,
    )


def case_legal_fork_flagged_shortcut(track: Track) -> Replay:
    """A legal fork route.  At coarse sampling the north-gate crossing is
    missed, so the inherited successor set never opens the fork branch and
    its (still geometrically real) merge crossing is judged as a backside
    shortcut — a state-inheritance error rooted in a geometric miss."""
    points = _three_lap_points()

    # thin out the approach to the cp2 north gate (z=20) so stride-4 sampling
    # jumps across its 0.6 m slab; the fork/merge branch geometry stays dense
    kept = []
    for i, p in enumerate(points):
        if 8.0 < p.z < 26.0 and abs(p.x) < 1.0 and i % 3 != 0:
            continue
        kept.append(p)
    samples = _to_samples(kept, speed=26.0)
    return _build_replay(
        "diag_legal_fork_flagged_shortcut", track.to_dict(), "V1",
        samples, len(samples) - 1,
    )


def case_config_version_mismatch(track: Track) -> Replay:
    """A perfectly clean record; the manifest points at an edited track so
    the UI must show a version-mismatch advisory, never a cheating verdict."""
    points = _three_lap_points()
    samples = _to_samples(points, speed=22.0)
    return _build_replay(
        "diag_config_version_mismatch", track.to_dict(), "V1",
        samples, len(samples) - 1,
    )


# --------------------------------------------------------------------- main
def write_case(replay: Replay, manifest_extra: dict, out_dir: str) -> str:
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"{replay.replay_id}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(replay.to_dict(), fh, ensure_ascii=False)
    manifest = {
        "replay_id": replay.replay_id,
        "replay_file": os.path.basename(path),
        "track_fingerprint": replay.track_fingerprint,
        "generated_by": "tools/generate_diagnostic_cases.py",
        "note": ("positions are raw generated telemetry; the judge never "
                 "rewrites them to fit a verdict"),
    }
    manifest.update(manifest_extra)
    mpath = os.path.join(out_dir, f"{replay.replay_id}.manifest.json")
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, ensure_ascii=False, indent=2)
    return path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--track", default=TRACK_PATH)
    parser.add_argument("--out-dir", default=OUT_DIR)
    args = parser.parse_args(argv)
    out_dir = args.out_dir

    track = Track.load(args.track)

    cases = [
        (case_high_speed_missed_lap(track),
         {"expected_category": "geometric_cross_missed_lap",
          "expected_divergence": "geometric_cross_missed",
          "description": "high-speed leap over the thin start slab"}),
        (case_legal_fork_flagged_shortcut(track),
         {"expected_category": "state_inheritance_after_geometric_miss",
          "expected_divergence": "state_inheritance_error",
          "description": "legal fork whose merge is later called a shortcut"}),
        (case_config_version_mismatch(track),
         {"expected_category": "config_version_mismatch",
          "expected_divergence": None,
          "description": "clean record under an edited configuration",
          "paired_track_sidecar": "fork_loop_edited.json"}),
    ]

    for replay, manifest in cases:
        # independent re-judge proves the reference verdict is computed,
        # not asserted by the generator
        judges = replay_judge(replay)
        judge = judges[replay.vehicles[0]]
        manifest["reference_laps"] = judge.current_lap
        manifest["reference_valid_crossings"] = len(judge.valid_crossings())
        path = write_case(replay, manifest, out_dir)
        print(f"wrote {path} (laps={judge.current_lap}, "
              f"crossings={len(judge.valid_crossings())})")

    # sidecar track for the version-mismatch case: one checkpoint thickness
    # edited, geometry otherwise untouched
    edited = copy.deepcopy(track.to_dict())
    edited["zones"][0]["thickness"] = round(
        edited["zones"][0]["thickness"] + 0.35, 4)
    sidecar = os.path.join(out_dir, "fork_loop_edited.json")
    with open(sidecar, "w", encoding="utf-8") as fh:
        json.dump(edited, fh, ensure_ascii=False, indent=2)
    print(f"wrote {sidecar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
