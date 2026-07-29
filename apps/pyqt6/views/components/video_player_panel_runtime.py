# -*- coding: utf-8 -*-
from __future__ import annotations

from math import isfinite
from time import monotonic
from typing import Any

from PyQt6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt, QUrl, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer, QVideoSink
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
    QStyle,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


class CourtLineOverlayWidget(QWidget):
    """Transparent cached overlay for court lines in label coordinates."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAutoFillBackground(False)

        self._court: dict[str, Any] | None = None
        self._court_key: tuple[Any, ...] | None = None
        self._source_size = QSize()
        self._display_rect = QRect()
        self._cached_pixmap: QPixmap | None = None
        self._dirty = True
        self._mask_alpha = 0.0
        self._line_thickness = 2.0
        self._selected_corner_index: int | None = None

    def clear(self) -> None:
        if self._court is None and self._cached_pixmap is None:
            return
        self._court = None
        self._court_key = None
        self._cached_pixmap = None
        self._selected_corner_index = None
        self._dirty = True
        self.update()

    def set_court(self, court: object | None) -> None:
        court_dict = self._normalize_court(court)
        court_key = self._make_court_key(court_dict)
        if court_key == self._court_key:
            return
        self._court = court_dict
        self._court_key = court_key
        if len(self.corners()) != 4:
            self._selected_corner_index = None
        self._dirty = True
        self.update()

    def set_selected_corner(self, index: int | None) -> None:
        normalized = int(index) if index is not None and 0 <= int(index) < 4 else None
        if normalized == self._selected_corner_index:
            return
        self._selected_corner_index = normalized
        self._dirty = True
        self.update()

    def set_video_geometry(self, source_size: QSize, display_rect: QRect) -> None:
        if source_size == self._source_size and display_rect == self._display_rect:
            return
        self._source_size = QSize(source_size)
        self._display_rect = QRect(display_rect)
        self._dirty = True
        self.update()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._dirty = True

    def paintEvent(self, event) -> None:
        if self._dirty:
            self._rebuild_cache()
        if self._cached_pixmap is None or self._cached_pixmap.isNull():
            return
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self._cached_pixmap)

    def _rebuild_cache(self) -> None:
        self._dirty = False
        if self.width() <= 0 or self.height() <= 0:
            self._cached_pixmap = None
            return

        pixmap = QPixmap(self.size())
        pixmap.fill(Qt.GlobalColor.transparent)
        self._cached_pixmap = pixmap

        court = self._court
        projected_lines = court.get("projected_lines") if isinstance(court, dict) else None
        provisional = bool(isinstance(court, dict) and court.get("provisional"))
        if (
            not isinstance(court, dict)
            or not (court.get("valid") or provisional)
            or not isinstance(projected_lines, dict)
            or self._source_size.width() <= 0
            or self._source_size.height() <= 0
            or self._display_rect.width() <= 0
            or self._display_rect.height() <= 0
        ):
            return

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setClipRect(self._display_rect)
        painter.translate(self._display_rect.topLeft())
        scale_x = self._display_rect.width() / max(1, self._source_size.width())
        scale_y = self._display_rect.height() / max(1, self._source_size.height())
        painter.scale(scale_x, scale_y)

        outer = projected_lines.get("doubles_outer")
        outer_polygon = self._polygon_from_points(outer)
        if outer_polygon.count() >= 3 and self._mask_alpha > 0.0 and not provisional:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(90, 210, 35, int(max(0.0, min(self._mask_alpha, 1.0)) * 255)))
            painter.drawPolygon(outer_polygon)

        line_color = QColor(238, 220, 151, 225) if provisional else QColor(218, 244, 157, 225)
        line_pen = QPen(line_color, self._line_thickness)
        line_pen.setCapStyle(Qt.PenCapStyle.SquareCap)
        line_pen.setJoinStyle(Qt.PenJoinStyle.MiterJoin)
        if provisional:
            line_pen.setStyle(Qt.PenStyle.DashLine)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(line_pen)
        for name, points in projected_lines.items():
            polygon = self._polygon_from_points(points)
            if polygon.count() < 2:
                continue
            is_closed = name == "doubles_outer"
            self._draw_line_shape(painter, polygon, is_closed)
        self._draw_corner_handles(painter, court)
        painter.end()

    def corners(self) -> list[tuple[float, float]]:
        court = self._court
        if not isinstance(court, dict) or not (
            court.get("valid") or court.get("provisional")
        ):
            return []
        return self._points_from_object(court.get("corners"))

    def _normalize_court(self, court: object | None) -> dict[str, Any] | None:
        if court is None:
            return None
        if isinstance(court, dict):
            return court
        to_dict = getattr(court, "to_dict", None)
        if callable(to_dict):
            value = to_dict()
            return value if isinstance(value, dict) else None
        return None

    def _make_court_key(self, court: dict[str, Any] | None) -> tuple[Any, ...] | None:
        if not isinstance(court, dict) or not (
            court.get("valid") or court.get("provisional")
        ):
            return None
        projected_lines = court.get("projected_lines")
        if not isinstance(projected_lines, dict):
            return None
        line_items = []
        for name in sorted(projected_lines):
            points = []
            for point in projected_lines.get(name) or []:
                if not isinstance(point, (list, tuple)) or len(point) < 2:
                    continue
                try:
                    x = float(point[0])
                    y = float(point[1])
                except (TypeError, ValueError):
                    continue
                if isfinite(x) and isfinite(y):
                    points.append((round(x, 2), round(y, 2)))
            line_items.append((str(name), tuple(points)))
        return (
            tuple(court.get("source_size") or ()),
            bool(court.get("valid")),
            bool(court.get("provisional")),
            tuple(line_items),
        )

    def _polygon_from_points(self, points: object) -> QPolygonF:
        polygon = QPolygonF()
        for x, y in self._points_from_object(points):
            polygon.append(QPointF(x, y))
        return polygon

    def _points_from_object(self, points: object) -> list[tuple[float, float]]:
        result: list[tuple[float, float]] = []
        if not isinstance(points, (list, tuple)):
            return result
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            try:
                x = float(point[0])
                y = float(point[1])
            except (TypeError, ValueError):
                continue
            if isfinite(x) and isfinite(y):
                result.append((x, y))
        return result

    def _draw_line_shape(self, painter: QPainter, polygon: QPolygonF, closed: bool) -> None:
        if closed:
            painter.drawPolygon(polygon)
        else:
            painter.drawPolyline(polygon)

    def _draw_corner_handles(self, painter: QPainter, court: dict[str, Any]) -> None:
        corners = self._points_from_object(court.get("corners"))
        if len(corners) != 4:
            return

        painter.resetTransform()
        painter.setClipRect(self._display_rect)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        scale_x = self._display_rect.width() / max(1, self._source_size.width())
        scale_y = self._display_rect.height() / max(1, self._source_size.height())
        provisional = bool(court.get("provisional"))
        for index, (x, y) in enumerate(corners):
            label_x = self._display_rect.left() + x * scale_x
            label_y = self._display_rect.top() + y * scale_y
            selected = index == self._selected_corner_index
            side = 13.0 if selected else 10.0
            rect = QRectF(label_x - side / 2.0, label_y - side / 2.0, side, side)
            if provisional:
                border = QColor(145, 115, 48, 245)
                fill = QColor(246, 226, 164, 238)
            else:
                border = QColor(73, 103, 37, 245) if selected else QColor(111, 139, 65, 235)
                fill = QColor(226, 247, 175, 240)
            painter.setPen(QPen(border, 2.0 if selected else 1.5))
            painter.setBrush(fill)
            painter.drawRect(rect)


class VideoPlayerWidget(QFrame):
    """由外部帧驱动的纯显示视频预览组件。"""

    _AUDIO_SYNC_TOLERANCE_MS = 300
    _AUDIO_SYNC_COOLDOWN_SECONDS = 1.0

    selectRequested = pyqtSignal()
    forceStopRequested = pyqtSignal()
    fullscreenRequested = pyqtSignal()
    framePointClicked = pyqtSignal(float, float)
    courtCornerDragged = pyqtSignal(int, float, float)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("videoPlayerCard")

        self._source_path = ""
        self._current_pixmap: QPixmap | None = None
        self._scaled_pixmap: QPixmap | None = None
        self._scaled_source_key: int | None = None
        self._scaled_label_size = QSize()
        self._point_capture_enabled = False
        self._dragging_court_corner_index: int | None = None
        self._selected_court_corner_index: int | None = None
        self._court_corner_hit_radius_px = 18.0
        self._status_text = ""
        self._status_state = ""
        self._audio_source_path = ""
        self._audio_output: QAudioOutput | None = None
        self._audio_video_sink: QVideoSink | None = None
        self._media_player: QMediaPlayer | None = None
        self._pending_audio_position_ms: int | None = None
        self._last_audio_correction_at = 0.0

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.btn_select_video = QPushButton("选择视频")
        self.btn_select_video.setObjectName("btnSelectVideo")
        self.btn_select_video.setAccessibleName("选择视频文件")

        self.path_edit = QLineEdit()
        self.path_edit.setObjectName("videoPathEdit")
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("视频路径")
        self.path_edit.setClearButtonEnabled(False)
        self.path_edit.setAccessibleName("当前视频文件路径")

        self.btn_force_stop = QPushButton("停止")
        self.btn_force_stop.setObjectName("btnForceStop")
        self.btn_force_stop.setAccessibleName("停止当前分析")

        self.btn_fullscreen = QPushButton("全屏")
        self.btn_fullscreen.setObjectName("btnFullscreen")
        self.btn_fullscreen.setCheckable(True)
        self.btn_fullscreen.setToolTip("放大视频为全屏")
        self.btn_fullscreen.setAccessibleName("全屏")

        self.preview_stack = QStackedWidget()
        self.preview_stack.setObjectName("videoStack")
        self.preview_stack.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

        placeholder_page = QWidget()
        placeholder_layout = QVBoxLayout(placeholder_page)
        placeholder_layout.setContentsMargins(0, 0, 0, 0)

        placeholder_frame = QFrame()
        placeholder_frame.setObjectName("videoPlaceholderFrame")
        placeholder_frame_layout = QVBoxLayout(placeholder_frame)
        placeholder_frame_layout.setContentsMargins(24, 24, 24, 24)
        placeholder_frame_layout.setSpacing(10)

        placeholder_title = QLabel("视频预览")
        placeholder_title.setObjectName("videoPlaceholderTitle")
        placeholder_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        placeholder_hint = QLabel("尚未选择视频")
        placeholder_hint.setObjectName("videoPlaceholderHint")
        placeholder_hint.setWordWrap(True)
        placeholder_hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        placeholder_frame_layout.addStretch(1)
        placeholder_frame_layout.addWidget(placeholder_title)
        placeholder_frame_layout.addWidget(placeholder_hint)
        placeholder_frame_layout.addStretch(1)
        placeholder_layout.addWidget(placeholder_frame)

        self.video_label = QLabel()
        self.video_label.setObjectName("videoLabel")
        self.video_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.video_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        self.video_label.setMinimumSize(320, 240)
        self.video_label.setAccessibleName("视频分析画面")
        self.video_label.setAccessibleDescription("球场标定完成后，可用数字 1 至 4 选择角点并用方向键微调")
        self.video_label.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.video_label.setMouseTracking(True)
        self.video_label.installEventFilter(self)
        self.court_overlay = CourtLineOverlayWidget(self.video_label)
        self.court_overlay.setGeometry(self.video_label.rect())
        self.court_overlay.raise_()

        self.video_badges = QWidget(self.video_label)
        self.video_badges.setObjectName("videoBadges")
        self.video_badges.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        badge_layout = QHBoxLayout(self.video_badges)
        badge_layout.setContentsMargins(0, 0, 0, 0)
        badge_layout.setSpacing(8)
        self.analysis_badge = QLabel("待机")
        self.analysis_badge.setObjectName("analysisBadge")
        self.analysis_badge.setProperty("state", "idle")
        self.fps_badge = QLabel("0.0 FPS", self.video_badges)
        self.fps_badge.setObjectName("runtimeBadge")
        self.fps_badge.setVisible(False)
        self.latency_badge = QLabel("-- ms", self.video_badges)
        self.latency_badge.setObjectName("runtimeBadge")
        self.latency_badge.setVisible(False)
        badge_layout.addWidget(self.analysis_badge)
        self.video_badges.adjustSize()
        self.video_badges.hide()

        self.preview_stack.addWidget(placeholder_page)
        self.preview_stack.addWidget(self.video_label)
        self.preview_stack.setCurrentWidget(placeholder_page)

        outer.addWidget(self.preview_stack, stretch=1)

        self.btn_select_video.clicked.connect(self.selectRequested.emit)
        self.btn_force_stop.clicked.connect(self.forceStopRequested.emit)
        self.btn_fullscreen.clicked.connect(lambda _checked=False: self.fullscreenRequested.emit())

    def eventFilter(self, watched: object, event: object) -> bool:
        if watched is self.video_label:
            event_type = event.type()
            if event_type == QEvent.Type.Resize:
                self._scaled_label_size = QSize()
                self.court_overlay.setGeometry(self.video_label.rect())
                QTimer.singleShot(0, self._render_current_pixmap)
                return False
            if event_type == QEvent.Type.FocusIn:
                if self._selected_court_corner_index is None and self.court_overlay.corners():
                    self._select_court_corner(0)
                else:
                    self.court_overlay.set_selected_corner(self._selected_court_corner_index)
                return False
            if event_type == QEvent.Type.FocusOut:
                self.court_overlay.set_selected_corner(None)
                return False
            if event_type == QEvent.Type.KeyPress and self._handle_court_key_press(event):
                return True
            if event_type == QEvent.Type.MouseButtonPress:
                if getattr(event, "button", lambda: None)() != Qt.MouseButton.LeftButton:
                    return False
                position = event.position() if hasattr(event, "position") else event.pos()
                self.video_label.setFocus(Qt.FocusReason.MouseFocusReason)
                if self._point_capture_enabled:
                    point = self._image_point_from_label_pos(position)
                    if point is None:
                        return False
                    self.framePointClicked.emit(point[0], point[1])
                    return True
                corner_index = self._court_corner_index_at_label_pos(position)
                if corner_index is not None:
                    self._dragging_court_corner_index = corner_index
                    self._select_court_corner(corner_index)
                    self.video_label.setCursor(Qt.CursorShape.ClosedHandCursor)
                    return True
            elif event_type == QEvent.Type.MouseMove:
                position = event.position() if hasattr(event, "position") else event.pos()
                if self._dragging_court_corner_index is not None:
                    point = self._image_point_from_label_pos(position)
                    if point is None:
                        return True
                    self.courtCornerDragged.emit(
                        self._dragging_court_corner_index,
                        point[0],
                        point[1],
                    )
                    return True
                self._update_court_corner_cursor(position)
            elif event_type == QEvent.Type.MouseButtonRelease:
                if self._dragging_court_corner_index is not None:
                    position = event.position() if hasattr(event, "position") else event.pos()
                    point = self._image_point_from_label_pos(position)
                    if point is not None:
                        self.courtCornerDragged.emit(
                            self._dragging_court_corner_index,
                            point[0],
                            point[1],
                        )
                    self._dragging_court_corner_index = None
                    self._update_court_corner_cursor(position)
                    return True
        return super().eventFilter(watched, event)

    def _select_court_corner(self, index: int) -> None:
        corners = self.court_overlay.corners()
        if not 0 <= int(index) < len(corners):
            return
        self._selected_court_corner_index = int(index)
        self.court_overlay.set_selected_corner(self._selected_court_corner_index)
        corner_names = ("左上", "右上", "右下", "左下")
        self.video_label.setAccessibleDescription(
            f"已选择{corner_names[self._selected_court_corner_index]}角点；使用方向键微调，按住 Shift 每次移动 10 像素"
        )

    def _handle_court_key_press(self, event: object) -> bool:
        corners = self.court_overlay.corners()
        if len(corners) != 4:
            return False
        number_keys = (
            Qt.Key.Key_1,
            Qt.Key.Key_2,
            Qt.Key.Key_3,
            Qt.Key.Key_4,
        )
        key = event.key()
        if key in number_keys:
            self._select_court_corner(number_keys.index(key))
            event.accept()
            return True
        offsets = {
            Qt.Key.Key_Left: (-1.0, 0.0),
            Qt.Key.Key_Right: (1.0, 0.0),
            Qt.Key.Key_Up: (0.0, -1.0),
            Qt.Key.Key_Down: (0.0, 1.0),
        }
        if key not in offsets:
            return False
        if self._selected_court_corner_index is None:
            self._select_court_corner(0)
        index = self._selected_court_corner_index
        if index is None:
            return False
        step = 10.0 if event.modifiers() & Qt.KeyboardModifier.ShiftModifier else 1.0
        dx, dy = offsets[key]
        x, y = corners[index]
        source_size = self.source_size()
        max_x = float(source_size[0] - 1) if source_size is not None else float("inf")
        max_y = float(source_size[1] - 1) if source_size is not None else float("inf")
        next_x = max(0.0, min(max_x, x + dx * step))
        next_y = max(0.0, min(max_y, y + dy * step))
        self.courtCornerDragged.emit(index, next_x, next_y)
        self.video_label.setAccessibleDescription(
            f"角点 {index + 1} 已移动到 {next_x:.0f}, {next_y:.0f}"
        )
        event.accept()
        return True

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render_current_pixmap()

    def _render_current_pixmap(self) -> None:
        if self._current_pixmap is None:
            self.court_overlay.setGeometry(self.video_label.rect())
            self.court_overlay.set_video_geometry(QSize(), QRect())
            self.video_badges.hide()
            return
        label_size = self.video_label.size()
        if label_size.width() <= 0 or label_size.height() <= 0:
            label_size = self.preview_stack.size()
        if label_size.width() <= 0 or label_size.height() <= 0:
            QTimer.singleShot(10, self._render_current_pixmap)
            return
        source_key = self._current_pixmap.cacheKey()
        if (
            self._scaled_pixmap is not None
            and not self._scaled_pixmap.isNull()
            and self._scaled_source_key == source_key
            and self._scaled_label_size == label_size
        ):
            scaled = self._scaled_pixmap
        else:
            scaled = self._current_pixmap.scaled(
                label_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.FastTransformation,
            )
            self._scaled_pixmap = scaled
            self._scaled_source_key = source_key
            self._scaled_label_size = QSize(label_size)
        self.video_label.setPixmap(scaled)
        self.court_overlay.setGeometry(self.video_label.rect())
        display_rect = QRect(
            (self.video_label.width() - scaled.width()) // 2,
            (self.video_label.height() - scaled.height()) // 2,
            scaled.width(),
            scaled.height(),
        )
        self.court_overlay.set_video_geometry(self._current_pixmap.size(), display_rect)
        self.court_overlay.raise_()
        self._position_video_badges(display_rect)

    def _position_video_badges(self, display_rect: QRect) -> None:
        self.video_badges.adjustSize()
        left = max(12, display_rect.left() + 16)
        top = max(12, display_rect.top() + 16)
        self.video_badges.move(left, top)
        self.video_badges.show()
        self.video_badges.raise_()

    def _set_status(self, text: str, state: str) -> None:
        if text == self._status_text and state == self._status_state:
            return
        state_changed = state != self._status_state
        self._status_text = text
        self._status_state = state
        self.video_label.setProperty("state", state)
        self.video_label.setToolTip(text)
        if state_changed:
            self.style().unpolish(self.video_label)
            self.style().polish(self.video_label)
            self.video_label.update()

    def display_image(self, image: QImage, court: object | None = None) -> None:
        if image.isNull():
            return
        self._current_pixmap = QPixmap.fromImage(image)
        self._scaled_pixmap = None
        self._scaled_source_key = None
        self._scaled_label_size = QSize()
        if self.preview_stack.currentWidget() is not self.video_label:
            self.preview_stack.setCurrentWidget(self.video_label)
        self.set_court_overlay(court)
        self._render_current_pixmap()
        self._set_status("帧已就绪", "loaded")

    def set_point_capture_enabled(self, enabled: bool) -> None:
        self._point_capture_enabled = bool(enabled)
        if enabled:
            self._selected_court_corner_index = None
            self.court_overlay.set_selected_corner(None)
            self.video_label.setCursor(Qt.CursorShape.CrossCursor)
            self.video_label.setFocus(Qt.FocusReason.OtherFocusReason)
        elif self._dragging_court_corner_index is None:
            self.video_label.setCursor(Qt.CursorShape.ArrowCursor)

    def source_size(self) -> tuple[int, int] | None:
        if self._current_pixmap is None:
            return None
        return self._current_pixmap.width(), self._current_pixmap.height()

    def set_court_overlay(self, court: object | None) -> None:
        self.court_overlay.set_court(court)
        if not self.court_overlay.corners() and not self._point_capture_enabled:
            self._dragging_court_corner_index = None
            self._selected_court_corner_index = None
            self.court_overlay.set_selected_corner(None)
            self.video_label.setCursor(Qt.CursorShape.ArrowCursor)

    def _image_point_from_label_pos(self, position: object) -> tuple[float, float] | None:
        if self._current_pixmap is None or self._scaled_pixmap is None or self._scaled_pixmap.isNull():
            return None
        x = float(position.x())
        y = float(position.y())
        display_rect = QRect(
            (self.video_label.width() - self._scaled_pixmap.width()) // 2,
            (self.video_label.height() - self._scaled_pixmap.height()) // 2,
            self._scaled_pixmap.width(),
            self._scaled_pixmap.height(),
        )
        if not display_rect.contains(int(round(x)), int(round(y))):
            return None
        source_x = (x - display_rect.left()) * self._current_pixmap.width() / max(1, display_rect.width())
        source_y = (y - display_rect.top()) * self._current_pixmap.height() / max(1, display_rect.height())
        return (
            max(0.0, min(float(self._current_pixmap.width() - 1), source_x)),
            max(0.0, min(float(self._current_pixmap.height() - 1), source_y)),
        )

    def _label_point_from_image_point(self, x: float, y: float) -> QPointF | None:
        if self._current_pixmap is None or self._scaled_pixmap is None or self._scaled_pixmap.isNull():
            return None
        display_rect = QRect(
            (self.video_label.width() - self._scaled_pixmap.width()) // 2,
            (self.video_label.height() - self._scaled_pixmap.height()) // 2,
            self._scaled_pixmap.width(),
            self._scaled_pixmap.height(),
        )
        scale_x = display_rect.width() / max(1, self._current_pixmap.width())
        scale_y = display_rect.height() / max(1, self._current_pixmap.height())
        return QPointF(display_rect.left() + float(x) * scale_x, display_rect.top() + float(y) * scale_y)

    def _court_corner_index_at_label_pos(self, position: object) -> int | None:
        if self._point_capture_enabled:
            return None
        x = float(position.x())
        y = float(position.y())
        for index, (corner_x, corner_y) in enumerate(self.court_overlay.corners()):
            label_point = self._label_point_from_image_point(corner_x, corner_y)
            if label_point is None:
                continue
            dx = x - label_point.x()
            dy = y - label_point.y()
            if (dx * dx + dy * dy) ** 0.5 <= self._court_corner_hit_radius_px:
                return index
        return None

    def _update_court_corner_cursor(self, position: object) -> None:
        if self._point_capture_enabled:
            self.video_label.setCursor(Qt.CursorShape.CrossCursor)
            return
        if self._court_corner_index_at_label_pos(position) is not None:
            self.video_label.setCursor(Qt.CursorShape.OpenHandCursor)
        else:
            self.video_label.setCursor(Qt.CursorShape.ArrowCursor)

    def set_video_path(self, path: str) -> None:
        self._source_path = path
        self._set_audio_source_path(path)
        self.path_edit.setText(path)
        self.path_edit.setToolTip(path)

    def set_live_source(self, label: str) -> None:
        self._source_path = label
        self._set_audio_source_path("")
        self.path_edit.setText(label)
        self.path_edit.setToolTip(label)

    def clear_video(self) -> None:
        self._source_path = ""
        self._set_audio_source_path("")
        self._current_pixmap = None
        self._scaled_pixmap = None
        self._scaled_source_key = None
        self._scaled_label_size = QSize()
        self._dragging_court_corner_index = None
        self._selected_court_corner_index = None
        self.path_edit.clear()
        self.path_edit.setToolTip("")
        self.video_label.clear()
        self.video_label.setCursor(Qt.CursorShape.ArrowCursor)
        self.court_overlay.clear()
        self.court_overlay.set_video_geometry(QSize(), QRect())
        self.video_badges.hide()
        self._set_status("未加载视频", "idle")
        self.preview_stack.setCurrentWidget(self.preview_stack.widget(0))

    def play(self, *, start_ms: int | None = None) -> None:
        if self._audio_source_path:
            player = self._ensure_audio_player()
            source = QUrl.fromLocalFile(self._audio_source_path)
            if player.source() != source:
                player.setSource(source)
            if start_ms is not None:
                self._pending_audio_position_ms = max(0, int(start_ms))
                player.setPosition(self._pending_audio_position_ms)
            self._last_audio_correction_at = 0.0
            player.play()
        self._set_status("播放中", "playing")

    def pause(self) -> None:
        if self._media_player is not None:
            self._media_player.pause()
        if self._source_path:
            self._set_status("已暂停", "loaded")

    def stop(self) -> None:
        self.stop_audio()
        if self._source_path:
            self._set_status("已停止", "stopped")

    def stop_audio(self) -> None:
        if self._media_player is not None:
            # Qt's FFmpeg backend can block in stop() while an active seek is settling.
            self._media_player.pause()
        self._pending_audio_position_ms = None
        self._last_audio_correction_at = 0.0

    def release_audio(self) -> None:
        self._dispose_audio_player()

    def sync_audio_position(self, position_ms: int) -> None:
        player = self._media_player
        if (
            player is None
            or not self._audio_source_path
            or player.playbackState() != QMediaPlayer.PlaybackState.PlayingState
        ):
            return
        target_ms = max(0, int(position_ms))
        if self._pending_audio_position_ms is not None:
            self._pending_audio_position_ms = target_ms
        if abs(player.position() - target_ms) < self._AUDIO_SYNC_TOLERANCE_MS:
            return
        now = monotonic()
        if now - self._last_audio_correction_at < self._AUDIO_SYNC_COOLDOWN_SECONDS:
            return
        player.setPosition(target_ms)
        self._last_audio_correction_at = now

    def _ensure_audio_player(self) -> QMediaPlayer:
        if self._media_player is not None:
            return self._media_player
        self._audio_output = QAudioOutput(self)
        self._audio_output.setMuted(False)
        self._audio_output.setVolume(1.0)
        # Qt's FFmpeg backend needs a video sink to advance mixed audio/video media.
        self._audio_video_sink = QVideoSink(self)
        self._media_player = QMediaPlayer(self)
        self._media_player.setAudioOutput(self._audio_output)
        self._media_player.setVideoSink(self._audio_video_sink)
        self._media_player.mediaStatusChanged.connect(self._on_audio_media_status_changed)
        return self._media_player

    def _on_audio_media_status_changed(self, status: QMediaPlayer.MediaStatus) -> None:
        if (
            self._media_player is None
            or self._pending_audio_position_ms is None
            or status
            not in (
                QMediaPlayer.MediaStatus.LoadedMedia,
                QMediaPlayer.MediaStatus.BufferingMedia,
                QMediaPlayer.MediaStatus.BufferedMedia,
            )
        ):
            return
        target_ms = self._pending_audio_position_ms
        self._pending_audio_position_ms = None
        self._media_player.setPosition(target_ms)

    def _set_audio_source_path(self, path: str) -> None:
        normalized_path = str(path).strip()
        if normalized_path == self._audio_source_path:
            return
        self._dispose_audio_player()
        self._audio_source_path = normalized_path

    def _dispose_audio_player(self) -> None:
        self.stop_audio()
        for media_object in (
            self._media_player,
            self._audio_output,
            self._audio_video_sink,
        ):
            delete_later = getattr(media_object, "deleteLater", None)
            if callable(delete_later):
                delete_later()
        self._media_player = None
        self._audio_output = None
        self._audio_video_sink = None

    def set_video_state(self, state: str) -> None:
        mapping = {
            "idle": "未加载视频",
            "loaded": "就绪",
            "playing": "播放中",
            "stopped": "已停止",
            "error": "视频加载失败",
        }
        self._set_status(mapping.get(state, state), state)

    def set_analysis_status(self, text: str, state: str = "idle") -> None:
        display_text = str(text).strip() or "待机"
        self.analysis_badge.setText(display_text)
        self.analysis_badge.setProperty("state", state)
        self.analysis_badge.style().unpolish(self.analysis_badge)
        self.analysis_badge.style().polish(self.analysis_badge)
        self.analysis_badge.update()

    def set_runtime_badges(self, display_fps: float, latency_ms: float) -> None:
        fps = max(0.0, float(display_fps))
        latency = max(0.0, float(latency_ms))
        self.fps_badge.setText(f"{fps:.1f} FPS")
        self.latency_badge.setText(f"{latency:.0f} ms" if latency > 0.0 else "-- ms")

    def set_fullscreen_mode(self, enabled: bool) -> None:
        self.btn_fullscreen.blockSignals(True)
        self.btn_fullscreen.setChecked(bool(enabled))
        self.btn_fullscreen.blockSignals(False)
        if enabled:
            self.btn_fullscreen.setText("")
            self.btn_fullscreen.setToolTip("退出全屏（Esc）")
            self.btn_fullscreen.setAccessibleName("退出全屏")
        else:
            self.btn_fullscreen.setText("")
            self.btn_fullscreen.setToolTip("放大视频为全屏")
            self.btn_fullscreen.setAccessibleName("全屏")

    def current_path(self) -> str:
        return self._source_path


class EventTimelineRail(QWidget):
    """Compact event rail synchronized with the video seek position."""

    seekRequested = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("eventTimelineRail")
        self.setFixedHeight(88)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("动作事件时间轴")
        self.setAccessibleDescription("使用左右方向键跳转到相邻事件")
        self._duration_ms = 0
        self._position_ms = 0
        self._events: list[tuple[int, str, float]] = []

    def set_duration(self, duration_ms: int) -> None:
        self._duration_ms = max(0, int(duration_ms))
        self.update()

    def set_position(self, position_ms: int) -> None:
        self._position_ms = max(0, int(position_ms))
        self.update()

    def add_event(self, timestamp_ms: int, label: str, confidence: float = 0.0) -> None:
        event = (max(0, int(timestamp_ms)), str(label).strip() or "事件", float(confidence))
        if any(existing[:2] == event[:2] for existing in self._events):
            return
        self._events.append(event)
        self._events = sorted(self._events, key=lambda item: item[0])[-48:]
        summary = "，".join(f"{item[1]} {item[0] / 1000:.1f} 秒" for item in self._events)
        self.setAccessibleDescription(summary)
        self.update()

    def clear_events(self) -> None:
        self._events.clear()
        self.setAccessibleDescription("暂无动作事件")
        self.update()

    def mousePressEvent(self, event) -> None:
        if event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        self.setFocus(Qt.FocusReason.MouseFocusReason)
        duration = self._effective_duration()
        bounds = QRectF(self.rect()).adjusted(22, 0, -22, 0)
        ratio = (float(event.position().x()) - bounds.left()) / max(1.0, bounds.width())
        timestamp_ms = round(max(0.0, min(1.0, ratio)) * duration)
        self.seekRequested.emit(timestamp_ms)
        event.accept()

    def keyPressEvent(self, event) -> None:
        if not self._events:
            super().keyPressEvent(event)
            return
        timestamps = [item[0] for item in self._events]
        key = event.key()
        if key == Qt.Key.Key_Left:
            candidates = [timestamp for timestamp in timestamps if timestamp < self._position_ms]
            target = candidates[-1] if candidates else timestamps[0]
        elif key == Qt.Key.Key_Right:
            target = next(
                (timestamp for timestamp in timestamps if timestamp > self._position_ms),
                timestamps[-1],
            )
        elif key == Qt.Key.Key_Home:
            target = timestamps[0]
        elif key == Qt.Key.Key_End:
            target = timestamps[-1]
        else:
            super().keyPressEvent(event)
            return
        self._position_ms = target
        selected = next(item for item in self._events if item[0] == target)
        self.setAccessibleDescription(
            f"已选择 {selected[1]}，时间 {self._format_time(target)}"
        )
        self.seekRequested.emit(target)
        self.update()
        event.accept()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        bounds = QRectF(self.rect()).adjusted(22, 5, -22, -5)
        duration = self._effective_duration()
        baseline_y = bounds.top() + 47.0

        painter.setPen(QPen(QColor("#98A1AD"), 1.0))
        painter.drawLine(QPointF(bounds.left(), baseline_y), QPointF(bounds.right(), baseline_y))

        tick_font = painter.font()
        tick_font.setPointSize(8)
        painter.setFont(tick_font)
        for index in range(7):
            ratio = index / 6.0
            x = bounds.left() + ratio * bounds.width()
            painter.setPen(QPen(QColor("#6B7280"), 1.0))
            painter.drawLine(QPointF(x, baseline_y - 5.0), QPointF(x, baseline_y + 5.0))
            label = self._format_time(round(duration * ratio))
            label_rect = QRectF(x - 28.0, baseline_y + 8.0, 56.0, 18.0)
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignHCenter, label)

        for timestamp_ms, label, _confidence in self._events:
            ratio = max(0.0, min(1.0, timestamp_ms / max(1, duration)))
            x = bounds.left() + ratio * bounds.width()
            color = self._event_color(label)
            painter.setPen(QPen(color, 1.4, Qt.PenStyle.DashLine))
            painter.drawLine(QPointF(x, bounds.top() + 16.0), QPointF(x, baseline_y))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)
            flag = QPolygonF(
                (
                    QPointF(x, bounds.top() + 14.0),
                    QPointF(x + 13.0, bounds.top() + 19.0),
                    QPointF(x, bounds.top() + 24.0),
                )
            )
            painter.drawPolygon(flag)
            painter.setPen(color.darker(145))
            label_rect = QRectF(x + 5.0, bounds.top() - 1.0, 70.0, 17.0)
            painter.drawText(label_rect, Qt.AlignmentFlag.AlignLeft, label[:6])

        current_ratio = max(0.0, min(1.0, self._position_ms / max(1, duration)))
        current_x = bounds.left() + current_ratio * bounds.width()
        painter.setPen(QPen(QColor("#087F45"), 1.5))
        painter.drawLine(QPointF(current_x, baseline_y - 12.0), QPointF(current_x, baseline_y + 7.0))
        painter.setBrush(QColor("#0B8F4D"))
        painter.setPen(QPen(QColor("#FFFFFF"), 2.0))
        painter.drawEllipse(QPointF(current_x, baseline_y), 7.0, 7.0)
        if self.hasFocus():
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QPen(QColor("#0F6CBD"), 2.0))
            painter.drawRoundedRect(QRectF(self.rect()).adjusted(2.0, 2.0, -2.0, -2.0), 4.0, 4.0)

    def _effective_duration(self) -> int:
        event_end = max((event[0] for event in self._events), default=0)
        return max(1000, self._duration_ms, event_end)

    @staticmethod
    def _format_time(ms: int) -> str:
        total_seconds = max(0, int(ms)) // 1000
        minutes, seconds = divmod(total_seconds, 60)
        return f"{minutes:02d}:{seconds:02d}"

    @staticmethod
    def _event_color(label: str) -> QColor:
        text = label.casefold()
        if "失误" in text or "error" in text:
            return QColor("#C92F38")
        return QColor("#087F45")


class VideoTimelineWidget(QFrame):
    """Video seek controls with replay, clip marks, and action events."""

    seekRequested = pyqtSignal(int)
    clipMarked = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("videoTimelineCard")
        self._dragging = False
        self._duration_ms = 0
        self._position_ms = 0
        self._last_label_text = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 7, 12, 8)
        layout.setSpacing(2)
        control_layout = QHBoxLayout()
        control_layout.setContentsMargins(0, 0, 0, 0)
        control_layout.setSpacing(9)

        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setObjectName("videoTimeline")
        self.seek_slider.setAccessibleName("视频时间轴")
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("timeLabel")
        self.time_label.setFixedWidth(112)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.time_label.setAccessibleName("视频播放时间")

        self.btn_replay = QToolButton()
        self.btn_replay.setObjectName("btnReplay")
        self.btn_replay.setText("回看 5 秒")
        self.btn_replay.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_MediaSeekBackward))
        self.btn_replay.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_replay.setAccessibleName("回看前 5 秒")

        self.btn_mark_clip = QToolButton()
        self.btn_mark_clip.setObjectName("btnMarkClip")
        self.btn_mark_clip.setText("标记片段")
        self.btn_mark_clip.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton))
        self.btn_mark_clip.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.btn_mark_clip.setAccessibleName("标记当前视频片段")

        control_layout.addWidget(self.seek_slider, 1)
        control_layout.addWidget(self.time_label)
        control_layout.addWidget(self.btn_replay)
        control_layout.addWidget(self.btn_mark_clip)

        self.event_rail = EventTimelineRail()
        self.event_rail.seekRequested.connect(self.request_seek)
        layout.addLayout(control_layout)
        layout.addWidget(self.event_rail)

        self.seek_slider.sliderPressed.connect(self._on_slider_pressed)
        self.seek_slider.sliderReleased.connect(self._on_slider_released)
        self.seek_slider.sliderMoved.connect(self._on_slider_moved)
        self.btn_replay.clicked.connect(lambda: self.request_seek(self._position_ms - 5000))
        self.btn_mark_clip.clicked.connect(self._mark_current_position)

    @staticmethod
    def _format_time(ms: int) -> str:
        total_sec = max(0, ms) // 1000
        hours, remainder = divmod(total_sec, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours:
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _refresh_label(self, position_ms: int | None = None) -> None:
        pos = self._position_ms if position_ms is None else position_ms
        text = f"{self._format_time(pos)} / {self._format_time(self._duration_ms)}"
        if text != self._last_label_text:
            self._last_label_text = text
            self.time_label.setText(text)

    def _on_slider_pressed(self) -> None:
        self._dragging = True

    def _on_slider_released(self) -> None:
        self._dragging = False
        self.request_seek(self.seek_slider.value())

    def _on_slider_moved(self, value: int) -> None:
        self._refresh_label(value)

    def request_seek(self, position_ms: int) -> None:
        maximum = self._duration_ms if self._duration_ms > 0 else max(0, int(position_ms))
        value = max(0, min(int(position_ms), maximum))
        self._position_ms = value
        if self.seek_slider.maximum() > 0 and self.seek_slider.value() != value:
            self.seek_slider.setValue(value)
        self._refresh_label()
        self.event_rail.set_position(value)
        self.seekRequested.emit(value)

    def _mark_current_position(self) -> None:
        self.add_event(self._position_ms, "片段标记", 1.0)
        self.clipMarked.emit(self._position_ms)

    def add_event(self, timestamp_ms: int, label: str, confidence: float = 0.0) -> None:
        self.event_rail.add_event(timestamp_ms, label, confidence)

    def clear_events(self) -> None:
        self.event_rail.clear_events()

    def set_duration(self, duration_ms: int) -> None:
        duration_ms = max(0, duration_ms)
        if duration_ms == self._duration_ms and self.seek_slider.maximum() == duration_ms:
            return
        self._duration_ms = duration_ms
        self.seek_slider.setRange(0, self._duration_ms)
        self.event_rail.set_duration(duration_ms)
        self._refresh_label()

    def set_position(self, position_ms: int) -> None:
        new_position = max(0, min(position_ms, self._duration_ms))
        if new_position == self._position_ms and (
            self._dragging or self.seek_slider.value() == new_position
        ):
            return
        self._position_ms = new_position
        self.event_rail.set_position(new_position)
        if not self._dragging:
            if self.seek_slider.value() != self._position_ms:
                self.seek_slider.setValue(self._position_ms)
            self._refresh_label()

    def set_interactive(self, enabled: bool) -> None:
        self.seek_slider.setEnabled(enabled)
        self.btn_replay.setEnabled(enabled)
        self.btn_mark_clip.setEnabled(enabled)
        self.event_rail.setEnabled(enabled)

    def reset(self) -> None:
        self._dragging = False
        self._duration_ms = 0
        self._position_ms = 0
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setValue(0)
        self.event_rail.set_duration(0)
        self.event_rail.set_position(0)
        self.event_rail.clear_events()
        self._last_label_text = "00:00 / 00:00"
        self.time_label.setText(self._last_label_text)
