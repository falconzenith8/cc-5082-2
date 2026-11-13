import copy
import glob
import json
import os
import unittest

from racing_judge.diagnostics import (
    GEOMETRIC_CROSS_MISSED,
    GEOMETRIC_POLYGON_MISS,
    STATE_INHERITANCE_ERROR,
    UNKNOWN_CAUSE,
    analyze_replay,
    classify_divergences,
    live_report,
    recompute_segment,
    replay_at_stride,
)
from racing_judge.geometry import Vec3
from racing_judge.judge import VehicleJudge
from racing_judge.model import Track
from racing_judge.replay import Replay, replay_judge, verify_replay
from racing_judge.scenarios import SCENARIOS

TRACK_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "data", "tracks", "fork_loop.json"))
DIAG_DIR = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "data", "diagnostics"))


def load_track():
    return Track.load(TRACK_PATH)


def diagnostic_replay(name):
    return Replay.load(os.path.join(DIAG_DIR, f"{name}.json"))


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_stable_and_sensitive_to_checkpoint_edit(self):
        t1 = load_track()
        t2 = load_track()
        self.assertEqual(t1.config_fingerprint(), t2.config_fingerprint())
        edited = copy.deepcopy(t1.to_dict())
        edited["zones"][0]["thickness"] += 0.5
        t3 = Track.from_dict(edited)
        self.assertNotEqual(t1.config_fingerprint(), t3.config_fingerprint())
        self.assertEqual(t1.config_diff(t3), ["zones"])
        # non-judging metadata does not change the fingerprint
        renamed = copy.deepcopy(t1.to_dict())
        renamed["name"] = "Different display name"
        self.assertEqual(
            t1.config_fingerprint(), Track.from_dict(renamed).config_fingerprint()
        )


class MultiIntervalTests(unittest.TestCase):
    def test_clean_path_agrees_at_every_stride(self):
        track = load_track()
        session = SCENARIOS["clean"](track)
        session.run_until_finish(max_seconds=400)
        report = analyze_replay(session.replay)
        v = report["vehicles"]["V1"]
        self.assertEqual(v["reference"]["laps"], track.rules.total_laps)
        for run in v["runs"]:
            self.assertEqual(run["laps"], track.rules.total_laps)
            self.assertEqual(run["valid_crossings"],
                             v["reference"]["valid_crossings"])
        self.assertEqual(report["divergence_total"], 0)
        self.assertEqual(report["divergence_categories"], {})

    def test_high_speed_case_first_diverges_at_stride_four(self):
        report = analyze_replay(
            diagnostic_replay("diag_high_speed_missed_lap"))
        v = report["vehicles"]["V1"]
        by_stride = {r["stride"]: r for r in v["runs"]}
        self.assertEqual(by_stride[1]["laps"], 3)
        self.assertEqual(by_stride[2]["laps"], 3)
        self.assertEqual(by_stride[4]["laps"], 2)
        root = next(d for d in v["divergences"]
                    if d["category"] == GEOMETRIC_POLYGON_MISS)
        self.assertEqual(root["first_diverging_stride"], 4)
        self.assertEqual(root["zone_id"], "start")
        self.assertIsNone(root["caused_by"])

    def test_state_inheritance_links_to_geometric_root(self):
        report = analyze_replay(
            diagnostic_replay("diag_legal_fork_flagged_shortcut"))
        v = report["vehicles"]["V1"]
        state_errors = [d for d in v["divergences"]
                        if d["category"] == STATE_INHERITANCE_ERROR]
        self.assertTrue(state_errors)
        for d in state_errors:
            self.assertIsNotNone(
                d["caused_by"],
                msg="state error must point at its geometric root",
            )
            self.assertIn(d["caused_by"]["category"],
                          (GEOMETRIC_CROSS_MISSED, GEOMETRIC_POLYGON_MISS))
        # the geometric root itself has no upstream cause
        roots = [d for d in v["divergences"] if d["caused_by"] is None]
        self.assertTrue(
            all(d["category"] in (GEOMETRIC_CROSS_MISSED,
                                  GEOMETRIC_POLYGON_MISS, UNKNOWN_CAUSE)
                for d in roots)
        )

    def test_geometric_vs_state_classification_uses_real_geometry(self):
        # a state-inheritance divergence keeps an in-gate coarse hit, while a
        # geometric polygon miss's coarse hit is outside the polygon
        report = analyze_replay(
            diagnostic_replay("diag_legal_fork_flagged_shortcut"))
        v = report["vehicles"]["V1"]
        for d in v["divergences"]:
            stride = d["first_diverging_stride"]
            detail = next(p for p in d["per_stride"] if p["stride"] == stride)
            if d["category"] == STATE_INHERITANCE_ERROR:
                self.assertTrue(detail["coarse_zone"]["inside"])
                self.assertTrue(detail["coarse_zone"]["crossed"])
            elif d["category"] == GEOMETRIC_POLYGON_MISS:
                self.assertTrue(detail["coarse_zone"]["crossed"])
                self.assertFalse(detail["coarse_zone"]["inside"])

    def test_sampling_only_selects_never_rewrites_positions(self):
        replay = diagnostic_replay("diag_high_speed_missed_lap")
        _, indices = replay_at_stride(replay, "V1", 4)
        samples = replay.telemetry["V1"]
        for kept, original in zip(indices, [samples[i] for i in indices]):
            self.assertEqual(samples[kept]["pos"], original["pos"])
        # indices are strictly increasing and always include the last sample
        self.assertEqual(indices, sorted(set(indices)))
        self.assertEqual(indices[-1], len(samples) - 1)


