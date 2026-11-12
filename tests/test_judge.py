import os
import unittest

from racing_judge.model import Track
from racing_judge.scenarios import SCENARIOS
from racing_judge.replay import verify_replay

TRACK_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "tracks",
                          "fork_loop.json")


def load_track():
    return Track.load(os.path.abspath(TRACK_PATH))


def run(name, track=None):
    track = track or load_track()
    session = SCENARIOS[name](track)
    snap = session.run_until_finish(max_seconds=400)
    return track, session, snap


class CleanLapTests(unittest.TestCase):
    def test_clean_run_finishes_required_laps(self):
        track, session, snap = run("clean")
        v = snap["vehicles"]["V1"]
        self.assertTrue(v["summary"]["finished"])
        self.assertEqual(v["summary"]["lap"], track.rules.total_laps)
        # 5 accepted crossings per lap (start + cp1 + cp2 + one fork + one
        # merge), plus the final start crossing that closes the last lap
        self.assertEqual(v["summary"]["valid_crossings"],
                         track.rules.total_laps * 5 + 1)
        self.assertEqual(v["summary"]["penalty_s"], 0)
        lap_events = [e for e in v["events"] if e["kind"] == "lap_completed"]
        self.assertEqual(len(lap_events), track.rules.total_laps)
        finish_events = [e for e in v["events"] if e["kind"] == "race_finish"]
        self.assertEqual(len(finish_events), 1)

    def test_fork_branches_both_valid_and_merge_rejoins(self):
        _, _, snap = run("fork_merge")
        for vid in ("VL", "VR"):
            v = snap["vehicles"][vid]
            self.assertTrue(v["summary"]["finished"], msg=vid)
            self.assertEqual(v["summary"]["valid_crossings"],
                             load_track().rules.total_laps * 5 + 1)
            self.assertEqual(v["summary"]["penalty_s"], 0)
        # east car is faster on average -> ranks first on finish event
        self.assertEqual(snap["ranking"][0]["vehicle_id"], "VR")

    def test_progress_driven_by_intersection_not_distance(self):
        # parking the car near start forever must never finish
        from racing_judge.geometry import Vec3
        from racing_judge.judge import VehicleJudge
        track = load_track()
        judge = VehicleJudge("V1", track)
        judge.start(0.0)
        pos = Vec3.from_iter(track.start_position)
        for k in range(300):
            judge.feed(k / 30.0, pos)
        self.assertFalse(judge.finished)
        self.assertEqual(judge.current_lap, 0)
        self.assertEqual(len(judge.valid_crossings()), 0)


class HighSpeedTests(unittest.TestCase):
    def test_high_speed_leap_across_thin_zone_still_counts(self):
        _, _, snap = run("high_speed")
        v = snap["vehicles"]["V1"]
        # continuous segment analysis must still catch every crossing
        self.assertEqual(v["summary"]["valid_crossings"],
                         load_track().rules.total_laps * 5 + 1)
        self.assertTrue(v["summary"]["finished"])
        # boost may produce long segments; either way crossings are retained
        long_ev = [e for e in v["events"] if e["kind"] == "long_segment"]
        # evidence shows real intersection parameters, not point proximity
        for e in v["evidence"]:
            if e["valid"]:
                self.assertTrue(0.0 <= e["segment_t"] <= 1.0)
                self.assertTrue(e["inside"])


class MultiZoneSameFrameTests(unittest.TestCase):
    def test_multi_zone_segment_ordered_geometrically(self):
        _, _, snap = run("multi_zone")
        v = snap["vehicles"]["V1"]
        ev = v["evidence"]
        # within each frame, crossings sorted by segment parameter
        by_t = {}
        for e in ev:
            by_t.setdefault(e["t"], []).append(e)
        for group in by_t.values():
            ts = [e["segment_t"] for e in group]
            self.assertEqual(ts, sorted(ts))
        # long teleport segments are explicitly flagged, not silently accepted
        self.assertTrue(
            any(e["kind"] == "long_segment" for e in v["events"])
        )


class ReverseReentryTests(unittest.TestCase):
    def test_reverse_cross_penalized_and_progress_not_advanced(self):
        _, _, snap = run("reverse")
        v = snap["vehicles"]["V1"]
        reverse_ev = [e for e in v["events"] if e["kind"] == "reverse_cross"]
        self.assertGreaterEqual(len(reverse_ev), 1)
        penalties = [p for p in v["penalties"] if p["source"] == "reverse_cross"]
        self.assertGreaterEqual(len(penalties), 1)
        # race can still finish: re-forward after reverse is accepted again
        self.assertTrue(v["summary"]["finished"])
        # invalid reverse evidence recorded alongside valid forward evidence
        invalids = [e for e in v["evidence"] if not e["valid"]]
        self.assertTrue(any(e["invalidation"] == "reverse" for e in invalids))


