from __future__ import annotations

import argparse
import csv
import json
import math
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.postprocess.fixed_lag_track import FixedLagTrackConfig, FixedLagTrackPostProcessor
from src.postprocess.track_filter import BallTrackFilter, BallTrackFilterConfig
from src.utils.structures import TrackResult


Point = tuple[float, float]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Replay cached TrackNet candidates through the production causal filter plus "
            "FixedLagTrackPostProcessor at multiple delay windows, and compare accuracy."
        ),
    )
    parser.add_argument("datasets", nargs="+", help="Dataset stems, for example: 1 2")
    parser.add_argument("--annotation-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--distance-threshold", type=float, default=20.0)
    parser.add_argument(
        "--delay-ms",
        action="append",
        type=int,
        default=[],
        help="Fixed-lag window in milliseconds; may be repeated. Default: 0 300 500 800 1000.",
    )
    parser.add_argument(
        "--active-range",
        action="append",
        default=[],
        metavar="DATASET:START:END",
        help="Limit positive evaluation to the airborne/rally span; may be repeated.",
    )
    parser.add_argument(
        "--lag-set",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Override a FixedLagTrackConfig field for every delay arm; may be repeated.",
    )
    return parser.parse_args()


def _apply_lag_overrides(config: FixedLagTrackConfig, values: list[str]) -> None:
    for value in values:
        name, raw_value = value.split("=", maxsplit=1)
        if not hasattr(config, name):
            raise ValueError(f"Unknown FixedLagTrackConfig field: {name}")
        current = getattr(config, name)
        if isinstance(current, bool):
            parsed: Any = raw_value.strip().lower() in {"true", "1"}
        elif isinstance(current, int):
            parsed = int(raw_value)
        elif isinstance(current, float):
            parsed = float(raw_value)
        else:
            raise ValueError(f"Unsupported config field type for {name}")
        setattr(config, name, parsed)


def _load_xml_annotations(xml_path: Path) -> tuple[dict[int, Point], set[int]]:
    root = ET.parse(xml_path).getroot()
    visible: dict[int, Point] = {}
    annotated: set[int] = set()
    for element in root.findall(".//track/points"):
        frame = int(element.attrib["frame"])
        annotated.add(frame)
        if element.attrib.get("outside", "0") == "1":
            continue
        values = element.attrib.get("points", "").split(",")
        if len(values) != 2:
            continue
        visible[frame] = (float(values[0]), float(values[1]))
    if not visible:
        raise ValueError(f"No visible point annotations found in {xml_path}")
    return visible, annotated


def _load_csv_annotations(
    csv_path: Path,
    width: int,
    height: int,
) -> tuple[dict[int, Point], set[int]]:
    visible: dict[int, Point] = {}
    annotated: set[int] = set()
    with csv_path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            frame = int(float(row["Frame"]))
            annotated.add(frame)
            if int(float(row.get("Ball", 0))) != 1:
                continue
            x = float(row["x"])
            y = float(row["y"])
            if 0.0 <= x <= 1.5 and 0.0 <= y <= 1.5:
                point = (x * width, y * height)
            else:
                point = (x, y)
            visible[frame] = point
    if not visible:
        raise ValueError(f"No visible annotations found in {csv_path}")
    return visible, annotated


def _load_annotations(
    annotation_dir: Path,
    name: str,
    width: int,
    height: int,
) -> tuple[dict[int, Point], set[int]]:
    xml_path = annotation_dir / f"{name}.xml"
    if xml_path.is_file():
        return _load_xml_annotations(xml_path)
    csv_path = annotation_dir / f"{name}.csv"
    if csv_path.is_file():
        return _load_csv_annotations(csv_path, width, height)
    raise FileNotFoundError(f"No annotation file for dataset {name} in {annotation_dir}")


