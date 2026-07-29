from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from math import hypot
from pathlib import Path

import cv2
import numpy as np

from src.utils.structures import FrameResult, TrackResult


DEFAULT_SKELETON = [
    (5, 7),
    (7, 9),
    (6, 8),
    (8, 10),
    (5, 6),
    (5, 11),
    (6, 12),
    (11, 12),
    (11, 13),
    (13, 15),
    (12, 14),
    (14, 16),
    (0, 1),
    (0, 2),
    (1, 3),
    (2, 4),
]
BALL_COLOR = (0, 0, 255)
TRAIL_OLD_COLOR = (255, 155, 35)
TRAIL_NEW_COLOR = (0, 225, 255)
HIT_COLOR = (65, 215, 80)
POSE_RECALL_HIT_COLOR = (0, 165, 255)
LANDING_COLOR = (0, 255, 0)
OUT_OF_FRAME_COLOR = (255, 0, 255)
MARKER_OUTLINE_COLOR = (255, 255, 255)

_TrailPoint = tuple[float, int, float, float, int]
_EventMarker = tuple[float, float, float, str, str, float | None]


@dataclass
class TrackTrailRenderer:
    fps: float = 25.0
    history_seconds: float = 0.5
    trail_break_threshold_px: float = 80.0
    segment_gap_seconds: float = 0.16
    event_marker_seconds: float = 1.2
    event_marker_radius: int = 26
    _points: deque[_TrailPoint] = field(default_factory=deque)
    _event_markers: deque[_EventMarker] = field(default_factory=deque)
    _segment_id: int = 0
    _last_visible_timestamp_s: float | None = None

    def draw(
        self,
        frame: np.ndarray,
        result: FrameResult,
        *,
        timestamp_ms: int | None = None,
        trajectory_event: object | None = None,
    ) -> np.ndarray:
        canvas = frame.copy()
        return self.draw_on(
            canvas,
            result,
            timestamp_ms=timestamp_ms,
            trajectory_event=trajectory_event,
        )

    def draw_on(
        self,
        canvas: np.ndarray,
        result: FrameResult,
        *,
        timestamp_ms: int | None = None,
        trajectory_event: object | None = None,
    ) -> np.ndarray:
        _draw_pose(canvas, result)
        timestamp_s = self.update_track_history(result, timestamp_ms=timestamp_ms)
        self.add_trajectory_event(trajectory_event)
        self._draw_trail(canvas, timestamp_s)
        self._draw_event_markers(canvas, timestamp_s)
        return canvas

    def update_track_history(
        self,
        result: FrameResult,
        *,
        timestamp_ms: int | None = None,
    ) -> float:
        timestamp_s = self._timestamp_seconds(result.frame_id, timestamp_ms)
        if result.track.visible:
            if (
                self._last_visible_timestamp_s is not None
                and timestamp_s - self._last_visible_timestamp_s > self.segment_gap_seconds
            ):
                self._segment_id += 1
            x, y = map(float, result.track.ball_xy)
            self._points.append((timestamp_s, int(result.frame_id), x, y, self._segment_id))
            self._last_visible_timestamp_s = timestamp_s
        elif self._last_visible_timestamp_s is not None:
            self._segment_id += 1
            self._last_visible_timestamp_s = None
        self._prune(timestamp_s)
        return timestamp_s

    def add_trajectory_event(self, event: object) -> None:
        if not isinstance(event, dict):
            return
        event_type = str(event.get("event_type", ""))
        if event_type not in {"hit", "landing", "out_of_frame"}:
            return
        ball_xy = event.get("ball_xy", [-1.0, -1.0])
        if not isinstance(ball_xy, (list, tuple)) or len(ball_xy) < 2:
            return
        try:
            x = float(ball_xy[0])
            y = float(ball_xy[1])
        except (TypeError, ValueError):
            return
        if not np.isfinite(x) or not np.isfinite(y):
            return
        try:
            timestamp_ms = int(event.get("timestamp_ms", 0))
        except (TypeError, ValueError):
            timestamp_ms = 0
        timestamp_s = max(0.0, float(timestamp_ms) / 1000.0)
        rule = str(event.get("rule", ""))
        speed_kmh = None
        if event_type == "hit":
            try:
                speed_value = float(event.get("speed_kmh", 0.0))
            except (TypeError, ValueError):
                speed_value = 0.0
            if np.isfinite(speed_value) and speed_value > 0.0:
                speed_kmh = speed_value
        self._event_markers.append((timestamp_s, x, y, event_type, rule, speed_kmh))

    def _timestamp_seconds(self, frame_id: int, timestamp_ms: int | None) -> float:
        if timestamp_ms is not None:
            return max(0.0, float(timestamp_ms) / 1000.0)
        fps = self.fps if self.fps > 0 else 25.0
        return max(0.0, float(frame_id) / fps)

    def _prune(self, timestamp_s: float) -> None:
        cutoff = timestamp_s - max(0.0, self.history_seconds)
        while self._points and self._points[0][0] < cutoff:
            self._points.popleft()
        event_cutoff = timestamp_s - max(0.0, self.event_marker_seconds)
        while self._event_markers and self._event_markers[0][0] <= event_cutoff:
            self._event_markers.popleft()

    def _draw_trail(self, canvas: np.ndarray, timestamp_s: float) -> None:
        trail_points = list(self._points)
        for previous, current in zip(trail_points, trail_points[1:]):
            _, _prev_frame_id, prev_x, prev_y, prev_segment_id = previous
            point_time, _frame_id, x, y, segment_id = current
            if segment_id != prev_segment_id:
                continue
            if hypot(x - prev_x, y - prev_y) > self.trail_break_threshold_px:
                continue
            age = max(0.0, timestamp_s - point_time)
            freshness = max(0.0, 1.0 - age / max(self.history_seconds, 1e-6))
            color = _gradient_color(TRAIL_OLD_COLOR, TRAIL_NEW_COLOR, freshness)
            cv2.line(
                canvas,
                (int(round(prev_x)), int(round(prev_y))),
                (int(round(x)), int(round(y))),
                color,
                max(1, int(round(1.0 + freshness * 2.0))),
                cv2.LINE_AA,
            )

    def _draw_event_markers(self, canvas: np.ndarray, timestamp_s: float) -> None:
        for event_time, x, y, event_type, rule, speed_kmh in self._event_markers:
            age = max(0.0, timestamp_s - event_time)
            self._draw_event_marker(canvas, (x, y), event_type, rule, age, speed_kmh)

    def _draw_event_marker(
        self,
        canvas: np.ndarray,
        point: tuple[float, float],
        event_type: str,
        rule: str,
        age: float,
        speed_kmh: float | None,
    ) -> None:
        x, y = self._marker_point(canvas, point)
        radius = max(3, int(self.event_marker_radius))
        if event_type == "landing":
            pts = np.array(
                [[x, y - radius], [x + radius, y], [x, y + radius], [x - radius, y]],
                dtype=np.int32,
            )
            cv2.fillConvexPoly(canvas, pts, LANDING_COLOR)
            cv2.polylines(canvas, [pts], isClosed=True, color=MARKER_OUTLINE_COLOR, thickness=1)
            return
        if event_type == "out_of_frame":
            cv2.line(canvas, (x - radius, y - radius), (x + radius, y + radius), OUT_OF_FRAME_COLOR, 3)
            cv2.line(canvas, (x - radius, y + radius), (x + radius, y - radius), OUT_OF_FRAME_COLOR, 3)
            cv2.circle(canvas, (x, y), radius + 3, MARKER_OUTLINE_COLOR, 1)
            return
        color = POSE_RECALL_HIT_COLOR if rule == "pose_recall" else HIT_COLOR
        freshness = max(0.0, 1.0 - age / max(self.event_marker_seconds, 1e-6))
        ring_radius = radius + int(round(5.0 * freshness))
        glow = canvas.copy()
        cv2.circle(glow, (x, y), ring_radius + 7, color, 7, cv2.LINE_AA)
        cv2.addWeighted(glow, 0.24, canvas, 0.76, 0.0, canvas)
        cv2.circle(canvas, (x, y), ring_radius + 2, MARKER_OUTLINE_COLOR, 5, cv2.LINE_AA)
        cv2.circle(canvas, (x, y), ring_radius, color, 3, cv2.LINE_AA)
        if speed_kmh is not None:
            self._draw_hit_speed_label(canvas, (x, y), ring_radius, speed_kmh, color)

    def _draw_hit_speed_label(
        self,
        canvas: np.ndarray,
        point: tuple[int, int],
        ring_radius: int,
        speed_kmh: float,
        accent_color: tuple[int, int, int],
    ) -> None:
        height, width = canvas.shape[:2]
        if height <= 0 or width <= 0:
            return

        label = f"{speed_kmh:.0f} km/h"
        font = cv2.FONT_HERSHEY_SIMPLEX
        font_scale = max(0.46, min(0.68, height / 1080.0 * 0.68))
        thickness = 2 if height >= 720 else 1
        pad_x = max(7, int(round(font_scale * 13)))
        pad_y = max(5, int(round(font_scale * 9)))
        margin = 4

        (text_width, text_height), baseline = cv2.getTextSize(label, font, font_scale, thickness)
        max_text_width = max(1, width - (margin + pad_x) * 2)
        if text_width > max_text_width:
            font_scale = max(0.30, font_scale * max_text_width / text_width)
            (text_width, text_height), baseline = cv2.getTextSize(label, font, font_scale, thickness)

        box_width = min(width - margin * 2, text_width + pad_x * 2)
        box_height = min(height - margin * 2, text_height + baseline + pad_y * 2)
        if box_width <= 0 or box_height <= 0:
            return

        x, y = point
        marker_extent = ring_radius + 7
        gap = max(8, int(round(font_scale * 16)))
        right_left = x + marker_extent + gap
        if right_left + box_width <= width - margin:
            left = right_left
            connector_start = (x + marker_extent, y)
            connector_on_left = True
        else:
            left = x - marker_extent - gap - box_width
            connector_start = (x - marker_extent, y)
            connector_on_left = False
        left = max(margin, min(width - margin - box_width, left))
        top = max(margin, min(height - margin - box_height, y - box_height // 2))
        right = left + box_width
        bottom = top + box_height

        connector_y = max(top, min(bottom, y))
        connector_end = (left if connector_on_left else right, connector_y)
        cv2.line(canvas, connector_start, connector_end, accent_color, 1, cv2.LINE_AA)

        shadow = canvas.copy()
        cv2.rectangle(
            shadow,
            (min(width - 1, left + 2), min(height - 1, top + 2)),
            (min(width - 1, right + 2), min(height - 1, bottom + 2)),
            (0, 0, 0),
            -1,
        )
        cv2.addWeighted(shadow, 0.28, canvas, 0.72, 0.0, canvas)

        panel = canvas.copy()
        cv2.rectangle(panel, (left, top), (right, bottom), (18, 24, 28), -1)
        cv2.addWeighted(panel, 0.88, canvas, 0.12, 0.0, canvas)
        cv2.rectangle(canvas, (left, top), (right, bottom), accent_color, 2, cv2.LINE_AA)
        text_x = left + max(1, (box_width - text_width) // 2)
        text_y = top + max(text_height, (box_height + text_height - baseline) // 2)
        cv2.putText(
            canvas,
            label,
            (text_x, text_y),
            font,
            font_scale,
            MARKER_OUTLINE_COLOR,
            thickness,
            cv2.LINE_AA,
        )

    def _marker_point(self, canvas: np.ndarray, point: tuple[float, float]) -> tuple[int, int]:
        height, width = canvas.shape[:2]
        x = int(round(point[0]))
        y = int(round(point[1]))
        if width > 0:
            x = max(0, min(width - 1, x))
        if height > 0:
            y = max(0, min(height - 1, y))
        return x, y


def _gradient_color(
    start: tuple[int, int, int],
    end: tuple[int, int, int],
    ratio: float,
) -> tuple[int, int, int]:
    amount = max(0.0, min(1.0, float(ratio)))
    return tuple(int(round(left + (right - left) * amount)) for left, right in zip(start, end))


def _draw_pose(canvas: np.ndarray, result: FrameResult) -> None:
    for person in result.pose:
        x1, y1, x2, y2 = map(int, person.bbox)
        cv2.rectangle(canvas, (x1, y1), (x2, y2), (0, 180, 255), 2)
        for x, y in person.keypoints:
            cv2.circle(canvas, (int(x), int(y)), 4, (0, 255, 0), -1)
        for a, b in DEFAULT_SKELETON:
            if a < len(person.keypoints) and b < len(person.keypoints):
                p1 = tuple(map(int, person.keypoints[a]))
                p2 = tuple(map(int, person.keypoints[b]))
                cv2.line(canvas, p1, p2, (255, 180, 0), 2)


def draw_result(frame: np.ndarray, result: FrameResult) -> np.ndarray:
    canvas = frame.copy()
    _draw_pose(canvas, result)
    if result.track.visible:
        x, y = map(int, result.track.ball_xy)
        cv2.circle(canvas, (x, y), 8, BALL_COLOR, 2)
        cv2.putText(canvas, f"{result.track.score:.2f}", (x + 8, y - 8), cv2.FONT_HERSHEY_SIMPLEX, 0.5, BALL_COLOR, 1)
    return canvas


def save_visualization_video(frames: list[np.ndarray], results: list[FrameResult], path: Path, fps: float = 25.0) -> None:
    if not frames:
        return
    h, w = frames[0].shape[:2]
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"mp4v"), fps, (w, h))
    trail_renderer = TrackTrailRenderer(fps=fps)
    for frame, result in zip(frames, results):
        writer.write(trail_renderer.draw(frame, result))
    writer.release()
