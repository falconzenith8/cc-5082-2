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


if __name__ == "__main__":
    unittest.main()
