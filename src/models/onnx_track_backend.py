from __future__ import annotations

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

    def __post_init__(self) -> None:
        if not Path(self.model_path).is_file():
            raise FileNotFoundError(f"TrackNet ONNX file not found: {self.model_path}")
        try:
            import onnxruntime as ort  # type: ignore
        except Exception as exc:
            raise RuntimeError("onnxruntime is required to load TrackNet .onnx models.") from exc

        providers = ["CPUExecutionProvider"]
        available = set(ort.get_available_providers())
        if self.device.startswith("cuda") and "CUDAExecutionProvider" in available:
            providers.insert(0, "CUDAExecutionProvider")

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self.session = ort.InferenceSession(self.model_path, sess_options=options, providers=providers)

        inputs = self.session.get_inputs()
        outputs = self.session.get_outputs()
        if len(inputs) != 1 or len(outputs) != 1:
            raise RuntimeError(
                f"TrackNet ONNX model must expose exactly 1 input and 1 output, got {len(inputs)} inputs and {len(outputs)} outputs."
            )
        self.input_name = inputs[0].name
        self.output_name = outputs[0].name

    def infer(self, tensor: torch.Tensor) -> np.ndarray:
        if tensor.is_cuda:
            tensor = tensor.detach().cpu()
        input_array = np.ascontiguousarray(tensor.numpy().astype(np.float32, copy=False))
        output = self.session.run([self.output_name], {self.input_name: input_array})[0]
        return np.asarray(output, dtype=np.float32)
