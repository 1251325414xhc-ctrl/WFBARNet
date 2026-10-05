from __future__ import annotations

from contextlib import nullcontext
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import torch


@dataclass(slots=True)
class ONNXTrackBackend:
    model_path: str
    device: str
    session: Any = field(init=False, repr=False)
    input_name: str = field(init=False)
    output_name: str = field(init=False)
    providers: tuple[str, ...] = field(init=False)

    def __post_init__(self) -> None:
        if not Path(self.model_path).is_file():
            raise FileNotFoundError(f"TrackNet ONNX model file not found: {self.model_path}")

        try:
            import onnxruntime as ort  # type: ignore
        except Exception as exc:
            raise RuntimeError(
                "ONNX Runtime is required to load .onnx TrackNet models. "
                "Install onnxruntime-gpu for NVIDIA GPU inference."
            ) from exc

        if hasattr(ort, "preload_dlls"):
            ort.preload_dlls()

        available = set(ort.get_available_providers())
        requested = self._requested_providers(available)
        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(
            self.model_path,
            sess_options=options,
            providers=requested,
        )
        self.providers = tuple(self.session.get_providers())

        inputs = self.session.get_inputs()
        outputs = self.session.get_outputs()
        if len(inputs) != 1 or len(outputs) != 1:
            raise RuntimeError(
                "TrackNet ONNX model must expose exactly 1 input and 1 output, "
                f"got {len(inputs)} inputs and {len(outputs)} outputs."
            )
        self.input_name = inputs[0].name
        self.output_name = outputs[0].name

    def _requested_providers(self, available: set[str]) -> list[str]:
        if self.device.startswith("cuda"):
            if "CUDAExecutionProvider" not in available:
                raise RuntimeError(
                    "CUDAExecutionProvider is unavailable. Install a CUDA-compatible "
                    "onnxruntime-gpu build or select CPU inference."
                )
            return ["CUDAExecutionProvider", "CPUExecutionProvider"]
        return ["CPUExecutionProvider"]

    def stream_context(self) -> Any:
        return nullcontext()

    @torch.inference_mode()
    def infer(self, tensor: torch.Tensor) -> np.ndarray:
        input_array = tensor.detach().float().cpu().contiguous().numpy()
        outputs = self.session.run(
            [self.output_name],
            {self.input_name: input_array},
        )
        if len(outputs) != 1:
            raise RuntimeError(f"TrackNet ONNX inference returned {len(outputs)} outputs.")
        heatmaps = np.asarray(outputs[0], dtype=np.float32)
        if heatmaps.ndim != 4:
            raise RuntimeError(f"Unexpected TrackNet ONNX heatmap shape: {heatmaps.shape}")
        return heatmaps