class DisconnectTests(unittest.TestCase):
    def test_gap_crossings_invalidated_but_race_recovers(self):
        _, _, snap = run("disconnect")
        v = snap["vehicles"]["V1"]
        gap_events = [e for e in v["events"] if e["kind"] == "telemetry_gap"]
        self.assertGreaterEqual(len(gap_events), 1)
        gap_invalid = [
            e for e in v["evidence"] if e["invalidation"] == "telemetry_gap"
        ]
        self.assertGreaterEqual(len(gap_invalid), 1)
        gap_pen = [p for p in v["penalties"] if p["source"] == "gap"]
        self.assertGreaterEqual(len(gap_pen), 1)
        # car completes remaining course after reconnecting
        self.assertTrue(v["summary"]["finished"])


class ResetTests(unittest.TestCase):
    def test_reset_keeps_legal_progress_and_records_penalty(self):
        track, session, snap = run("reset")
        v = snap["vehicles"]["V1"]
        resets = [e for e in v["events"] if e["kind"] == "vehicle_reset"]
        self.assertEqual(len(resets), 1)
        reset_pen = [p for p in v["penalties"] if p["source"] == "reset"]
        self.assertEqual(len(reset_pen), 1)
        # valid crossings before the reset remain in evidence
        reset_t = resets[0]["t"]
        before = [e for e in v["evidence"] if e["valid"] and e["t"] < reset_t]
        after = [e for e in v["evidence"] if e["valid"] and e["t"] > reset_t]
        self.assertGreater(len(before), 0)
        self.assertGreater(len(after), 0)
        self.assertTrue(v["summary"]["finished"])
        # recovery position declared in config was actually used
        rec_pos = track.recovery.position
        idx = next(i for i, s in enumerate(session.telemetry["V1"])
                   if any(r["t"] == s["t"] for r in session.resets["V1"]))
        nxt = session.telemetry["V1"][idx]
        for got, want in zip(nxt["pos"], rec_pos):
            self.assertAlmostEqual(got, want, places=4)


class JitterTests(unittest.TestCase):
    def test_edge_oscillation_does_not_count_as_crossing(self):
        _, _, snap = run("jitter")
        v = snap["vehicles"]["V1"]
        # during the pure edge-jitter phase (first 0.3 s, before the car
        # leaves the slab and rejoins the loop) no valid crossing is created
        early = [e for e in v["evidence"] if e["t"] < 0.3]
        self.assertFalse(any(e["valid"] for e in early))
        invalids = [e for e in early if not e["valid"]]
        self.assertTrue(all(e["invalidation"] == "outside_polygon"
                            for e in invalids))
        grazes = [e for e in v["events"] if e["kind"] == "edge_graze"]
        self.assertGreater(len(grazes), 0)
        jitters = [e for e in v["events"] if e["kind"] == "edge_jitter"]
        self.assertGreaterEqual(len(jitters), 1)
        # course still completed afterwards via genuine crossings
        self.assertTrue(v["summary"]["finished"])


class BacksideTests(unittest.TestCase):
    def test_backside_cross_never_advances(self):
        _, _, snap = run("backside")
        v = snap["vehicles"]["V1"]
        back = [e for e in v["events"] if e["kind"] == "backside_cross"]
        self.assertGreaterEqual(len(back), 1)
        invalid = [e for e in v["evidence"]
                   if e["invalidation"] == "backside_not_expected"]
        self.assertGreaterEqual(len(invalid), 1)
        pen_sources = {p["source"] for p in v["penalties"]}
        self.assertIn("backside_cross", pen_sources)
        self.assertIn("wrong_order", pen_sources)


class ReplayConsistencyTests(unittest.TestCase):
    def test_every_scenario_replays_to_identical_laps_and_evidence(self):
        for name in SCENARIOS:
            with self.subTest(scenario=name):
                track, session, snap = run(name)
                original = {
                    vid: {
                        "laps": d["summary"]["lap"] -
                        (0 if d["summary"]["finished"] else 1),
                        "valid_crossings": d["summary"]["valid_crossings"],
                        "penalty_s": d["summary"]["penalty_s"],
                        "total_time_s": d["summary"]["total_time_s"],
                    }
                    for vid, d in snap["vehicles"].items()
                }
                report = verify_replay(session.replay, original)
                self.assertTrue(report["matches"], msg=name)
                # evidence counts identical after re-judging raw telemetry
                for vid, entry in report["vehicles"].items():
                    self.assertEqual(
                        len(entry["replayed"]["evidence"]),
                        len(snap["vehicles"][vid]["evidence"]),
                    )


if __name__ == "__main__":
    unittest.main()
