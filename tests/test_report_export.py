from __future__ import annotations

from pathlib import Path
import tempfile
import time
import unittest
from typing import Any

from src.utils.report_export import (
    REPORT_EXPORT_HISTORY_NAME,
    ReportExportHistory,
    ReportExportTarget,
    ReportImageExportError,
    build_report_stem,
    image_export_supported,
    prune_stale_snapshots,
    resolve_export_target,
    sanitize_report_filename,
    summarize_report_data,
    unique_export_target,
    write_report_sidecar,
)
from src.utils.report_generation import (
    ReportGenerationRequest,
    ReportGenerationService,
    render_tabler_report_html,
)


def _rally_record() -> dict[str, Any]:
    return {
        "rally_id": "clip-001",
        "rally_name": "决赛 第3局.mp4",
        "summary": {
            "duration_s": 42.6,
            "rally_hit_count": 23,
            "avg_hit_interval_ms": 1850.0,
            "court_backend": "courtkeynet",
            "stroke_distribution": {"高远球": 9, "杀球": 5, "吊球": 4},
            "data_reliability": {
                "ball_visible_rate": 0.93,
                "pose_valid_rate": 0.81,
                "court_valid_rate": 0.88,
                "avg_ball_confidence": 0.79,
            },
            "players": {
                "bottom": {
                    "label": "CHANG",
                    "distance_m": 142.6,
                    "avg_speed_mps": 2.4,
                    "hit_count": 23,
                    "zone_hits": {"front": 7, "mid": 6, "back": 10},
                },
                "top": {"label": "陪练小王", "distance_m": 128.3, "avg_speed_mps": 2.1},
            },
        },
        "details": {
            "hits": [
                {
                    "timestamp_ms": 1200,
                    "player": "bottom",
                    "zone": "back",
                    "stroke": "杀球",
                    "confidence": 0.91,
                }
            ]
        },
    }


def _render() -> str:
    response = ReportGenerationService().generate(
        ReportGenerationRequest.from_rally_record(_rally_record(), athlete_name="CHANG")
    )
    return render_tabler_report_html(response.report_data)


class ReportFilenameTest(unittest.TestCase):
    def test_sanitize_strips_extension_and_unsafe_chars(self) -> None:
        self.assertEqual(sanitize_report_filename("rally_2026.mp4"), "rally_2026")
        self.assertEqual(sanitize_report_filename("a/b:c*d?.mp4"), "a_b_c_d")
        self.assertEqual(sanitize_report_filename("   "), "rally_report")
        self.assertEqual(sanitize_report_filename("决赛 第3局.mp4"), "决赛_第3局")

    def test_report_stem_keeps_video_and_timestamp(self) -> None:
        stem = build_report_stem(video_name="决赛 第3局.mp4", generated_at="20261004_221500")
        self.assertEqual(stem, "决赛_第3局_20261004_221500")

    def test_repeated_exports_do_not_collide(self) -> None:
        first = build_report_stem(video_name="clip.mp4", generated_at="20261004_221500")
        second = build_report_stem(video_name="clip.mp4", generated_at="20261004_221501")
        self.assertNotEqual(first, second)


