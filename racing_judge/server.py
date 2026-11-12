"""Stdlib-only HTTP API for track editing, live judging and replays.

Endpoints (all JSON):
  GET  /api/tracks                 list track ids
  GET  /api/tracks/<id>            full track definition
  PUT  /api/tracks/<id>            validate + save a track definition
  GET  /api/scenarios              available built-in scenarios
  POST /api/races/run              {track_id, scenario} -> full judged snapshot
  POST /api/sessions               {track_id, vehicles} -> live session id
  GET  /api/sessions/<sid>         live snapshot (eligibility + penalties)
  POST /api/sessions/<sid>/step    advance one tick (optional manual controls)
  POST /api/sessions/<sid>/control {vehicle_id, throttle, steer}
  POST /api/sessions/<sid>/reset   {vehicle_id, reason?}
  GET  /api/sessions/<sid>/replay  downloadable replay document
  POST /api/replays/verify         replay document -> re-judge comparison
"""

from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional
from urllib.parse import urlparse

from .judge import VehicleJudge
from .model import Track
from .race import RaceSession
from .replay import Replay, replay_judge, verify_replay
from .scenarios import SCENARIOS
from .simulator import Control, ManualController

TRACK_DIR = "data/tracks"


class ApiState:
    """Process-wide registry of live sessions (thread guarded)."""

    def __init__(self, track_dir: str = TRACK_DIR):
        self.track_dir = track_dir
        self.sessions: Dict[str, RaceSession] = {}
        self.lock = threading.Lock()

    def track_path(self, track_id: str) -> str:
        safe = track_id.replace("/", "_").replace("..", "_")
        return f"{self.track_dir}/{safe}.json"

    def load_track(self, track_id: str) -> Track:
        return Track.load(self.track_path(track_id))


STATE = ApiState()


