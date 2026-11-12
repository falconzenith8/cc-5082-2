"""Deterministic race scenarios exercising every judging rule."""

from __future__ import annotations

import math
from typing import Dict, List, Tuple

from .geometry import Vec3
from .model import Track
from .race import RaceSession
from .simulator import ScriptedController, TeleportController


def _dense(corners: List[Vec3], spacing: float = 4.0) -> List[Vec3]:
    """Resample a polyline so pure pursuit always has a nearby target."""
    out: List[Vec3] = []
    for a, b in zip(corners, corners[1:]):
        d = a.distance(b)
        n = max(1, int(math.ceil(d / spacing)))
        for k in range(n):
            t = k / n
            out.append(Vec3(a.x + (b.x - a.x) * t,
                            a.y + (b.y - a.y) * t,
                            a.z + (b.z - a.z) * t))
    out.append(corners[-1].copy())
    return out


def _arc(cx: float, cz: float, radius: float, a0: float, a1: float,
         steps: int = 8) -> List[Vec3]:
    pts = []
    for k in range(steps + 1):
        a = a0 + (a1 - a0) * k / steps
        pts.append(Vec3(cx + radius * math.cos(a), 0,
                        cz + radius * math.sin(a)))
    return pts


def _closed_loop_points(side: int) -> List[Vec3]:
    """Closed rounded-rectangle loop whose return leg merges horizontally.

    The south arc ends at (ncx, -30) and the car returns on a constant-z=-30
    horizontal segment to the center straight.  That segment never crosses any
    gate plane, so each gate is intersected exactly once per lap inside its
    polygon:
      center gates (start, cp1, cp2) narrow polygons around x=0;
      branch gates (fork_*, merge_*) polygons around x=+-44 only.
    Arc crossings of shared z-planes land outside every gate polygon and are
    ignored as plane misses on non-expected zones.
    """
    r = 20.0
    branch_x = side * 44.0
    z_north = 30.0
    z_south = -30.0
    arc_cx = side * 24.0           # arc center x, radius 20 -> reaches +-44
    raw: List[Vec3] = []
    raw.append(Vec3(0, 0, z_south))            # center straight, south end
    raw.append(Vec3(0, 0, z_north))            # northbound through all center gates
    raw.append(Vec3(arc_cx, 0, z_north))       # connector to north arc
    raw += _arc(arc_cx, z_north - r, r,
                math.pi / 2,
                math.pi if side < 0 else 0.0)[1:]
    raw.append(Vec3(branch_x, 0, z_south + r))  # branch straight southbound
    raw += _arc(arc_cx, z_south + r, r,
                math.pi if side < 0 else 0.0,
                3 * math.pi / 2 if side < 0 else -math.pi / 2)[1:]
    raw.append(Vec3(0, 0, z_south))            # horizontal merge back to center
    dense = _dense(raw, spacing=2.5)
    return dense[:-1]


LEFT_BRANCH = _closed_loop_points(-1)
RIGHT_BRANCH = _closed_loop_points(+1)


def _lap_waypoints(branch: str, laps: int) -> List[Vec3]:
    """Repeat a geometrically closed loop; the path itself has no seam."""
    base = LEFT_BRANCH if branch == "left" else RIGHT_BRANCH
    pts: List[Vec3] = []
    for _ in range(laps):
        pts.extend(p.copy() for p in base)
    return pts


def _session(track: Track, vid: str = "V1") -> RaceSession:
    return RaceSession(track, [vid])


def scenario_clean_laps(track: Track, laps: int = None) -> RaceSession:
    s = _session(track)
    s.attach_controller("V1", ScriptedController(
        LEFT_BRANCH, max_speed=22, lookahead=7.0, loop=True))
    return s


def scenario_fork_merge(track: Track) -> RaceSession:
    """Two vehicles take different branches; both must count the merge."""
    s = RaceSession(track, ["VL", "VR"])
    s.attach_controller(
        "VL", ScriptedController(LEFT_BRANCH, max_speed=22,
                                 lookahead=7.0, loop=True)
    )
    s.attach_controller(
        "VR", ScriptedController(RIGHT_BRANCH, max_speed=24,
                                 lookahead=7.0, loop=True)
    )
    return s


def scenario_high_speed_tunnel(track: Track) -> RaceSession:
    """Boost so a single frame leap crosses the thin slab (still detected
    via continuous segment intersection)."""
    s = _session(track)
    events = {round(2.0, 6): {"kind": "boost", "duration": 1.2}}
    s.attach_controller("V1", ScriptedController(
        LEFT_BRANCH, events, max_speed=26, lookahead=7.0, loop=True))
    return s