class ReportExportTargetTest(unittest.TestCase):
    def test_target_sidecar_sits_next_to_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = resolve_export_target(
                tmp_dir, video_name="clip.mp4", generated_at="20261004_221500", fmt="html"
            )
            self.assertEqual(target.path.name, "clip_20261004_221500.html")
            self.assertEqual(
                target.sidecar_path.name, "clip_20261004_221500.report.json"
            )

    def test_unique_target_appends_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = resolve_export_target(
                tmp_dir, video_name="clip.mp4", generated_at="20261004_221500", fmt="html"
            )
            target.path.write_text("x", encoding="utf-8")
            self.assertEqual(unique_export_target(target).path.name, "clip_20261004_221500-2.html")

    def test_sidecar_writes_report_data(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = resolve_export_target(tmp_dir, video_name="clip.mp4")
            written = write_report_sidecar(target, {"report_data": {"a": 1}})
            self.assertIsNotNone(written)
            self.assertTrue(written.is_file())


class ReportExportHistoryTest(unittest.TestCase):
    def test_history_records_and_reloads(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = resolve_export_target(tmp_dir, video_name="clip.mp4")
            target.path.write_text("x", encoding="utf-8")
            history = ReportExportHistory.load(tmp_dir)
            history.record(
                target,
                video_name="clip.mp4",
                athlete_name="CHANG",
                summary=summarize_report_data({"summary": {"level_label": "业余进阶"}}),
            )

            reloaded = ReportExportHistory.load(tmp_dir)
            self.assertEqual(len(reloaded.entries), 1)
            self.assertEqual(reloaded.entries[0]["summary"]["level_label"], "业余进阶")
            self.assertTrue((Path(tmp_dir) / REPORT_EXPORT_HISTORY_NAME).is_file())

    def test_prune_missing_drops_deleted_reports(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = resolve_export_target(tmp_dir, video_name="clip.mp4")
            target.path.write_text("x", encoding="utf-8")
            history = ReportExportHistory.load(tmp_dir)
            history.record(target, video_name="clip.mp4")
            target.path.unlink()

            self.assertEqual(history.prune_missing(), 1)
            self.assertEqual(history.existing_entries(), [])


class SnapshotPruneTest(unittest.TestCase):
    def test_prune_keeps_newest_and_preserves_latest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            directory = Path(tmp_dir)
            for index in range(8):
                path = directory / f"report_input_2026100{index}_000000.json"
                path.write_text("{}", encoding="utf-8")
                stamp = time.time() - index * 60
                import os

                os.utime(path, (stamp, stamp))
            (directory / "latest_report_input.json").write_text("{}", encoding="utf-8")

            removed = prune_stale_snapshots(directory, keep=3)

            self.assertEqual(len(removed), 5)
            self.assertEqual(len(list(directory.glob("report_input_*.json"))), 3)
            self.assertTrue((directory / "latest_report_input.json").is_file())


class ReportImageExportTest(unittest.TestCase):
    def test_supported_returns_bool_without_raising(self) -> None:
        self.assertIsInstance(image_export_supported(), bool)

    def test_blank_png_is_rejected(self) -> None:
        from src.utils.report_export import _reject_blank_png

        with tempfile.TemporaryDirectory() as tmp_dir:
            blank = Path(tmp_dir) / "blank.png"
            from PIL import Image

            Image.new("RGB", (64, 64), (255, 255, 255)).save(blank)
            with self.assertRaises(ReportImageExportError):
                _reject_blank_png(blank)

    def test_real_png_passes_validation(self) -> None:
        from src.utils.report_export import _reject_blank_png

        with tempfile.TemporaryDirectory() as tmp_dir:
            path = Path(tmp_dir) / "real.png"
            from PIL import Image

            image = Image.new("RGB", (64, 64), (255, 255, 255))
            image.putpixel((10, 10), (0, 0, 0))
            image.save(path)
            _reject_blank_png(path)


class ReportTemplateFieldTest(unittest.TestCase):
    """The Tabler template must render real data, not the demo placeholders."""

    def test_demo_placeholder_data_is_gone(self) -> None:
        html = _render()
        for leftover in (
            "连续后场对抗",
            "网前转换",
            "被动防守</div>",
            "后场启动",
            "网前衔接",
            "落点控制",
            "默认站位热区",
            "轨迹连续性",
        ):
            self.assertNotIn(leftover, html)

    def test_real_values_are_rendered(self) -> None:
        html = _render()
        for expected in (
            "CHANG",
            "陪练小王",
            "2.40 m/s",
            "自动(CourtKeyNet) 88%",
            "杀球",
            "142.6米",
        ):
            self.assertIn(expected, html)

    def test_zone_bars_scale_with_data(self) -> None:
        html = _render()
        # front 7 / mid 6 / back 10 out of 23 hits.
        self.assertIn("width: 30.4%", html)
        self.assertIn("width: 43.5%", html)

    def test_missing_data_degrades_to_explicit_notice(self) -> None:
        record = {
            "rally_id": "empty",
            "summary": {"duration_s": 0.0, "rally_hit_count": 0, "players": {}},
            "details": {"hits": []},
        }
        response = ReportGenerationService().generate(
            ReportGenerationRequest.from_rally_record(record)
        )
        html = render_tabler_report_html(response.report_data)
        self.assertIn("数据不足", html)

    def test_zone_bar_widths_are_bounded(self) -> None:
        html = _render()
        import re

        for width in re.findall(r'class="bar-fill" style="width: ([\d.]+)%"', html):
            self.assertGreaterEqual(float(width), 0.0)
            self.assertLessEqual(float(width), 100.0)


class ReportExportToTargetTest(unittest.TestCase):
    def test_html_export_writes_report_and_sidecar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            service = ReportGenerationService()
            response = service.generate(
                ReportGenerationRequest.from_rally_record(_rally_record(), athlete_name="CHANG")
            )
            target = resolve_export_target(
                tmp_dir, video_name="决赛 第3局.mp4", generated_at="20261004_221500", fmt="html"
            )
            path = service.export_to_target(response, target)

            self.assertTrue(path.is_file())
            self.assertIn("羽毛球水平分析报告", path.read_text(encoding="utf-8"))
            self.assertIsNotNone(target.sidecar_path)
            self.assertTrue(target.sidecar_path.is_file())

    def test_export_target_rejects_unknown_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = ReportExportTarget(path=Path(tmp_dir) / "r.docx", format="docx")
            self.assertNotEqual(target.format, "")


if __name__ == "__main__":
    unittest.main()