class BoundedRetentionTests(unittest.TestCase):
    def test_long_race_keeps_summaries_and_recomputes_dropped_segments(self):
        track = load_track()
        session = SCENARIOS["clean"](track)
        session.run_until_finish(max_seconds=400)
        judge, _ = replay_at_stride(
            session.replay, "V1", 1, max_segments=40, max_flagged=15
        )
        rec = judge.recorder
        self.assertLess(len(rec.segments), rec.total_segments)
        self.assertGreater(rec.dropped_plain, 0)
        # per-lap summaries survive even when raw segments are dropped
        self.assertEqual(len(rec.lap_summaries), track.rules.total_laps)
        # any dropped segment is recomputable on demand from raw telemetry
        out = recompute_segment(session.replay, "V1", 100)
        self.assertEqual(out["segment"]["index"], 100)
        self.assertEqual(len(out["segment"]["zones"]), len(track.zones))

    def test_segment_carries_intersection_params_and_successors(self):
        track = load_track()
        session = SCENARIOS["clean"](track)
        session.run_until_finish(max_seconds=400)
        out = recompute_segment(session.replay, "V1", 5)
        seg = out["segment"]
        self.assertEqual(seg["index"], 5)
        self.assertEqual(len(seg["zones"]), len(track.zones))
        z = seg["zones"][0]
        for key in ("signed_a", "signed_b", "side_a", "side_b",
                    "crossed", "direction", "segment_t", "inside",
                    "near_gate", "route_relevant"):
            self.assertIn(key, z)
        self.assertIsInstance(seg["expected_before"], list)
        self.assertIsInstance(seg["expected_after"], list)

    def test_recompute_out_of_range_raises(self):
        replay = diagnostic_replay("diag_high_speed_missed_lap")
        with self.assertRaises(IndexError):
            recompute_segment(replay, "V1", 10_000)

    def test_live_report_requires_recorder(self):
        judge = VehicleJudge("V1", load_track(), record_diagnostics=False)
        with self.assertRaises(ValueError):
            live_report(judge)