def scenario_multi_zone_same_frame(track: Track) -> RaceSession:
    """One continuous segment intersects several zones in a single frame.

    A teleport-style controller emits long segments: cp1+cp2 share one z-plane
    jump, fork+merge share one branch jump.  The judge orders intersections by
    segment parameter and flags the long segments explicitly, while every
    crossing remains a real continuous intersection.
    """
    s = _session(track)
    wps = [
        Vec3(0, 0, -24),       # opening start crossing
        Vec3(0, 0, 14),        # cp1 + cp2 in one segment (ordered on t)
        Vec3(-44, 0, 14),      # lateral reposition (no gate plane crossed)
        Vec3(-44, 0, -14),     # fork_left + merge_w in one segment
        Vec3(-44, 0, -30),
        Vec3(0, 0, -30),       # horizontal return below the start plane
        Vec3(0, 0, -24),       # closing start crossing opens the next lap
    ] * (track.rules.total_laps + 1)
    s.attach_controller("V1", TeleportController(wps))
    # long real segments (>= 38 lateral / 44 longitudinal) are flagged
    track.rules.max_segment_length = 30.0
    return s


def scenario_reverse_reentry(track: Track) -> RaceSession:
    """Cross a gate forward, reverse back through it, then go forward again.

    Reverse crossing is penalized and yields no progress; the re-forward is
    no longer an expected zone (backside), while the declared fork/merge
    route remains available so the lap can still be completed.
    """
    s = _session(track)
    wps = [
        Vec3(0, 0, -24),
        Vec3(0, 0, 24),      # through start, cp1, cp2
        Vec3(0, 0, 14),      # back through cp2 (reverse)
        Vec3(0, 0, 24),      # forward again: cp2 now unexpected (backside)
        Vec3(-44, 0, 24), Vec3(-44, 0, -14),
        Vec3(-44, 0, -30), Vec3(0, 0, -30), Vec3(0, 0, -24),
    ] * (track.rules.total_laps + 1)
    s.attach_controller("V1", TeleportController(wps))
    return s


def scenario_disconnect_return(track: Track) -> RaceSession:
    """Telemetry gap while moving; any crossing inside the gap is invalid."""
    s = _session(track)
    events = {
        round(0.5, 6): {"kind": "gap", "duration": 1.6},
        round(6.0, 6): {"kind": "gap", "duration": 0.9},
    }
    s.attach_controller("V1", ScriptedController(
        LEFT_BRANCH, events, max_speed=22, lookahead=7.0, loop=True))
    return s


def scenario_reset_retains_progress(track: Track) -> RaceSession:
    """Reset mid-lap: vehicle returns to recovery point, progress retained,
    penalty recorded."""
    s = _session(track)
    events = {round(4.0, 6): {"kind": "reset", "reason": "spin_out"}}
    s.attach_controller("V1", ScriptedController(
        LEFT_BRANCH, events, max_speed=22, lookahead=7.0, loop=True))
    return s


def scenario_edge_jitter(track: Track) -> RaceSession:
    """Oscillate beside a gate edge inside the slab without crossing it."""
    s = _session(track)
    jitter = []
    for _ in range(6):
        jitter.append(Vec3(6.4, 0, -28.4))
        jitter.append(Vec3(6.9, 0, -27.6))
    # approach the gate edge from outside (east) without cutting the plane
    wps = [Vec3(9.0, 0, -28.0)] + jitter + [
        Vec3(8.0, 0, -28.0), Vec3(9.0, 0, -24.0)
    ] + LEFT_BRANCH
    s.attach_controller("V1", ScriptedController(
        wps, max_speed=12, lookahead=1.0, loop=True))
    return s


def scenario_backside_cross(track: Track) -> RaceSession:
    """Skip the north gate by cutting across: hit fork_left before cp2 is
    legally satisfied -> backside / wrong-order, no progress."""
    s = _session(track)
    wps = [
        Vec3(0, 0, -24),        # opening start crossing only
        Vec3(-22, 0, -10),
        Vec3(-44, 0, 0),       # approach fork_left from the wrong side
        Vec3(-44, 0, 14),      # cross fork_left while cp2 is expected
        Vec3(-44, 0, -14),     # merge_w also not yet legal
        Vec3(-44, 0, -30), Vec3(0, 0, -30),
    ] * (track.rules.total_laps + 1)
    s.attach_controller("V1", TeleportController(wps))
    return s


SCENARIOS = {
    "clean": scenario_clean_laps,
    "fork_merge": scenario_fork_merge,
    "high_speed": scenario_high_speed_tunnel,
    "multi_zone": scenario_multi_zone_same_frame,
    "reverse": scenario_reverse_reentry,
    "disconnect": scenario_disconnect_return,
    "reset": scenario_reset_retains_progress,
    "jitter": scenario_edge_jitter,
    "backside": scenario_backside_cross,
}
