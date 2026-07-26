from __future__ import annotations

"""Prototype: global candidate-graph association vs the production filter stack.

Replays cached TrackNet candidates and compares:
  1. oracle      — nearest candidate per frame (association upper bound)
  2. naive       — highest-score candidate per frame (WASB-style, no tracker)
  3. dp_offline  — shortest-path/DP association over the whole clip
  4. dp_lag      — same DP, but decisions committed with a fixed-lag window
                   (emulates streaming with the accepted latency budget)

Usage:
  python scripts/experiment_windowed_dp_track.py 1 2 \
      --annotation-dir F:/DataSet/BD/New \
      --cache-dir outputs/fixed_lag_experiment/cache_new \
      --output-dir outputs/fixed_lag_experiment/dp_prototype \
      --lag-frames 24
"""

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.evaluate_fixed_lag_delay import (  # noqa: E402
    _dataset_court_prediction,
    _load_annotations,
    _load_cache,
    _metrics,
)
from src.postprocess.track_filter import (  # noqa: E402
    _extract_court_filter,
    _point_inside_court_plane,
    _point_inside_projected_court_air,
)


Point = tuple[float, float]


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("datasets", nargs="+")
    parser.add_argument("--annotation-dir", type=Path, required=True)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--distance-threshold", type=float, default=20.0)
    parser.add_argument("--lag-frames", type=int, default=24)
    parser.add_argument("--max-gap-frames", type=int, default=12)
    parser.add_argument("--min-score", type=float, default=0.15)
    parser.add_argument("--node-base-gain", type=float, default=1.0)
    parser.add_argument("--node-score-gain", type=float, default=2.0)
    parser.add_argument("--speed-cost-per-px", type=float, default=0.020)
    parser.add_argument("--gap-cost", type=float, default=0.35)
    parser.add_argument("--static-penalty", type=float, default=0.8)
    parser.add_argument("--static-speed-px", type=float, default=3.0)
    parser.add_argument("--court-out-penalty", type=float, default=1.5)
    parser.add_argument("--min-chain-gain", type=float, default=6.0)
    return parser.parse_args()


class _Node:
    __slots__ = ("frame_pos", "point", "score", "gain", "best", "parent", "used")

    def __init__(self, frame_pos: int, point: Point, score: float, gain: float) -> None:
        self.frame_pos = frame_pos
        self.point = point
        self.score = score
        self.gain = gain
        self.best = gain
        self.parent: _Node | None = None
        self.used = False


def _distance(a: Point, b: Point) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _point_in_court(point: Point, court_filter, frame_size) -> bool:
    if court_filter is None:
        return True
    if court_filter.corners is not None:
        return _point_inside_projected_court_air(
            point,
            court_filter.corners,
            margin_px=96.0,
            air_extension_ratio=1.0,
            lateral_expansion_ratio=1.25,
            frame_size=frame_size,
        )
    if court_filter.image_to_court_h is None:
        return False
    return _point_inside_court_plane(point, court_filter.image_to_court_h, 140.0)


def _build_nodes(
    cache_rows: list[dict[str, Any]],
    args: argparse.Namespace,
    court_filter,
    frame_size,
) -> list[list[_Node]]:
    frames: list[list[_Node]] = []
    for frame_pos, row in enumerate(cache_rows):
        nodes: list[_Node] = []
        for item in row.get("candidates", []):
            if not item.get("visible"):
                continue
            xy = item.get("ball_xy", [-1.0, -1.0])
            x, y = float(xy[0]), float(xy[1])
            score = float(item.get("score", 0.0))
            if x < 0 or y < 0 or score < args.min_score:
                continue
            point = (x, y)
            gain = args.node_base_gain + args.node_score_gain * score
            if not _point_in_court(point, court_filter, frame_size):
                gain -= args.court_out_penalty
            nodes.append(_Node(frame_pos, point, score, gain))
        frames.append(nodes)
    return frames


def _edge_cost(previous: _Node, node: _Node, args: argparse.Namespace) -> float:
    gap = node.frame_pos - previous.frame_pos
    speed = _distance(previous.point, node.point) / gap
    cost = speed * args.speed_cost_per_px + args.gap_cost * (gap - 1)
    if speed < args.static_speed_px:
        cost += args.static_penalty
    return cost


def _run_dp(frames: list[list[_Node]], args: argparse.Namespace) -> None:
    max_gap = max(1, args.max_gap_frames)
    for frame_pos, nodes in enumerate(frames):
        for node in nodes:
            node.best = node.gain
            node.parent = None
            for gap in range(1, max_gap + 1):
                previous_pos = frame_pos - gap
                if previous_pos < 0:
                    break
                for previous in frames[previous_pos]:
                    total = previous.best + node.gain - _edge_cost(previous, node, args)
                    if total > node.best:
                        node.best = total
                        node.parent = previous


def _extract_chains(frames: list[list[_Node]], args: argparse.Namespace) -> dict[int, _Node]:
    assignment: dict[int, _Node] = {}
    all_nodes = [node for nodes in frames for node in nodes]
    while True:
        tail = max(
            (node for node in all_nodes if not node.used),
            key=lambda node: node.best,
            default=None,
        )
        if tail is None or tail.best < args.min_chain_gain:
            break
        chain: list[_Node] = []
        node: _Node | None = tail
        conflict = False
        while node is not None:
            if node.used or node.frame_pos in assignment:
                conflict = True
                break
            chain.append(node)
            node = node.parent
        if conflict or not chain:
            tail.used = True
            continue
        for item in chain:
            item.used = True
            assignment[item.frame_pos] = item
    return assignment