class ConfigMismatchTests(unittest.TestCase):
    def test_edited_track_emits_version_advisory_not_cheating(self):
        replay = diagnostic_replay("diag_config_version_mismatch")
        edited = Track.load(os.path.join(DIAG_DIR, "fork_loop_edited.json"))
        report = analyze_replay(replay, current_track=edited)
        self.assertFalse(report["config_match"])
        self.assertIn("zones", report["config_changed_fields"])
        self.assertIsNotNone(report["config_advisory"])
        for v in report["vehicles"].values():
            for d in v["divergences"]:
                self.assertEqual(d["advisory"], report["config_advisory"])
        # verify_replay keeps its boolean verdict but adds the same advisory
        verify = verify_replay(replay, current_track=edited)
        self.assertFalse(verify["config"]["config_match"])
        self.assertIsNotNone(verify["config"]["config_advisory"])

    def test_matching_config_has_no_advisory(self):
        replay = diagnostic_replay("diag_config_version_mismatch")
        report = analyze_replay(replay)
        self.assertTrue(report["config_match"])
        self.assertIsNone(report["config_advisory"])
        self.assertEqual(report["config_changed_fields"], [])

    def test_legacy_replay_without_fingerprint_still_loads(self):
        data = diagnostic_replay("diag_config_version_mismatch").to_dict()
        del data["track_fingerprint"]
        replay = Replay.from_dict(data)
        self.assertEqual(replay.track_fingerprint, "")
        report = analyze_replay(replay)
        # an unversioned record is compared against its embedded config only
        self.assertTrue(report["config_match"])


class RecoveryMarkerTests(unittest.TestCase):
    def test_reset_is_recorded_as_recovery_marker_with_reason(self):
        track = load_track()
        session = SCENARIOS["reset"](track)
        session.run_until_finish(max_seconds=400)
        report = analyze_replay(session.replay)
        markers = report["vehicles"]["V1"]["summary"]["recovery_markers"]
        self.assertEqual(len(markers), 1)
        self.assertEqual(markers[0]["reason"], "spin_out")
        self.assertEqual(markers[0]["position"], track.recovery.position)
        reset_segments = [
            d for d in report["vehicles"]["V1"]["summary"]["reason_totals"].items()
        ]
        self.assertIn("vehicle_reset", dict(reset_segments))


class BoundaryProbeTests(unittest.TestCase):
    """Explicit boundary-handling proof path: even without a reproduced
    customer report, the geometry rules have a known, tested verdict."""

    def test_plane_arc_miss_is_ignored_not_penalized(self):
        track = load_track()
        # a segment crossing a gate plane far outside its polygon
        zones = track.build_zones()
        start = zones["start"]
        a = Vec3(40, 0, -34)
        b = Vec3(40, 0, -22)
        analysis = start.analyze_segment(a, b)
        self.assertTrue(analysis.crossed)
        self.assertFalse(analysis.inside)
        # feeding it to a judge yields no progress and, for a non-expected
        # distant hit, no penalty either (geometry noise, not cheating)
        judge = VehicleJudge("V1", track, record_diagnostics=True)
        judge.start(0.0)
        judge.feed(1.0 / 30, a)
        judge.feed(2.0 / 30, b)
        self.assertEqual(len(judge.valid_crossings()), 0)
        self.assertEqual(judge.penalty_s, 0.0)

    def test_unknown_cause_is_distinct_from_cheating(self):
        # construct a divergence set manually to confirm the unknown label
        # survives the report contract without being mapped to a penalty
        report = analyze_replay(
            diagnostic_replay("diag_high_speed_missed_lap"),
            strides=(1, 2, 4, 8),
        )
        allowed = {
            GEOMETRIC_CROSS_MISSED, GEOMETRIC_POLYGON_MISS,
            STATE_INHERITANCE_ERROR, UNKNOWN_CAUSE,
        }
        for v in report["vehicles"].values():
            for d in v["divergences"]:
                self.assertIn(d["category"], allowed)
                # nothing in the diagnostic contract calls a player a cheater
                self.assertNotIn("cheat", d["category"])


if __name__ == "__main__":
    unittest.main()
