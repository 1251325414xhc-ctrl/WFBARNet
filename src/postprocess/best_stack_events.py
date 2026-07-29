from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from math import acos, hypot
from typing import Mapping, Sequence

import numpy as np

from src.postprocess.trajectory_events import (
    RealtimeTrajectoryEventDetector,
    TrajectoryEventCandidateGenerator,
    TrajectoryEventDetectorConfig,
)
from src.utils.structures import FrameResult, PersonPoseResult, TrackResult


GAP_REVERSAL_RULE = "gap_reversal"
POSE_RECALL_RULE = "pose_recall"


@dataclass(slots=True)
class BestStackEventConfig:
    gap_reversal_enabled: bool = True
    gap_delta_px: float = 12.0
    gap_max_frames: int = 6
    gap_min_frames: int = 2
    gap_boundary_points: int = 3
    player_bbox_expansion_px: float = 144.0
    proximity_window_frames: int = 2
    relocation_window_frames: int = 8
    relocation_min_turn_speed: float = 2.0
    relocation_min_angle: float = 1.5
    relocation_angle_margin: float = 0.3
    pose_recall_dv_px: float = 20.0
    pose_recall_speed_threshold: float = 0.30
    pose_recall_speed_window_frames: int = 4
    pose_recall_dedup_frames: int = 5
    pose_recall_neighbor_gap: int = 3
    pose_recall_local_max_window: int = 2
    keypoint_min_score: float = 0.30
    pose_association_gate_px: float = 150.0
    pose_track_max_gap_frames: int = 2
    wrist_speed_cap_bbox_heights: float = 2.0
    context_history_frames: int = 240
    min_hit_track_score: float = 0.48


class GapReversalCandidateGenerator(TrajectoryEventCandidateGenerator):
    def __init__(
        self,
        config: TrajectoryEventDetectorConfig,
        best_config: BestStackEventConfig,
    ) -> None:
        super().__init__(config)
        self.best_config = best_config

    def generate(
        self,
        x: Sequence[float],
        y: Sequence[float],
        visibility: Sequence[int],
        *,
        img_height: int = 288,
        img_width: int = 512,
        include_trajectory_end: bool = True,
    ) -> list[dict[str, object]]:
        merged = super().generate(
            x,
            y,
            visibility,
            img_height=img_height,
            img_width=img_width,
            include_trajectory_end=include_trajectory_end,
        )
        gap_candidates = (
            self._gap_reversal_candidates(
                np.asarray(x, dtype=np.float64),
                np.asarray(y, dtype=np.float64),
                np.asarray(visibility, dtype=np.float64),
            )
            if self.best_config.gap_reversal_enabled
            else []
        )
        merge_window = max(1, int(self.config.merge_window))
        existing_frames = [int(candidate["frame"]) for candidate in merged]
        for candidate in gap_candidates:
            if any(abs(int(candidate["frame"]) - frame) <= merge_window for frame in existing_frames):
                continue
            merged.append(candidate)
        merged.sort(key=lambda candidate: int(candidate["frame"]))
        return merged

    def _gap_reversal_candidates(
        self,
        x: np.ndarray,
        y: np.ndarray,
        visibility: np.ndarray,
    ) -> list[dict[str, object]]:
        candidates: list[dict[str, object]] = []
        index = 0
        while index < len(visibility):
            if visibility[index] == 1:
                index += 1
                continue
            gap_end = index
            while gap_end < len(visibility) and visibility[gap_end] == 0:
                gap_end += 1
            gap_length = gap_end - index
            if index == 0 or gap_end >= len(visibility):
                index = gap_end
                continue
            if not self.best_config.gap_min_frames <= gap_length <= self.best_config.gap_max_frames:
                index = gap_end
                continue
            velocity_before = self._segment_velocity(x, y, visibility, index - 1, -1)
            velocity_after = self._segment_velocity(x, y, visibility, gap_end, 1)
            if velocity_before is None or velocity_after is None:
                index = gap_end
                continue
            delta = hypot(
                velocity_after[0] - velocity_before[0],
                velocity_after[1] - velocity_before[1],
            )
            if delta < self.best_config.gap_delta_px:
                index = gap_end
                continue
            middle = (index + gap_end - 1) // 2
            left = index - 1
            alpha = (middle - left) / (gap_end - left)
            candidates.append(
                {
                    "frame": middle,
                    "x": float(x[left] + (x[gap_end] - x[left]) * alpha),
                    "y": float(y[left] + (y[gap_end] - y[left]) * alpha),
                    "event_type": "hit",
                    "rule": GAP_REVERSAL_RULE,
                    "confidence": 0.60,
                    "all_rules": [GAP_REVERSAL_RULE],
                    "auxiliary_rules": [],
                    "features": {
                        "gap_start": index,
                        "gap_end": gap_end - 1,
                        "gap_len": gap_length,
                        "v_before": list(velocity_before),
                        "v_after": list(velocity_after),
                        "delta": delta,
                    },
                }
            )
            index = gap_end
        return candidates

    def _segment_velocity(
        self,
        x: np.ndarray,
        y: np.ndarray,
        visibility: np.ndarray,
        anchor: int,
        direction: int,
    ) -> tuple[float, float] | None:
        if anchor < 0 or anchor >= len(visibility) or visibility[anchor] != 1:
            return None
        indices = [anchor]
        cursor = anchor
        for _ in range(2 * self.best_config.gap_boundary_points):
            cursor += direction
            if cursor < 0 or cursor >= len(visibility):
                break
            if visibility[cursor] == 1:
                indices.append(cursor)
                if len(indices) >= self.best_config.gap_boundary_points:
                    break
        if len(indices) < 2:
            return None
        far = indices[-1]
        elapsed = abs(far - anchor)
        if direction > 0:
            return ((float(x[far]) - float(x[anchor])) / elapsed, (float(y[far]) - float(y[anchor])) / elapsed)
        return ((float(x[anchor]) - float(x[far])) / elapsed, (float(y[anchor]) - float(y[far])) / elapsed)


