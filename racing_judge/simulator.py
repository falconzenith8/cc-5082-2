"""Simple vehicle dynamics and scriptable controllers.

The simulator is deliberately simple (kinematic bicycle-ish motion); judging
complexity lives entirely in the judge, fed by these continuous trajectories.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from .geometry import Vec3


@dataclass
class Control:
    throttle: float = 0.0  # -1 .. 1
    steer: float = 0.0     # -1 (left) .. 1 (right)


@dataclass
class VehicleState:
    pos: Vec3
    heading: float
    speed: float = 0.0

    def copy(self) -> "VehicleState":
        return VehicleState(Vec3(self.pos.x, self.pos.y, self.pos.z),
                            self.heading, self.speed)


@dataclass
class TelemetrySample:
    t: float
    pos: List[float]
    heading: float
    speed: float
    connected: bool = True


class Vehicle:
    def __init__(
        self,
        vehicle_id: str,
        pos: Vec3,
        heading: float,
        max_speed: float = 28.0,
        accel: float = 16.0,
        brake: float = 26.0,
        turn_rate: float = 1.0,
        wheelbase: float = 2.6,
    ):
        self.vehicle_id = vehicle_id
        self.state = VehicleState(pos.copy(), heading, 0.0)
        self.max_speed = max_speed
        self.accel = accel
        self.brake = brake
        self.turn_rate = turn_rate
        self.wheelbase = wheelbase

    def step(self, ctrl: Control, dt: float) -> VehicleState:
        s = self.state
        if ctrl.throttle >= 0:
            s.speed += self.accel * ctrl.throttle * dt
        else:
            s.speed += self.brake * ctrl.throttle * dt
        s.speed = max(-self.max_speed * 0.4, min(self.max_speed, s.speed))
        # bicycle yaw rate; steering input is the front wheel angle fraction
        steer_angle = ctrl.steer * self.turn_rate
        yaw_rate = (s.speed / self.wheelbase) * math.tan(steer_angle)
        s.heading += yaw_rate * dt
        s.pos = Vec3(
            s.pos.x + math.sin(s.heading) * s.speed * dt,
            s.pos.y,
            s.pos.z + math.cos(s.heading) * s.speed * dt,
        )
        return s.copy()

    def teleport(self, pos: Vec3, heading: float) -> None:
        self.state = VehicleState(pos.copy(), heading, 0.0)


# ---------------------------------------------------------------- controllers
class ScriptedController:
    """Pure-pursuit waypoint follower with event scripts.

    Speed is scaled by the curvature to the lookahead target so sharp corners
    are actually drivable; waypoints advanced on arrival or overshoot.
    """

    def __init__(
        self,
        waypoints: List[Vec3],
        events: Optional[Dict[float, dict]] = None,
        max_speed: float = 24.0,
        lookahead: float = 6.0,
        loop: bool = True,
    ):
        self.waypoints = waypoints
        self.events = events or {}
        self.max_speed = max_speed
        self.lookahead = lookahead
        self.loop = loop
        self.wp_index = 0
        self.laps_seen = 0
        self.disconnect_until: Optional[float] = None
        self.reset_pending: Optional[dict] = None
        self.boost_until: Optional[float] = None

    def control(self, vehicle: Vehicle, t: float, dt: float) -> Tuple[Control, dict]:
        flag = {"connected": True, "reset": None}
        ev = self.events.get(round(t, 6))
        if ev is None:
            for et, payload in self.events.items():
                if abs(et - t) <= dt * 0.5:
                    ev = payload
                    break
        if ev:
            kind = ev["kind"]
            if kind == "gap":
                self.disconnect_until = t + ev["duration"]
            elif kind == "reset":
                self.reset_pending = ev
            elif kind == "boost":
                self.boost_until = t + ev["duration"]

        if self.reset_pending is not None:
            r = self.reset_pending
            self.reset_pending = None
            flag["reset"] = r
            return Control(0, 0), flag

        if self.disconnect_until is not None:
            if t < self.disconnect_until:
                flag["connected"] = False
            else:
                self.disconnect_until = None

        if self.wp_index >= len(self.waypoints):
            return Control(-0.5, 0.0), flag

        s = vehicle.state
        target, segment, finished = self._acquire_target(s.pos)
        if finished:
            return Control(-0.6, 0.0), flag

        dx = target.x - s.pos.x
        dz = target.z - s.pos.z
        dist = math.hypot(dx, dz)
        desired = math.atan2(dx, dz)
        # wrapped heading error stays in (-pi, pi], no wrap discontinuity
        heading_err = _wrap_angle(desired - s.heading)
        # damped pure-pursuit: curvature command, gain well below saturation
        curvature = 2.0 * math.sin(heading_err) / max(dist, 1.0)
        steer_angle = max(
            -vehicle.turn_rate,
            min(vehicle.turn_rate, curvature * self.wheelbase_effective()),
        )
        steer = steer_angle / vehicle.turn_rate

        boost = self.boost_until is not None and t < self.boost_until
        target_speed = self.max_speed * (2.0 if boost else 1.0)
        # slow smoothly and early for corners
        target_speed *= max(0.2, math.cos(heading_err))
        if not self.loop and self.wp_index >= len(self.waypoints) - 2:
            target_speed = min(target_speed, 6.0)
        throttle = 1.0 if s.speed < target_speed else -0.8
        return Control(throttle, steer), flag

    def wheelbase_effective(self) -> float:
        return 2.6

    def _wp(self, index: int) -> Vec3:
        n = len(self.waypoints)
        if self.loop:
            return self.waypoints[index % n]
        return self.waypoints[min(index, n - 1)]

    def _acquire_target(self, pos: Vec3):
        n = len(self.waypoints)
        arrival = self.lookahead * 0.5
        while True:
            wp = self._wp(self.wp_index)
            if math.hypot(wp.x - pos.x, wp.z - pos.z) > arrival:
                break
            self.wp_index += 1
            if self.loop:
                if self.wp_index >= n:
                    self.wp_index -= n
                    self.laps_seen += 1
                continue
            if self.wp_index >= n:
                return None, None, True

        anchor = self._wp(self.wp_index)
        prev = pos
        traveled = 0.0
        j = self.wp_index
        limit = self.wp_index + n if self.loop else n
        while j < limit:
            wp = self._wp(j)
            seg = math.hypot(wp.x - prev.x, wp.z - prev.z)
            if traveled + seg >= self.lookahead:
                remain = self.lookahead - traveled
                f = remain / max(seg, 1e-9)
                target = Vec3(prev.x + (wp.x - prev.x) * f,
                              prev.y,
                              prev.z + (wp.z - prev.z) * f)
                return target, (anchor, wp), False
            traveled += seg
            prev = wp
            j += 1
        last = self._wp(n - 1)
        return last, (self._wp(n - 2), last), not self.loop


class TeleportController:
    """Moves the vehicle directly to the next waypoint every tick.

    Unlike the smooth followers this deliberately emits long movement
    segments, so a single continuous segment can intersect several zones at
    once and the long-segment rule is exercised with real geometry.
    """

    def __init__(self, waypoints: List[Vec3], step_speed: float = 80.0):
        self.waypoints = waypoints
        self.step_speed = step_speed
        self.index = 0
        self.laps_seen = 0

    def control(self, vehicle: Vehicle, t: float, dt: float) -> Tuple[Control, dict]:
        flag = {"connected": True, "reset": None}
        if self.index >= len(self.waypoints):
            return Control(-0.6, 0.0), flag
        target = self.waypoints[self.index]
        self.index += 1
        vehicle.teleport(target, vehicle.state.heading)
        return Control(0.0, 0.0), flag


class ManualController:

    def __init__(self):
        self._queue: List[Tuple[float, Control]] = []

    def push(self, t: float, ctrl: Control) -> None:
        self._queue.append((t, ctrl))

    def control(self, vehicle: Vehicle, t: float, dt: float) -> Tuple[Control, dict]:
        if self._queue and abs(self._queue[0][0] - t) <= dt * 0.5:
            _, ctrl = self._queue.pop(0)
            return ctrl, {"connected": True, "reset": None}
        return Control(0.0, 0.0), {"connected": True, "reset": None}


def _wrap_angle(a: float) -> float:
    while a > math.pi:
        a -= 2 * math.pi
    while a < -math.pi:
        a += 2 * math.pi
    return a
