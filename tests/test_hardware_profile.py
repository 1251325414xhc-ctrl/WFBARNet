from __future__ import annotations

import unittest

from apps.pyqt6.controllers.hardware_profile import (
    TIER_CPU,
    TIER_ENTRY,
    TIER_HIGH,
    TIER_INTEGRATED,
    TIER_MAINLINE,
    AnalysisPreset,
    HardwareProfile,
    _OPTIMISTIC_STRIDE,
    _PRESET_LADDER,
    _budget_ms,
    _fit_to_budget,
    _planning_ms,
    _projected_ms,
    classify_tier,
    derive_preset,
)


def _profile(**kwargs) -> HardwareProfile:
    base = dict(
        cuda_available=True,
        device_name="Test GPU",
        total_memory_gb=8.0,
        compute_capability=(12, 0),
        multiprocessor_count=36,
        supports_fp16=True,
        cpu_count=16,
    )
    base.update(kwargs)
    return HardwareProfile(**base)


# Measured on the RTX 5070 Laptop with both models resident, 1080p input,
# cudnn.benchmark off. This is the curve that drove the whole redesign.
_REAL_CURVE: dict[tuple[int, int], tuple[float, float]] = {
    (512, 288): (25.0, 11.6),
    (448, 256): (19.2, 20.0),
    (384, 216): (13.6, 12.4),
}

# A synthetic mainline curve heavy enough at the top that the walk has to move,
# and cheap enough further down that it lands inside the 30.7ms budget at the
# planner's stride-2 assumption: (34+24+11.8)/2 = 35.4ms (over),
# (18+20+11.8)/2 = 24.9ms (fits).
_MAINLINE_CURVE: dict[tuple[int, int], tuple[float, float]] = {
    (448, 256): (34.0, 24.0),
    (384, 216): (18.0, 20.0),
    (352, 200): (14.0, 16.0),
}


class ClassifyTierTest(unittest.TestCase):
    def test_no_cuda_is_cpu(self) -> None:
        self.assertEqual(classify_tier(HardwareProfile(cuda_available=False)), TIER_CPU)

    def test_integrated_is_integrated(self) -> None:
        profile = _profile(is_integrated=True, multiprocessor_count=96)
        self.assertEqual(classify_tier(profile), TIER_INTEGRATED)

    def test_pre_turing_discrete_is_entry(self) -> None:
        profile = _profile(compute_capability=(6, 1), total_memory_gb=6.0)
        self.assertEqual(classify_tier(profile), TIER_ENTRY)

    def test_tiny_discrete_stays_entry(self) -> None:
        profile = _profile(compute_capability=(8, 6), total_memory_gb=2.0, multiprocessor_count=8)
        self.assertEqual(classify_tier(profile), TIER_ENTRY)

    def test_blackwell_laptop_is_high(self) -> None:
        """Regression: RTX 5070 Laptop was misread as an integrated GPU."""
        profile = _profile(
            device_name="NVIDIA GeForce RTX 5070 Laptop GPU",
            compute_capability=(12, 0),
            total_memory_gb=8.55,
            multiprocessor_count=36,
            is_integrated=False,
        )
        self.assertEqual(classify_tier(profile), TIER_HIGH)

    def test_blackwell_small_is_mainline(self) -> None:
        profile = _profile(compute_capability=(12, 0), total_memory_gb=4.0, multiprocessor_count=20)
        self.assertEqual(classify_tier(profile), TIER_MAINLINE)

    def test_old_flagship_is_high(self) -> None:
        profile = _profile(compute_capability=(8, 6), total_memory_gb=12.0, multiprocessor_count=48)
        self.assertEqual(classify_tier(profile), TIER_HIGH)


