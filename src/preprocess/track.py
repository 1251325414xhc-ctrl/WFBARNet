from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence, Tuple

import cv2
import numpy as np
import torch


# TrackNetV3 downsamples three times (MaxPool2d) and upsamples three times
# (interpolate scale_factor=2). Any input whose height or width is not a
# multiple of 8 makes the skip-connection tensors misalign, which surfaces as
# "Sizes of tensors must match except in dimension 1". Real-world phone
# footage hits this constantly (854x480, 1280x720, 1920x1080 all fail), so
# sizes are snapped to the nearest valid value before resizing.
TRACK_SIZE_ALIGNMENT = 8


@dataclass
class TrackPreprocessMeta:
    orig_size: tuple[int, int]
    resized_size: tuple[int, int]
    scale_x: float
    scale_y: float


def align_track_input_size(size: Tuple[int, int]) -> Tuple[int, int]:
    """Snap (width, height) to multiples of 8, never shrinking below 8px."""
    width, height = int(size[0]), int(size[1])
    aligned_w = max(TRACK_SIZE_ALIGNMENT, int(round(width / TRACK_SIZE_ALIGNMENT)) * TRACK_SIZE_ALIGNMENT)
    aligned_h = max(TRACK_SIZE_ALIGNMENT, int(round(height / TRACK_SIZE_ALIGNMENT)) * TRACK_SIZE_ALIGNMENT)
    return aligned_w, aligned_h


def is_track_input_size_valid(size: Tuple[int, int]) -> bool:
    width, height = int(size[0]), int(size[1])
    return width % TRACK_SIZE_ALIGNMENT == 0 and height % TRACK_SIZE_ALIGNMENT == 0


def preprocess_track_window(
    frames: Sequence[np.ndarray],
    input_size: Tuple[int, int],
    device: str,
) -> tuple[torch.Tensor, TrackPreprocessMeta]:
    if len(frames) != 3:
        raise ValueError("Track branch expects exactly 3 frames.")
    in_w, in_h = align_track_input_size(input_size)
    orig_h, orig_w = frames[1].shape[:2]
    stacked = np.empty((9, in_h, in_w), dtype=np.uint8)
    for index, frame in enumerate(frames):
        resized = cv2.resize(frame, (in_w, in_h), interpolation=cv2.INTER_LINEAR)
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        stacked[index * 3 : (index + 1) * 3] = rgb.transpose(2, 0, 1)
    tensor = torch.from_numpy(stacked).unsqueeze(0).to(device=device, dtype=torch.float32).mul_(1.0 / 255.0)
    meta = TrackPreprocessMeta(
        orig_size=(orig_w, orig_h),
        resized_size=(in_w, in_h),
        scale_x=orig_w / float(in_w),
        scale_y=orig_h / float(in_h),
    )
    return tensor, meta


def preprocess_track_batch(
    batch_frames: list[Sequence[np.ndarray]],
    input_size: Tuple[int, int],
    device: str,
) -> tuple[torch.Tensor, list[TrackPreprocessMeta]]:
    if not batch_frames:
        raise ValueError("Batch is empty.")

    in_w, in_h = align_track_input_size(input_size)
    batch_tensor = np.empty((len(batch_frames), 9, in_h, in_w), dtype=np.uint8)

    metas = []
    for batch_index, frames in enumerate(batch_frames):
        if len(frames) != 3:
            raise ValueError("Each window must have exactly 3 frames.")

        orig_h, orig_w = frames[1].shape[:2]
        for frame_index, frame in enumerate(frames):
            resized = cv2.resize(frame, (in_w, in_h), interpolation=cv2.INTER_LINEAR)
            rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
            batch_tensor[batch_index, frame_index * 3 : (frame_index + 1) * 3] = rgb.transpose(2, 0, 1)

        metas.append(TrackPreprocessMeta(
            orig_size=(orig_w, orig_h),
            resized_size=(in_w, in_h),
            scale_x=orig_w / float(in_w),
            scale_y=orig_h / float(in_h),
        ))

    tensor = torch.from_numpy(batch_tensor).to(device=device, dtype=torch.float32).mul_(1.0 / 255.0)
    return tensor, metas
