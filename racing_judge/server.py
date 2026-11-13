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
  GET  /api/sessions/<sid>/diagnostics?vehicle_id=V1[&segment_index=N]
                                   bounded diagnostic view / one raw segment
  POST /api/replays/diagnostics    replay doc -> multi-interval divergence report
  POST /api/replays/segment        replay doc + index -> recomputed raw segment
  GET  /api/replays/diagnostic-cases                 committed failure samples
  GET  /api/replays/diagnostic-cases/<replay_id>     one failure-sample replay
"""

from __future__ import annotations

import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Dict, Optional
from urllib.parse import parse_qs, urlparse

from .diagnostics import analyze_replay, live_report, recompute_segment
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

            if len(parts) == 4 and parts[:2] == ["api", "sessions"] \
                    and parts[3] == "diagnostics":
                return self._session_diagnostics(parts[2], parse_qs(parsed.query))

            if parts == ["api", "replays", "diagnostic-cases"]:
                return self._list_diagnostic_cases()

            if len(parts) == 4 and parts[:2] == ["api", "replays"] \
                    and parts[2] == "diagnostic-cases":
                return self._load_diagnostic_case(parts[3])

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

            if parts == ["api", "replays", "diagnostic-cases"]:
                return self._list_diagnostic_cases()

            if len(parts) == 4 and parts[:2] == ["api", "replays"] \
                    and parts[2] == "diagnostic-cases":
                return self._load_diagnostic_case(parts[3])

            if parts == ["api", "replays", "diagnostics"]:
                return self._replay_diagnostics(data)

            if parts == ["api", "replays", "segment"]:
                return self._replay_segment(data)

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
        current_track = self._optional_current_track(
            data.get("current_track_id"), replay
        )
        report = verify_replay(replay, current_track=current_track)
        self._send(report)

    def _optional_current_track(self, track_id, replay: Replay):
        """Resolve the on-disk track for version comparison, if requested and
        present; absent/unknown tracks never silently replace the embedded
        configuration."""
        if not track_id:
            return None
        try:
            return STATE.load_track(track_id)
        except FileNotFoundError:
            return None

    def _session_diagnostics(self, sid: str, query: dict) -> None:
        sess = self._get_session(sid)
        if sess is None:
            return self._error("unknown session", 404)
        vid = (query.get("vehicle_id") or [sess.replay.vehicles[0]])[0]
        if vid not in sess.judges:
            return self._error("unknown vehicle", 404)
        judge = sess.judges[vid]
        seg_index = query.get("segment_index")
        if seg_index is not None:
            try:
                index = int(seg_index[0])
            except ValueError:
                return self._error("segment_index must be an integer", 400)
            seg = judge.recorder.get_segment(index)
            if seg is None:
                # bounded retention dropped this plain segment; re-derive it
                # on demand from the session's untouched raw telemetry
                try:
                    recomputed = recompute_segment(
                        sess.replay, vid, index)
                except (IndexError, LookupError) as exc:
                    return self._error(str(exc), 404)
                return self._send({
                    "vehicle_id": vid,
                    "segment": recomputed["segment"],
                    "total_segments": recomputed["total_segments"],
                    "recomputed": True,
                })
            return self._send({
                "vehicle_id": vid,
                "segment": seg.to_dict(),
                "total_segments": judge.recorder.total_segments,
                "recomputed": False,
            })
        self._send(live_report(judge))

    def _replay_diagnostics(self, data: dict) -> None:
        replay = Replay.from_dict(data)
        current_track = self._optional_current_track(
            data.get("current_track_id"), replay
        )
        strides = data.get("strides") or [1, 2, 4, 8]
        report = analyze_replay(
            replay, current_track=current_track, strides=strides
        )
        self._send(report)

    def _replay_segment(self, data: dict) -> None:
        replay = Replay.from_dict(data)
        vid = data.get("vehicle_id") or replay.vehicles[0]
        try:
            index = int(data["segment_index"])
        except (KeyError, TypeError, ValueError):
            return self._error("segment_index is required", 400)
        stride = int(data.get("stride", 1))
        try:
            self._send(recompute_segment(replay, vid, index, stride))
        except (IndexError, LookupError) as exc:
            self._error(str(exc), 404)

    def _diagnostic_dir(self) -> str:
        import os
        return os.path.join(os.path.dirname(STATE.track_dir), "diagnostics")

    def _list_diagnostic_cases(self) -> None:
        import os
        directory = self._diagnostic_dir()
        cases = []
        if os.path.isdir(directory):
            for fn in sorted(os.listdir(directory)):
                if not fn.endswith(".manifest.json"):
                    continue
                with open(os.path.join(directory, fn), encoding="utf-8") as fh:
                    manifest = json.load(fh)
                cases.append({
                    "replay_id": manifest.get("replay_id"),
                    "replay_file": manifest.get("replay_file"),
                    "expected_category": manifest.get("expected_category"),
                    "expected_divergence": manifest.get("expected_divergence"),
                    "description": manifest.get("description"),
                    "reference_laps": manifest.get("reference_laps"),
                    "track_fingerprint": manifest.get("track_fingerprint"),
                    "paired_track_sidecar":
                        manifest.get("paired_track_sidecar"),
                })
        self._send({"cases": cases})

    def _load_diagnostic_case(self, replay_id: str) -> None:
        import os
        safe = replay_id.replace("/", "_").replace("..", "_")
        path = os.path.join(self._diagnostic_dir(), f"{safe}.json")
        if not os.path.isfile(path):
            return self._error("diagnostic case not found", 404)
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
        self._send(payload)


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