class _GapReversalDetector(RealtimeTrajectoryEventDetector):
    def __init__(
        self,
        *,
        fps: float,
        best_config: BestStackEventConfig,
        detector_config: TrajectoryEventDetectorConfig | None = None,
    ) -> None:
        super().__init__(detector_config, fps=fps)
        self.best_config = best_config
        self.generator = GapReversalCandidateGenerator(self.config, best_config)

    def _is_hit_event_valid(self, event, samples, local_index, img_height) -> bool:
        if str(event.get("rule", "")) != GAP_REVERSAL_RULE:
            return super()._is_hit_event_valid(event, samples, local_index, img_height)
        max_reach = self.best_config.gap_max_frames + 2
        for direction in (-1, 1):
            boundary = None
            for offset in range(1, max_reach + 1):
                index = local_index + direction * offset
                if index < 0 or index >= len(samples):
                    break
                if samples[index].visible:
                    boundary = samples[index]
                    break
            if boundary is None or boundary.score < self.config.min_hit_neighbor_score:
                return False
        ball_xy = event.get("ball_xy", [-1.0, -1.0])
        if not isinstance(ball_xy, (list, tuple)) or len(ball_xy) < 2:
            return False
        top_band = max(
            float(self.config.hit_top_ignore_px),
            float(img_height) * max(0.0, float(self.config.hit_top_ignore_ratio)),
        )
        return float(ball_xy[1]) > top_band


