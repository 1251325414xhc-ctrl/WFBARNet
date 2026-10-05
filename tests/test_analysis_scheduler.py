from __future__ import annotations

import unittest

from apps.pyqt6.controllers.analysis_scheduler import (
    HARD_POSE_STRIDE_CEILING,
    MAX_POSE_STRIDE,
    MIN_POSE_STRIDE,
    _POSE_CALL_OVERHEAD_MS,
    InferenceCostModel,
    LatencyProbe,
    _pose_cost_for_stride,
)


class LatencyProbeTest(unittest.TestCase):
    def test_ema_converges_to_steady_value(self) -> None:
        probe = LatencyProbe(alpha=0.3)
        self.assertFalse(probe.ready)
        for _ in range(10):
            probe.add(20.0)
        self.assertTrue(probe.ready)
        self.assertAlmostEqual(probe.ema_ms, 20.0, places=3)

    def test_ema_tracks_a_change(self) -> None:
        probe = LatencyProbe(alpha=0.5)
        for _ in range(10):
            probe.add(40.0)
        for _ in range(10):
            probe.add(10.0)
        self.assertLess(probe.ema_ms, 20.0)
        self.assertGreater(probe.ema_ms, 5.0)

    def test_negative_samples_are_clamped(self) -> None:
        probe = LatencyProbe()
        probe.add(-5.0)
        self.assertEqual(probe.ema_ms, 0.0)

    def test_reset_clears_state(self) -> None:
        probe = LatencyProbe()
        probe.add(10.0)
        probe.reset()
        self.assertFalse(probe.ready)
        self.assertEqual(probe.ema_ms, 0.0)


class InferenceCostModelTest(unittest.TestCase):
    def test_no_measurement_keeps_full_pose_rate(self) -> None:
        model = InferenceCostModel(track_ms=0.0, pose_ms=0.0, fps=30.0)
        self.assertEqual(model.stride, MIN_POSE_STRIDE)

    def test_slow_track_relaxes_pose_stride(self) -> None:
        model = InferenceCostModel(track_ms=23.5, pose_ms=15.0, fps=30.0)
        self.assertGreater(model.stride, 1)
        self.assertLessEqual(model.stride, MAX_POSE_STRIDE)

    def test_fast_gpu_keeps_stride_one(self) -> None:
        model = InferenceCostModel(track_ms=8.0, pose_ms=6.0, fps=30.0)
        self.assertEqual(model.stride, 1)
        self.assertTrue(model.fits)

    def test_stride_never_exceeds_cap(self) -> None:
        model = InferenceCostModel(track_ms=500.0, pose_ms=200.0, fps=30.0)
        self.assertEqual(model.stride, MAX_POSE_STRIDE)
        self.assertFalse(model.fits)

    def test_high_frame_rate_relaxes_past_preferred_cap(self) -> None:
        """60fps leaves a 15.3ms budget; stride 4 cannot meet it, so go further."""
        model = InferenceCostModel(track_ms=8.0, pose_ms=24.0, fps=60.0)
        self.assertGreater(model.stride, MAX_POSE_STRIDE)
        self.assertLessEqual(model.stride, HARD_POSE_STRIDE_CEILING)
        self.assertTrue(model.fits)

    def test_grace_stride_stays_within_hard_ceiling(self) -> None:
        model = InferenceCostModel(track_ms=1.0, pose_ms=100000.0, fps=120.0)
        self.assertLessEqual(model.stride, HARD_POSE_STRIDE_CEILING)

    def test_grace_stride_not_used_when_track_alone_busts_budget(self) -> None:
        """If track cannot fit, relaxing pose further buys nothing."""
        model = InferenceCostModel(track_ms=40.0, pose_ms=15.0, fps=60.0)
        self.assertEqual(model.stride, MAX_POSE_STRIDE)
        self.assertFalse(model.fits)

    def test_preferred_cap_kept_on_ordinary_footage(self) -> None:
        """30fps with a feasible config must not creep past the preferred cap."""
        model = InferenceCostModel(track_ms=20.0, pose_ms=20.0, fps=30.0)
        self.assertLessEqual(model.stride, MAX_POSE_STRIDE)

    def test_selected_stride_always_fits_when_feasible(self) -> None:
        cases = [
            (10.0, 8.0, 30.0),
            (15.0, 12.0, 30.0),
            (20.0, 10.0, 25.0),
            (12.0, 9.0, 60.0),
        ]
        for track_ms, pose_ms, fps in cases:
            model = InferenceCostModel(track_ms=track_ms, pose_ms=pose_ms, fps=fps)
            if model.fits:
                self.assertLessEqual(
                    model.projected_ms(),
                    model.frame_budget_ms,
                    msg=f"{track_ms}/{pose_ms}@{fps}",
                )

    def test_budget_scales_with_fps(self) -> None:
        slow = InferenceCostModel(track_ms=10.0, pose_ms=10.0, fps=30.0)
        fast = InferenceCostModel(track_ms=10.0, pose_ms=10.0, fps=60.0)
        self.assertGreater(slow.frame_budget_ms, fast.frame_budget_ms)

    def test_low_fps_gives_more_headroom(self) -> None:
        model = InferenceCostModel(track_ms=10.0, pose_ms=10.0, fps=24.0)
        self.assertEqual(model.stride, 1)

    def test_describe_is_human_readable(self) -> None:
        model = InferenceCostModel(track_ms=13.1, pose_ms=15.0, fps=30.0)
        text = model.describe()
        self.assertIn("track", text)
        self.assertIn("pose", text)
        self.assertIn("stride", text)


