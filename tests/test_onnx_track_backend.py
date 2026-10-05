from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np
import torch

from src.models.onnx_track_backend import ONNXTrackBackend
from src.models.track_branch import TrackBranch


class _FakeValueInfo:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeSession:
    def __init__(self, *_args, providers: list[str], **_kwargs) -> None:
        self._providers = providers

    def get_providers(self) -> list[str]:
        return self._providers

    def get_inputs(self) -> list[_FakeValueInfo]:
        return [_FakeValueInfo("input")]

    def get_outputs(self) -> list[_FakeValueInfo]:
        return [_FakeValueInfo("heatmap")]

    def run(self, output_names, input_feed):
        self.last_output_names = output_names
        self.last_input_feed = input_feed
        batch = next(iter(input_feed.values())).shape[0]
        return [np.zeros((batch, 3, 288, 512), dtype=np.float32)]


class ONNXTrackBackendTest(unittest.TestCase):
    def test_backend_runs_tensor_through_onnx_session(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            model_path = Path(tmp_dir) / "track.onnx"
            model_path.touch()
            fake_ort = unittest.mock.MagicMock()
            fake_ort.get_available_providers.return_value = [
                "CUDAExecutionProvider",
                "CPUExecutionProvider",
            ]
            fake_ort.InferenceSession.side_effect = _FakeSession
            fake_ort.GraphOptimizationLevel.ORT_ENABLE_ALL = object()

            with patch.dict("sys.modules", {"onnxruntime": fake_ort}):
                backend = ONNXTrackBackend(str(model_path), "cuda")
                output = backend.infer(torch.zeros((2, 9, 288, 512)))

        self.assertEqual(output.shape, (2, 3, 288, 512))
        self.assertEqual(
            backend.providers,
            ("CUDAExecutionProvider", "CPUExecutionProvider"),
        )

    @patch("src.models.track_branch.ONNXTrackBackend")
    def test_track_branch_dispatches_onnx_models(self, backend_type) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            model_path = Path(tmp_dir) / "track.onnx"
            model_path.touch()
            branch = TrackBranch(str(model_path), device="cpu")

        backend_type.assert_called_once_with(str(model_path), "cpu")
        self.assertEqual(branch.backend_name, "onnx")


if __name__ == "__main__":
    unittest.main()
