# -*- coding: utf-8 -*-
from __future__ import annotations

from math import isfinite
from typing import Any

from PyQt6.QtCore import QEvent, QPointF, QRect, QRectF, QSize, Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QColor, QFont, QImage, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSizePolicy,
    QSlider,
    QStackedWidget,
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
        self._mask_alpha = 0.14
        self._line_thickness = 3.0

    def clear(self) -> None:
        if self._court is None and self._cached_pixmap is None:
            return
        self._court = None
        self._court_key = None
        self._cached_pixmap = None
        self._dirty = True
        self.update()

    def set_court(self, court: object | None) -> None:
        court_dict = self._normalize_court(court)
        court_key = self._make_court_key(court_dict)
        if court_key == self._court_key:
            return
        self._court = court_dict
        self._court_key = court_key
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
        if (
            not isinstance(court, dict)
            or not court.get("valid")
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
        if outer_polygon.count() >= 3 and self._mask_alpha > 0.0:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(90, 210, 35, int(max(0.0, min(self._mask_alpha, 1.0)) * 255)))
            painter.drawPolygon(outer_polygon)

        dark_pen = QPen(QColor(25, 65, 0), self._line_thickness + 3.0)
        bright_pen = QPen(QColor(110, 245, 40), self._line_thickness)
        for pen in (dark_pen, bright_pen):
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)

        painter.setBrush(Qt.BrushStyle.NoBrush)
        for name, points in projected_lines.items():
            polygon = self._polygon_from_points(points)
            if polygon.count() < 2:
                continue
            is_closed = name == "doubles_outer"
            painter.setPen(dark_pen)
            self._draw_line_shape(painter, polygon, is_closed)
            painter.setPen(bright_pen)
            self._draw_line_shape(painter, polygon, is_closed)
        self._draw_corner_handles(painter, court)
        painter.end()

    def corners(self) -> list[tuple[float, float]]:
        court = self._court
        if not isinstance(court, dict) or not court.get("valid"):
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
        if not isinstance(court, dict) or not court.get("valid"):
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
        radius = 7.0
        for x, y in corners:
            label_x = self._display_rect.left() + x * scale_x
            label_y = self._display_rect.top() + y * scale_y
            center = QPointF(label_x, label_y)
            painter.setPen(QPen(QColor(15, 55, 0), 3.0))
            painter.setBrush(QColor(255, 255, 255, 235))
            painter.drawEllipse(center, radius + 2.0, radius + 2.0)
            painter.setPen(QPen(QColor(75, 190, 30), 2.0))
            painter.setBrush(QColor(110, 245, 40, 240))
            painter.drawEllipse(center, radius, radius)