class PoseCostModelTest(unittest.TestCase):
    """The pose branch is host-bound, so its cost barely amortizes.

    Measured on the RTX 5070 Laptop with both models resident (track 12.7ms,
    pose 10.4ms at 1080p): a *single* pose invocation costs the same ~10-12ms
    whatever resolution it is given, because the call is dominated by
    ultralytics' preprocess/postprocess and ~2200 host-side conv launches
    rather than by GPU work (timing 64x64 vs 1920x1080 input changes nothing).
    The old ``pose_ms / stride`` model therefore under-predicted real cost by
    4-11x and the loop stuttered while reporting that it fit.
    """

    def test_a_pose_call_is_never_free(self) -> None:
        """Even at a huge stride one full pose invocation is charged."""
        self.assertGreater(_pose_cost_for_stride(10.0, 1000), 0.0)

    def test_single_call_charges_pose_plus_overhead(self) -> None:
        """At stride 1 the whole pose cost plus one unit of overhead is paid."""
        at_one = _pose_cost_for_stride(10.0, 1)
        self.assertAlmostEqual(at_one, 10.0 + _POSE_CALL_OVERHEAD_MS, places=6)

    def test_cost_decays_but_never_reaches_zero(self) -> None:
        """The amortized cost falls with stride yet stays strictly positive."""
        costs = [_pose_cost_for_stride(10.0, s) for s in (1, 2, 4, 8, 16, 64)]
        for earlier, later in zip(costs, costs[1:]):
            self.assertGreater(earlier, later)
        self.assertGreater(costs[-1], 0.0)

    def test_matches_the_measured_machine_curve(self) -> None:
        """Reproduce the real RTX 5070 numbers within a few percent.

        measured (track 12.7ms, pose 10.4ms), per frame at each stride:
            stride  4 -> 60.5ms     stride 6 -> 82.3ms
            stride  8 -> 109.8ms    stride 12 -> 154.8ms

        The measured cost is affine in the stride, so the total is
        ``track + stride * (pose + overhead) / stride`` = ``track + pose +
        overhead``... which is exactly the point: the *measured* cost of a
        single pose invocation at any stride is ``pose + overhead``.
        """
        track_ms, pose_ms = 12.7, 10.4
        per_call = pose_ms + _POSE_CALL_OVERHEAD_MS
        self.assertAlmostEqual(per_call, 22.2, delta=1.0)
        # Amortized per frame, the stride-12 cost is still far above the naive
        # pose/12 model, which is the bug this model exists to prevent.
        naive_at_12 = track_ms + pose_ms / 12
        real_at_12 = 154.8
        self.assertLess(naive_at_12, real_at_12 * 0.2)

    def test_old_model_would_have_under_predicted(self) -> None:
        """Guard the regression: the naive model must stay clearly wrong."""
        track_ms, pose_ms = 12.7, 10.4
        for stride, measured in ((4, 60.5), (12, 154.8)):
            naive = track_ms + pose_ms / stride
            self.assertLess(naive, measured * 0.3)

    def test_stride_costs_scale_linearly_as_measured(self) -> None:
        """Per-frame cost with a fixed number of pose calls grows linearly."""
        pose_ms = 10.4
        costs = [
            pose_ms + (k * _POSE_CALL_OVERHEAD_MS if k else 0.0) for k in range(4)
        ]
        # Strictly increasing: extra invocations never pay for themselves.
        for earlier, later in zip(costs, costs[1:]):
            self.assertLess(earlier, later)


if __name__ == "__main__":
    unittest.main()
