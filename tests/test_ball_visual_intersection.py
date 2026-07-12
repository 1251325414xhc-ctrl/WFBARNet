from __future__ import annotations

import unittest

from collections import deque

from apps.pyqt6.controllers.analysis_controller_runtime import (
    attach_hawkeye_call_to_event,
    estimate_hawkeye_landing_court_xy,
    project_ball_visual_intersection,
)
from src.utils.structures import TrackResult


class BallVisualIntersectionTest(unittest.TestCase):
    def test_visible_ball_maps_image_point_to_court_plane(self) -> None:
        court_prediction = {
            "valid": True,
            "image_to_court_h": [
                [2.0, 0.0, 1.0],
                [0.0, 3.0, -5.0],
                [0.0, 0.0, 1.0],
            ],
        }
        track = TrackResult(ball_xy=[10.0, 20.0], visible=1, score=0.9)

        self.assertEqual(
            project_ball_visual_intersection(track, court_prediction),
            (21.0, 55.0),
        )

    def test_invisible_ball_has_no_visual_intersection(self) -> None:
        court_prediction = {
            "valid": True,
            "image_to_court_h": [
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0],
            ],
        }
        track = TrackResult(ball_xy=[10.0, 20.0], visible=0, score=0.0)

        self.assertIsNone(project_ball_visual_intersection(track, court_prediction))

    def test_invalid_court_has_no_visual_intersection(self) -> None:
        track = TrackResult(ball_xy=[10.0, 20.0], visible=1, score=0.9)

        self.assertIsNone(
            project_ball_visual_intersection(
                track,
                {
                    "valid": False,
                    "image_to_court_h": [
                        [1.0, 0.0, 0.0],
                        [0.0, 1.0, 0.0],
                        [0.0, 0.0, 1.0],
                    ],
                },
            )
        )

    def test_hawkeye_landing_uses_first_contact_event_point(self) -> None:
        court_prediction = {
            "valid": True,
            "image_to_court_h": [
                [1.0, 0.0, 0.0],
                [0.0, 1.0, 0.0],
                [0.0, 0.0, 1.0],
            ],
        }
        landing_event = {
            "event_type": "landing",
            "ball_xy": [100.0, 200.0],
            "confidence": 0.9,
        }

        landing_xy = estimate_hawkeye_landing_court_xy(
            landing_event,
            court_prediction,
            current_ball_court_xy=(900.0, 900.0),
            recent_ball_court_points=deque([(850.0, 850.0), (900.0, 900.0)], maxlen=12),
        )

        self.assertEqual(landing_xy, (100.0, 200.0))

    def test_hawkeye_payload_marks_first_contact_source(self) -> None:
        event = attach_hawkeye_call_to_event(
            {
                "event_type": "landing",
                "ball_xy": [100.0, 200.0],
                "confidence": 0.9,
            },
            (100.0, 200.0),
        )

        self.assertEqual(event["hawkeye"]["landing_source"], "first_contact_event")


if __name__ == "__main__":
    unittest.main()