class FitToBudgetTest(unittest.TestCase):
    def test_no_measurement_keeps_top_quality_rung(self) -> None:
        ladder = _PRESET_LADDER[TIER_HIGH]
        preset = _fit_to_budget(ladder, TIER_HIGH, {})
        self.assertEqual(preset.track_input_size, ladder[0][0])
        self.assertEqual(preset.pose_imgsz, ladder[0][1])

    def test_feasible_top_rung_is_kept(self) -> None:
        ladder = _PRESET_LADDER[TIER_HIGH]
        cheapest = {(tuple(ladder[0][0]), ladder[0][1]): (5.0, 5.0)}
        preset = _fit_to_budget(ladder, TIER_HIGH, cheapest)
        self.assertEqual(preset.track_input_size, ladder[0][0])

    def test_over_budget_top_rung_downgrades(self) -> None:
        """The regression this whole change exists for: on the RTX 5070 Laptop
        the top rung cost 25.0+11.6ms resident and had to be walked down."""
        ladder = _PRESET_LADDER[TIER_HIGH]
        measured = {}
        for track_size, pose_imgsz, _crop, _crop_imgsz, _stride, _reason in ladder:
            cost = _REAL_CURVE.get(tuple(track_size))
            if cost is not None:
                measured[(tuple(track_size), pose_imgsz)] = cost
        preset = _fit_to_budget(ladder, TIER_HIGH, measured)
        projected = _planning_ms(*_REAL_CURVE[tuple(preset.track_input_size)])
        self.assertLessEqual(projected, _budget_ms())
        self.assertNotEqual(preset.track_input_size, ladder[0][0])

    def test_real_machine_curve_selects_the_384_rung(self) -> None:
        """Pin the actual decision made on the RTX 5070 Laptop."""
        ladder = _PRESET_LADDER[TIER_HIGH]
        measured = {}
        for track_size, pose_imgsz, _crop, _crop_imgsz, _stride, _reason in ladder:
            cost = _REAL_CURVE.get(tuple(track_size))
            if cost is not None:
                measured[(tuple(track_size), pose_imgsz)] = cost
        preset = _fit_to_budget(ladder, TIER_HIGH, measured)
        self.assertEqual(preset.track_input_size, (384, 216))
        self.assertEqual(preset.pose_imgsz, 640)
        self.assertIn("（按实测延迟自动下调", preset.reason)

    def test_flat_cost_walks_to_the_cheapest_rung(self) -> None:
        """Identical cost at every rung must end on the cheapest one."""
        ladder = _PRESET_LADDER[TIER_HIGH]
        measured = {}
        for track_size, pose_imgsz, _crop, _crop_imgsz, _stride, _reason in ladder:
            measured[(tuple(track_size), pose_imgsz)] = (20.0, 20.0)
        preset = _fit_to_budget(ladder, TIER_HIGH, measured)
        # 20 + 20/1.5 = 33.3ms over budget at every rung -> cheapest, with an
        # honest shortfall note rather than a false "downgraded to fit" claim.
        self.assertEqual(preset.track_input_size, ladder[-1][0])
        self.assertIn("预算", preset.reason)
        self.assertNotIn("（按实测延迟自动下调", preset.reason)

    def test_top_rung_wins_when_it_fits(self) -> None:
        ladder = _PRESET_LADDER[TIER_HIGH]
        # Uniform and comfortably inside budget at every rung.
        measured = {}
        for track_size, pose_imgsz, _crop, _crop_imgsz, _stride, _reason in ladder:
            measured[(tuple(track_size), pose_imgsz)] = (12.0, 9.0)
        preset = _fit_to_budget(ladder, TIER_HIGH, measured)
        # (12 + 9 + 11.8) / 2 = 16.4ms fits, so the top rung is kept untouched.
        self.assertEqual(preset.track_input_size, ladder[0][0])
        self.assertEqual(preset.pose_imgsz, ladder[0][1])
        self.assertNotIn("（按实测延迟自动下调", preset.reason)

    def test_flagship_ladder_varies_the_track_resolution(self) -> None:
        """The top rung must not be the only lever on ``TIER_HIGH``.

        All the intermediate rungs shrink only the pose size; if the track
        resolution never dropped, the walk would have nothing to walk to and
        the user would keep a stuttering pipeline.
        """
        ladder = _PRESET_LADDER[TIER_HIGH]
        track_sizes = [tuple(rung[0]) for rung in ladder]
        self.assertGreater(
            len(set(track_sizes)), 1, "TIER_HIGH rungs must vary the track resolution"
        )

    def test_planning_stride_is_not_the_cap(self) -> None:
        """Projecting at ``max_pose_stride`` rejects configs the pipeline can
        actually sustain, because the runtime tuner widens the stride itself."""
        self.assertLess(_OPTIMISTIC_STRIDE, min(rung[4] for rung in _PRESET_LADDER[TIER_HIGH]))
        self.assertEqual(_planning_ms(20.0, 10.0), _projected_ms(20.0, 10.0, _OPTIMISTIC_STRIDE))

    def test_gpu_tiers_have_a_rung_inside_budget(self) -> None:
        """Guards the ladder against a future rung too heavy to ever fit.

        CPU is excluded on purpose: software inference of these models is an
        order of magnitude off real time, so the honest outcome there is the
        cheapest rung plus an explicit shortfall note (see
        ``test_cpu_tier_reports_shortfall``).
        """
        curves = {
            TIER_INTEGRATED: {352 * 200: (30.0, 24.0), 320 * 184: (22.0, 18.0), 288 * 160: (14.0, 12.0)},
            TIER_ENTRY: {384 * 216: (32.0, 28.0), 352 * 200: (26.0, 22.0), 320 * 184: (16.0, 14.0)},
            TIER_MAINLINE: {448 * 256: (34.0, 24.0), 384 * 216: (24.0, 18.0), 352 * 200: (16.0, 12.0)},
            TIER_HIGH: {512 * 288: (30.0, 16.0), 448 * 256: (24.0, 14.0), 384 * 216: (16.0, 10.0)},
        }
        for tier, curve in curves.items():
            ladder = _PRESET_LADDER[tier]
            measured = {}
            for track_size, pose_imgsz, _crop, _crop_imgsz, _stride, _reason in ladder:
                cost = curve.get(track_size[0] * track_size[1])
                if cost is not None:
                    measured[(tuple(track_size), pose_imgsz)] = cost
            preset = _fit_to_budget(ladder, tier, measured)
            projected = _planning_ms(*curve[preset.track_input_size[0] * preset.track_input_size[1]])
            self.assertLessEqual(projected, _budget_ms(), msg=f"{tier} {preset.track_input_size}")

    def test_cpu_tier_reports_shortfall(self) -> None:
        ladder = _PRESET_LADDER[TIER_CPU]
        measured = {}
        for track_size, pose_imgsz, _crop, _crop_imgsz, _stride, _reason in ladder:
            measured[(tuple(track_size), pose_imgsz)] = (200.0, 300.0)
        preset = _fit_to_budget(ladder, TIER_CPU, measured)
        self.assertEqual(preset.track_input_size, ladder[-1][0])
        self.assertIn("预算", preset.reason)

    def test_reason_mentions_shortfall_not_downgrade(self) -> None:
        ladder = _PRESET_LADDER[TIER_MAINLINE]
        measured = {}
        for track_size, pose_imgsz, _crop, _crop_imgsz, _stride, _reason in ladder:
            measured[(tuple(track_size), pose_imgsz)] = (40.0, 60.0)
        preset = _fit_to_budget(ladder, TIER_MAINLINE, measured)
        # 40 + 60/1.5 = 80ms, over budget everywhere -> cheapest rung + a plain
        # shortfall note, never a claim that we "downgraded to fit".
        self.assertEqual(preset.track_input_size, ladder[-1][0])
        self.assertIn("预算", preset.reason)
        self.assertNotIn("（按实测延迟自动下调", preset.reason)

    def test_downgrade_reason_when_a_lower_rung_fits(self) -> None:
        ladder = _PRESET_LADDER[TIER_MAINLINE]
        measured = {}
        for track_size, pose_imgsz, _crop, _crop_imgsz, _stride, _reason in ladder:
            cost = _MAINLINE_CURVE.get(tuple(track_size))
            if cost is not None:
                measured[(tuple(track_size), pose_imgsz)] = cost
        preset = _fit_to_budget(ladder, TIER_MAINLINE, measured)
        self.assertNotEqual(preset.track_input_size, ladder[0][0])
        self.assertIn("（按实测延迟自动下调", preset.reason)

    def test_infeasible_ladder_reports_shortfall(self) -> None:
        ladder = _PRESET_LADDER[TIER_HIGH]
        measured = {}
        for track_size, pose_imgsz, _crop, _crop_imgsz, _stride, _reason in ladder:
            measured[(tuple(track_size), pose_imgsz)] = (500.0, 400.0)
        preset = _fit_to_budget(ladder, TIER_HIGH, measured)
        self.assertEqual(preset.track_input_size, ladder[-1][0])
        self.assertIn("预算", preset.reason)

    def test_unmeasurable_middle_rung_does_not_jump(self) -> None:
        ladder = _PRESET_LADDER[TIER_HIGH]
        measured = {(tuple(ladder[0][0]), ladder[0][1]): (99.0, 99.0)}
        preset = _fit_to_budget(ladder, TIER_HIGH, measured)
        # Falls back to the top rung rather than into an unvetted config.
        self.assertEqual(preset.track_input_size, ladder[0][0])

    def test_unmeasurable_rung_keeps_last_validated_rung(self) -> None:
        """A gap after a rung that *did* fit keeps that rung, not the top one."""
        ladder = _PRESET_LADDER[TIER_HIGH]
        # Rung 0 is over budget, rung 1 fits, rung 2 is unmeasured.
        measured = {
            (tuple(ladder[0][0]), ladder[0][1]): (40.0, 30.0),
            (tuple(ladder[1][0]), ladder[1][1]): (10.0, 20.0),
        }
        preset = _fit_to_budget(ladder, TIER_HIGH, measured)
        self.assertEqual(preset.track_input_size, ladder[1][0])
        self.assertEqual(preset.pose_imgsz, ladder[1][1])
        self.assertIn("（按实测延迟自动下调", preset.reason)


