from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QLabel, QWidget

from nsy_broadcasting_platform.compat import CV2
from nsy_broadcasting_platform.models import Layer, Scene
from nsy_broadcasting_platform.ui.theme import PREVIEW_FRAME_STYLE, T
from nsy_broadcasting_platform.utils import fit_rect

cv2 = CV2.module


class _PreviewOverlay(QWidget):
    def __init__(self, owner: "PreviewWidget") -> None:
        super().__init__(owner)
        self.owner = owner
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        self.owner._paint_overlay(painter)


class PreviewWidget(QWidget):
    layer_selected = pyqtSignal(str)
    layer_transform_changed = pyqtSignal(str, int, int, int, int)

    def __init__(self, title: str, canvas_width: int, canvas_height: int, editable: bool) -> None:
        super().__init__()
        self.title = title
        self.scene_name = ""
        self.canvas_width = canvas_width
        self.canvas_height = canvas_height
        self.editable = editable

        self._frame_image: QImage | None = None
        self._frame_pixmap: QPixmap | None = None
        self._scene: Scene | None = None
        self._selected_layer_id: str | None = None
        self._emphasis_layer_id: str | None = None
        self._interaction_mode = "position"

        self._drag_mode: str | None = None
        self._drag_start = QPoint()
        self._layer_start: tuple[int, int, int, int] | None = None

        self._frame_label = QLabel(self)
        self._frame_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._frame_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._frame_label.setStyleSheet(PREVIEW_FRAME_STYLE)
        self._frame_label.setText("等待画面...")

        self._overlay = _PreviewOverlay(self)

        self.setMinimumSize(220, 150)
        self.setMouseTracking(True)
        self._update_content_layout()

    def set_canvas_size(self, canvas_width: int, canvas_height: int) -> None:
        self.canvas_width = max(1, int(canvas_width))
        self.canvas_height = max(1, int(canvas_height))
        self._update_content_layout()
        self.update()

    def set_frame(self, frame) -> None:
        self._frame_image = self._to_qimage(frame)
        self._frame_pixmap = None
        if self._frame_image is not None and not self._frame_image.isNull():
            self._frame_pixmap = QPixmap.fromImage(self._frame_image)
        self._update_frame_label()
        self.update()

    def set_scene(self, scene: Scene | None) -> None:
        self._scene = scene
        self._overlay.update()
        self.update()

    def set_selected_layer(self, layer_id: str | None) -> None:
        self._selected_layer_id = layer_id
        self._overlay.update()
        self.update()

    def set_emphasis_layer(self, layer_id: str | None) -> None:
        self._emphasis_layer_id = layer_id
        self._overlay.update()
        self.update()

    def set_interaction_mode(self, mode: str) -> None:
        if mode not in {"position", "size", "lock"}:
            mode = "position"
        self._interaction_mode = mode
        self._overlay.update()
        self.update()

    def set_scene_name(self, scene_name: str) -> None:
        self.scene_name = scene_name
        self.update()

    def current_frame_image(self) -> QImage | None:
        if self._frame_image is None or self._frame_image.isNull():
            return None
        return self._frame_image.copy()

    def _target_rect(self) -> QRect:
        x, y, w, h = fit_rect(self.canvas_width, self.canvas_height, max(1, self.width() - 24), max(1, self.height() - 68))
        return QRect(x + 12, y + 48, w, h)

    def _update_content_layout(self) -> None:
        target = self._target_rect()
        self._frame_label.setGeometry(target)
        self._overlay.setGeometry(self.rect())
        self._update_frame_label()
        self._overlay.raise_()

    def _update_frame_label(self) -> None:
        target = self._target_rect()
        if target.width() <= 0 or target.height() <= 0:
            return
        self._frame_label.setGeometry(target)
        if self._frame_pixmap is not None and not self._frame_pixmap.isNull():
            scaled = self._frame_pixmap.scaled(
                target.size(),
                Qt.AspectRatioMode.IgnoreAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self._frame_label.setPixmap(scaled)
            self._frame_label.setText("")
        else:
            self._frame_label.setPixmap(QPixmap())
            self._frame_label.setText("等待画面...")

    def _canvas_to_widget(self, x: int, y: int) -> tuple[int, int]:
        rect = self._target_rect()
        sx = rect.width() / max(1, self.canvas_width)
        sy = rect.height() / max(1, self.canvas_height)
        return int(rect.x() + x * sx), int(rect.y() + y * sy)

    def _widget_to_canvas(self, px: int, py: int) -> tuple[int, int]:
        rect = self._target_rect()
        sx = self.canvas_width / max(1, rect.width())
        sy = self.canvas_height / max(1, rect.height())
        cx = int((px - rect.x()) * sx)
        cy = int((py - rect.y()) * sy)
        return cx, cy

    def _to_qimage(self, frame) -> QImage | None:
        if frame is None:
            return None
        if isinstance(frame, QImage):
            return frame.copy()
        if getattr(frame, "ndim", 0) == 2:
            h, w = frame.shape[:2]
            return QImage(frame.data, w, h, frame.strides[0], QImage.Format.Format_Grayscale8).copy()
        if getattr(frame, "ndim", 0) != 3:
            return None
        h, w = frame.shape[:2]
        channels = frame.shape[2]
        if channels == 3:
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) if cv2 is not None else frame[:, :, ::-1]
            return QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888).copy()
        if channels == 4:
            rgba = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGBA) if cv2 is not None else frame[:, :, [2, 1, 0, 3]]
            return QImage(rgba.data, w, h, rgba.strides[0], QImage.Format.Format_RGBA8888).copy()
        return None

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor(T.bg))

        panel_rect = self.rect().adjusted(1, 1, -2, -2)
        accent = QColor(T.green if not self.editable else T.amber)
        painter.setPen(QPen(QColor(T.border), 1))
        painter.setBrush(QColor(T.panel))
        painter.drawRoundedRect(panel_rect, 16, 16)
        painter.setPen(QPen(accent, 3))
        painter.drawLine(panel_rect.left() + 14, panel_rect.top() + 9, min(panel_rect.left() + 96, panel_rect.right() - 14), panel_rect.top() + 9)

        title_rect = QRect(14, 8, max(10, self.width() - 28), 18)
        painter.setPen(QColor(T.text))
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, self.title)
        if self.scene_name:
            subtitle_rect = QRect(14, 26, max(10, self.width() - 28), 18)
            painter.setPen(QColor(T.text_muted))
            painter.drawText(
                subtitle_rect,
                Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                f"当前场景: {self.scene_name}",
            )

        target = self._target_rect()
        target_inner = target.adjusted(0, 0, -1, -1)
        painter.setPen(QPen(accent.darker(115), 1))
        painter.drawRect(target_inner)

    def _paint_overlay(self, painter: QPainter) -> None:
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        if self._scene is None:
            return
        ordered_layers = sorted(self._scene.layers, key=lambda layer: layer.priority)
        if self.editable:
            for layer in ordered_layers:
                if not layer.enabled:
                    continue
                self._draw_layer_box(painter, layer)

        if self._emphasis_layer_id:
            for layer in self._scene.layers:
                if layer.id == self._emphasis_layer_id and layer.enabled:
                    self._draw_emphasis_box(painter, layer)
                    break

    def _draw_layer_box(self, painter: QPainter, layer: Layer) -> None:
        x1, y1 = self._canvas_to_widget(layer.x, layer.y)
        x2, y2 = self._canvas_to_widget(layer.x + layer.width, layer.y + layer.height)
        rect = QRect(x1, y1, x2 - x1, y2 - y1)
        if rect.width() <= 2 or rect.height() <= 2:
            return
        is_selected = layer.id == self._selected_layer_id
        color = QColor(T.amber_hover) if is_selected else QColor(T.text_weak)
        if layer.locked:
            color = QColor(T.danger)
        painter.setPen(QPen(color, 2 if is_selected else 1))
        painter.drawRect(rect)
        painter.setPen(color)
        painter.drawText(rect.x() + 4, rect.y() + 16, layer.name)
        if is_selected and not layer.locked and self._interaction_mode == "size":
            handle = QRect(rect.right() - 10, rect.bottom() - 10, 10, 10)
            painter.fillRect(handle, color)

    def _draw_emphasis_box(self, painter: QPainter, layer: Layer) -> None:
        x1, y1 = self._canvas_to_widget(layer.x, layer.y)
        x2, y2 = self._canvas_to_widget(layer.x + layer.width, layer.y + layer.height)
        rect = QRect(x1, y1, x2 - x1, y2 - y1)
        if rect.width() <= 2 or rect.height() <= 2:
            return
        glow = QColor(T.amber_hover)
        painter.setPen(QPen(glow, 3))
        painter.drawRect(rect.adjusted(-2, -2, 2, 2))
        painter.fillRect(QRect(rect.x(), max(0, rect.y() - 18), min(rect.width(), 96), 16), glow)
        painter.setPen(QColor(24, 24, 24))
        painter.drawText(rect.x() + 6, rect.y() - 5, "AI关注")

    def _find_hit_layer(self, cx: int, cy: int) -> Layer | None:
        if self._scene is None:
            return None
        for layer in sorted(self._scene.layers, key=lambda item: item.priority, reverse=True):
            if not layer.enabled:
                continue
            if layer.x <= cx <= layer.x + layer.width and layer.y <= cy <= layer.y + layer.height:
                return layer
        return None

    def mousePressEvent(self, event):  # noqa: N802
        if not self.editable or self._scene is None:
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        target = self._target_rect()
        if not target.contains(event.pos()):
            return
        cx, cy = self._widget_to_canvas(event.position().x(), event.position().y())
        hit = self._find_hit_layer(cx, cy)
        if hit is None:
            self._selected_layer_id = None
            self.update()
            return

        self._selected_layer_id = hit.id
        self.layer_selected.emit(hit.id)
        self._drag_start = event.position().toPoint()
        self._layer_start = (hit.x, hit.y, hit.width, hit.height)

        if hit.locked or self._interaction_mode == "lock":
            self._drag_mode = None
        elif self._interaction_mode == "size":
            self._drag_mode = "resize"
        elif not hit.locked:
            self._drag_mode = "move"
        else:
            self._drag_mode = None
        self.update()

    def mouseMoveEvent(self, event):  # noqa: N802
        if not self.editable or self._drag_mode is None or self._selected_layer_id is None or self._layer_start is None:
            return
        dx_widget = event.position().x() - self._drag_start.x()
        dy_widget = event.position().y() - self._drag_start.y()
        target = self._target_rect()
        if target.width() <= 0 or target.height() <= 0:
            return
        sx = self.canvas_width / target.width()
        sy = self.canvas_height / target.height()
        dx = int(dx_widget * sx)
        dy = int(dy_widget * sy)

        x, y, w, h = self._layer_start
        if self._drag_mode == "move":
            nx = max(-w + 10, min(self.canvas_width - 10, x + dx))
            ny = max(-h + 10, min(self.canvas_height - 10, y + dy))
            self.layer_transform_changed.emit(self._selected_layer_id, nx, ny, w, h)
        else:
            nw = max(40, min(self.canvas_width, w + dx))
            nh = max(40, min(self.canvas_height, h + dy))
            self.layer_transform_changed.emit(self._selected_layer_id, x, y, nw, nh)

    def mouseReleaseEvent(self, event):  # noqa: N802
        self._drag_mode = None
        self._layer_start = None

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._update_content_layout()
