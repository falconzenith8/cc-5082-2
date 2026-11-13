import json
import os
import shutil
import tempfile
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer

from racing_judge import server
from racing_judge.server import ApiHandler


def call(method, url, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req) as res:
            return res.status, json.loads(res.read().decode())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode())


class ServerApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        src = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "data", "tracks", "fork_loop.json"))
        shutil.copy(src, os.path.join(cls.tmp, "fork_loop.json"))
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), ApiHandler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()
        server.STATE = server.ApiState(cls.tmp)
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_tracks_and_scenarios(self):
        _, tracks = call("GET", f"{self.base}/api/tracks")
        self.assertIn("fork_loop", tracks["tracks"])
        _, scenarios = call("GET", f"{self.base}/api/scenarios")
        self.assertIn("clean", scenarios["scenarios"])

    def test_get_track_and_static(self):
        _, track = call("GET", f"{self.base}/api/tracks/fork_loop")
        self.assertEqual(track["track_id"], "fork_loop_03")
        with urllib.request.urlopen(f"{self.base}/index.html") as res:
            self.assertEqual(res.status, 200)

    def test_run_scenario_and_verify_replay(self):
        status, out = call("POST", f"{self.base}/api/races/run",
                           {"track_id": "fork_loop", "scenario": "clean"})
        self.assertEqual(status, 200)
        v = out["snapshot"]["vehicles"]["V1"]["summary"]
        self.assertTrue(v["finished"])
        self.assertEqual(v["penalty_s"], 0)
        rep = out["replay"]
        status, report = call("POST", f"{self.base}/api/replays/verify", rep)
        self.assertEqual(status, 200)
        self.assertTrue(report["matches"])

    def test_live_session_step_reset_and_replay(self):
        _, out = call("POST", f"{self.base}/api/sessions",
                      {"track_id": "fork_loop", "vehicles": ["V1"]})
        sid = out["session_id"]
        _, stepped = call("POST", f"{self.base}/api/sessions/{sid}/step",
                          {"controls": {"V1": {"throttle": 1, "steer": 0}}})
        self.assertGreater(stepped["t"], 0)
        _, reset = call("POST", f"{self.base}/api/sessions/{sid}/reset",
                        {"vehicle_id": "V1", "reason": "test"})
        self.assertEqual(reset["vehicles"]["V1"]["penalties"][0]["source"],
                         "reset")
        _, replay = call("GET", f"{self.base}/api/sessions/{sid}/replay")
        self.assertEqual(len(replay["resets"]["V1"]), 1)

    def test_invalid_track_rejected(self):
        bad = {
            "track_id": "bad", "name": "x", "zones": [],
            "start_position": [0, 0, 0], "start_heading": 0,
            "recovery": {}, "rules": {}, "penalty_policy": {},
        }
        status, out = call("PUT", f"{self.base}/api/tracks/bad", bad)
        self.assertEqual(status, 422)
        self.assertIn("error", out)

    def test_valid_track_save_roundtrip(self):
        _, track = call("GET", f"{self.base}/api/tracks/fork_loop")
        status, out = call("PUT", f"{self.base}/api/tracks/fork_loop_copy",
                           track)
        self.assertEqual(status, 200)
        self.assertTrue(os.path.exists(
            os.path.join(self.tmp, "fork_loop_copy.json")))

    def test_live_session_diagnostics_and_segment(self):
        _, out = call("POST", f"{self.base}/api/sessions",
                      {"track_id": "fork_loop", "vehicles": ["V1"]})
        sid = out["session_id"]
        for _ in range(6):
            call("POST", f"{self.base}/api/sessions/{sid}/step",
                 {"controls": {"V1": {"throttle": 1, "steer": 0}}})
        status, diag = call(
            "GET", f"{self.base}/api/sessions/{sid}/diagnostics?vehicle_id=V1")
        self.assertEqual(status, 200)
        self.assertIn("retention", diag["summary"])
        self.assertIn("lap_summaries", diag["summary"])
        self.assertTrue(diag["summary"]["retention"]["total_segments"] > 0)
        index = diag["current_segment_index"]
        status, seg = call(
            "GET", f"{self.base}/api/sessions/{sid}/diagnostics"
                   f"?vehicle_id=V1&segment_index={index}")
        self.assertEqual(status, 200)
        self.assertEqual(seg["segment"]["index"], index)
        self.assertTrue(len(seg["segment"]["zones"]) >= 1)

    def test_live_dropped_segment_is_recomputed_on_demand(self):
        from racing_judge.diagnostics import DiagnosticRecorder
        _, out = call("POST", f"{self.base}/api/sessions",
                      {"track_id": "fork_loop", "vehicles": ["V1"]})
        sid = out["session_id"]
        for _ in range(6):
            call("POST", f"{self.base}/api/sessions/{sid}/step",
                 {"controls": {"V1": {"throttle": 1, "steer": 0}}})
        sess = server.STATE.sessions[sid]
        judge = sess.judges["V1"]
        # swap in a tiny empty recorder to simulate bounded retention having
        # dropped the early plain segments; raw telemetry is untouched
        judge.recorder = DiagnosticRecorder(max_segments=4, max_flagged=2)
        self.assertIsNone(judge.recorder.get_segment(0))
        status, seg = call(
            "GET", f"{self.base}/api/sessions/{sid}/diagnostics"
                   f"?vehicle_id=V1&segment_index=0")
        self.assertEqual(status, 200)
        self.assertTrue(seg["recomputed"])
        self.assertEqual(seg["segment"]["index"], 0)
        self.assertGreater(seg["total_segments"], 0)
        # an out-of-range index is still a hard 404
        status, err = call(
            "GET", f"{self.base}/api/sessions/{sid}/diagnostics"
                   f"?vehicle_id=V1&segment_index=99999")
        self.assertEqual(status, 404)
        self.assertIn("error", err)

    def test_replay_diagnostics_endpoint_classifies_divergence(self):
        diag_dir = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "data", "diagnostics"))
        with open(os.path.join(diag_dir,
                               "diag_high_speed_missed_lap.json")) as fh:
            replay = json.load(fh)
        status, report = call("POST",
                              f"{self.base}/api/replays/diagnostics", replay)
        self.assertEqual(status, 200)
        self.assertIn("geometric_polygon_miss",
                      report["divergence_categories"])
        status, seg = call("POST", f"{self.base}/api/replays/segment", {
            **replay, "vehicle_id": "V1", "segment_index": 3,
        })
        self.assertEqual(status, 200)
        self.assertEqual(seg["segment"]["index"], 3)

    def test_diagnostic_cases_listed_and_loadable(self):
        # the API resolves diagnostics as a sibling of the configured track
        # directory; stage the committed samples next to the temp track dir
        diag_tmp = os.path.join(os.path.dirname(self.tmp), "diagnostics")
        os.makedirs(diag_tmp, exist_ok=True)
        src_diag = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "data", "diagnostics"))
        staged = []
        try:
            for fn in os.listdir(src_diag):
                if fn.endswith(".json"):
                    shutil.copy(os.path.join(src_diag, fn),
                                os.path.join(diag_tmp, fn))
                    staged.append(os.path.join(diag_tmp, fn))
            status, listing = call("GET",
                                   f"{self.base}/api/replays/diagnostic-cases")
            self.assertEqual(status, 200)
            ids = [c["replay_id"] for c in listing["cases"]]
            self.assertIn("diag_high_speed_missed_lap", ids)
            case = next(c for c in listing["cases"]
                        if c["replay_id"] == "diag_high_speed_missed_lap")
            self.assertTrue(case["replay_file"].endswith(".json"))
            status, replay = call(
                "GET",
                f"{self.base}/api/replays/diagnostic-cases/"
                "diag_high_speed_missed_lap")
            self.assertEqual(status, 200)
            self.assertEqual(replay["replay_id"],
                             "diag_high_speed_missed_lap")
            self.assertIn("V1", replay["vehicles"])
            # traversal attempts cannot escape the diagnostics directory
            status, _ = call(
                "GET",
                f"{self.base}/api/replays/diagnostic-cases/"
                "..%2f..%2ffork_loop")
            self.assertEqual(status, 404)
        finally:
            for path in staged:
                os.remove(path)
            os.rmdir(diag_tmp)

    def test_replay_verify_reports_config_mismatch_advisory(self):
        diag_dir = os.path.abspath(os.path.join(
            os.path.dirname(__file__), "..", "data", "diagnostics"))
        with open(os.path.join(diag_dir,
                               "diag_config_version_mismatch.json")) as fh:
            replay = json.load(fh)
        # register the edited sidecar as a track, then verify against it
        with open(os.path.join(diag_dir, "fork_loop_edited.json")) as fh:
            edited = json.load(fh)
        status, _ = call("PUT", f"{self.base}/api/tracks/fork_loop_edited",
                         edited)
        self.assertEqual(status, 200)
        status, report = call("POST", f"{self.base}/api/replays/verify", {
            **replay, "current_track_id": "fork_loop_edited",
        })
        self.assertEqual(status, 200)
        self.assertFalse(report["config"]["config_match"])
        self.assertIsNotNone(report["config"]["config_advisory"])


if __name__ == "__main__":
    unittest.main()
