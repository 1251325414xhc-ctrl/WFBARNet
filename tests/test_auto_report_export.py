"""Auto-export decision logic after an analysis run ends.

Regression this guards: a *completed* video analysis used to produce no report
at all, because the auto-export hook only fired when ``stopped`` was True
*and* ``_auto_report_after_stop`` was set — and that flag is only ever set for
camera runs. Video runs therefore required a manual 导出报告 click, which read
as "分析完成后没有生成报告".

The rule now:
  * natural end of footage (``stopped=False``)  -> auto-export
  * camera run the user stopped (flag set)      -> auto-export
  * video run the user stopped (flag unset)     -> NO export (partial rally)
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

if importlib.util.find_spec("PyQt6") is None:
    raise unittest.SkipTest("PyQt6 is not installed in this test environment.")

from apps.pyqt6.controllers.analysis_controller_runtime import MainController


def _record() -> dict:
    return {
        "rally_id": "match.mp4",
        "rally_name": "match.mp4",
        "summary": {"rally_name": "match.mp4", "rally_hit_count": 12},
        "details": {"hits": [], "players": {}},
    }


class _Log:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def append_log(self, message: str) -> None:
        self.lines.append(str(message))


class _AutoReportDecisionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="wfb_auto_report_"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.export_calls: list[dict] = []
        self.log = _Log()

        controller = MainController.__new__(MainController)
        controller.view = self.log
        controller._report_export_dir = lambda: self.tmp
        controller._report_video_name = lambda record: "match"
        controller._start_report_generation = (
            lambda record, *, trigger, target=None: self.export_calls.append(
                {"trigger": trigger, "target": target}
            )
        )
        self.controller = controller

    def _arm(self, *, record, flag: bool) -> None:
        self.controller._auto_report_after_stop = flag
        self.controller._current_rally_record = record
        self.controller._live_rally_record = record

    # --- the new behaviour -------------------------------------------------

    def test_natural_completion_exports(self) -> None:
        """The fix: reaching the end of the footage must produce a report."""
        self._arm(record=_record(), flag=False)
        self.controller._maybe_auto_export_report(False)
        self.assertEqual(len(self.export_calls), 1)
        self.assertEqual(self.export_calls[0]["trigger"], "completed")

    def test_camera_stop_still_exports(self) -> None:
        """Pre-existing behaviour must not regress."""
        self._arm(record=_record(), flag=True)
        self.controller._maybe_auto_export_report(True)
        self.assertEqual(len(self.export_calls), 1)
        self.assertEqual(self.export_calls[0]["trigger"], "stopped")

    def test_flag_is_cleared_after_export(self) -> None:
        self._arm(record=_record(), flag=True)
        self.controller._maybe_auto_export_report(True)
        self.assertFalse(self.controller._auto_report_after_stop)

    # --- must NOT export ---------------------------------------------------

    def test_video_user_stop_does_not_export(self) -> None:
        """Pausing to inspect a partial rally is not a request to save it."""
        self._arm(record=_record(), flag=False)
        self.controller._maybe_auto_export_report(True)
        self.assertEqual(self.export_calls, [])

    def test_seek_stop_does_not_export(self) -> None:
        """handle_seek stops the worker with stopped=True and flag False."""
        self._arm(record=_record(), flag=False)
        self.controller._maybe_auto_export_report(True)
        self.assertEqual(self.export_calls, [])

    def test_missing_record_does_not_export_and_explains(self) -> None:
        self._arm(record=None, flag=False)
        self.controller._maybe_auto_export_report(False)
        self.assertEqual(self.export_calls, [])
        self.assertTrue(any("统计快照" in line for line in self.log.lines), self.log.lines)

    def test_export_is_only_attempted_once_per_ending(self) -> None:
        """A second call (e.g. a stray signal) must not double-export."""
        self._arm(record=_record(), flag=True)
        self.controller._maybe_auto_export_report(True)
        # The record is not cleared, but the flag is: a video-style stop after
        # a camera-style stop must be a no-op.
        self.controller._maybe_auto_export_report(True)
        self.assertEqual(len(self.export_calls), 1)

    # --- naming ------------------------------------------------------------

    def test_export_target_is_html_in_the_reports_dir(self) -> None:
        self._arm(record=_record(), flag=False)
        self.controller._maybe_auto_export_report(False)
        target = self.export_calls[0]["target"]
        self.assertIsNotNone(target)
        self.assertEqual(Path(target.path).parent, self.tmp)
        self.assertEqual(Path(target.path).suffix, ".html")

    def test_repeated_runs_do_not_overwrite(self) -> None:
        """CHANG chose 'one new file per analysis, never overwrite'."""
        self._arm(record=_record(), flag=False)
        self.controller._maybe_auto_export_report(False)
        first = Path(self.export_calls[0]["target"].path)
        # Materialise the first file so the next call sees a name collision.
        first.write_text("<html></html>", encoding="utf-8")

        self._arm(record=_record(), flag=False)
        self.controller._maybe_auto_export_report(False)
        second = Path(self.export_calls[1]["target"].path)

        self.assertNotEqual(first, second)
        self.assertTrue(first.is_file())


if __name__ == "__main__":
    unittest.main()
