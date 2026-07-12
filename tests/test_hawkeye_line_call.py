from __future__ import annotations

import unittest

from src.hawkeye import judge_landing, judge_landing_event


class HawkeyeLineCallTest(unittest.TestCase):
    def test_doubles_center_is_in(self) -> None:
        call = judge_landing((305.0, 670.0), court_mode="doubles")
        self.assertTrue(call.valid)
        self.assertEqual(call.call, "in")

    def test_doubles_outside_back_boundary_is_out(self) -> None:
        call = judge_landing((305.0, 1350.0), court_mode="doubles", line_tolerance_cm=2.0)
        self.assertEqual(call.call, "out")
        self.assertEqual(call.nearest_boundary, "back_boundary")
        self.assertAlmostEqual(call.out_by_cm, 8.0)

    def test_line_tolerance_counts_as_in(self) -> None:
        call = judge_landing((611.5, 670.0), court_mode="doubles", line_tolerance_cm=2.0)
        self.assertEqual(call.call, "in")

    def test_singles_uses_inner_sideline(self) -> None:
        doubles_call = judge_landing((30.0, 670.0), court_mode="doubles")
        singles_call = judge_landing((30.0, 670.0), court_mode="singles")
        self.assertEqual(doubles_call.call, "in")
        self.assertEqual(singles_call.call, "out")
        self.assertEqual(singles_call.nearest_boundary, "left_sideline")

    def test_landing_event_attaches_source_confidence(self) -> None:
        event = {
            "event_type": "landing",
            "frame_id": 12,
            "timestamp_ms": 400,
            "confidence": 0.72,
        }
        call = judge_landing_event(event, ball_court_xy=(305.0, -5.0), court_mode="doubles")
        self.assertEqual(call.call, "out")
        self.assertAlmostEqual(call.confidence, 0.72)
        self.assertEqual(call.nearest_boundary, "front_boundary")


if __name__ == "__main__":
    unittest.main()
