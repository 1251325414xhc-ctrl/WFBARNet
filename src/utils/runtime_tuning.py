"""CUDA runtime tuning shared by every backend.

The default PyTorch configuration is a poor fit for real-time video analysis,
and one specific setting is actively harmful:

``torch.backends.cudnn.benchmark = True``
    cuDNN benchmarks every candidate convolution algorithm *per unique input
    shape*. TrackNet and YOLO run hundreds of convolutions at a fixed
    resolution, so this cost is not amortised the way the heuristic assumes:
    each new shape re-triggers a search. Measured on an RTX 5070 Laptop with
    the shipped models:

        track (TrackNet, PyTorch)   first inference  6 800 ms
        pose  (YOLO26s-Pose)        first inference 51 600 ms
        ------------------------------------------------
        total cold start                            58 400 ms

    With ``benchmark = False`` the same models settle in 680 ms / 6 800 ms
    respectively, because cuDNN uses its built-in heuristics instead of
    searching. Steady-state throughput is unchanged (measured 49.6 ms vs
    49.4 ms per frame for the combined pipeline), so paying ~58 s of cold
    start buys nothing.

We therefore keep ``benchmark`` off and instead opt in explicitly to the
things that *do* help and cost nothing per-shape:

* TF32 for both convolutions and matmuls on Ampere and newer.
* ``float32_matmul_precision = "high"`` so PyTorch agrees with the TF32 opt-in.
* A single warm-up pass at the real input size, done once at load time in a
  worker thread, so the first *analysed* frame is already warm.
"""

from __future__ import annotations

# Set to True only for offline benchmarking runs, never in the interactive app.
_ENABLE_CUDNN_BENCHMARK = False

_configured = False


def configure_torch_runtime(*, enable_cudnn_benchmark: bool | None = None) -> dict[str, object]:
    """Apply process-wide CUDA/CPU tuning. Idempotent and never raises.

    Returns a snapshot of the effective settings so callers can log what was
    actually applied rather than what was requested.
    """
    global _configured
    import torch

    want_benchmark = (
        _ENABLE_CUDNN_BENCHMARK
        if enable_cudnn_benchmark is None
        else bool(enable_cudnn_benchmark)
    )

    cuda_ok = False
    try:
        cuda_ok = bool(torch.cuda.is_available())
    except Exception:
        cuda_ok = False

    try:
        # Explicitly OFF: per-shape algorithm search dominates cold start and
        # buys nothing for fixed-resolution video inference.
        torch.backends.cudnn.benchmark = want_benchmark
    except Exception:
        pass

    if cuda_ok:
        try:
            # TF32 is free accuracy-wise for float32 convs and matmuls on
            # Ampere+ and roughly halves their cost.
            torch.backends.cudnn.allow_tf32 = True
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.set_float32_matmul_precision("high")
        except Exception:
            pass

    _configured = True
    return effective_settings()


def effective_settings() -> dict[str, object]:
    """Read back the live torch settings (safe to call without CUDA)."""
    import torch

    def _get(fn, default=None):
        try:
            return fn()
        except Exception:
            return default

    return {
        "cudnn_benchmark": _get(lambda: torch.backends.cudnn.benchmark),
        "cudnn_allow_tf32": _get(lambda: torch.backends.cudnn.allow_tf32),
        "matmul_allow_tf32": _get(lambda: torch.backends.cuda.matmul.allow_tf32),
        "float32_matmul_precision": _get(torch.get_float32_matmul_precision),
        "configured": _configured,
    }


def describe_runtime() -> str:
    """One-line summary for the application log."""
    s = effective_settings()
    return (
        "CUDA 调优: cudnn.benchmark="
        f"{'开' if s['cudnn_benchmark'] else '关'} | TF32 "
        f"{'开' if s['cudnn_allow_tf32'] else '关'} | "
        f"matmul 精度 {s['float32_matmul_precision']}"
    )