def _run_dp_fixed_lag(
    frames: list[list[_Node]],
    args: argparse.Namespace,
) -> dict[int, _Node]:
    """Streaming variant: at frame t, commit the assignment for frame t - lag.

    Runs the same forward DP incrementally; when committing frame p, backtrack
    from the best-scoring node in the lookahead window and take that path's
    node at p (if any).
    """
    max_gap = max(1, args.max_gap_frames)
    lag = max(0, args.lag_frames)
    committed: dict[int, _Node] = {}

    def _relax(frame_pos: int) -> None:
        for node in frames[frame_pos]:
            node.best = node.gain
            node.parent = None
            for gap in range(1, max_gap + 1):
                previous_pos = frame_pos - gap
                if previous_pos < 0:
                    break
                for previous in frames[previous_pos]:
                    total = previous.best + node.gain - _edge_cost(previous, node, args)
                    if total > node.best:
                        node.best = total
                        node.parent = previous

    def _commit(frame_pos: int, horizon: int) -> None:
        window_nodes = [
            node
            for pos in range(frame_pos, min(horizon + 1, len(frames)))
            for node in frames[pos]
        ]
        best_tail = max(window_nodes, key=lambda node: node.best, default=None)
        if best_tail is None or best_tail.best < args.min_chain_gain:
            return
        node: _Node | None = best_tail
        while node is not None and node.frame_pos > frame_pos:
            node = node.parent
        if node is not None and node.frame_pos == frame_pos:
            committed[frame_pos] = node

    for frame_pos in range(len(frames)):
        _relax(frame_pos)
        commit_pos = frame_pos - lag
        if commit_pos >= 0:
            _commit(commit_pos, frame_pos)
    for commit_pos in range(max(0, len(frames) - lag), len(frames)):
        _commit(commit_pos, len(frames) - 1)
    return committed


def _emitted_from_assignment(assignment: dict[int, _Node], frame_ids: list[int]):
    emitted = {}
    for frame_pos, node in assignment.items():
        emitted[frame_ids[frame_pos]] = (node.point, node.score, "dp", "dp")
    return emitted


def main() -> None:
    args = _parse_args()
    cache_meta = json.loads((args.cache_dir / "cache_meta.json").read_text(encoding="utf-8"))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    combined: dict[str, Any] = {}

    for name in args.datasets:
        dataset_meta = cache_meta["datasets"][name]
        fps = float(dataset_meta["fps"])
        width = int(dataset_meta["width"])
        height = int(dataset_meta["height"])
        frame_size = (float(width), float(height))
        cache_rows = _load_cache(Path(dataset_meta["cache"]))
        frame_ids = [int(row["frame_id"]) for row in cache_rows]
        visible_gt, _ = _load_annotations(args.annotation_dir, name, width, height)
        active_start, active_end = min(visible_gt), max(visible_gt)
        court_prediction = _dataset_court_prediction(cache_meta, dataset_meta)
        court_filter = _extract_court_filter(court_prediction)

        results: dict[str, Any] = {"fps": fps, "frame_count": len(cache_rows), "methods": {}}

        oracle = {}
        naive = {}
        for row, frame_id in zip(cache_rows, frame_ids):
            gt_point = visible_gt.get(frame_id)
            best_error = None
            best_point = None
            best_score_point = None
            best_score = -1.0
            for item in row.get("candidates", []):
                if not item.get("visible"):
                    continue
                xy = item.get("ball_xy", [-1.0, -1.0])
                x, y = float(xy[0]), float(xy[1])
                if x < 0 or y < 0:
                    continue
                score = float(item.get("score", 0.0))
                if score >= 0.35 and score > best_score:
                    best_score = score
                    best_score_point = (x, y)
                if gt_point is not None:
                    error = _distance((x, y), gt_point)
                    if best_error is None or error < best_error:
                        best_error = error
                        best_point = (x, y)
            if best_point is not None:
                oracle[frame_id] = (best_point, 1.0, "oracle", "oracle")
            if best_score_point is not None:
                naive[frame_id] = (best_score_point, best_score, "naive", "naive")

        metric_args = {
            "threshold": float(args.distance_threshold),
            "active_start": active_start,
            "active_end": active_end,
        }
        results["methods"]["oracle"] = _metrics(oracle, visible_gt, frame_ids, **metric_args)
        results["methods"]["naive_top_score"] = _metrics(naive, visible_gt, frame_ids, **metric_args)

        frames = _build_nodes(cache_rows, args, court_filter, frame_size)
        _run_dp(frames, args)
        assignment = _extract_chains(frames, args)
        results["methods"]["dp_offline"] = _metrics(
            _emitted_from_assignment(assignment, frame_ids), visible_gt, frame_ids, **metric_args
        )

        frames_lag = _build_nodes(cache_rows, args, court_filter, frame_size)
        committed = _run_dp_fixed_lag(frames_lag, args)
        results["methods"][f"dp_lag_{args.lag_frames}f"] = _metrics(
            _emitted_from_assignment(committed, frame_ids), visible_gt, frame_ids, **metric_args
        )

        for method, block in results["methods"].items():
            print(
                f"[{name}] {method:<16} F1={block['f1']:.4f} P={block['precision']:.4f} "
                f"R={block['recall']:.4f} jumps={block['jumps_over_110px']}"
            )
        combined[name] = results

    output_path = args.output_dir / "dp_prototype.json"
    output_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output_path)


if __name__ == "__main__":
    main()
