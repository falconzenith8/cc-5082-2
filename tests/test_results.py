import os
import unittest

from racing_judge.geometry import Vec3
from racing_judge.judge import VehicleJudge
from racing_judge.model import Track
from racing_judge.results import rank_vehicles

TRACK_PATH = os.path.abspath(os.path.join(
    os.path.dirname(__file__), "..", "data", "tracks", "fork_loop.json"))


def make_judge(vid, track, finished, finish_t, valid_s, penalty_s, crossings):
    j = VehicleJudge(vid, track)
    j.finished = finished
    j.finish_t = finish_t
    j.valid_driving_s = valid_s
    j.penalty_s = penalty_s
    j.current_lap = track.rules.total_laps if finished else 1
    from racing_judge.judge import CrossingEvidence
    j.evidence = [
        CrossingEvidence(t=float(k), zone_id="z", direction=1, valid=True,
                         segment_t=0.5, hit=[0, 0, 0], side_a=-1, side_b=1,
                         inside=True, lap_at_event=1)
        for k in range(crossings)
    ]
    return j


class RankingTests(unittest.TestCase):
    def setUp(self):
        self.track = Track.load(TRACK_PATH)

    def test_finish_event_order_with_tie_window(self):
        self.track.rules.tie_policy = "finish_event"
        self.track.rules.finish_window_s = 0.05
        judges = [
            make_judge("A", self.track, True, 10.00, 10.0, 0.0, 15),
            make_judge("B", self.track, True, 10.03, 9.0, 1.0, 15),
            make_judge("C", self.track, True, 12.00, 12.0, 0.0, 15),
        ]
        rows = rank_vehicles(judges, self.track)
        self.assertEqual([r["vehicle_id"] for r in rows], ["A", "B", "C"])
        self.assertEqual(rows[0]["rank"], rows[1]["rank"])  # tied inside window
        self.assertNotEqual(rows[1]["rank"], rows[2]["rank"])

    def test_min_valid_time_breaks_tie(self):
        self.track.rules.tie_policy = "min_valid_time"
        judges = [
            make_judge("A", self.track, True, 10.0, 10.0, 0.0, 15),
            make_judge("B", self.track, True, 10.0, 9.2, 0.8, 15),
        ]
        rows = rank_vehicles(judges, self.track)
        self.assertEqual(rows[0]["vehicle_id"], "B")
        self.assertEqual(rows[0]["rank"], 1)

    def test_shared_rank_dense_ordering(self):
        self.track.rules.tie_policy = "shared_rank"
        self.track.rules.finish_window_s = 0.05
        judges = [
            make_judge("A", self.track, True, 10.0, 10.0, 0.0, 15),
            make_judge("B", self.track, True, 10.04, 10.0, 0.0, 15),
            make_judge("C", self.track, True, 11.0, 11.0, 0.0, 15),
        ]
        rows = rank_vehicles(judges, self.track)
        self.assertEqual(rows[0]["rank"], 1)
        self.assertEqual(rows[1]["rank"], 1)
        self.assertEqual(rows[2]["rank"], 2)

    def test_dnf_sorted_by_evidence_then_elapsed(self):
        self.track.rules.tie_policy = "finish_event"
        j1 = make_judge("D1", self.track, False, None, 5.0, 0.0, 4)
        j1.elapsed_s = 20.0
        j2 = make_judge("D2", self.track, False, None, 5.0, 0.0, 8)
        j2.elapsed_s = 30.0
        j3 = make_judge("F1", self.track, True, 15.0, 15.0, 0.0, 15)
        rows = rank_vehicles([j1, j2, j3], self.track)
        self.assertEqual(rows[0]["vehicle_id"], "F1")
        self.assertEqual(rows[1]["vehicle_id"], "D2")
        self.assertEqual(rows[2]["status"], "dnf")
        self.assertIsNone(rows[2]["rank"])


if __name__ == "__main__":
    unittest.main()