class ApiHandler(BaseHTTPRequestHandler):
    server_version = "RacingJudge/1.0"

    def log_message(self, fmt, *args):  # quiet by default
        return

    # ------------------------------------------------------ static frontend
    # (routing handled inside do_GET below)

    # ------------------------------------------------------------- helpers
    def _send(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _serve_static(self, path: str) -> None:
        import os
        web_root = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "web")
        if path in ("", "/"):
            path = "/index.html"
        # prevent path traversal and map extension to content type
        rel = os.path.normpath(path.lstrip("/"))
        full = os.path.normpath(os.path.join(web_root, rel))
        if not full.startswith(web_root + os.sep) or not os.path.isfile(full):
            return self._error("not found", 404)
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".js": "text/javascript; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".json": "application/json; charset=utf-8",
            ".svg": "image/svg+xml",
        }.get(os.path.splitext(full)[1], "application/octet-stream")
        with open(full, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if not length:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def _error(self, message: str, status: int = 400) -> None:
        self._send({"error": message}, status)

    def _path_parts(self):
        return [p for p in urlparse(self.path).path.split("/") if p]

    # ---------------------------------------------------------------- GET
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        parts = [p for p in parsed.path.split("/") if p]
        if not parts or parts[0] != "api":
            return self._serve_static(parsed.path)
        try:
            if parts == ["api", "tracks"]:
                import os
                ids = [
                    fn[:-5] for fn in os.listdir(STATE.track_dir)
                    if fn.endswith(".json")
                ]
                return self._send({"tracks": sorted(ids)})

            if len(parts) == 3 and parts[:2] == ["api", "tracks"]:
                track = STATE.load_track(parts[2])
                return self._send(track.to_dict())

            if parts == ["api", "scenarios"]:
                return self._send({"scenarios": sorted(SCENARIOS)})

            if len(parts) == 3 and parts[:2] == ["api", "sessions"]:
                with STATE.lock:
                    sess = STATE.sessions.get(parts[2])
                if sess is None:
                    return self._error("unknown session", 404)
                return self._send(sess.snapshot())

            if len(parts) == 4 and parts[:2] == ["api", "sessions"] \
                    and parts[3] == "replay":
                with STATE.lock:
                    sess = STATE.sessions.get(parts[2])
                if sess is None:
                    return self._error("unknown session", 404)
                return self._send(sess.replay.to_dict())

            return self._error("not found", 404)
        except FileNotFoundError:
            return self._error("track not found", 404)
        except Exception as exc:  # surface API errors as JSON
            return self._error(str(exc), 500)

    # ---------------------------------------------------------------- POST
    def do_POST(self) -> None:
        parts = self._path_parts()
        try:
            data = self._read_json()

            if parts == ["api", "races", "run"]:
                return self._run_scenario(data)

            if parts == ["api", "sessions"]:
                return self._create_session(data)

            if len(parts) == 4 and parts[:2] == ["api", "sessions"] \
                    and parts[3] == "step":
                return self._step(parts[2], data)

            if len(parts) == 4 and parts[:2] == ["api", "sessions"] \
                    and parts[3] == "control":
                return self._control(parts[2], data)

            if len(parts) == 4 and parts[:2] == ["api", "sessions"] \
                    and parts[3] == "reset":
                return self._reset(parts[2], data)

            if parts == ["api", "replays", "verify"]:
                return self._verify(data)

            return self._error("not found", 404)
        except json.JSONDecodeError:
            return self._error("invalid JSON body", 400)
        except Exception as exc:
            return self._error(str(exc), 500)

    # ----------------------------------------------------------------- PUT
    def do_PUT(self) -> None:
        parts = self._path_parts()
        try:
            if len(parts) == 3 and parts[:2] == ["api", "tracks"]:
                data = self._read_json()
                track = Track.from_dict(data)
                track.build_zones()  # raises on invalid geometry / graph
                track.save(STATE.track_path(parts[2]))
                return self._send({"saved": parts[2], "zones": len(track.zones)})
            return self._error("not found", 404)
        except (ValueError, KeyError) as exc:
            return self._error(f"invalid track: {exc}", 422)
        except Exception as exc:
            return self._error(str(exc), 500)

    # ----------------------------------------------------------- operations
    def _run_scenario(self, data: dict) -> None:
        name = data["scenario"]
        if name not in SCENARIOS:
            return self._error(f"unknown scenario {name!r}", 400)
        track = STATE.load_track(data["track_id"])
        session = SCENARIOS[name](track)
        snap = session.run_until_finish(
            max_seconds=float(data.get("max_seconds", 400))
        )
        self._send({"snapshot": snap, "replay": session.replay.to_dict()})

    def _create_session(self, data: dict) -> None:
        track = STATE.load_track(data["track_id"])
        vehicle_ids = data.get("vehicles") or ["V1"]
        session = RaceSession(track, vehicle_ids)
        for vid in vehicle_ids:
            session.attach_controller(vid, ManualController())
        sid = uuid.uuid4().hex[:12]
        with STATE.lock:
            STATE.sessions[sid] = session
        self._send({"session_id": sid, "snapshot": session.snapshot()})

    def _get_session(self, sid: str) -> Optional[RaceSession]:
        with STATE.lock:
            return STATE.sessions.get(sid)

    def _step(self, sid: str, data: dict) -> None:
        sess = self._get_session(sid)
        if sess is None:
            return self._error("unknown session", 404)
        for vid, c in data.get("controls", {}).items():
            controller = sess.controllers.get(vid)
            if not isinstance(controller, ManualController):
                controller = ManualController()
                sess.attach_controller(vid, controller)
            controller.push(
                sess.t + sess.dt,
                Control(float(c.get("throttle", 0)),
                        float(c.get("steer", 0))),
            )
        sess.start()
        sess.step()
        self._send(sess.snapshot())

    def _control(self, sid: str, data: dict) -> None:
        sess = self._get_session(sid)
        if sess is None:
            return self._error("unknown session", 404)
        vid = data["vehicle_id"]
        controller = sess.controllers.get(vid)
        if not isinstance(controller, ManualController):
            controller = ManualController()
            sess.attach_controller(vid, controller)
        controller.push(
            sess.t + sess.dt,
            Control(float(data.get("throttle", 0)),
                    float(data.get("steer", 0))),
        )
        self._send({"queued": True, "vehicle_id": vid})

    def _reset(self, sid: str, data: dict) -> None:
        sess = self._get_session(sid)
        if sess is None:
            return self._error("unknown session", 404)
        vid = data["vehicle_id"]
        from .geometry import Vec3
        rec = sess.track.recovery
        rec_pos = Vec3.from_iter(rec.position)
        sess.vehicles[vid].teleport(rec_pos, rec.heading)
        reason = data.get("reason", "manual")
        sess.judges[vid].reset_vehicle(sess.t, reason)
        sess.resets[vid].append({
            "t": round(sess.t, 6),
            "reason": reason,
            "position": rec_pos.to_list(),
            "heading": rec.heading,
        })
        self._send(sess.snapshot())

    def _verify(self, data: dict) -> None:
        replay = Replay.from_dict(data)
        report = verify_replay(replay)
        self._send(report)


def serve(host: str = "127.0.0.1", port: int = 8000,
          track_dir: str = TRACK_DIR) -> None:
    global STATE
    STATE = ApiState(track_dir)
    httpd = ThreadingHTTPServer((host, port), ApiHandler)
    print(f"Racing judge API on http://{host}:{port} (tracks: {track_dir})")
    httpd.serve_forever()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Racing judge HTTP API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--track-dir", default=TRACK_DIR)
    args = parser.parse_args()
    serve(args.host, args.port, args.track_dir)
