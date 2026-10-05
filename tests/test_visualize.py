from __future__ import annotations

import unittest

import numpy as np

from src.utils.structures import FrameResult, TrackResult
from src.utils.visualize import HIT_COLOR, LANDING_COLOR, OUT_OF_FRAME_COLOR, TrackTrailRenderer


def _frame_result(
    frame_id: int,
    x: float,
    y: float,
    score: float = 0.85,
) -> FrameResult:
    track = TrackResult(ball_xy=[x, y], visible=1, score=score, heatmap_shape=[288, 512])
    return FrameResult(frame_id=frame_id, pose=[], track=track)


def _missing_result(frame_id: int) -> FrameResult:
    track = TrackResult(ball_xy=[-1.0, -1.0], visible=0, score=0.0, heatmap_shape=[288, 512])
    return FrameResult(frame_id=frame_id, pose=[], track=track)


class TrackTrailRendererTest(unittest.TestCase):
    def test_large_trail_jump_is_not_connected_visually(self) -> None:
        renderer = TrackTrailRenderer(fps=25.0, trail_break_threshold_px=80.0)
        frame = np.zeros((120, 240, 3), dtype=np.uint8)

        renderer.draw_on(frame.copy(), _frame_result(0, 20.0, 40.0), timestamp_ms=0)
        renderer.draw_on(frame.copy(), _frame_result(1, 60.0, 40.0), timestamp_ms=40)
        canvas = renderer.draw_on(frame.copy(), _frame_result(2, 180.0, 40.0), timestamp_ms=80)

        self.assertTrue(np.any(canvas[40, 40] > 0))
        self.assertTrue(np.array_equal(canvas[40, 120], np.zeros(3, dtype=np.uint8)))

    def test_missing_track_segment_is_not_connected_visually(self) -> None:
        renderer = TrackTrailRenderer(fps=25.0, trail_break_threshold_px=80.0)
        frame = np.zeros((120, 120, 3), dtype=np.uint8)

        renderer.draw_on(frame.copy(), _frame_result(0, 20.0, 40.0), timestamp_ms=0)
        renderer.draw_on(frame.copy(), _missing_result(1), timestamp_ms=40)
        canvas = renderer.draw_on(frame.copy(), _frame_result(2, 60.0, 40.0), timestamp_ms=80)

        self.assertTrue(np.array_equal(canvas[40, 40], np.zeros(3, dtype=np.uint8)))

    def test_trajectory_event_hit_marker_is_a_hit_coloured_ring_until_expiry(self) -> None:
        renderer = TrackTrailRenderer(fps=25.0)
        marker_seconds = renderer.event_marker_seconds
        radius = renderer.event_marker_radius
        frame = np.zeros((160, 180, 3), dtype=np.uint8)
        hit_event = {
            "event_type": "hit",
            "frame_id": 1,
            "timestamp_ms": 40,
            "ball_xy": [70.0, 80.0],
        }

        hit_canvas = renderer.draw_on(
            frame.copy(),
            _missing_result(1),
            timestamp_ms=40,
            trajectory_event=hit_event,
        )

        def _ring_has_hit_colour(canvas: np.ndarray) -> bool:
            band = canvas[80, 70 + radius - 4 : 70 + radius + 9]
            target = np.array(HIT_COLOR, dtype=np.uint8)
            return any(np.array_equal(colour, target) for colour in band)

        # The hit marker is a hollow ring: HIT_COLOR sits on the ring band and
        # the centre stays background so the shuttle point itself is visible.
        self.assertTrue(np.array_equal(hit_canvas[80, 70], np.zeros(3, dtype=np.uint8)))
        self.assertTrue(_ring_has_hit_colour(hit_canvas))

        # It survives right up to the TTL and is culled once the TTL elapses.
        last_live_ms = 40 + int(round(marker_seconds * 1000.0)) - 1
        first_dead_ms = 40 + int(round(marker_seconds * 1000.0))
        before_expiry = renderer.draw_on(frame.copy(), _missing_result(2), timestamp_ms=last_live_ms)
        after_expiry = renderer.draw_on(frame.copy(), _missing_result(3), timestamp_ms=first_dead_ms)
        self.assertTrue(_ring_has_hit_colour(before_expiry))
        self.assertFalse(np.any(after_expiry))

    def test_motion_pattern_does_not_create_hit_marker_without_event(self) -> None:
        renderer = TrackTrailRenderer(fps=25.0)
        frame = np.zeros((160, 180, 3), dtype=np.uint8)

        renderer.draw_on(frame.copy(), _frame_result(0, 40.0, 80.0), timestamp_ms=0)
        renderer.draw_on(frame.copy(), _frame_result(1, 70.0, 80.0), timestamp_ms=40)
        canvas = renderer.draw_on(frame.copy(), _frame_result(2, 70.0, 45.0), timestamp_ms=80)

        self.assertFalse(np.array_equal(canvas[80, 70], np.array(HIT_COLOR, dtype=np.uint8)))

    def test_trajectory_event_markers_use_distinct_landing_and_out_colors(self) -> None:
        renderer = TrackTrailRenderer(fps=25.0)
        frame = np.zeros((120, 120, 3), dtype=np.uint8)

        landing_canvas = renderer.draw_on(
            frame.copy(),
            _missing_result(0),
            timestamp_ms=1000,
            trajectory_event={
                "event_type": "landing",
                "frame_id": 0,
                "timestamp_ms": 1000,
                "ball_xy": [30.0, 40.0],
            },
        )
        out_canvas = renderer.draw_on(
            frame.copy(),
            _missing_result(1),
            timestamp_ms=1040,
            trajectory_event={
                "event_type": "out_of_frame",
                "frame_id": 1,
                "timestamp_ms": 1040,
                "ball_xy": [50.0, 60.0],
            },
        )

        self.assertTrue(np.array_equal(landing_canvas[40, 30], np.array(LANDING_COLOR, dtype=np.uint8)))
        self.assertTrue(np.array_equal(out_canvas[60, 50], np.array(OUT_OF_FRAME_COLOR, dtype=np.uint8)))
        self.assertFalse(np.array_equal(np.array(LANDING_COLOR), np.array(OUT_OF_FRAME_COLOR)))


if __name__ == "__main__":
    unittest.main()