def _load_cache(cache_path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with cache_path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _track_from_cache(item: dict[str, Any]) -> TrackResult:
    return TrackResult(
        ball_xy=[float(value) for value in item.get("ball_xy", [-1.0, -1.0])[:2]],
        visible=int(bool(item.get("visible", 0))),
        score=float(item.get("score", 0.0)),
        heatmap_shape=[int(value) for value in item.get("heatmap_shape", [])],
    )


def _point_from_track(track: TrackResult) -> Point | None:
    if not track.visible or len(track.ball_xy) < 2:
        return None
    x, y = float(track.ball_xy[0]), float(track.ball_xy[1])
    if x < 0 or y < 0:
        return None
    return x, y


def _distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return ordered[lower]
    alpha = position - lower
    return ordered[lower] * (1.0 - alpha) + ordered[upper] * alpha


def _dataset_court_prediction(meta: dict[str, Any], dataset_meta: dict[str, Any]) -> dict | None:
    prediction = dataset_meta.get("court_prediction")
    if isinstance(prediction, dict) and prediction.get("valid"):
        return prediction
    corners = dataset_meta.get("court_corners") or meta.get("court_corners")
    if corners is None:
        return None
    return {"valid": True, "corners": corners}


def _replay(
    cache_rows: list[dict[str, Any]],
    *,
    fps: float,
    width: int,
    height: int,
    court_prediction: dict | None,
    delay_ms: int,
    lag_overrides: list[str] | None = None,
) -> dict[int, tuple[Point | None, float, str, str]]:
    """Return {frame_id: (output_point, score, action, reason)} after fixed-lag repair."""
    track_filter = BallTrackFilter(BallTrackFilterConfig(fps=fps), debug_enabled=True)
    lag_config = FixedLagTrackConfig(fps=fps, delay_ms=delay_ms)
    _apply_lag_overrides(lag_config, lag_overrides or [])
    postprocessor = FixedLagTrackPostProcessor(lag_config)
    emitted: dict[int, tuple[Point | None, float, str, str]] = {}

    def _collect(lagged) -> None:
        if lagged is None:
            return
        debug = lagged.debug_record or {}
        emitted[int(lagged.frame_index)] = (
            _point_from_track(lagged.track),
            float(lagged.track.score),
            str(debug.get("action", "")),
            str(debug.get("reason", "")),
        )

    for cache_row in cache_rows:
        candidates = [_track_from_cache(item) for item in cache_row.get("candidates", [])]
        person_bboxes = [
            tuple(float(value) for value in bbox)
            for bbox in cache_row.get("person_bboxes", [])
        ]
        causal = track_filter.update_candidates(
            candidates,
            dt=1.0 / fps,
            frame_shape=(height, width, 3),
            court_prediction=court_prediction,
            person_bboxes=person_bboxes,
        )
        _collect(
            postprocessor.push(
                causal,
                candidates=candidates,
                debug_record=track_filter.last_debug_record(),
            )
        )
        track_filter.debug_records.clear()

    for lagged in postprocessor.flush():
        _collect(lagged)
    return emitted


def _metrics(
    emitted: dict[int, tuple[Point | None, float, str, str]],
    visible_gt: dict[int, Point],
    frame_ids: list[int],
    *,
    threshold: float,
    active_start: int,
    active_end: int,
) -> dict[str, Any]:
    active_ids = [frame_id for frame_id in frame_ids if active_start <= frame_id <= active_end]
    gt_positive = sum(frame_id in visible_gt for frame_id in active_ids)
    pred_visible_active = 0
    correct = 0
    errors: list[float] = []
    miss_reasons: Counter[str] = Counter()
    for frame_id in active_ids:
        point = emitted.get(frame_id, (None, 0.0, "", ""))[0]
        gt_point = visible_gt.get(frame_id)
        if point is not None:
            pred_visible_active += 1
        if gt_point is None:
            continue
        if point is not None:
            error = _distance(point, gt_point)
            errors.append(error)
            if error <= threshold:
                correct += 1
                continue
        miss_reasons[emitted.get(frame_id, (None, 0.0, "", "no_emit"))[3] or "no_emit"] += 1

    precision = correct / pred_visible_active if pred_visible_active else 0.0
    recall = correct / gt_positive if gt_positive else 0.0
    f1 = 2.0 * precision * recall / (precision + recall) if precision + recall else 0.0

    jumps = 0
    previous: tuple[int, Point] | None = None
    for frame_id in frame_ids:
        point = emitted.get(frame_id, (None, 0.0, "", ""))[0]
        if point is None:
            previous = None
            continue
        if previous is not None and frame_id == previous[0] + 1:
            if _distance(previous[1], point) > 110.0:
                jumps += 1
        previous = (frame_id, point)

    pre_fp = sum(
        frame_id < active_start and emitted.get(frame_id, (None,))[0] is not None
        for frame_id in frame_ids
    )
    post_fp = sum(
        frame_id > active_end and emitted.get(frame_id, (None,))[0] is not None
        for frame_id in frame_ids
    )
    return {
        "gt_positive": gt_positive,
        "pred_visible_active": pred_visible_active,
        "correct": correct,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "error_median": median(errors) if errors else None,
        "error_p90": _percentile(errors, 0.90),
        "error_max": max(errors) if errors else None,
        "jumps_over_110px": jumps,
        "pre_fp": pre_fp,
        "post_fp": post_fp,
        "miss_reasons": dict(miss_reasons.most_common(12)),
    }


def main() -> None:
    args = _parse_args()
    delays = args.delay_ms or [0, 300, 500, 800, 1000]
    cache_meta = json.loads((args.cache_dir / "cache_meta.json").read_text(encoding="utf-8"))
    active_ranges: dict[str, tuple[int, int]] = {}
    for value in args.active_range:
        name, start, end = value.split(":", maxsplit=2)
        active_ranges[name] = (int(start), int(end))
    args.output_dir.mkdir(parents=True, exist_ok=True)

    combined: dict[str, Any] = {}
    for name in args.datasets:
        dataset_meta = cache_meta["datasets"][name]
        fps = float(dataset_meta["fps"])
        width = int(dataset_meta["width"])
        height = int(dataset_meta["height"])
        cache_rows = _load_cache(Path(dataset_meta["cache"]))
        frame_ids = [int(row["frame_id"]) for row in cache_rows]
        visible_gt, _ = _load_annotations(args.annotation_dir, name, width, height)
        active_start, active_end = active_ranges.get(
            name, (min(visible_gt), max(visible_gt))
        )
        court_prediction = _dataset_court_prediction(cache_meta, dataset_meta)

        results: dict[str, Any] = {
            "fps": fps,
            "frame_count": len(cache_rows),
            "active_start": active_start,
            "active_end": active_end,
            "court_filter": court_prediction is not None,
            "delays": {},
        }
        for delay in delays:
            emitted = _replay(
                cache_rows,
                fps=fps,
                width=width,
                height=height,
                court_prediction=court_prediction,
                delay_ms=delay,
                lag_overrides=list(args.lag_set),
            )
            delay_frames = FixedLagTrackConfig(fps=fps, delay_ms=delay).delay_frames
            block = _metrics(
                emitted,
                visible_gt,
                frame_ids,
                threshold=float(args.distance_threshold),
                active_start=active_start,
                active_end=active_end,
            )
            block["delay_frames"] = delay_frames
            results["delays"][str(delay)] = block
            print(
                f"[{name}] delay={delay:>4}ms ({delay_frames:>2}f) "
                f"F1={block['f1']:.4f} P={block['precision']:.4f} R={block['recall']:.4f} "
                f"jumps={block['jumps_over_110px']}"
            )
        combined[name] = results

    output_path = args.output_dir / "fixed_lag_delay_sweep.json"
    output_path.write_text(
        json.dumps(combined, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(output_path)


if __name__ == "__main__":
    main()