class DerivePresetTest(unittest.TestCase):
    def test_all_tiers_produce_aligned_sizes(self) -> None:
        cases = [
            TIER_CPU,
            TIER_INTEGRATED,
            TIER_ENTRY,
            TIER_MAINLINE,
            TIER_HIGH,
        ]
        profiles = {
            TIER_CPU: HardwareProfile(cuda_available=False, cpu_count=8),
            TIER_INTEGRATED: _profile(is_integrated=True, multiprocessor_count=96),
            TIER_ENTRY: _profile(compute_capability=(6, 1), total_memory_gb=6.0),
            TIER_MAINLINE: _profile(compute_capability=(8, 6), total_memory_gb=6.0, multiprocessor_count=30),
            TIER_HIGH: _profile(),
        }
        for tier in cases:
            preset = derive_preset(profiles[tier])
            self.assertIsInstance(preset, AnalysisPreset)
            width, height = preset.track_input_size
            # TrackNetV3 downsamples 3x: both dimensions must be multiples of 8.
            self.assertEqual(width % 8, 0, msg=f"{tier} width {width}")
            self.assertEqual(height % 8, 0, msg=f"{tier} height {height}")

    def test_every_ladder_rung_is_aligned(self) -> None:
        for tier, ladder in _PRESET_LADDER.items():
            for track_size, pose_imgsz, _crop, _crop_imgsz, stride, _reason in ladder:
                width, height = track_size
                self.assertEqual(width % 8, 0, msg=f"{tier} {track_size}")
                self.assertEqual(height % 8, 0, msg=f"{tier} {track_size}")
                self.assertGreaterEqual(pose_imgsz, 256, msg=f"{tier} {track_size}")
                self.assertGreaterEqual(stride, 1, msg=f"{tier} {track_size}")

    def test_ladder_is_ordered_expensive_first(self) -> None:
        for tier, ladder in _PRESET_LADDER.items():
            costs = [size[0] * size[1] for size, *_rest in ladder]
            self.assertEqual(costs, sorted(costs, reverse=True), msg=tier)


class RuntimeTuningTest(unittest.TestCase):
    def test_benchmark_stays_off(self) -> None:
        """cudnn.benchmark cost ~58s of cold start and bought nothing."""
        import torch

        from src.utils.runtime_tuning import configure_torch_runtime, effective_settings

        configure_torch_runtime()
        self.assertFalse(effective_settings()["cudnn_benchmark"])
        self.assertFalse(torch.backends.cudnn.benchmark)

    def test_describe_runtime_is_a_string(self) -> None:
        from src.utils.runtime_tuning import describe_runtime

        self.assertIsInstance(describe_runtime(), str)

    def test_explicit_override_is_honoured(self) -> None:
        from src.utils.runtime_tuning import configure_torch_runtime, effective_settings

        try:
            configure_torch_runtime(enable_cudnn_benchmark=True)
            self.assertTrue(effective_settings()["cudnn_benchmark"])
        finally:
            # Restore the production policy for the rest of the test session.
            configure_torch_runtime(enable_cudnn_benchmark=False)


if __name__ == "__main__":
    unittest.main()