class BestStackTrajectoryEventDetector:
    def __init__(
        self,
        *,
        fps: float,
        config: BestStackEventConfig | None = None,
        detector_config: TrajectoryEventDetectorConfig | None = None,
    ) -> None:
        self.config = config or BestStackEventConfig()
        self.fps = float(fps) if fps > 0 else 30.0
        if detector_config is None:
            detector_config = TrajectoryEventDetectorConfig(
                min_hit_track_score=self.config.min_hit_track_score,
            )
        self._detector = _GapReversalDetector(
            fps=self.fps,
            best_config=self.config,
            detector_config=detector_config,
        )
        self._tracks: dict[int, TrackResult] = {}
        self._bboxes: dict[int, list[list[float]]] = {}
        self._timestamps: dict[int, int] = {}
        self._wrist_speed: dict[int, float | None] = {}
        self._wrist_history: dict[tuple[int, int], tuple[int, float, float]] = {}
        self._pose_tracks: dict[int, tuple[float, float, int]] = {}
        self._next_pose_track_id = 0
        self._pending_hits: dict[int, dict[str, object]] = {}
        self._accepted_hit_frames: list[int] = []
        self._ready_events: deque[dict[str, object]] = deque()
        self._last_frame_id = -1
        self._frame_shape: Sequence[int] | None = None

    def reset(self) -> None:
        self._detector.reset()
        self._tracks.clear()
        self._bboxes.clear()
        self._timestamps.clear()
        self._wrist_speed.clear()
        self._wrist_history.clear()
        self._pose_tracks.clear()
        self._next_pose_track_id = 0
        self._pending_hits.clear()
        self._accepted_hit_frames.clear()
        self._ready_events.clear()
        self._last_frame_id = -1
        self._frame_shape = None

    def update(
        self,
        result: FrameResult,
        *,
        timestamp_ms: int | None = None,
        frame_shape: Sequence[int] | None = None,
        court_prediction: object | None = None,
        wrist_pose: Sequence[PersonPoseResult] | None = None,
    ) -> dict[str, object] | None:
        frame_id = int(result.frame_id)
        timestamp = int(timestamp_ms) if timestamp_ms is not None else int(round(frame_id * 1000.0 / self.fps))
        self._last_frame_id = frame_id
        self._frame_shape = frame_shape or self._frame_shape
        self._tracks[frame_id] = _copy_track(result.track)
        self._bboxes[frame_id] = [list(map(float, pose.bbox[:4])) for pose in result.pose if len(pose.bbox) >= 4]
        self._timestamps[frame_id] = timestamp
        self._wrist_speed[frame_id] = self._update_wrist_speed(frame_id, wrist_pose if wrist_pose is not None else result.pose)

        event = self._detector.update(
            result,
            timestamp_ms=timestamp,
            frame_shape=frame_shape,
            court_prediction=court_prediction,
        )
        if isinstance(event, dict):
            if event.get("event_type") == "hit":
                self._pending_hits[int(event["frame_id"])] = dict(event)
            else:
                self._ready_events.append(dict(event))

        self._collect_pose_recall(frame_id)
        self._finalize_ready_hits(frame_id)
        self._trim_history(frame_id)
        return self._ready_events.popleft() if self._ready_events else None

    def flush(self) -> list[dict[str, object]]:
        self._finalize_ready_hits(self._last_frame_id, force=True)
        events = list(self._ready_events)
        self._ready_events.clear()
        return events

    def _collect_pose_recall(self, current_frame: int) -> None:
        frame = current_frame - self.config.pose_recall_speed_window_frames
        if frame < 0 or frame in self._pending_hits:
            return
        delta = self._velocity_delta(frame)
        if delta is None or delta < self.config.pose_recall_dv_px:
            return
        neighbor_deltas = [
            value
            for candidate in range(
                frame - self.config.pose_recall_local_max_window,
                frame + self.config.pose_recall_local_max_window + 1,
            )
            if (value := self._velocity_delta(candidate)) is not None
        ]
        if neighbor_deltas and delta < max(neighbor_deltas):
            return
        if any(abs(frame - accepted) <= self.config.pose_recall_dedup_frames for accepted in self._accepted_hit_frames):
            return
        point = _track_point(self._tracks.get(frame))
        if point is None or self._in_top_band(point[1]):
            return
        if not self._near_player(frame):
            return
        wrist_peak = self._window_wrist_peak(frame)
        if wrist_peak is None or wrist_peak < self.config.pose_recall_speed_threshold:
            return
        self._pending_hits[frame] = {
            "event_type": "hit",
            "frame_id": frame,
            "timestamp_ms": self._timestamps.get(frame, int(round(frame * 1000.0 / self.fps))),
            "ball_xy": [point[0], point[1]],
            "rule": POSE_RECALL_RULE,
            "confidence": 0.5,
            "all_rules": [POSE_RECALL_RULE],
            "auxiliary_rules": [],
            "features": {"dv": delta, "wrist_norm_speed_peak": wrist_peak},
        }

    def _finalize_ready_hits(self, current_frame: int, *, force: bool = False) -> None:
        ready_frames = [
            frame
            for frame, event in self._pending_hits.items()
            if force
            or current_frame - frame
            >= self.config.relocation_window_frames
            + (self.config.pose_recall_dedup_frames if event.get("rule") == POSE_RECALL_RULE else 0)
        ]
        for frame in sorted(ready_frames):
            event = self._pending_hits.pop(frame)
            if event.get("rule") == POSE_RECALL_RULE and self._near_pending_trajectory_hit(frame):
                continue
            if event.get("rule") != POSE_RECALL_RULE and not self._passes_proximity(event):
                continue
            relocated = dict(event) if event.get("rule") == POSE_RECALL_RULE else self._relocate_event(event)
            final_frame = int(relocated["frame_id"])
            duplicate = (
                any(abs(final_frame - accepted) <= self.config.pose_recall_dedup_frames for accepted in self._accepted_hit_frames)
                if event.get("rule") == POSE_RECALL_RULE
                else final_frame in self._accepted_hit_frames
            )
            if duplicate:
                continue
            self._accepted_hit_frames.append(final_frame)
            self._ready_events.append(relocated)

    def _near_pending_trajectory_hit(self, frame: int) -> bool:
        return any(
            abs(frame - pending_frame) <= self.config.pose_recall_dedup_frames
            and event.get("rule") != POSE_RECALL_RULE
            for pending_frame, event in self._pending_hits.items()
        )

    def _passes_proximity(self, event: Mapping[str, object]) -> bool:
        frame = int(event["frame_id"])
        positions: list[tuple[int, float, float]] = []
        for candidate in range(frame - self.config.proximity_window_frames, frame + self.config.proximity_window_frames + 1):
            point = _track_point(self._tracks.get(candidate))
            if point is not None:
                positions.append((candidate, point[0], point[1]))
        if not positions:
            ball_xy = event.get("ball_xy")
            if isinstance(ball_xy, (list, tuple)) and len(ball_xy) >= 2:
                positions.append((frame, float(ball_xy[0]), float(ball_xy[1])))
        had_bbox = False
        for candidate, x, y in positions:
            bboxes = self._bboxes.get(candidate, [])
            had_bbox = had_bbox or bool(bboxes)
            if _point_near_any_bbox(x, y, bboxes, self.config.player_bbox_expansion_px):
                return True
        return not positions or not had_bbox

    def _near_player(self, frame: int) -> bool:
        for candidate in range(frame - self.config.proximity_window_frames, frame + self.config.proximity_window_frames + 1):
            point = _track_point(self._tracks.get(candidate))
            if point is None:
                continue
            if _point_near_any_bbox(
                point[0],
                point[1],
                self._bboxes.get(candidate, []),
                self.config.player_bbox_expansion_px,
            ):
                return True
        return False

    def _relocate_event(self, event: Mapping[str, object]) -> dict[str, object]:
        original = int(event["frame_id"])
        if _track_point(self._tracks.get(original)) is None:
            return dict(event)
        detected_angle = self._turn_angle(original)
        best_frame = original
        best_angle = -1.0
        for frame in range(original - self.config.relocation_window_frames, original + self.config.relocation_window_frames + 1):
            angle = self._turn_angle(frame)
            if angle is None:
                continue
            if (angle, -abs(frame - original)) > (best_angle, -abs(best_frame - original)):
                best_frame = frame
                best_angle = angle
        if best_frame == original or best_angle < self.config.relocation_min_angle:
            return dict(event)
        if detected_angle is not None and best_angle < detected_angle + self.config.relocation_angle_margin:
            return dict(event)
        relocated = dict(event)
        relocated["frame_id"] = best_frame
        relocated["timestamp_ms"] = self._timestamps.get(best_frame, int(round(best_frame * 1000.0 / self.fps)))
        features = dict(relocated.get("features") or {})
        features["relocated_from_frame"] = original
        features["relocation_turn_angle"] = best_angle
        relocated["features"] = features
        return relocated

    def _turn_angle(self, frame: int) -> float | None:
        incoming = self._velocity(frame, -1)
        outgoing = self._velocity(frame, 1)
        if incoming is None or outgoing is None:
            return None
        speed_in = hypot(*incoming)
        speed_out = hypot(*outgoing)
        if speed_in < self.config.relocation_min_turn_speed or speed_out < self.config.relocation_min_turn_speed:
            return None
        cosine = (incoming[0] * outgoing[0] + incoming[1] * outgoing[1]) / (speed_in * speed_out)
        return acos(max(-1.0, min(1.0, cosine)))

    def _velocity_delta(self, frame: int) -> float | None:
        incoming = self._velocity(frame, -1)
        outgoing = self._velocity(frame, 1)
        if incoming is None or outgoing is None:
            return None
        return hypot(outgoing[0] - incoming[0], outgoing[1] - incoming[1])

    def _velocity(self, frame: int, direction: int) -> tuple[float, float] | None:
        point = _track_point(self._tracks.get(frame))
        if point is None:
            return None
        for gap in range(1, self.config.pose_recall_neighbor_gap + 1):
            other = _track_point(self._tracks.get(frame + direction * gap))
            if other is None:
                continue
            if direction < 0:
                return ((point[0] - other[0]) / gap, (point[1] - other[1]) / gap)
            return ((other[0] - point[0]) / gap, (other[1] - point[1]) / gap)
        return None

    def _update_wrist_speed(self, frame: int, poses: Sequence[PersonPoseResult]) -> float | None:
        frame_peak: float | None = None
        assignments = self._associate_poses(frame, poses)
        for pose, track_id in assignments:
            if len(pose.bbox) < 4:
                continue
            bbox_height = max(float(pose.bbox[3]) - float(pose.bbox[1]), 1.0)
            for wrist_index in (9, 10):
                if wrist_index >= len(pose.keypoints) or wrist_index >= len(pose.scores):
                    continue
                if float(pose.scores[wrist_index]) < self.config.keypoint_min_score:
                    continue
                wrist = pose.keypoints[wrist_index]
                if len(wrist) < 2:
                    continue
                key = (track_id, wrist_index)
                previous = self._wrist_history.get(key)
                self._wrist_history[key] = (frame, float(wrist[0]), float(wrist[1]))
                if previous is None:
                    continue
                gap = frame - previous[0]
                if gap < 1 or gap > self.config.pose_track_max_gap_frames:
                    continue
                speed = hypot(float(wrist[0]) - previous[1], float(wrist[1]) - previous[2]) / gap
                if speed > self.config.wrist_speed_cap_bbox_heights * bbox_height:
                    continue
                normalized = speed / bbox_height
                frame_peak = normalized if frame_peak is None else max(frame_peak, normalized)
        return frame_peak

    def _associate_poses(
        self,
        frame: int,
        poses: Sequence[PersonPoseResult],
    ) -> list[tuple[PersonPoseResult, int]]:
        self._pose_tracks = {
            track_id: state
            for track_id, state in self._pose_tracks.items()
            if frame - state[2] <= self.config.pose_track_max_gap_frames
        }
        centers = [
            ((float(pose.bbox[0]) + float(pose.bbox[2])) / 2.0, (float(pose.bbox[1]) + float(pose.bbox[3])) / 2.0)
            if len(pose.bbox) >= 4
            else None
            for pose in poses
        ]
        pairs: list[tuple[float, int, int]] = []
        for pose_index, center in enumerate(centers):
            if center is None:
                continue
            for track_id, state in self._pose_tracks.items():
                distance = hypot(center[0] - state[0], center[1] - state[1])
                if distance <= self.config.pose_association_gate_px:
                    pairs.append((distance, pose_index, track_id))
        pairs.sort()
        assigned_poses: set[int] = set()
        assigned_tracks: set[int] = set()
        assignments: dict[int, int] = {}
        for _, pose_index, track_id in pairs:
            if pose_index in assigned_poses or track_id in assigned_tracks:
                continue
            assignments[pose_index] = track_id
            assigned_poses.add(pose_index)
            assigned_tracks.add(track_id)
        output: list[tuple[PersonPoseResult, int]] = []
        for pose_index, pose in enumerate(poses):
            track_id = assignments.get(pose_index)
            if track_id is None:
                track_id = self._next_pose_track_id
                self._next_pose_track_id += 1
            center = centers[pose_index]
            if center is not None:
                self._pose_tracks[track_id] = (center[0], center[1], frame)
            output.append((pose, track_id))
        return output

    def _window_wrist_peak(self, frame: int) -> float | None:
        values = [
            value
            for candidate in range(
                frame - self.config.pose_recall_speed_window_frames,
                frame + self.config.pose_recall_speed_window_frames + 1,
            )
            if (value := self._wrist_speed.get(candidate)) is not None
        ]
        return max(values) if values else None

    def _in_top_band(self, y: float) -> bool:
        height = int(self._frame_shape[0]) if self._frame_shape else 288
        top_band = max(
            float(self._detector.config.hit_top_ignore_px),
            height * max(0.0, float(self._detector.config.hit_top_ignore_ratio)),
        )
        return y <= top_band

    def _trim_history(self, current_frame: int) -> None:
        oldest = current_frame - max(32, self.config.context_history_frames)
        for mapping in (self._tracks, self._bboxes, self._timestamps, self._wrist_speed):
            for frame in [frame for frame in mapping if frame < oldest]:
                del mapping[frame]
        self._accepted_hit_frames = [frame for frame in self._accepted_hit_frames if frame >= oldest]


def _copy_track(track: TrackResult) -> TrackResult:
    return TrackResult(
        ball_xy=[float(value) for value in track.ball_xy],
        visible=int(track.visible),
        score=float(track.score),
        heatmap_shape=[int(value) for value in track.heatmap_shape],
    )


def _track_point(track: TrackResult | None) -> tuple[float, float] | None:
    if track is None or not track.visible or len(track.ball_xy) < 2:
        return None
    x, y = float(track.ball_xy[0]), float(track.ball_xy[1])
    if x < 0.0 or y < 0.0:
        return None
    return x, y


def _point_near_any_bbox(x: float, y: float, bboxes: Sequence[Sequence[float]], expand: float) -> bool:
    return any(
        len(bbox) >= 4
        and float(bbox[0]) - expand <= x <= float(bbox[2]) + expand
        and float(bbox[1]) - expand <= y <= float(bbox[3]) + expand
        for bbox in bboxes
    )
