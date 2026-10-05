"""Report export helpers: naming, formats, save-as targets and export history.

This module owns everything about *writing a generated report to disk*:
file naming, target resolution, sidecar JSON, and a small history index so
the PyQt6 UI can list and reopen previous exports. It deliberately knows
nothing about how the report is generated.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

REPORT_EXPORT_HISTORY_VERSION = "wfbar-report-history.v1"
REPORT_EXPORT_HISTORY_NAME = "report_exports.json"
REPORT_EXPORT_SIDECAR_SUFFIX = ".report.json"
REPORT_EXPORT_PREVIEW_NAME = "index.html"
REPORT_EXPORT_HISTORY_KEEP = 50

_UNSAFE_FILENAME_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')
_WHITESPACE = re.compile(r"\s+")


def sanitize_report_filename(name: str, *, fallback: str = "rally_report") -> str:
    """Turn arbitrary video/report text into a Windows-safe file stem.

    Spaces are collapsed into single underscores so the resulting name is
    safe both for Windows paths and for command-line / URL use.
    """
    text = _WHITESPACE.sub(" ", str(name or "")).strip()
    text = text.replace("﻿", "")
    # Drop a trailing extension so "clip.mp4" does not become "clip.mp4_报告".
    text = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", text)
    text = _UNSAFE_FILENAME_CHARS.sub("_", text)
    text = re.sub(r"[\s_]+", "_", text).strip(" ._")
    if not text:
        return fallback
    if len(text) > 80:
        text = text[:80].rstrip(" ._")
    return text or fallback


def build_report_stem(
    *,
    video_name: str | None = None,
    generated_at: datetime | str | None = None,
    suffix: str | None = None,
) -> str:
    """Build a ``<video>_<timestamp>`` stem so repeated exports never collide."""
    base = sanitize_report_filename(video_name or "rally_report")
    if isinstance(generated_at, datetime):
        stamp = generated_at.strftime("%Y%m%d_%H%M%S")
    elif generated_at:
        stamp = sanitize_report_filename(str(generated_at), fallback="")
        stamp = re.sub(r"[^0-9A-Za-z_-]+", "_", stamp).strip("_")
    else:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tail = sanitize_report_filename(suffix, fallback="") if suffix else ""
    parts = [part for part in (base, stamp, tail) if part]
    return "_".join(parts)


@dataclass(slots=True)
class ReportExportTarget:
    """A resolved on-disk destination for one report export."""

    path: Path
    format: str = "html"
    include_sidecar: bool = True
    include_assets: bool = True

    @property
    def directory(self) -> Path:
        return self.path.parent

    @property
    def sidecar_path(self) -> Path | None:
        if not self.include_sidecar:
            return None
        return self.path.with_name(self.path.stem + REPORT_EXPORT_SIDECAR_SUFFIX)

    def to_dict(self) -> dict[str, Any]:
        sidecar = self.sidecar_path
        return {
            "path": str(self.path),
            "format": self.format,
            "include_sidecar": bool(self.include_sidecar),
            "include_assets": bool(self.include_assets),
            "sidecar_path": str(sidecar) if sidecar else None,
        }


def resolve_export_target(
    directory: Path | str,
    *,
    video_name: str | None = None,
    generated_at: datetime | str | None = None,
    fmt: str = "html",
    stem: str | None = None,
    include_sidecar: bool = True,
    include_assets: bool = True,
) -> ReportExportTarget:
    """Build a collision-free export target inside ``directory``."""
    suffix = str(fmt or "html").lower().lstrip(".")
    report_stem = stem or build_report_stem(video_name=video_name, generated_at=generated_at)
    return ReportExportTarget(
        path=Path(directory) / f"{report_stem}.{suffix}",
        format=suffix,
        include_sidecar=include_sidecar,
        include_assets=include_assets,
    )


def unique_export_target(target: ReportExportTarget) -> ReportExportTarget:
    """Append ``-2``, ``-3``... until the path is free."""
    if not target.path.exists():
        return target
    stem = target.path.stem
    suffix = target.path.suffix
    parent = target.path.parent
    for index in range(2, 1000):
        candidate = parent / f"{stem}-{index}{suffix}"
        if not candidate.exists():
            return ReportExportTarget(
                path=candidate,
                format=target.format,
                include_sidecar=target.include_sidecar,
                include_assets=target.include_assets,
            )
    raise FileExistsError(f"无法为报告找到可用的文件名: {target.path}")


def write_report_sidecar(target: ReportExportTarget, payload: Mapping[str, Any]) -> Path | None:
    """Write the structured report data next to the exported report."""
    sidecar = target.sidecar_path
    if sidecar is None:
        return None
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return sidecar


def prune_export_assets(directory: Path | str) -> int:
    """Drop a stale ``static/`` bundle that no exported report references.

    Only removes the copy that this tool created; the project's own template
    assets live elsewhere and are never touched.
    """
    static_dir = Path(directory) / "static"
    if not static_dir.is_dir():
        return 0
    if any(static_dir.rglob("*.css")) or any(static_dir.rglob("*.js")):
        return 0
    shutil.rmtree(static_dir, ignore_errors=True)
    return 1


@dataclass(slots=True)
class ReportExportHistory:
    """A small append-only index of report exports."""

    path: Path
    entries: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def load(cls, directory: Path | str) -> "ReportExportHistory":
        history_path = Path(directory) / REPORT_EXPORT_HISTORY_NAME
        if not history_path.is_file():
            return cls(path=history_path)
        try:
            decoded = json.loads(history_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return cls(path=history_path)
        entries = decoded.get("entries") if isinstance(decoded, Mapping) else None
        return cls(
            path=history_path,
            entries=[dict(item) for item in entries if isinstance(item, Mapping)]
            if isinstance(entries, list)
            else [],
        )

    def record(
        self,
        target: ReportExportTarget,
        *,
        video_name: str | None = None,
        athlete_name: str = "",
        provider: str = "local",
        trigger: str = "manual",
        summary: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        sidecar = target.sidecar_path
        entry = {
            "exported_at": datetime.now().isoformat(timespec="seconds"),
            "path": str(target.path),
            "format": target.format,
            "sidecar_path": str(sidecar) if sidecar else None,
            "video_name": str(video_name or ""),
            "athlete_name": str(athlete_name or ""),
            "provider": str(provider or "local"),
            "trigger": str(trigger or "manual"),
            "exists": target.path.exists(),
            "summary": dict(summary) if isinstance(summary, Mapping) else {},
        }
        self.entries = [item for item in self.entries if item.get("path") != entry["path"]]
        self.entries.insert(0, entry)
        self.entries = self.entries[:REPORT_EXPORT_HISTORY_KEEP]
        self.save()
        return entry

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "version": REPORT_EXPORT_HISTORY_VERSION,
                    "entries": self.entries,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    def existing_entries(self) -> list[dict[str, Any]]:
        """Return entries whose report file is still on disk, newest first."""
        alive: list[dict[str, Any]] = []
        for entry in self.entries:
            path = Path(str(entry.get("path") or ""))
            if path.is_file():
                alive.append(entry)
        return alive

    def prune_missing(self) -> int:
        """Drop entries whose report file disappeared; return how many were removed."""
        before = len(self.entries)
        self.entries = [
            entry
            for entry in self.entries
            if Path(str(entry.get("path") or "")).is_file()
        ]
        removed = before - len(self.entries)
        if removed:
            self.save()
        return removed


def prune_stale_snapshots(
    directory: Path | str,
    *,
    pattern: str = "report_input_*.json",
    keep: int = 20,
) -> list[Path]:
    """Delete old timestamped input snapshots, keeping the newest ``keep`` files."""
    snapshot_dir = Path(directory)
    if not snapshot_dir.is_dir():
        return []
    snapshots = sorted(snapshot_dir.glob(pattern), key=lambda item: item.stat().st_mtime, reverse=True)
    stale = snapshots[max(0, keep):]
    removed: list[Path] = []
    for path in stale:
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(path)
    return removed


def iter_report_files(directory: Path | str) -> Iterable[Path]:
    """Yield exported report files (excluding sidecars) in a directory."""
    report_dir = Path(directory)
    if not report_dir.is_dir():
        return []
    return [
        path
        for path in sorted(report_dir.glob("*.html"))
        if not path.name.endswith(REPORT_EXPORT_SIDECAR_SUFFIX)
    ]


class ReportImageExportError(RuntimeError):
    """Raised when a PDF/PNG export cannot be produced."""


def image_export_supported() -> bool:
    """Whether this environment can rasterize/print reports.

    ``QWebEnginePage.printToPdf`` and ``QWebEngineView.grab`` both live in
    Qt's GPU-backed render process. Headless / software-rasterizer-only
    environments (CI, RDP sessions, some VMs) abort the process outright, so
    callers should check this first and fall back to HTML export.
    """
    if os.environ.get("WFBAR_DISABLE_IMAGE_EXPORT", "").strip() not in ("", "0", "false"):
        return False
    try:
        _require_web_engine()
    except ReportImageExportError:
        return False
    return True


def export_report_image(
    report_path: Path | str,
    output_path: Path | str,
    *,
    fmt: str = "pdf",
    landscape: bool = True,
    width: int = 1280,
    timeout_ms: int = 40000,
) -> Path:
    """Export a report to PDF or PNG, isolating the render process.

    Rendering happens in a child process because QtWebEngine can abort the
    whole interpreter on a headless host. A crash surfaces as a normal
    :class:`ReportImageExportError` instead of taking the app down.
    """
    suffix = str(fmt or "pdf").lower().lstrip(".")
    if suffix not in ("pdf", "png"):
        raise ReportImageExportError(f"不支持的报告格式: {fmt}")

    if not image_export_supported():
        raise ReportImageExportError(
            "当前环境不支持 PDF/长图导出（缺少 GPU 或 WebEngine 渲染后端），"
            "请改用 HTML 导出，或在本机正常桌面会话中运行 WFBARNet。"
        )

    script = _IMAGE_EXPORT_SCRIPT.read_text(encoding="utf-8")
    with tempfile.TemporaryDirectory(prefix="wfbar_img_") as tmp_dir:
        result_path = Path(tmp_dir) / "result.txt"
        argv = [
            sys.executable,
            "-c",
            script,
            str(Path(report_path).resolve()),
            str(Path(output_path).resolve()),
            suffix,
            "1" if landscape else "0",
            str(int(width)),
            str(int(timeout_ms)),
            str(result_path),
        ]
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        completed = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(30, timeout_ms // 1000 + 30),
            env=env,
        )
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            hint = ""
            if completed.returncode in (-1073740791, 3221226505, 139, 134):
                # 0xC0000409 / SIGSEGV / SIGABRT: QtWebEngine's GPU render
                # process died, which is environmental rather than a code bug.
                hint = "（QtWebEngine 渲染进程崩溃，通常是当前会话缺少可用的图形加速后端）"
            raise ReportImageExportError(
                f"{suffix.upper()} 导出进程异常退出 (code {completed.returncode}){hint}："
                f"{detail[:300] or '无详细输出'}"
            )
        if not Path(output_path).is_file():
            raise ReportImageExportError(f"{suffix.upper()} 导出未生成文件。")
        if suffix == "png":
            _reject_blank_png(Path(output_path))
    return Path(output_path)


def _reject_blank_png(path: Path) -> None:
    """Fail loudly when a PNG capture came out blank.

    On hosts without a usable GPU backend QtWebEngine composites to an empty
    surface, so ``grab()`` silently yields an all-white image. Writing that as
    a "successful" export would hand the user a useless file.
    """
    try:
        from PIL import Image
    except ImportError:  # pragma: no cover - Pillow is a hard dependency
        return
    try:
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            # Sampling is enough to tell "blank page" from "rendered report".
            extrema = rgb.resize((64, 64)).getextrema()
    except (OSError, ValueError) as exc:
        raise ReportImageExportError(f"PNG 校验失败：{exc}") from exc
    if all(low == high for low, high in extrema):
        raise ReportImageExportError(
            "PNG 导出结果是空白页面（当前环境缺少可用的图形渲染后端）。"
            "请改用 HTML 导出，或在本机正常桌面会话中运行 WFBARNet。"
        )


_IMAGE_EXPORT_SCRIPT = Path(__file__).with_name("_report_image_worker.py")


def _require_web_engine():
    """Import the WebEngine classes lazily.

    ``QtWebEngineWidgets`` must be imported *before* any QCoreApplication is
    constructed, so a late import fails with a misleading "must be imported
    or Qt.AA_ShareOpenGLContexts must be set" error. Detect that case and say
    so plainly.
    """
    from PyQt6.QtCore import QEventLoop, QTimer

    try:
        from PyQt6.QtWebEngineCore import QWebEngineSettings
        from PyQt6.QtWebEngineWidgets import QWebEngineView
    except ImportError as exc:  # pragma: no cover - depends on local install
        if "AA_ShareOpenGLContexts" in str(exc) or "must be imported" in str(exc):
            raise ReportImageExportError(
                "PDF/长图导出必须在 QApplication 创建之前导入 QtWebEngineWidgets；"
                "请通过 WFBARNet 主程序导出，而不是在已启动的 Qt 事件循环中调用。"
            ) from exc
        raise ReportImageExportError(
            "导出 PDF/长图需要 PyQt6-WebEngine，请用完整环境运行 WFBARNet。"
        ) from exc
    return QEventLoop, QTimer, QWebEngineSettings, QWebEngineView


def summarize_report_data(report_data: Mapping[str, Any]) -> dict[str, Any]:
    """Extract a compact, human-readable summary for the export history."""
    summary = report_data.get("summary")
    summary_map: Mapping[str, Any] = summary if isinstance(summary, Mapping) else {}
    meta = report_data.get("meta")
    meta_map: Mapping[str, Any] = meta if isinstance(meta, Mapping) else {}
    return {
        "level_label": str(summary_map.get("level_label") or ""),
        "overall_score": summary_map.get("overall_score"),
        "athlete_name": str(meta_map.get("athlete_name") or ""),
        "video_name": str(meta_map.get("video_name") or ""),
        "generated_at": str(meta_map.get("generated_at") or ""),
    }