class HawkeyeChallengeOverlayWidget(QWidget):
    """Animated challenge overlay shown on top of the current video frame."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAutoFillBackground(False)
        self.hide()

        self._source_size = QSize()
        self._display_rect = QRect()
        self._payload: dict[str, Any] | None = None
        self._progress = 0.0
        self._timer = QTimer(self)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._tick)

    def set_video_geometry(self, source_size: QSize, display_rect: QRect) -> None:
        self._source_size = QSize(source_size)
        self._display_rect = QRect(display_rect)
        self.update()

    def play(self, payload: object) -> None:
        if not isinstance(payload, dict):
            return
        self._payload = payload
        self._progress = 0.0
        self.show()
        self.raise_()
        self._timer.start()
        self.update()

    def clear(self) -> None:
        self._timer.stop()
        self._payload = None
        self._progress = 0.0
        self.hide()
        self.update()

    def _tick(self) -> None:
        self._progress += 0.018
        if self._progress >= 1.0:
            self._progress = 1.0
            self._timer.stop()
        self.update()

    def paintEvent(self, event) -> None:
        del event
        payload = self._payload
        if not isinstance(payload, dict) or self._display_rect.width() <= 0 or self._display_rect.height() <= 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(2, 8, 23, 118))
        self._draw_title(painter)
        self._draw_trajectory(painter, payload)
        self._draw_verdict(painter, payload)
        painter.end()

    def _draw_title(self, painter: QPainter) -> None:
        title_rect = QRectF(self._display_rect.left() + 24, self._display_rect.top() + 20, 360, 42)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(15, 23, 42, 210))
        painter.drawRoundedRect(title_rect, 18, 18)
        painter.setPen(QColor(125, 211, 252))
        painter.setFont(QFont("Segoe UI", 15, QFont.Weight.Bold))
        painter.drawText(title_rect.adjusted(18, 0, -12, 0), Qt.AlignmentFlag.AlignVCenter, "HAWK-EYE CHALLENGE")

    def _draw_trajectory(self, painter: QPainter, payload: dict[str, Any]) -> None:
        points = self._label_points(payload.get("trajectory_points"))
        landing = self._landing_label_point(payload)
        if landing is not None and (not points or self._distance(points[-1], landing) > 2.0):
            points.append(landing)
        if len(points) >= 2:
            visible_count = max(2, min(len(points), int(round(2 + (len(points) - 1) * min(self._progress / 0.72, 1.0)))))
            active_points = points[:visible_count]
            path = QPainterPath(active_points[0])
            for point in active_points[1:]:
                path.lineTo(point)
            for width, alpha, color in (
                (12.0, 70, QColor(34, 211, 238)),
                (6.0, 160, QColor(56, 189, 248)),
                (2.5, 255, QColor(240, 249, 255)),
            ):
                pen = QPen(QColor(color.red(), color.green(), color.blue(), alpha), width)
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawPath(path)
        if landing is not None and self._progress > 0.58:
            pulse = min(1.0, (self._progress - 0.58) / 0.28)
            radius = 12.0 + 34.0 * pulse
            painter.setPen(QPen(QColor(250, 204, 21, int(220 * (1.0 - pulse))), 4.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawEllipse(landing, radius, radius)
            painter.setPen(QPen(QColor(255, 255, 255), 3.0))
            painter.setBrush(QColor(250, 204, 21, 230))
            painter.drawEllipse(landing, 8.0, 8.0)

    def _draw_verdict(self, painter: QPainter, payload: dict[str, Any]) -> None:
        if self._progress < 0.72:
            return
        hawkeye = payload.get("hawkeye")
        if not isinstance(hawkeye, dict):
            hawkeye = {}
        call = str(hawkeye.get("call", "unknown")).upper()
        out_by = float(hawkeye.get("out_by_cm", 0.0) or 0.0)
        boundary = str(hawkeye.get("nearest_boundary", "unknown"))
        mode_label = "单打边界" if str(hawkeye.get("court_mode", "")) == "singles" else "双打边界"
        color = QColor(34, 197, 94) if call == "IN" else QColor(239, 68, 68) if call == "OUT" else QColor(148, 163, 184)
        rect = QRectF(self._display_rect.center().x() - 155, self._display_rect.bottom() - 138, 310, 96)
        painter.setPen(QPen(color, 2.0))
        painter.setBrush(QColor(15, 23, 42, 230))
        painter.drawRoundedRect(rect, 20, 20)
        painter.setPen(color)
        painter.setFont(QFont("Segoe UI", 30, QFont.Weight.Bold))
        painter.drawText(rect.adjusted(0, 8, 0, -42), Qt.AlignmentFlag.AlignCenter, call)
        detail_core = "落点在线内" if call == "IN" else f"{boundary} 出界 {out_by:.1f}cm" if call == "OUT" else "落点投影不足，无法裁决"
        detail = f"{mode_label} · {detail_core}"
        painter.setPen(QColor(226, 232, 240))
        painter.setFont(QFont("Microsoft YaHei", 10, QFont.Weight.Medium))
        painter.drawText(rect.adjusted(12, 58, -12, -8), Qt.AlignmentFlag.AlignCenter, detail)

    def _label_points(self, points: object) -> list[QPointF]:
        result: list[QPointF] = []
        if not isinstance(points, (list, tuple)):
            return result
        for point in points:
            if not isinstance(point, (list, tuple)) or len(point) < 2:
                continue
            mapped = self._map_image_point(point[0], point[1])
            if mapped is not None:
                result.append(mapped)
        return result

    def _landing_label_point(self, payload: dict[str, Any]) -> QPointF | None:
        event = payload.get("event")
        if not isinstance(event, dict):
            return None
        ball_xy = event.get("ball_xy")
        if not isinstance(ball_xy, (list, tuple)) or len(ball_xy) < 2:
            return None
        return self._map_image_point(ball_xy[0], ball_xy[1])

    def _map_image_point(self, x_obj: object, y_obj: object) -> QPointF | None:
        if self._source_size.width() <= 0 or self._source_size.height() <= 0:
            return None
        try:
            x = float(x_obj)
            y = float(y_obj)
        except (TypeError, ValueError):
            return None
        if not isfinite(x) or not isfinite(y) or x < 0.0 or y < 0.0:
            return None
        scale_x = self._display_rect.width() / max(1, self._source_size.width())
        scale_y = self._display_rect.height() / max(1, self._source_size.height())
        return QPointF(self._display_rect.left() + x * scale_x, self._display_rect.top() + y * scale_y)

    def _distance(self, a: QPointF, b: QPointF) -> float:
        dx = a.x() - b.x()
        dy = a.y() - b.y()
        return (dx * dx + dy * dy) ** 0.5


class VideoPlayerWidget(QFrame):
    """由外部帧驱动的纯显示视频预览组件。"""

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
        self._court_corner_hit_radius_px = 18.0
        self._status_text = ""
        self._status_state = ""

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self.btn_select_video = QPushButton("选择视频")
        self.btn_select_video.setObjectName("btnSelectVideo")

        self.path_edit = QLineEdit()
        self.path_edit.setObjectName("videoPathEdit")
        self.path_edit.setReadOnly(True)
        self.path_edit.setPlaceholderText("视频路径")
        self.path_edit.setClearButtonEnabled(False)

        self.btn_force_stop = QPushButton("停止")
        self.btn_force_stop.setObjectName("btnForceStop")

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

        placeholder_title = QLabel("预览")
        placeholder_title.setObjectName("videoPlaceholderTitle")
        placeholder_title.setAlignment(Qt.AlignmentFlag.AlignCenter)

        placeholder_hint = QLabel("在此处选择视频以预览 TrackNetV3 帧。")
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
        self.video_label.setMouseTracking(True)
        self.video_label.installEventFilter(self)
        self.court_overlay = CourtLineOverlayWidget(self.video_label)
        self.court_overlay.setGeometry(self.video_label.rect())
        self.court_overlay.raise_()
        self.hawkeye_overlay = HawkeyeChallengeOverlayWidget(self.video_label)
        self.hawkeye_overlay.setGeometry(self.video_label.rect())
        self.hawkeye_overlay.raise_()

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
            if event_type == QEvent.Type.MouseButtonPress:
                if getattr(event, "button", lambda: None)() != Qt.MouseButton.LeftButton:
                    return False
                position = event.position() if hasattr(event, "position") else event.pos()
                if self._point_capture_enabled:
                    point = self._image_point_from_label_pos(position)
                    if point is None:
                        return False
                    self.framePointClicked.emit(point[0], point[1])
                    return True
                corner_index = self._court_corner_index_at_label_pos(position)
                if corner_index is not None:
                    self._dragging_court_corner_index = corner_index
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

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._render_current_pixmap()

    def _render_current_pixmap(self) -> None:
        if self._current_pixmap is None:
            self.court_overlay.setGeometry(self.video_label.rect())
            self.court_overlay.set_video_geometry(QSize(), QRect())
            self.hawkeye_overlay.setGeometry(self.video_label.rect())
            self.hawkeye_overlay.set_video_geometry(QSize(), QRect())
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
        self.hawkeye_overlay.setGeometry(self.video_label.rect())
        display_rect = QRect(
            (self.video_label.width() - scaled.width()) // 2,
            (self.video_label.height() - scaled.height()) // 2,
            scaled.width(),
            scaled.height(),
        )
        self.court_overlay.set_video_geometry(self._current_pixmap.size(), display_rect)
        self.hawkeye_overlay.set_video_geometry(self._current_pixmap.size(), display_rect)
        self.court_overlay.raise_()
        self.hawkeye_overlay.raise_()

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

    def play_hawkeye_challenge(self, payload: object) -> None:
        self.hawkeye_overlay.play(payload)

    def set_point_capture_enabled(self, enabled: bool) -> None:
        self._point_capture_enabled = bool(enabled)
        if enabled:
            self.video_label.setCursor(Qt.CursorShape.CrossCursor)
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
        self.path_edit.setText(path)
        self.path_edit.setToolTip(path)

    def set_live_source(self, label: str) -> None:
        self._source_path = label
        self.path_edit.setText(label)
        self.path_edit.setToolTip(label)

    def clear_video(self) -> None:
        self._source_path = ""
        self._current_pixmap = None
        self._scaled_pixmap = None
        self._scaled_source_key = None
        self._scaled_label_size = QSize()
        self._dragging_court_corner_index = None
        self.path_edit.clear()
        self.path_edit.setToolTip("")
        self.video_label.clear()
        self.video_label.setCursor(Qt.CursorShape.ArrowCursor)
        self.court_overlay.clear()
        self.court_overlay.set_video_geometry(QSize(), QRect())
        self.hawkeye_overlay.clear()
        self.hawkeye_overlay.set_video_geometry(QSize(), QRect())
        self._set_status("未加载视频", "idle")
        self.preview_stack.setCurrentWidget(self.preview_stack.widget(0))

    def play(self) -> None:
        self._set_status("播放中", "playing")

    def pause(self) -> None:
        if self._source_path:
            self._set_status("已暂停", "loaded")

    def stop(self) -> None:
        if self._source_path:
            self._set_status("已停止", "stopped")

    def set_video_state(self, state: str) -> None:
        mapping = {
            "idle": "未加载视频",
            "loaded": "就绪",
            "playing": "播放中",
            "stopped": "已停止",
            "error": "视频加载失败",
        }
        self._set_status(mapping.get(state, state), state)

    def set_fullscreen_mode(self, enabled: bool) -> None:
        self.btn_fullscreen.blockSignals(True)
        self.btn_fullscreen.setChecked(bool(enabled))
        self.btn_fullscreen.blockSignals(False)
        if enabled:
            self.btn_fullscreen.setText("退出全屏")
            self.btn_fullscreen.setToolTip("退出全屏（Esc）")
            self.btn_fullscreen.setAccessibleName("退出全屏")
        else:
            self.btn_fullscreen.setText("全屏")
            self.btn_fullscreen.setToolTip("放大视频为全屏")
            self.btn_fullscreen.setAccessibleName("全屏")

    def current_path(self) -> str:
        return self._source_path


class VideoTimelineWidget(QFrame):
    """从控制器层手动控制的时间轴。"""

    seekRequested = pyqtSignal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("videoTimelineCard")
        self._dragging = False
        self._duration_ms = 0
        self._position_ms = 0
        self._last_label_text = ""

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 6, 12, 6)
        layout.setSpacing(10)

        self.seek_slider = QSlider(Qt.Orientation.Horizontal)
        self.seek_slider.setObjectName("videoTimeline")
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )

        self.time_label = QLabel("00:00 / 00:00")
        self.time_label.setObjectName("timeLabel")
        self.time_label.setFixedWidth(110)
        self.time_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        layout.addWidget(self.seek_slider)
        layout.addWidget(self.time_label)

        self.seek_slider.sliderPressed.connect(self._on_slider_pressed)
        self.seek_slider.sliderReleased.connect(self._on_slider_released)
        self.seek_slider.sliderMoved.connect(self._on_slider_moved)

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
        value = self.seek_slider.value()
        self._position_ms = value
        self._refresh_label()
        self.seekRequested.emit(value)

    def _on_slider_moved(self, value: int) -> None:
        self._refresh_label(value)

    def set_duration(self, duration_ms: int) -> None:
        duration_ms = max(0, duration_ms)
        if duration_ms == self._duration_ms and self.seek_slider.maximum() == duration_ms:
            return
        self._duration_ms = duration_ms
        self.seek_slider.setRange(0, self._duration_ms)
        self._refresh_label()

    def set_position(self, position_ms: int) -> None:
        new_position = max(0, min(position_ms, self._duration_ms))
        if new_position == self._position_ms and (
            self._dragging or self.seek_slider.value() == new_position
        ):
            return
        self._position_ms = new_position
        if not self._dragging:
            if self.seek_slider.value() != self._position_ms:
                self.seek_slider.setValue(self._position_ms)
            self._refresh_label()

    def set_interactive(self, enabled: bool) -> None:
        self.seek_slider.setEnabled(enabled)

    def reset(self) -> None:
        self._dragging = False
        self._duration_ms = 0
        self._position_ms = 0
        self.seek_slider.setRange(0, 0)
        self.seek_slider.setValue(0)
        self._last_label_text = "00:00 / 00:00"
        self.time_label.setText(self._last_label_text)
