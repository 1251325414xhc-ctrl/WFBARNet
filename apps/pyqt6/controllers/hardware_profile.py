"""Hardware-adaptive analysis presets.

Video analysis throughput depends almost entirely on the GPU, but the right
parameters differ a lot across machines:

* On a discrete GPU, TrackNet input resolution and pose image size dominate.
* On integrated graphics or CPU, both models must be shrunk aggressively or the
  loop cannot keep up with the video's wall clock.

Rather than making the user guess, this module probes the machine once, derives
a preset, and reports the reasoning. The runtime latency probe
(:mod:`analysis_scheduler`) then fine-tunes the pose stride from measurements,
so an imperfect initial guess still converges.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

# --- tiers ---------------------------------------------------------------

TIER_CPU = "cpu"
TIER_INTEGRATED = "integrated"
TIER_ENTRY = "entry"
TIER_MAINLINE = "mainline"
TIER_HIGH = "high"

TIER_LABELS: dict[str, str] = {
    TIER_CPU: "CPU（无可用显卡）",
    TIER_INTEGRATED: "集成显卡",
    TIER_ENTRY: "入门独显",
    TIER_MAINLINE: "主流独显",
    TIER_HIGH: "高性能独显",
}


@dataclass(frozen=True, slots=True)
class AnalysisPreset:
    """Concrete analysis parameters chosen for a machine."""

    track_input_size: tuple[int, int]
    pose_imgsz: int
    pose_crop_pose: bool
    pose_crop_imgsz: int
    max_pose_stride: int
    tier: str
    reason: str

    def explain(self) -> str:
        width, height = self.track_input_size
        return (
            f"{TIER_LABELS.get(self.tier, self.tier)}："
            f"轨迹 {width}×{height}，姿态 {'裁剪+' if self.pose_crop_pose else ''}{self.pose_imgsz}px，"
            f"姿态跳帧上限 {self.max_pose_stride}。{self.reason}"
        )


@dataclass(slots=True)
class HardwareProfile:
    """What we could learn about the machine we are running on."""

    cuda_available: bool = False
    device_name: str = ""
    total_memory_gb: float = 0.0
    compute_capability: tuple[int, int] = (0, 0)
    multiprocessor_count: int = 0
    supports_fp16: bool = False
    has_tensorrt: bool = False
    is_integrated: bool = False
    cpu_count: int = 0

    def describe(self) -> str:
        if not self.cuda_available:
            return f"未检测到可用 CUDA 显卡，使用 CPU（{self.cpu_count} 线程）"
        mem = f"{self.total_memory_gb:.1f}GB"
        cc = f"{self.compute_capability[0]}.{self.compute_capability[1]}"
        parts = [f"{self.device_name}", f"{mem} 显存", f"算力 {cc}", f"{self.multiprocessor_count} SM"]
        if self.supports_fp16:
            parts.append("支持 FP16")
        if self.has_tensorrt:
            parts.append("支持 TensorRT")
        return " | ".join(parts)


def probe_hardware() -> HardwareProfile:
    """Inspect the current machine. Never raises; degrades to a CPU profile."""
    import importlib.util
    import os

    profile = HardwareProfile(cpu_count=os.cpu_count() or 1)
    try:
        import torch
    except ImportError:
        return profile

    try:
        if not torch.cuda.is_available():
            return profile
        props = torch.cuda.get_device_properties(0)
        profile.cuda_available = True
        profile.device_name = str(getattr(props, "name", "unknown"))
        profile.total_memory_gb = float(getattr(props, "total_memory", 0)) / 1e9
        profile.multiprocessor_count = int(getattr(props, "multi_processor_count", 0))
        profile.compute_capability = (
            int(getattr(props, "major", 0)),
            int(getattr(props, "minor", 0)),
        )
        profile.is_integrated = bool(getattr(props, "is_integrated", 0))
        try:
            profile.supports_fp16 = bool(torch.cuda.is_bf16_supported())
        except Exception:
            profile.supports_fp16 = profile.compute_capability[0] >= 7
    except Exception:
        return profile

    try:
        profile.has_tensorrt = importlib.util.find_spec("tensorrt") is not None
    except Exception:
        profile.has_tensorrt = False
    return profile


def classify_tier(profile: HardwareProfile) -> str:
    """Map a hardware profile onto a coarse capability tier."""
    if not profile.cuda_available:
        return TIER_CPU
    if profile.is_integrated:
        return TIER_INTEGRATED
    sm = profile.multiprocessor_count
    vram = profile.total_memory_gb
    major, minor = profile.compute_capability
    # Pre-Turing parts (compute < 7) have poor FP16 throughput, so they are
    # treated as entry-level even when they are discrete cards.
    if major < 7:
        return TIER_ENTRY
    # Very small parts (e.g. MX150/550, GT 1030) are discrete but weaker than
    # a modern integrated GPU; do not promote them above entry level.
    if vram < 3.0 or sm < 12:
        return TIER_ENTRY
    # Sm_120 (Blackwell) is a full generation ahead of Ada/Ampere at the same
    # SM count because its tensor cores are far denser.
    if major >= 12:
        if vram < 5.0 or sm < 24:
            return TIER_MAINLINE
        return TIER_HIGH
    if vram < 4.0 or sm < 16:
        return TIER_ENTRY
    if vram < 7.0 or sm < 40:
        return TIER_MAINLINE
    return TIER_HIGH


def derive_preset(
    profile: HardwareProfile | None = None,
    *,
    measured_cost: dict[tuple[tuple[int, int], int], tuple[float, float]] | None = None,
) -> AnalysisPreset:
    """Pick analysis parameters for the detected hardware.

    ``max_pose_stride`` is a *safety ceiling*, not a quality target: it must
    stay generous enough that the runtime latency tuner can always find a
    stride that fits the frame budget. Capping it too low would leave the
    adaptive layer unable to compensate for a slow GPU.

    The ladder is ordered *expensive first* so ``_fit_to_budget`` can walk down
    until the projected cost actually fits a 30 fps frame. A fixed
    tier -> resolution mapping is not enough on its own: on an RTX 5070 Laptop
    the "high" ladder (track 512x288 + pose 960) measured 34.2 ms per frame at
    the loosest usable stride while the 30 fps budget is only 30.7 ms. The
    rungs in between only cut the *pose* resolution, which is why the walk has
    to reach the last rung (it is the only one that lowers the track cost).

    This function is **cheap and synchronous**. Pass ``measured_cost`` (see
    :func:`measure_ladder_cost`) to make it budget-aware; without it the top
    rung is chosen. Measuring inside this call would block the GUI thread for
    seconds, so the app measures once on a background thread instead.
    """
    profile = profile or probe_hardware()
    tier = classify_tier(profile)
    ladder = _PRESET_LADDER.get(tier, _PRESET_LADDER[TIER_ENTRY])
    return _fit_to_budget(ladder, tier, measured_cost or {})


# Ordered best-quality -> cheapest. Each rung is (track_size, pose_imgsz,
# pose_crop, pose_crop_imgsz, max_pose_stride, reason).
_PRESET_LADDER: dict[str, tuple[tuple, ...]] = {
    TIER_CPU: (
        ((320, 184), 512, False, 384, 4, "使用最小分辨率与最低姿态频率，优先保证不卡顿。"),
        ((288, 160), 448, False, 320, 4, "进一步压低分辨率，CPU 上优先保证能跑完。"),
    ),
    TIER_INTEGRATED: (
        ((352, 200), 640, False, 448, 4, "显存与算力有限，关闭姿态裁剪以避免额外推理次数。"),
        ((320, 184), 576, False, 384, 4, "进一步压低轨迹与姿态分辨率以维持播放速度。"),
        ((288, 160), 512, False, 320, 5, "使用最低档配置，优先保证流畅播放。"),
    ),
    TIER_ENTRY: (
        ((384, 216), 640, True, 480, 4, "保留姿态裁剪但压低分辨率，兼顾精度与速度。"),
        ((352, 200), 576, True, 448, 4, "下调轨迹分辨率以跟上视频帧率。"),
        ((320, 184), 512, False, 384, 5, "使用保守配置，优先保证流畅播放。"),
    ),
    TIER_MAINLINE: (
        ((448, 256), 768, True, 576, 4, "显存充足，提高轨迹分辨率以改善球点精度。"),
        ((384, 216), 768, True, 512, 4, "下调轨迹分辨率以稳住实时速度。"),
        ((352, 200), 640, True, 448, 5, "使用保守配置，优先保证流畅播放。"),
    ),
    TIER_HIGH: (
        ((512, 288), 960, True, 640, 4, "算力充裕，使用高精度配置；姿态跳帧由实时延迟自动调节。"),
        ((512, 288), 768, True, 640, 4, "姿态分辨率下调一档，换取稳定的实时速度。"),
        ((448, 256), 768, True, 576, 4, "下调轨迹分辨率，确保复杂视频也能保持实时。"),
        ((384, 216), 640, True, 512, 6, "轨迹与姿态分辨率均已压到最低档。"),
    ),
}

# The stride used when *projecting* a rung's cost against the frame budget.
# Projecting at the rung's own ``max_pose_stride`` is wrong: that field is a
# *cap* for a tuner that measures real latency and widens the stride on its own,
# so projecting at the cap systematically rejects configurations the pipeline
# can actually sustain. Projecting at stride 1 is equally wrong in the other
# direction — it demands every frame run pose, which no tier can afford.
#
# 2.0 is the honest middle: on the measured RTX 5070 Laptop curve the top rung
# costs 36.7ms at stride 2 versus a 30.7ms budget, so it is *rejected* (which is
# correct — that rung needs stride 5), while the 384x216 rung lands at 25.7ms
# and is accepted. See _pose_cost_for_stride for why pose cost barely amortizes.
_OPTIMISTIC_STRIDE = 2.0

# Reference per-frame cost (ms) of the *heaviest* rung, measured at 1080p on a
# machine of each tier. Used to decide whether the adaptive scheduler can still
# absorb the load by raising the pose stride. Deliberately conservative.
_TIER_COST_HINT_MS: dict[str, float] = {
    TIER_CPU: 999.0,
    TIER_INTEGRATED: 120.0,
    TIER_ENTRY: 60.0,
    TIER_MAINLINE: 40.0,
    TIER_HIGH: 34.0,
}

# Video frame rates we must stay real-time for. 30 fps is the common phone
# capture rate and the tightest case that still matters for badminton footage.
_TARGET_FPS = 30.0


def _budget_ms(fps: float = _TARGET_FPS) -> float:
    from apps.pyqt6.controllers.analysis_scheduler import FRAME_BUDGET_HEADROOM

    return max(1.0, 1000.0 / max(1.0, fps)) * FRAME_BUDGET_HEADROOM


def _fit_to_budget(
    ladder: tuple[tuple, ...],
    tier: str,
    measured: dict[tuple[tuple[int, int], int], tuple[float, float]],
) -> AnalysisPreset:
    """Walk the ladder until the projected cost fits a 30 fps frame.

    ``measured`` comes from :func:`measure_ladder_cost`. When it is empty (no
    CUDA, missing weights, or the probe has not finished yet) the top rung is
    accepted unchanged, which is exactly the pre-optimisation behaviour.
    """
    budget = _budget_ms()
    if not measured:
        return _rung_to_preset(ladder[0], tier)

    fallback = ladder[0]
    for rung in ladder:
        track_size, pose_imgsz, crop_pose, crop_imgsz, stride, reason = rung
        cost = measured.get((tuple(track_size), int(pose_imgsz)))
        if cost is None:
            # A middle rung could not be measured: keep the last rung we
            # actually validated rather than jumping to an unvetted config.
            # ``fallback`` is set whenever a rung fits, so on the first rung it
            # is still the top rung.
            return _rung_to_preset(fallback, tier, reason=_downgrade_note(fallback, ladder))
        track_ms, pose_ms = cost
        projection = _planning_ms(track_ms, pose_ms)
        if projection <= budget:
            fallback = rung
            if rung is ladder[0]:
                break
            return _rung_to_preset(
                rung, tier, reason=f"{reason}（按实测延迟自动下调，预计 {projection:.0f}ms/帧）"
            )
    else:
        # Nothing fits: take the cheapest rung and say so plainly; the runtime
        # scheduler will surface the shortfall in the log. Note the rung is
        # *kept*, not silently downgraded further — a slower-than-realtime
        # analysis is still correct, and lowering the stride cap would only
        # hurt quality without fixing the shortfall.
        final = ladder[-1]
        cost = measured.get((tuple(final[0]), int(final[1])))
        extra = ""
        if cost is not None:
            projection = _planning_ms(cost[0], cost[1])
            extra = f"（预计 {projection:.0f}ms/帧，高于 {budget:.0f}ms 预算，建议降低视频帧率）"
        return _rung_to_preset(final, tier, reason=f"{final[5]}{extra}")

    # Top rung fits as measured: accept it untouched.
    return _rung_to_preset(ladder[0], tier)


def _downgrade_note(rung: tuple, ladder: tuple[tuple, ...]) -> str | None:
    """Annotate a fallback rung that is not the top rung, so the log is honest."""
    if rung is ladder[0]:
        return None
    return f"{rung[5]}（按实测延迟自动下调）"


def _projected_ms(track_ms: float, pose_ms: float, stride: int) -> float:
    """Per-frame cost at a stride, using the *same* model the scheduler uses.

    Delegating to :func:`analysis_scheduler._pose_cost_for_stride` matters: the
    planner and the runtime tuner must not disagree about what a stride costs,
    or the planner will hand the tuner a preset it cannot honour. The naive
    ``pose_ms / stride`` model under-predicted real cost by 4-11x.
    """
    from apps.pyqt6.controllers.analysis_scheduler import _pose_cost_for_stride

    return float(track_ms) + _pose_cost_for_stride(pose_ms, stride)


def _planning_ms(track_ms: float, pose_ms: float) -> float:
    """Cost as the planner should see it: some pose decimation already assumed.

    See :data:`_OPTIMISTIC_STRIDE` for why this is deliberately not the rung's
    own ``max_pose_stride``.
    """
    return _projected_ms(track_ms, pose_ms, _OPTIMISTIC_STRIDE)


def _rung_to_preset(rung: tuple, tier: str, *, reason: str | None = None) -> AnalysisPreset:
    track_size, pose_imgsz, crop_pose, crop_imgsz, stride, rung_reason = rung
    return AnalysisPreset(
        track_input_size=tuple(track_size),
        pose_imgsz=int(pose_imgsz),
        pose_crop_pose=bool(crop_pose),
        pose_crop_imgsz=int(crop_imgsz),
        max_pose_stride=int(stride),
        tier=tier,
        reason=rung_reason if reason is None else reason,
    )


def measure_ladder_cost(
    tier: str | None = None,
) -> dict[tuple[tuple[int, int], int], tuple[float, float]]:
    """Measure (track_ms, pose_ms) for a tier's ladder. Safe to call any time.

    **This blocks for seconds** — it loads the real models and runs several
    timed inferences per configuration. Call it from a worker thread, never
    from the GUI thread.

    Never raises: any failure returns an empty dict, which makes
    ``derive_preset`` fall back to the top-quality rung.
    """
    try:
        if tier is None:
            tier = classify_tier(probe_hardware())
        ladder = _PRESET_LADDER.get(tier, _PRESET_LADDER[TIER_ENTRY])
    except Exception:
        return {}

    hint = _TIER_COST_HINT_MS.get(tier, 40.0)
    if hint >= 999.0:
        return {}

    try:
        import numpy as np
        import torch
    except Exception:
        return {}
    try:
        if not torch.cuda.is_available():
            return {}
    except Exception:
        return {}

    track_weight, pose_weight = _locate_default_weights()
    if not track_weight or not pose_weight:
        return {}

    from apps.pyqt6.controllers.analysis_controller_runtime import (
        _create_runtime_pose_branch,
        _create_runtime_track_branch,
    )

    # Distinct configurations only, so we do not pay for duplicates.
    configs: list[tuple[tuple[int, int], int, bool, int]] = []
    seen = set()
    for track_size, pose_imgsz, crop_pose, crop_imgsz, _stride, _reason in ladder:
        key = (tuple(track_size), int(pose_imgsz))
        if key in seen:
            continue
        seen.add(key)
        configs.append((key[0], key[1], bool(crop_pose), int(crop_imgsz)))

    frame = (np.random.rand(1080, 1920, 3) * 255).astype(np.uint8)
    triple = [frame, frame, frame]

    def _best_ms(fn, repeats: int = 5) -> float:
        """Minimum of several runs.

        The minimum, not the median: we want this machine's *capability*, and
        every source of noise here (other processes, clock ramping, allocator
        warm-up) can only make a run slower. Using the median made the chosen
        rung flip between runs on the same machine.
        """
        samples = []
        for _ in range(repeats):
            start = time.perf_counter()
            fn()
            samples.append((time.perf_counter() - start) * 1000.0)
        samples.sort()
        return samples[0]

    results: dict[tuple[tuple[int, int], int], tuple[float, float]] = {}
    pose_cache: dict[tuple[int, bool, int], float] = {}
    try:
        for track_size, pose_imgsz, crop_pose, crop_imgsz in configs:
            try:
                track_branch = _create_runtime_track_branch(track_weight, track_size)
                track_branch.infer_candidate_results(triple)  # warm
                track_ms = _best_ms(
                    lambda: track_branch.infer_candidate_results(triple)
                )
            except Exception:
                continue

            pose_key = (pose_imgsz, crop_pose, crop_imgsz)
            pose_ms = pose_cache.get(pose_key)
            if pose_ms is None:
                try:
                    pose_branch = _create_runtime_pose_branch(
                        pose_weight,
                        imgsz=pose_imgsz,
                        crop_pose=crop_pose,
                        crop_imgsz=crop_imgsz,
                    )
                    pose_branch.infer(frame, court_prediction=None)  # warm
                    pose_ms = _best_ms(
                        lambda: pose_branch.infer(frame, court_prediction=None)
                    )
                except Exception:
                    pose_ms = None
                if pose_ms is not None:
                    pose_cache[pose_key] = pose_ms
            if pose_ms is None:
                continue

            results[(track_size, pose_imgsz)] = (track_ms, pose_ms)
            del track_branch
    except Exception:
        return {}
    return results


def _locate_default_weights() -> tuple[str | None, str | None]:
    """Find the shipped track/pose weights relative to the project root."""
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    track_dir = root / "assets" / "weights" / "track"
    pose_dir = root / "assets" / "weights" / "pose"

    track_pref = ["model_best.pt", "tracknetv3_int8.onnx"]
    pose_pref = ["yolo26s-pose.pt"]
    for names, folder in ((track_pref, track_dir), (pose_pref, pose_dir)):
        for name in names:
            candidate = folder / name
            if candidate.is_file():
                if folder == track_dir:
                    track_path = str(candidate)
                else:
                    pose_path = str(candidate)
                break
    try:
        return track_path, pose_path
    except UnboundLocalError:
        return None, None


PRESET_CACHE_TTL_S = 30.0
_preset_cache: tuple[float, AnalysisPreset, HardwareProfile] | None = None
# Cost measurements are separate from the profile cache because they are far
# more expensive to produce and must survive cache churn.
_cost_cache: dict[tuple[tuple[int, int], int], tuple[float, float]] | None = None


def get_machine_preset(*, refresh: bool = False) -> tuple[AnalysisPreset, HardwareProfile]:
    """Return the cached (preset, profile) pair for this machine.

    Uses any cost measurement already taken by :func:`warm_preset_cache`;
    without one this picks the tier's top rung, which is the pre-optimisation
    behaviour and costs only microseconds.
    """
    global _preset_cache, _cost_cache
    import time

    now = time.monotonic()
    if not refresh and _preset_cache is not None:
        stamp, preset, profile = _preset_cache
        if now - stamp < PRESET_CACHE_TTL_S:
            return preset, profile
    profile = probe_hardware()
    measured = None if refresh else _cost_cache
    preset = derive_preset(profile, measured_cost=measured)
    _preset_cache = (now, preset, profile)
    return preset, profile


def warm_preset_cache() -> AnalysisPreset:
    """Measure the ladder cost and refresh the preset cache.

    **Blocking** (seconds). Intended to run on a worker thread at startup so
    the first real analysis gets a budget-fitted preset. Falls back to the
    top rung if measurement is impossible.
    """
    global _cost_cache, _preset_cache
    import time

    profile = probe_hardware()
    tier = classify_tier(profile)
    measured = measure_ladder_cost(tier)
    if measured:
        _cost_cache = measured
    preset = derive_preset(profile, measured_cost=measured or None)
    _preset_cache = (time.monotonic(), preset, profile)
    return preset


def measured_cost_snapshot() -> dict[tuple[tuple[int, int], int], tuple[float, float]]:
    """The cost table from the last successful measurement (may be empty)."""
    return dict(_cost_cache or {})


def clear_machine_preset_cache() -> None:
    global _preset_cache, _cost_cache
    _preset_cache = None
    _cost_cache = None


# --- quality modes exposed in the UI -------------------------------------
# "auto" resolves through the hardware profile; the rest are explicit
# overrides for users who know what they want.

QUALITY_AUTO = "auto"
QUALITY_FAST = "fast"
QUALITY_BALANCED = "balanced"
QUALITY_PRECISE = "precise"

QUALITY_CHOICES: tuple[tuple[str, str, str], ...] = (
    (QUALITY_AUTO, "自动（推荐，按显卡适配）", "auto"),
    (QUALITY_FAST, "流畅（牺牲精度换速度）", "fast"),
    (QUALITY_BALANCED, "均衡", "balanced"),
    (QUALITY_PRECISE, "精确（高精度，可能卡顿）", "precise"),
)

_MANUAL_TRACK_SIZES: dict[str, tuple[int, int]] = {
    QUALITY_FAST: (384, 216),
    QUALITY_BALANCED: (448, 256),
    QUALITY_PRECISE: (512, 288),
}

# Manual modes keep the high-quality pose settings; only the track resolution
# is user-controlled there.
_MANUAL_POSE = {
    "imgsz": 960,
    "crop_pose": True,
    "crop_imgsz": 640,
    "max_pose_stride": 2,
}


def resolve_preset(quality: str) -> AnalysisPreset:
    """Resolve a UI quality choice into concrete parameters."""
    name = str(quality or QUALITY_AUTO).strip().lower()
    if name == QUALITY_AUTO:
        preset, _ = get_machine_preset()
        return preset
    if name not in _MANUAL_TRACK_SIZES:
        name = QUALITY_AUTO
        preset, _ = get_machine_preset()
        return preset
    return AnalysisPreset(
        track_input_size=_MANUAL_TRACK_SIZES[name],
        pose_imgsz=_MANUAL_POSE["imgsz"],
        pose_crop_pose=_MANUAL_POSE["crop_pose"],
        pose_crop_imgsz=_MANUAL_POSE["crop_imgsz"],
        max_pose_stride=_MANUAL_POSE["max_pose_stride"],
        tier=name,
        reason="手动指定的性能档位。",
    )
