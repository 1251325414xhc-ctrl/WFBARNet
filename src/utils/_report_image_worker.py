"""Child-process worker that renders one report to PDF or PNG.

Run as a standalone script so a QtWebEngine abort cannot take the WFBARNet
process down with it. Invoked by :func:`src.utils.report_export.export_report_image`.

Usage:
    python _report_image_worker.py <html> <output> <pdf|png> <landscape> <width> <timeout_ms> <result_file>
"""

from __future__ import annotations

import sys
from pathlib import Path


def _log(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def main(argv: list[str]) -> int:
    if len(argv) < 8:
        _log("bad argv")
        return 2
    source = Path(argv[1])
    destination = Path(argv[2])
    fmt = argv[3].strip().lower()
    landscape = argv[4] == "1"
    width = int(argv[5])
    timeout_ms = int(argv[6])
    result_path = Path(argv[7])

    # QtWebEngineWidgets MUST be imported before any QCoreApplication exists.
    from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    from PyQt6.QtCore import QEventLoop, QTimer, QUrl
    from PyQt6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    destination.parent.mkdir(parents=True, exist_ok=True)

    if fmt == "pdf":
        from PyQt6.QtCore import QMarginsF
        from PyQt6.QtGui import QPageLayout, QPageSize

        view = QWebEngineView()
        page = view.page()
        state = {"ok": False, "size": 0}

        def _on_pdf(data: bytes) -> None:
            try:
                payload = bytes(data or b"")
                destination.write_bytes(payload)
                state["ok"] = True
                state["size"] = len(payload)
            except OSError as exc:
                _log(f"write failed: {exc}")

        def _on_load(_ok: bool) -> None:
            layout = QPageLayout(
                QPageSize(QPageSize.PageSizeId.A4),
                QPageLayout.Orientation.Landscape if landscape else QPageLayout.Orientation.Portrait,
                QMarginsF(8, 8, 8, 8),
                QPageLayout.Unit.Millimeter,
            )
            page.printToPdf(str(destination), layout, callback=_on_pdf)

        page.loadFinished.connect(_on_load)
        page.load(QUrl.fromLocalFile(str(source.resolve())))
    else:
        view = QWebEngineView()
        view.resize(int(width), 900)
        state = {"ok": False, "size": 0}
        grabbed = {"done": False}

        def _on_height(value: object) -> None:
            try:
                doc_height = int(float(value))
            except (TypeError, ValueError):
                doc_height = 0
            doc_height = max(1, min(24000, doc_height or 900))
            view.resize(int(width), doc_height)
            # Give the compositor time to lay out and paint at the new height.
            QTimer.singleShot(700, _grab)

        def _grab() -> None:
            try:
                pixmap = view.grab()
                if pixmap.isNull() or pixmap.width() < 8 or pixmap.height() < 8:
                    _log("grab returned an empty pixmap")
                    return
                if not pixmap.save(str(destination), "PNG"):
                    _log("pixmap.save failed")
                    return
                state["ok"] = destination.is_file()
                state["size"] = destination.stat().st_size if destination.is_file() else 0
            except (RuntimeError, OSError) as exc:
                _log(f"grab failed: {exc}")
            finally:
                grabbed["done"] = True

        def _on_load(_ok: bool) -> None:
            view.page().runJavaScript("document.documentElement.scrollHeight", _on_height)

        view.loadFinished.connect(_on_load)
        view.load(QUrl.fromLocalFile(str(source.resolve())))

    loop = QEventLoop()
    poll = QTimer()
    poll.setInterval(50)
    poll.timeout.connect(lambda: (state["ok"] and loop.quit()))
    poll.start()
    watchdog = QTimer()
    watchdog.setSingleShot(True)
    watchdog.timeout.connect(loop.quit)
    watchdog.start(int(timeout_ms))
    loop.exec()
    poll.stop()
    watchdog.stop()

    if not state["ok"]:
        _log("render timed out or failed")
        return 1
    result_path.write_text(str(state["size"]), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
