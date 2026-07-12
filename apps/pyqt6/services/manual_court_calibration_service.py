from __future__ import annotations

from typing import Sequence

import numpy as np
from PyQt6.QtCore import QObject, pyqtSignal

from src.court import opencv_court_homography_core as _court_core
from src.court.opencv_court_detector import CourtLinePrediction


class ManualCourtCalibrationService(QObject):
    """Manual four-point court calibration source compatible with court detection service calls."""

    resultReady = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self) -> None:
        super().__init__()
        self._latest_prediction: CourtLinePrediction | None = None

    def start(self) -> None:
        return

    def stop(self) -> None:
        return

    def reset(self) -> None:
        return

    def request_prediction(self) -> None:
        return

    def clear_pending(self) -> None:
        return

    def submit_frame(self, frame: object, frame_id: int, timestamp_ms: int) -> bool:
        return False

    def latest_prediction(self) -> CourtLinePrediction | None:
        return self._latest_prediction

    def latest_prediction_dict(self) -> dict | None:
        prediction = self.latest_prediction()
        return prediction.to_dict() if prediction is not None else None

    def clear_calibration(self) -> None:
        self._latest_prediction = None
        self.resultReady.emit(None)

    def set_calibration(
        self,
        corners: Sequence[Sequence[float]],
        *,
        source_size: tuple[int, int],
        frame_id: int = 0,
        timestamp_ms: int = 0,
    ) -> CourtLinePrediction:
        prediction = manual_court_prediction_from_corners(
            corners,
            source_size=source_size,
            frame_id=frame_id,
            timestamp_ms=timestamp_ms,
        )
        self._latest_prediction = prediction
        self.resultReady.emit(prediction.to_dict())
        return prediction


def manual_court_prediction_from_corners(
    corners: Sequence[Sequence[float]],
    *,
    source_size: tuple[int, int],
    frame_id: int = 0,
    timestamp_ms: int = 0,
) -> CourtLinePrediction:
    corner_array = np.asarray(corners, dtype=np.float32).reshape(-1, 2)
    if corner_array.shape != (4, 2):
        raise ValueError("Manual court calibration requires exactly four points.")
    ordered_corners = _court_core.order_quad_points(corner_array)
    if ordered_corners is None:
        raise ValueError("Manual court calibration points must form a convex quadrilateral.")
    _validate_manual_court_quad(ordered_corners, source_size)

    court_to_image_h, image_to_court_h = _court_core.compute_homographies(ordered_corners)
    if court_to_image_h is None or image_to_court_h is None:
        raise ValueError("Manual court calibration points cannot produce a valid homography.")

    names_and_points = _court_core.template_keypoints_for_scheme("8")
    keypoint_names = [item[0] for item in names_and_points]
    template_points = np.asarray([item[1] for item in names_and_points], dtype=np.float32)
    keypoints = _court_core.project_points(template_points, court_to_image_h)
    projected_lines = _court_core.project_template_lines(court_to_image_h)
    width, height = int(source_size[0]), int(source_size[1])

    return CourtLinePrediction(
        frame_id=int(frame_id),
        timestamp_ms=max(0, int(timestamp_ms)),
        source_size=(width, height),
        valid=True,
        attempted=True,
        updated=True,
        update_type="manual calibration",
        status="manual calibration",
        confidence=1.0,
        candidate_confidence=1.0,
        reason="manual four-point calibration using BWF doubles outer court",
        scheme="manual",
        corners=_points_to_list(ordered_corners),
        keypoints=_keypoints_to_list(keypoint_names, keypoints),
        court_to_image_h=_matrix_to_list(court_to_image_h),
        image_to_court_h=_matrix_to_list(image_to_court_h),
        projected_lines={name: _points_to_list(points) for name, points in projected_lines.items()},
        metrics={
            "manual": True,
            "manual_ordered_corners": True,
            "bwf_standard": True,
            "court_width_cm": float(_court_core.COURT_WIDTH),
            "court_length_cm": float(_court_core.COURT_LENGTH),
            "supported_keypoints": len(keypoint_names),
        },
        detect_ms=0.0,
        rejected_count=0,
    )


def _validate_manual_court_quad(corners: np.ndarray, source_size: tuple[int, int]) -> None:
    width, height = int(source_size[0]), int(source_size[1])
    if width <= 0 or height <= 0:
        raise ValueError("Manual court calibration source size is invalid.")

    frame_area = float(width * height)
    frame_diag = float(np.hypot(width, height))
    area = abs(_court_core.polygon_area(corners))
    if area < max(200.0, frame_area * 0.01):
        raise ValueError("Manual court calibration area is too small; please mark the full doubles outer court.")

    edge_lengths = [
        float(np.linalg.norm(corners[(index + 1) % 4] - corners[index]))
        for index in range(4)
    ]
    if min(edge_lengths) < max(12.0, frame_diag * 0.03):
        raise ValueError("Manual court calibration edges are too short; please mark the full doubles outer court.")

    top_width, right_depth, bottom_width, left_depth = edge_lengths
    width_ratio = max(top_width, bottom_width) / max(1.0, min(top_width, bottom_width))
    depth_ratio = max(left_depth, right_depth) / max(1.0, min(left_depth, right_depth))
    if width_ratio > 5.0 or depth_ratio > 5.0:
        raise ValueError("Manual court calibration shape is too distorted; please click the four doubles outer corners again.")


def create_manual_court_calibration_service() -> ManualCourtCalibrationService:
    service = ManualCourtCalibrationService()
    service.start()
    return service


def _points_to_list(points: object) -> list[list[float]]:
    array = np.asarray(points, dtype=np.float32).reshape(-1, 2)
    return [[float(x), float(y)] for x, y in array]


def _keypoints_to_list(names: list[str], points: object) -> list[dict[str, object]]:
    point_list = _points_to_list(points)
    return [
        {
            "name": names[index] if index < len(names) else f"keypoint_{index}",
            "point": point,
        }
        for index, point in enumerate(point_list)
    ]


def _matrix_to_list(matrix: object) -> list[list[float]]:
    array = np.asarray(matrix, dtype=np.float64)
    if array.shape != (3, 3):
        return []
    return [[float(value) for value in row] for row in array.tolist()]
