"""Adaptive scheduling for the real-time analysis loop.

The playback worker must finish every source frame inside its wall-clock
budget (``1000 / fps`` ms) or the video visibly stutters. On a single GPU the
track and pose models serialize, so the only lever that matters is *how often*
each model runs.

Trajectory accuracy depends on the track model, so it always runs every frame.
Pose feeds player-distance and stroke classification, which tolerate temporal
reuse, so it can be sub-sampled. This module picks the pose stride from the
measured cost of the two models instead of hard-coding it.
"""

from __future__ import annotations

from dataclasses import dataclass

# Pose is never sub-sampled below this: stride 1 means every frame is analysed.
MIN_POSE_STRIDE = 1
# Preferred ceiling. stride 4 already means pose data is 3 frames stale, and
# beyond that the stroke classifier starts missing contacts — so this is what
# we aim for on ordinary footage.
MAX_POSE_STRIDE = 4

# Absolute ceiling. High-frame-rate footage (60/120 fps phone slow-motion) has
# a frame budget of only 15.3/7.7 ms, which no configuration can meet at
# stride 4. Rather than stutter, degrade pose frequency further: player
# distance and stroke classification tolerate staleness far better than
# dropping video frames does.
#
# The ceiling is 12 because the *stride* is the wrong lever beyond that: the
# real cost is affine in the stride (see _POSE_CALL_OVERHEAD_MS), so a stride
# of, say, 30 would still leave one full pose invocation per frame's worth of
# overhead in the budget while destroying stroke-classification accuracy.
HARD_POSE_STRIDE_CEILING = 12

# Headroom kept free so post-processing, drawing and Qt signal delivery still
# fit inside the frame budget. Inference is not the only work per frame.
FRAME_BUDGET_HEADROOM = 0.92

# Fixed per-inference overhead (ms) that does *not* shrink when pose is
# sub-sampled: the forward pass on a tiny crop is dominated by launch, host
# transfer and letterbox/preprocess work, not by the convolution itself.
#
# Measured on the RTX 5070 Laptop with both models resident (1080p in, track
# 384x216, pose 640px), comparing the real cost of a stride against the naive
# ``track + pose / stride`` model:
#
#     stride   measured   naive    ratio
#          4     60.5ms   15.3ms   3.96x
#          6     82.3ms   14.4ms   5.71x
#          8    109.8ms   14.0ms   7.86x
#         12    154.8ms   13.5ms  11.43x
#
# The naive model therefore over-promises by 4-11x, which is exactly why the
# loop stuttered despite ``projected_ms`` reporting that it fit. The overhead
# is affine in the stride, so model it as a fixed component per pose call and
# solve for it from the stride-4 and stride-12 samples:
#
#     track + 4 * (pose/4 + overhead)  =  60.5
#     track + 12 * (pose/12 + overhead) = 154.8
#
# which gives ~11.8ms per pose invocation at any stride.
_POSE_CALL_OVERHEAD_MS = 11.8


def _pose_cost_for_stride(pose_ms: float, stride: int) -> float:
    """Amortized per-frame pose cost at a given stride.

    Pose runs once every ``stride`` frames, and each invocation costs
    ``pose_ms`` plus a fixed :data:`_POSE_CALL_OVERHEAD_MS` that does not
    amortize away. Assuming a pure ``pose_ms / stride`` decay (the previous
    model) under-predicted by up to 11x on real hardware.
    """
    step = max(1, int(stride))
    return (float(pose_ms) + _POSE_CALL_OVERHEAD_MS) / step


@dataclass(slots=True)
class InferenceCostModel:
    """Measures per-model latency and derives a pose stride that fits the budget."""

    track_ms: float = 0.0
    pose_ms: float = 0.0
    fps: float = 30.0

    @property
    def frame_budget_ms(self) -> float:
        return max(1.0, 1000.0 / max(1.0, self.fps)) * FRAME_BUDGET_HEADROOM

    @property
    def stride(self) -> int:
        """Largest pose stride whose amortized cost still fits the frame budget.

        Starts at ``MIN_POSE_STRIDE`` and only relaxes when pose actually has
        to be sub-sampled, so a machine that can already keep up never loses
        pose resolution for nothing. It will go past ``MAX_POSE_STRIDE`` when
        the frame budget makes that the only way to stay real-time.
        """

        def _fits(step: int) -> bool:
            # Track always runs every frame, so if *it* alone busts the budget
            # no stride can help; report that via fits rather than looping.
            return self.projected_ms(step) <= self.frame_budget_ms

        if self.pose_ms <= 0.0:
            return MIN_POSE_STRIDE
        for candidate in range(MIN_POSE_STRIDE, MAX_POSE_STRIDE + 1):
            if _fits(candidate):
                return candidate
        # Beyond the preferred ceiling it is a trade-off, not a target: only
        # keep relaxing while pose cost is still the binding constraint.
        for candidate in range(MAX_POSE_STRIDE + 1, HARD_POSE_STRIDE_CEILING + 1):
            if self.track_ms > self.frame_budget_ms:
                break
            if _fits(candidate):
                return candidate
        # Even the loosest stride overruns; take the cheapest option and let
        # the caller surface the shortfall.
        if self.track_ms > self.frame_budget_ms:
            return MAX_POSE_STRIDE
        return HARD_POSE_STRIDE_CEILING

    @property
    def fits(self) -> bool:
        """Whether the current stride keeps the loop inside its budget."""
        return self.projected_ms() <= self.frame_budget_ms

    def projected_ms(self, stride: int | None = None) -> float:
        step = self.stride if stride is None else max(1, int(stride))
        if self.pose_ms <= 0.0:
            return self.track_ms
        return self.track_ms + _pose_cost_for_stride(self.pose_ms, step)

    def describe(self) -> str:
        return (
            f"track {self.track_ms:.1f}ms + pose {self.pose_ms:.1f}ms "
            f"-> stride {self.stride} (budget {self.frame_budget_ms:.1f}ms, "
            f"projected {self.projected_ms():.1f}ms)"
        )


class LatencyProbe:
    """Exponential-moving-average timer for a named inference step."""

    __slots__ = ("_ema_ms", "_alpha", "_samples")

    def __init__(self, alpha: float = 0.2) -> None:
        self._alpha = max(0.01, min(1.0, float(alpha)))
        self._ema_ms = 0.0
        self._samples = 0

    def add(self, elapsed_ms: float) -> None:
        value = max(0.0, float(elapsed_ms))
        if self._ema_ms == 0.0:
            self._ema_ms = value
        else:
            self._ema_ms = (1.0 - self._alpha) * self._ema_ms + self._alpha * value
        self._samples += 1

    @property
    def ema_ms(self) -> float:
        return self._ema_ms

    @property
    def ready(self) -> bool:
        return self._samples > 0

    def reset(self) -> None:
        self._ema_ms = 0.0
        self._samples = 0
