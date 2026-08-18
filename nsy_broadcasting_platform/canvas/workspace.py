from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any, Callable

from PyQt6.QtCore import QMimeData, QPoint, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QFont, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGraphicsItem,
    QGraphicsObject,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from nsy_broadcasting_platform.canvas.bridge import DirectorCanvasBridge, SceneCanvasAdapter
from nsy_broadcasting_platform.canvas.models import CanvasDocument, CanvasGroupModel, CanvasItemModel
from nsy_broadcasting_platform.capture.window_capture_win import enum_windows
from nsy_broadcasting_platform.models import Layer, LayerType, Scene, new_id
from nsy_broadcasting_platform.ui.ai_model_dialog import AIModelWorkbenchDialog
from nsy_broadcasting_platform.ui.audio_mixer_dialog import AudioMixerDialog
from nsy_broadcasting_platform.ui.theme import HAULIX_APP_QSS


class CanvasHistoryManager:
    """保存轻量文档快照，支持撤销与重做。"""

    def __init__(self, limit: int = 40) -> None:
        self.limit = max(5, int(limit))
        self._stack: list[CanvasDocument] = []
        self._index = -1

    def reset(self, document: CanvasDocument) -> None:
        self._stack = [document.clone()]
        self._index = 0

    def push(self, document: CanvasDocument) -> None:
        doc = document.clone()
        if self._index >= 0 and self._stack and self._stack[self._index].to_dict() == doc.to_dict():
            return
        if self._index < len(self._stack) - 1:
            self._stack = self._stack[: self._index + 1]
        self._stack.append(doc)
        if len(self._stack) > self.limit:
            self._stack = self._stack[-self.limit :]
        self._index = len(self._stack) - 1

    def can_undo(self) -> bool:
        return self._index > 0

    def can_redo(self) -> bool:
        return 0 <= self._index < len(self._stack) - 1

    def undo(self) -> CanvasDocument | None:
        if not self.can_undo():
            return None
        self._index -= 1
        return self._stack[self._index].clone()

    def redo(self) -> CanvasDocument | None:
        if not self.can_redo():
            return None
        self._index += 1
        return self._stack[self._index].clone()


class CanvasSelectionManager:
    """管理画布选择态，避免选择逻辑散落在视图里。"""

    def __init__(self) -> None:
        self._selected_ids: list[str] = []

    def set_selected_ids(self, ids: list[str]) -> None:
        self._selected_ids = list(dict.fromkeys(ids))

    def selected_ids(self) -> list[str]:
        return list(self._selected_ids)

    def selected_count(self) -> int:
        return len(self._selected_ids)

    def clear(self) -> None:
        self._selected_ids.clear()


class CanvasTransformController:
    """提供缩放、吸附和对齐的轻量辅助函数。"""

    @staticmethod
    def snap_value(value: float, step: int = 10) -> int:
        step = max(1, int(step))
        return int(round(value / step) * step)

    @staticmethod
    def zoom_clamp(zoom: float) -> float:
        return max(0.15, min(4.0, float(zoom)))


class CanvasRenderer:
    """负责画布底纹、网格和输出框的低成本绘制。"""

    def __init__(self) -> None:
        self.base_grid = 40
        self.major_grid = 200

    def paint_background(self, painter: QPainter, rect: QRectF, zoom: float, low_performance: bool, output_frame: tuple[int, int, int, int]) -> None:
        painter.save()
        painter.fillRect(rect, QColor(3, 4, 4))
        step = 80 if low_performance else self.base_grid
        major = step * 5

        left = int(rect.left()) - (int(rect.left()) % step) - step
        right = int(rect.right()) + step
        top = int(rect.top()) - (int(rect.top()) % step) - step
        bottom = int(rect.bottom()) + step

        minor_pen = QPen(QColor(42, 39, 36, 120), 1)
        major_pen = QPen(QColor(83, 52, 44, 170), 1)
        painter.setPen(minor_pen)
        x = left
        while x <= right:
            painter.drawLine(x, top, x, bottom)
            x += step
        painter.setPen(major_pen)
        x = left
        while x <= right:
            painter.drawLine(x, top, x, bottom)
            x += major

        painter.setPen(minor_pen)
        y = top
        while y <= bottom:
            painter.drawLine(left, y, right, y)
            y += step
        painter.setPen(major_pen)
        y = top
        while y <= bottom:
            painter.drawLine(left, y, right, y)
            y += major

        ox, oy, ow, oh = output_frame
        if ow > 0 and oh > 0:
            painter.setPen(QPen(QColor(162, 89, 65, 230), 2, Qt.PenStyle.DashLine))
            painter.drawRect(QRectF(ox, oy, ow, oh))
            painter.setPen(QColor(240, 239, 238))
            painter.setFont(QFont("Microsoft YaHei UI", 9, QFont.Weight.Bold))
            painter.drawText(ox + 10, oy + 20, "Preview / Program")
        painter.restore()


class CanvasExportService:
    """负责把画布文档写回导播场景。"""

    def __init__(self, bridge: DirectorCanvasBridge) -> None:
        self.bridge = bridge

    def import_scene(self, scene_id: str | None, canvas_width: int, canvas_height: int) -> CanvasDocument:
        return self.bridge.build_document_from_scene(scene_id, canvas_width, canvas_height)

    def import_active_scene(self, canvas_width: int, canvas_height: int) -> CanvasDocument:
        return self.bridge.import_active_scene(canvas_width, canvas_height)

    def export_document(
        self,
        document: CanvasDocument,
        *,
        target_scene_id: str | None = None,
        create_new_scene: bool = False,
        activate: bool = False,
    ) -> tuple[bool, str, str | None]:
        return self.bridge.export_document_to_scene(
            document,
            target_scene_id=target_scene_id,
            create_new_scene=create_new_scene,
            activate=activate,
        )

    def save_document_to_file(self, document: CanvasDocument, file_path: str) -> bool:
        try:
            Path(file_path).write_text(json.dumps(document.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
            return True
        except Exception:
            return False

    def load_document_from_file(self, file_path: str) -> CanvasDocument | None:
        try:
            payload = json.loads(Path(file_path).read_text(encoding="utf-8"))
        except Exception:
            return None
        try:
            return CanvasDocument.from_dict(payload if isinstance(payload, dict) else {})
        except Exception:
            return None


class CanvasGraphicsItem(QGraphicsObject):
    """画布中的单个对象，负责显示、移动与基础缩放。"""

    geometry_committed = pyqtSignal(str)
    _PIXMAP_CACHE: dict[str, QPixmap | None] = {}

    _TYPE_COLORS = {
        "scene": QColor(162, 89, 65),
        "camera": QColor(113, 138, 182),
        "screen": QColor(125, 158, 122),
        "window": QColor(197, 186, 184),
        "network": QColor(178, 115, 81),
        "video": QColor(145, 119, 93),
        "png": QColor(171, 139, 111),
        "text": QColor(240, 219, 167),
        "group": QColor(92, 92, 93),
    }

    def __init__(self, model: CanvasItemModel) -> None:
        super().__init__()
        self.model = model.clone()
        self._rect = QRectF(0.0, 0.0, float(max(1, self.model.width)), float(max(1, self.model.height)))
        self._resizing = False
        self._scene_border_dragging = False
        self._resize_start = QPointF()
        self._scene_drag_start = QPointF()
        self._scene_drag_origin = QPointF()
        self._hover_handle = False
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not self.model.locked and self.model.type != "scene")
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges, True)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsFocusable, True)
        self.apply_model(self.model)

    def boundingRect(self) -> QRectF:  # noqa: D401
        return self._rect.adjusted(-1, -1, 12, 12)

    def _type_color(self) -> QColor:
        return self._TYPE_COLORS.get(self.model.type, QColor(96, 150, 212))

    def _handle_rect(self) -> QRectF:
        return QRectF(self._rect.width() - 12, self._rect.height() - 12, 12, 12)

    def _scene_border_hit(self, point: QPointF) -> bool:
        if self.model.type != "scene":
            return False
        outer = QRectF(0.0, 0.0, self._rect.width(), self._rect.height())
        inner = outer.adjusted(14.0, 14.0, -14.0, -14.0)
        return outer.contains(point) and not inner.contains(point)

    def apply_model(self, model: CanvasItemModel) -> None:
        self.model = model.clone()
        self.prepareGeometryChange()
        self._rect = QRectF(0.0, 0.0, float(max(1, self.model.width)), float(max(1, self.model.height)))
        self.setPos(self.model.x, self.model.y)
        self.setRotation(float(self.model.rotation))
        self.setOpacity(float(self.model.opacity))
        self.setVisible(bool(self.model.visible))
        self.setZValue(float(self.model.z_index))
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not self.model.locked and self.model.type != "scene")
        self.update()

    def to_model(self) -> CanvasItemModel:
        model = self.model.clone()
        model.x = int(round(self.pos().x()))
        model.y = int(round(self.pos().y()))
        model.width = max(1, int(round(self._rect.width())))
        model.height = max(1, int(round(self._rect.height())))
        model.rotation = float(self.rotation())
        model.opacity = float(self.opacity())
        model.visible = bool(self.isVisible())
        model.locked = bool(self.model.locked)
        model.z_index = int(round(self.zValue()))
        return model

    def set_locked(self, locked: bool) -> None:
        self.model.locked = bool(locked)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsMovable, not self.model.locked and self.model.type != "scene")
        self.update()

    def set_visible_state(self, visible: bool) -> None:
        self.model.visible = bool(visible)
        self.setVisible(bool(visible))

    def hoverMoveEvent(self, event):  # noqa: N802
        if self.model.locked:
            self.setCursor(Qt.CursorShape.ArrowCursor)
            return super().hoverMoveEvent(event)
        if self._handle_rect().contains(event.pos()):
            self._hover_handle = True
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif self._scene_border_hit(event.pos()):
            self._hover_handle = False
            self.setCursor(Qt.CursorShape.SizeAllCursor)
        elif self.model.type == "scene":
            self._hover_handle = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
        else:
            self._hover_handle = False
            self.setCursor(Qt.CursorShape.OpenHandCursor if self.isSelected() else Qt.CursorShape.ArrowCursor)
        return super().hoverMoveEvent(event)

    def hoverLeaveEvent(self, event):  # noqa: N802
        self._hover_handle = False
        self.unsetCursor()
        return super().hoverLeaveEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        if self.model.locked:
            event.ignore()
            return
        if event.button() == Qt.MouseButton.LeftButton and self._handle_rect().contains(event.pos()):
            self._resizing = True
            self._resize_start = event.pos()
            event.accept()
            return
        if self.model.type == "scene" and event.button() == Qt.MouseButton.LeftButton:
            self.setSelected(True)
            if self._scene_border_hit(event.pos()):
                self._scene_border_dragging = True
                self._scene_drag_start = event.scenePos()
                self._scene_drag_origin = self.pos()
                self.setCursor(Qt.CursorShape.SizeAllCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._resizing and not self.model.locked:
            nx = max(64.0, event.pos().x())
            ny = max(48.0, event.pos().y())
            self.prepareGeometryChange()
            self._rect = QRectF(0.0, 0.0, nx, ny)
            self.model.width = int(round(nx))
            self.model.height = int(round(ny))
            self.update()
            event.accept()
            return
        if self._scene_border_dragging and self.model.type == "scene" and not self.model.locked:
            delta = event.scenePos() - self._scene_drag_start
            self.setPos(self._scene_drag_origin + delta)
            self.model.x = int(round(self.pos().x()))
            self.model.y = int(round(self.pos().y()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        was_resizing = self._resizing
        was_scene_dragging = self._scene_border_dragging
        self._resizing = False
        self._scene_border_dragging = False
        super().mouseReleaseEvent(event)
        if was_resizing or was_scene_dragging or event.button() == Qt.MouseButton.LeftButton:
            self.geometry_committed.emit(self.model.item_id)

    def itemChange(self, change, value):  # noqa: N802
        if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged:
            self.model.x = int(round(value.x()))
            self.model.y = int(round(value.y()))
        elif change == QGraphicsItem.GraphicsItemChange.ItemSelectedHasChanged:
            pass
        return super().itemChange(change, value)

    @classmethod
    def _load_pixmap(cls, path: str) -> QPixmap | None:
        path = str(path or "").strip()
        if not path:
            return None
        cached = cls._PIXMAP_CACHE.get(path)
        if cached is not None or path in cls._PIXMAP_CACHE:
            return cached
        pixmap = QPixmap(path)
        if pixmap.isNull():
            cls._PIXMAP_CACHE[path] = None
            return None
        cls._PIXMAP_CACHE[path] = pixmap
        return pixmap

    def _image_path(self) -> str:
        source = dict(self.model.metadata.get("source_snapshot") or {})
        return str(source.get("image_path") or source.get("path") or source.get("file_path") or "").strip()

    def _draw_pixmap_preview(self, painter: QPainter, rect: QRectF, pixmap: QPixmap) -> bool:
        if rect.width() < 24 or rect.height() < 24 or pixmap.isNull():
            return False
        scaled = pixmap.scaled(
            int(rect.width()),
            int(rect.height()),
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
        sx = max(0, (scaled.width() - int(rect.width())) // 2)
        sy = max(0, (scaled.height() - int(rect.height())) // 2)
        painter.drawPixmap(rect, scaled, QRectF(sx, sy, int(rect.width()), int(rect.height())))
        return True

    def _draw_source_preview(self, painter: QPainter, rect: QRectF) -> None:
        source = dict(self.model.metadata.get("source_snapshot") or {})
        image_path = self._image_path()
        pixmap = self._load_pixmap(image_path)
        if pixmap is not None and self._draw_pixmap_preview(painter, rect, pixmap):
            return

        painter.save()
        base = self._type_color()
        base.setAlpha(72)
        painter.setBrush(base)
        painter.setPen(QPen(base.lighter(130), 1))
        painter.drawRoundedRect(rect, 8, 8)
        painter.setPen(QColor(211, 226, 242))
        font = painter.font()
        font.setPointSize(11)
        font.setBold(True)
        painter.setFont(font)
        label_map = {
            "camera": "摄像头预览",
            "screen": "屏幕预览",
            "window": "窗口预览",
            "network": "网络流预览",
            "video": "视频预览",
            "png": "图片预览",
        }
        painter.drawText(rect.adjusted(10, 8, -10, -8), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, label_map.get(self.model.type, "图层预览"))
        font.setPointSize(8)
        font.setBold(False)
        painter.setFont(font)
        hint = ""
        if self.model.type == "camera":
            hint = f"索引 {source.get('camera_index', 0)}"
        elif self.model.type == "screen":
            hint = f"显示器 {source.get('monitor_index', 1)}"
        elif self.model.type == "window":
            hint = str(source.get("title") or "窗口源")
        elif self.model.type == "network":
            hint = str(source.get("url") or "RTMP / RTSP / HTTP")
        elif image_path:
            hint = Path(image_path).name
        painter.setPen(QColor(197, 186, 184))
        painter.drawText(rect.adjusted(10, 32, -10, -8), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, hint[:42])
        painter.restore()

    def _draw_scene_preview(self, painter: QPainter, rect: QRectF) -> None:
        snapshot = dict(self.model.metadata.get("scene_snapshot") or {})
        layers = list(snapshot.get("layers") or [])
        painter.save()
        painter.setBrush(QColor(11, 12, 12, 190))
        painter.setPen(QPen(QColor(83, 52, 44, 140), 1))
        painter.drawRoundedRect(rect, 8, 8)
        if not layers:
            painter.setPen(QColor(197, 186, 184))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "空场景")
            painter.restore()
            return
        source_w = max(1, int(self.model.metadata.get("scene_canvas_width") or 1280))
        source_h = max(1, int(self.model.metadata.get("scene_canvas_height") or 720))
        for layer in sorted(layers, key=lambda item: int(item.get("priority", 0))):
            if not bool(layer.get("enabled", True)):
                continue
            lx = rect.left() + float(layer.get("x", 0)) * rect.width() / source_w
            ly = rect.top() + float(layer.get("y", 0)) * rect.height() / source_h
            lw = max(2.0, float(layer.get("width", 1)) * rect.width() / source_w)
            lh = max(2.0, float(layer.get("height", 1)) * rect.height() / source_h)
            layer_rect = QRectF(lx, ly, lw, lh).intersected(rect)
            if layer_rect.isEmpty():
                continue
            source = dict(layer.get("source") or {})
            pixmap = self._load_pixmap(str(source.get("image_path") or ""))
            if pixmap is not None:
                self._draw_pixmap_preview(painter, layer_rect, pixmap)
            else:
                color = self._TYPE_COLORS.get(str(layer.get("layer_type") or "png"), QColor(96, 150, 212))
                fill = QColor(color)
                fill.setAlpha(95)
                painter.setBrush(fill)
                painter.setPen(QPen(fill.lighter(145), 1))
                painter.drawRect(layer_rect)
        painter.restore()

    def paint(self, painter: QPainter, option, widget=None):  # noqa: D401
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        fill = self._type_color()
        fill.setAlpha(50 if self.model.visible else 24)
        painter.setPen(QPen(fill.darker(145), 1))
        painter.setBrush(fill)
        painter.drawRoundedRect(self._rect.adjusted(0.5, 0.5, -0.5, -0.5), 10, 10)
        preview_rect = self._rect.adjusted(8, 54, -8, -8)
        if preview_rect.width() > 32 and preview_rect.height() > 26:
            if self.model.type == "scene":
                self._draw_scene_preview(painter, preview_rect)
            else:
                self._draw_source_preview(painter, preview_rect)

        if self.model.locked:
            border = QColor(163, 86, 85)
        elif self.isSelected():
            border = QColor(162, 89, 65)
        else:
            border = QColor(58, 51, 47)

        painter.setPen(QPen(border, 2 if self.isSelected() else 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(self._rect.adjusted(1, 1, -1, -1), 10, 10)

        title_color = QColor(240, 239, 238) if self.isSelected() else QColor(197, 186, 184)
        type_color = QColor(139, 133, 130)
        painter.setPen(title_color)
        font = painter.font()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(QRectF(10, 8, self._rect.width() - 20, 20), Qt.AlignmentFlag.AlignLeft, self.model.name[:24])
        font.setPointSize(8)
        font.setBold(False)
        painter.setFont(font)
        if self.model.type == "scene":
            status = str(self.model.metadata.get("director_status") or "普通场景")
            painter.setPen(QColor(125, 158, 122) if status == "当前导播" else type_color)
            painter.drawText(QRectF(10, 28, self._rect.width() - 20, 16), Qt.AlignmentFlag.AlignLeft, status)
        else:
            painter.setPen(type_color)
            painter.drawText(QRectF(10, 28, self._rect.width() - 20, 16), Qt.AlignmentFlag.AlignLeft, self.model.type.upper())
            if self.model.parent_item_id:
                painter.setPen(QColor(125, 158, 122))
                painter.drawText(
                    QRectF(10, 46, self._rect.width() - 20, 16),
                    Qt.AlignmentFlag.AlignLeft,
                    f"归入：{self.model.metadata.get('parent_scene_name', '场景')}",
                )

        if self.isSelected() and not self.model.locked:
            handle = self._handle_rect()
            painter.setBrush(border)
            painter.setPen(QPen(border.darker(130), 1))
            painter.drawRect(handle.adjusted(1, 1, -1, -1))


class InfiniteCanvasView(QGraphicsView):
    """无限画布视图，负责缩放、平移、选择、拖拽和基础命中。"""

    item_drop_requested = pyqtSignal(object, object)
    selection_models_changed = pyqtSignal(object)
    document_changed = pyqtSignal()
    zoom_changed = pyqtSignal(float)
    undo_requested = pyqtSignal()
    redo_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._scene = QGraphicsScene(self)
        self._renderer = CanvasRenderer()
        self._selection_manager = CanvasSelectionManager()
        self._transform = CanvasTransformController()
        self._document = CanvasDocument()
        self._items: dict[str, CanvasGraphicsItem] = {}
        self._clipboard: list[dict[str, Any]] = []
        self._space_pressed = False
        self._panning = False
        self._pan_start = QPoint()
        self._zoom = 1.0
        self._low_performance_mode = False
        self._dirty_block = False
        self.setScene(self._scene)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
        self.setAcceptDrops(True)
        self._scene.selectionChanged.connect(self._emit_selection)

    def set_low_performance_mode(self, enabled: bool) -> None:
        self._low_performance_mode = bool(enabled)
        self.setRenderHint(QPainter.RenderHint.Antialiasing, not enabled)
        self.viewport().update()

    def set_document(self, document: CanvasDocument) -> None:
        self._dirty_block = True
        self._document = document.clone()
        self._items.clear()
        self._scene.clear()
        self._scene.setSceneRect(-4000, -3000, 8000, 6000)
        for item in sorted(self._document.items, key=lambda item: item.z_index):
            self._add_item_model(item, silent=True)
        for scene_item in self._scene_items():
            self._sync_scene_children(scene_item.model.item_id)
        self._selection_manager.clear()
        self._dirty_block = False
        self._emit_selection()
        self.viewport().update()

    def document(self) -> CanvasDocument:
        doc = self._document.clone()
        doc.items = [item.to_model() for item in sorted(self._items.values(), key=lambda it: it.zValue())]
        doc.touch()
        return doc

    def set_zoom(self, zoom: float) -> None:
        zoom = self._transform.zoom_clamp(zoom)
        factor = zoom / self._zoom
        self._zoom = zoom
        self.scale(factor, factor)
        self.zoom_changed.emit(self._zoom)

    def zoom_percent(self) -> int:
        return max(1, int(round(self._zoom * 100)))

    def fit_to_document(self) -> None:
        if self._document.output_frame.width <= 0 or self._document.output_frame.height <= 0:
            return
        rect = QRectF(
            self._document.output_frame.x,
            self._document.output_frame.y,
            self._document.output_frame.width,
            self._document.output_frame.height,
        )
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = 1.0
        self.zoom_changed.emit(self._zoom)

    def fit_to_selection(self) -> None:
        selected = self._scene.selectedItems()
        if not selected:
            return
        rect = QRectF()
        for item in selected:
            rect = rect.united(item.sceneBoundingRect())
        self.fitInView(rect.adjusted(-40, -40, 40, 40), Qt.AspectRatioMode.KeepAspectRatio)
        self._zoom = 1.0
        self.zoom_changed.emit(self._zoom)

    def center_to_document(self) -> None:
        self.centerOn(QPointF(self._document.output_frame.width / 2, self._document.output_frame.height / 2))

    def _emit_selection(self) -> None:
        models = [self._items[item.model.item_id].to_model() for item in self._scene.selectedItems() if isinstance(item, CanvasGraphicsItem)]
        self._selection_manager.set_selected_ids([model.item_id for model in models])
        self.selection_models_changed.emit(models)

    def selected_models(self) -> list[CanvasItemModel]:
        return [item.to_model() for item in self._scene.selectedItems() if isinstance(item, CanvasGraphicsItem)]

    def select_item(self, item_id: str | None) -> None:
        for item in self._items.values():
            item.setSelected(item.model.item_id == item_id)

    def center_on_item(self, item_id: str | None) -> bool:
        item = self._items.get(str(item_id or ""))
        if item is None:
            return False
        self._scene.clearSelection()
        item.setSelected(True)
        self.centerOn(item.sceneBoundingRect().center())
        self.ensureVisible(item.sceneBoundingRect().adjusted(-40, -40, 40, 40))
        return True

    def item_model(self, item_id: str | None) -> CanvasItemModel | None:
        item = self._items.get(str(item_id or ""))
        return None if item is None else item.to_model()

    def find_canvas_items(self, *, source_ref: str | None = None, scene_ref: str | None = None, item_type: str | None = None) -> list[CanvasGraphicsItem]:
        found: list[CanvasGraphicsItem] = []
        for item in self._items.values():
            model = item.to_model()
            if source_ref is not None and model.source_ref != source_ref:
                continue
            if scene_ref is not None and model.scene_ref != scene_ref:
                continue
            if item_type is not None and model.type != item_type:
                continue
            found.append(item)
        return found

    def remove_item_by_id(self, item_id: str | None) -> bool:
        item = self._items.pop(str(item_id or ""), None)
        if item is None:
            return False
        for child in self._items.values():
            if child.model.parent_item_id == item.model.item_id:
                self._store_child_local_geometry(child, None)
        self._scene.removeItem(item)
        self.document_changed.emit()
        self._emit_selection()
        return True

    def scene_models(self) -> list[CanvasItemModel]:
        return [item.to_model() for item in self._items.values() if item.model.type == "scene"]

    def sync_scene_snapshots(self, scene_lookup: Callable[[str], Scene | None], active_scene_id: str | None = None) -> None:
        """只同步场景框的快照，不重建整个画布，避免覆盖用户正在编辑的对象。"""
        updated = False
        for item in self._items.values():
            model = item.to_model()
            if model.type != "scene" or not model.scene_ref:
                continue
            scene = scene_lookup(model.scene_ref)
            if scene is None:
                continue
            model.metadata = dict(model.metadata or {})
            model.metadata["scene_snapshot"] = SceneCanvasAdapter._scene_to_dict(scene)
            model.metadata["scene_canvas_width"] = max(1, self._document.output_frame.width)
            model.metadata["scene_canvas_height"] = max(1, self._document.output_frame.height)
            model.metadata["director_status"] = "当前导播" if scene.id == active_scene_id else ("占位场景" if scene.is_placeholder else "普通场景")
            item.apply_model(model)
            updated = True
        if updated:
            self.viewport().update()

    def _scene_items(self) -> list[CanvasGraphicsItem]:
        return [item for item in self._items.values() if item.model.type == "scene" and item.isVisible()]

    def _scene_item_at(self, point: QPointF, exclude_item_id: str | None = None) -> CanvasGraphicsItem | None:
        candidates = []
        for item in self._scene_items():
            if exclude_item_id is not None and item.model.item_id == exclude_item_id:
                continue
            if item.sceneBoundingRect().contains(point):
                candidates.append(item)
        if not candidates:
            return None
        return max(candidates, key=lambda it: it.zValue())

    def _scene_base_size(self, item: CanvasGraphicsItem) -> tuple[int, int]:
        model = item.to_model()
        source_w = int(model.metadata.get("scene_canvas_width") or model.width or self._document.output_frame.width or 1280)
        source_h = int(model.metadata.get("scene_canvas_height") or model.height or self._document.output_frame.height or 720)
        return max(1, source_w), max(1, source_h)

    def _store_child_local_geometry(self, child: CanvasGraphicsItem, parent: CanvasGraphicsItem | None) -> None:
        model = child.to_model()
        if parent is None:
            old_scene_ref = model.scene_ref
            model.parent_item_id = None
            model.scene_ref = None
            model.metadata = dict(model.metadata or {})
            if old_scene_ref and (model.source_ref or model.item_id):
                model.metadata["removed_from_scene_ref"] = old_scene_ref
            model.metadata.pop("local_geometry", None)
            model.metadata.pop("parent_scene_name", None)
            child.apply_model(model)
            return

        parent_model = parent.to_model()
        base_w, base_h = self._scene_base_size(parent)
        local_x = (model.x - parent_model.x) * base_w / max(1, parent_model.width)
        local_y = (model.y - parent_model.y) * base_h / max(1, parent_model.height)
        local_w = model.width * base_w / max(1, parent_model.width)
        local_h = model.height * base_h / max(1, parent_model.height)
        model.parent_item_id = parent_model.item_id
        model.scene_ref = parent_model.scene_ref
        model.metadata = dict(model.metadata or {})
        model.metadata.pop("removed_from_scene_ref", None)
        model.metadata["parent_scene_name"] = parent_model.name
        model.metadata["local_geometry"] = {
            "x": int(round(local_x)),
            "y": int(round(local_y)),
            "w": max(1, int(round(local_w))),
            "h": max(1, int(round(local_h))),
            "base_w": base_w,
            "base_h": base_h,
        }
        child.apply_model(model)

    def _restore_child_from_parent(self, child: CanvasGraphicsItem, parent: CanvasGraphicsItem) -> None:
        model = child.to_model()
        local = dict(model.metadata.get("local_geometry") or {})
        if not local:
            return
        parent_model = parent.to_model()
        base_w, base_h = self._scene_base_size(parent)
        model.x = int(round(parent_model.x + local.get("x", 0) * parent_model.width / max(1, base_w)))
        model.y = int(round(parent_model.y + local.get("y", 0) * parent_model.height / max(1, base_h)))
        model.width = max(1, int(round(local.get("w", model.width) * parent_model.width / max(1, base_w))))
        model.height = max(1, int(round(local.get("h", model.height) * parent_model.height / max(1, base_h))))
        child.apply_model(model)

    def _sync_scene_children(self, parent_item_id: str) -> None:
        parent = self._items.get(parent_item_id)
        if parent is None or parent.model.type != "scene":
            return
        for item in self._items.values():
            if item.model.parent_item_id == parent_item_id and item.model.type != "scene":
                self._restore_child_from_parent(item, parent)

    def _refresh_parent_assignment(self, item: CanvasGraphicsItem) -> None:
        if item.model.type == "scene":
            self._sync_scene_children(item.model.item_id)
            return
        parent = self._scene_item_at(item.sceneBoundingRect().center(), exclude_item_id=item.model.item_id)
        if parent is None:
            self._store_child_local_geometry(item, None)
            return
        self._store_child_local_geometry(item, parent)

    def _add_item_model(self, model: CanvasItemModel, silent: bool = False) -> CanvasGraphicsItem:
        item = CanvasGraphicsItem(model)
        item.geometry_committed.connect(self._on_item_committed)
        self._scene.addItem(item)
        self._items[item.model.item_id] = item
        if not silent:
            self._refresh_parent_assignment(item)
        if not silent:
            self.document_changed.emit()
        return item

    def add_item_model(self, model: CanvasItemModel) -> CanvasGraphicsItem:
        return self._add_item_model(model, silent=False)

    def _on_item_committed(self, item_id: str = "") -> None:
        if not self._dirty_block:
            item = self._items.get(item_id)
            if item is not None:
                self._refresh_parent_assignment(item)
            self.document_changed.emit()

    def update_selected_items(self, updater: Callable[[CanvasItemModel], CanvasItemModel]) -> None:
        for item in self._scene.selectedItems():
            if not isinstance(item, CanvasGraphicsItem):
                continue
            new_model = updater(item.to_model())
            item.apply_model(new_model)
            self._refresh_parent_assignment(item)
        self.document_changed.emit()
        self._emit_selection()

    def delete_selected_items(self) -> None:
        selected = [item for item in self._scene.selectedItems() if isinstance(item, CanvasGraphicsItem)]
        if not selected:
            return
        for item in selected:
            self._items.pop(item.model.item_id, None)
            self._scene.removeItem(item)
        self.document_changed.emit()
        self._emit_selection()

    def duplicate_selected_items(self) -> None:
        selected = [item.to_model() for item in self._scene.selectedItems() if isinstance(item, CanvasGraphicsItem)]
        if not selected:
            return
        self._scene.clearSelection()
        for model in selected:
            model.item_id = new_id("canvas_item")
            model.x += 24
            model.y += 24
            model.parent_item_id = None
            model.metadata = dict(model.metadata or {})
            model.metadata.pop("local_geometry", None)
            model.metadata.pop("parent_scene_name", None)
            self.add_item_model(model)
        self.document_changed.emit()

    def copy_selection(self) -> None:
        self._clipboard = [item.to_model().to_dict() for item in self._scene.selectedItems() if isinstance(item, CanvasGraphicsItem)]

    def paste_selection(self) -> None:
        if not self._clipboard:
            return
        self._scene.clearSelection()
        for data in self._clipboard:
            data = dict(data)
            data["item_id"] = new_id("canvas_item")
            data["x"] = int(data.get("x", 0)) + 24
            data["y"] = int(data.get("y", 0)) + 24
            data["parent_item_id"] = None
            metadata = dict(data.get("metadata") or {})
            metadata.pop("local_geometry", None)
            metadata.pop("parent_scene_name", None)
            data["metadata"] = metadata
            self.add_item_model(CanvasItemModel.from_dict(data))
        self.document_changed.emit()

    def _move_selected_z(self, delta: int) -> None:
        selected = [item for item in self._scene.selectedItems() if isinstance(item, CanvasGraphicsItem)]
        if not selected:
            return
        for item in selected:
            item.setZValue(item.zValue() + delta)
            item.model.z_index = int(round(item.zValue()))
        self.document_changed.emit()

    def bring_to_front(self) -> None:
        max_z = max([item.zValue() for item in self._items.values()], default=0.0) + 1.0
        for item in self._scene.selectedItems():
            if isinstance(item, CanvasGraphicsItem):
                item.setZValue(max_z)
                item.model.z_index = int(round(max_z))
                max_z += 1.0
        self.document_changed.emit()

    def send_to_back(self) -> None:
        min_z = min([item.zValue() for item in self._items.values()], default=0.0) - 1.0
        for item in self._scene.selectedItems():
            if isinstance(item, CanvasGraphicsItem):
                item.setZValue(min_z)
                item.model.z_index = int(round(min_z))
                min_z -= 1.0
        self.document_changed.emit()

    def move_up_one(self) -> None:
        self._move_selected_z(1)

    def move_down_one(self) -> None:
        self._move_selected_z(-1)

    def align_selected(self, mode: str) -> None:
        items = [item for item in self._scene.selectedItems() if isinstance(item, CanvasGraphicsItem)]
        if len(items) < 2:
            return
        xs = [item.pos().x() for item in items]
        ys = [item.pos().y() for item in items]
        if mode == "left":
            target = min(xs)
            for item in items:
                item.setPos(target, item.pos().y())
        elif mode == "right":
            target = max(item.pos().x() + item.model.width for item in items)
            for item in items:
                item.setPos(target - item.model.width, item.pos().y())
        elif mode == "center":
            target = sum(xs) / len(xs)
            for item in items:
                item.setPos(target - item.model.width / 2, item.pos().y())
        elif mode == "top":
            target = min(ys)
            for item in items:
                item.setPos(item.pos().x(), target)
        elif mode == "middle":
            target = sum(ys) / len(ys)
            for item in items:
                item.setPos(item.pos().x(), target - item.model.height / 2)
        elif mode == "bottom":
            target = max(item.pos().y() + item.model.height for item in items)
            for item in items:
                item.setPos(item.pos().x(), target - item.model.height)
        self.document_changed.emit()

    def distribute_selected(self, axis: str) -> None:
        items = [item for item in self._scene.selectedItems() if isinstance(item, CanvasGraphicsItem)]
        if len(items) < 3:
            return
        if axis == "horizontal":
            items.sort(key=lambda item: item.pos().x())
            left = items[0].pos().x()
            right = items[-1].pos().x()
            span = right - left
            gap = span / max(1, len(items) - 1)
            for index, item in enumerate(items):
                item.setPos(left + gap * index, item.pos().y())
        elif axis == "vertical":
            items.sort(key=lambda item: item.pos().y())
            top = items[0].pos().y()
            bottom = items[-1].pos().y()
            span = bottom - top
            gap = span / max(1, len(items) - 1)
            for index, item in enumerate(items):
                item.setPos(item.pos().x(), top + gap * index)
        self.document_changed.emit()

    def group_selected(self) -> None:
        items = [item for item in self._scene.selectedItems() if isinstance(item, CanvasGraphicsItem)]
        if len(items) < 2:
            return
        group_id = new_id("canvas_group")
        for item in items:
            model = item.to_model()
            model.metadata = dict(model.metadata or {})
            model.metadata["group_id"] = group_id
            item.apply_model(model)
        self._document.groups = [group for group in self._document.groups if group.group_id != group_id]
        self._document.groups.append(CanvasGroupModel(group_id=group_id, name="组合", item_ids=[item.model.item_id for item in items]))
        self.document_changed.emit()

    def ungroup_selected(self) -> None:
        items = [item for item in self._scene.selectedItems() if isinstance(item, CanvasGraphicsItem)]
        if not items:
            return
        for item in items:
            model = item.to_model()
            model.metadata = dict(model.metadata or {})
            model.metadata.pop("group_id", None)
            item.apply_model(model)
        self._document.groups = [group for group in self._document.groups if not set(group.item_ids).issuperset({item.model.item_id for item in items})]
        self.document_changed.emit()

    def change_selected_opacity(self, opacity: float) -> None:
        opacity = max(0.0, min(1.0, float(opacity)))
        for item in self._scene.selectedItems():
            if isinstance(item, CanvasGraphicsItem):
                item.setOpacity(opacity)
                item.model.opacity = opacity
        self.document_changed.emit()

    def change_selected_rotation(self, rotation: float) -> None:
        for item in self._scene.selectedItems():
            if isinstance(item, CanvasGraphicsItem):
                item.setRotation(rotation)
                item.model.rotation = float(rotation)
        self.document_changed.emit()

    def set_canvas_name(self, name: str) -> None:
        self._document.name = str(name or "场景画布")

    def drawBackground(self, painter: QPainter, rect: QRectF) -> None:  # noqa: N802
        self._renderer.paint_background(
            painter,
            rect,
            self._zoom,
            self._low_performance_mode,
            (
                self._document.output_frame.x,
                self._document.output_frame.y,
                self._document.output_frame.width,
                self._document.output_frame.height,
            ),
        )

    def wheelEvent(self, event):  # noqa: N802
        angle = event.angleDelta().y()
        if angle == 0:
            return
        factor = 1.15 if angle > 0 else 1 / 1.15
        new_zoom = self._transform.zoom_clamp(self._zoom * factor)
        factor = new_zoom / self._zoom
        self._zoom = new_zoom
        self.scale(factor, factor)
        self.zoom_changed.emit(self._zoom)
        event.accept()

    def keyPressEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Space:
            self._space_pressed = True
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        ctrl = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
        if event.key() == Qt.Key.Key_Delete:
            self.delete_selected_items()
            event.accept()
            return
        if ctrl and event.key() == Qt.Key.Key_C:
            self.copy_selection()
            event.accept()
            return
        if ctrl and event.key() == Qt.Key.Key_V:
            self.paste_selection()
            event.accept()
            return
        if ctrl and event.key() == Qt.Key.Key_Z:
            self.undo_requested.emit()
            event.accept()
            return
        if ctrl and event.key() == Qt.Key.Key_Y:
            self.redo_requested.emit()
            event.accept()
            return
        if ctrl and event.key() == Qt.Key.Key_G:
            if event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                self.ungroup_selected()
            else:
                self.group_selected()
            event.accept()
            return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):  # noqa: N802
        if event.key() == Qt.Key.Key_Space:
            self._space_pressed = False
            self.unsetCursor()
            event.accept()
            return
        super().keyReleaseEvent(event)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.MiddleButton or (event.button() == Qt.MouseButton.LeftButton and self._space_pressed):
            self._panning = True
            self._pan_start = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if self._panning:
            delta = event.position().toPoint() - self._pan_start
            self._pan_start = event.position().toPoint()
            self.horizontalScrollBar().setValue(self.horizontalScrollBar().value() - delta.x())
            self.verticalScrollBar().setValue(self.verticalScrollBar().value() - delta.y())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):  # noqa: N802
        if self._panning:
            self._panning = False
            self.unsetCursor()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def dragEnterEvent(self, event):  # noqa: N802
        if event.mimeData().hasFormat("application/x-nsy-canvas-item") or event.mimeData().hasFormat("application/x-nsy-ai-result"):
            event.acceptProposedAction()
            return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):  # noqa: N802
        if event.mimeData().hasFormat("application/x-nsy-canvas-item") or event.mimeData().hasFormat("application/x-nsy-ai-result"):
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):  # noqa: N802
        mime_key = ""
        if event.mimeData().hasFormat("application/x-nsy-canvas-item"):
            mime_key = "application/x-nsy-canvas-item"
        elif event.mimeData().hasFormat("application/x-nsy-ai-result"):
            mime_key = "application/x-nsy-ai-result"
        if not mime_key:
            return super().dropEvent(event)
        try:
            payload = json.loads(bytes(event.mimeData().data(mime_key)).decode("utf-8"))
        except Exception:
            return
        self.item_drop_requested.emit(payload, self.mapToScene(event.position().toPoint()))
        event.acceptProposedAction()

    def contextMenuEvent(self, event):  # noqa: N802
        item = self.itemAt(event.pos())
        menu = QMenu(self)
        fit_doc = menu.addAction("适配画布")
        fit_sel = menu.addAction("适配选中对象")
        menu.addSeparator()
        dup = menu.addAction("复制对象")
        delete = menu.addAction("删除对象")
        menu.addSeparator()
        front = menu.addAction("置顶")
        back = menu.addAction("置底")
        up = menu.addAction("上移一层")
        down = menu.addAction("下移一层")
        menu.addSeparator()
        lock = menu.addAction("锁定/解锁")
        hide = menu.addAction("显示/隐藏")
        if item is None or not isinstance(item, CanvasGraphicsItem):
            action = menu.exec(event.globalPos())
            if action == fit_doc:
                self.fit_to_document()
            elif action == fit_sel:
                self.fit_to_selection()
            return
        if not item.isSelected():
            self._scene.clearSelection()
            item.setSelected(True)
        action = menu.exec(event.globalPos())
        if action == fit_doc:
            self.fit_to_document()
        elif action == fit_sel:
            self.fit_to_selection()
        elif action == dup:
            self.duplicate_selected_items()
        elif action == delete:
            self.delete_selected_items()
        elif action == front:
            self.bring_to_front()
        elif action == back:
            self.send_to_back()
        elif action == up:
            self.move_up_one()
        elif action == down:
            self.move_down_one()
        elif action == lock:
            for selected in self.selectedItems():
                if isinstance(selected, CanvasGraphicsItem):
                    model = selected.to_model()
                    model.locked = not model.locked
                    selected.apply_model(model)
            self.document_changed.emit()
        elif action == hide:
            for selected in self.selectedItems():
                if isinstance(selected, CanvasGraphicsItem):
                    model = selected.to_model()
                    model.visible = not model.visible
                    selected.apply_model(model)
            self.document_changed.emit()


class CanvasResourceListWidget(QListWidget):
    """可拖拽的资源列表。"""

    resource_activated = pyqtSignal(object)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setDefaultDropAction(Qt.DropAction.CopyAction)
        self.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)

    def startDrag(self, supportedActions):  # noqa: N802
        item = self.currentItem()
        if item is None:
            return
        payload = item.data(Qt.ItemDataRole.UserRole)
        if not payload:
            return
        mime = QMimeData()
        mime.setData("application/x-nsy-canvas-item", json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)

    def mouseDoubleClickEvent(self, event):  # noqa: N802
        item = self.itemAt(event.position().toPoint())
        if item is not None:
            payload = item.data(Qt.ItemDataRole.UserRole)
            if payload:
                self.resource_activated.emit(payload)
        super().mouseDoubleClickEvent(event)


class CanvasPropertyPanel(QWidget):
    """右侧属性面板。单选显示参数，多选显示批量操作。"""

    apply_requested = pyqtSignal()
    save_new_scene_requested = pyqtSignal()
    export_preview_requested = pyqtSignal()
    export_program_requested = pyqtSignal()
    import_current_scene_requested = pyqtSignal()
    delete_requested = pyqtSignal()
    duplicate_requested = pyqtSignal()
    fit_selection_requested = pyqtSignal()
    fit_document_requested = pyqtSignal()
    align_requested = pyqtSignal(str)
    distribute_requested = pyqtSignal(str)
    group_requested = pyqtSignal()
    ungroup_requested = pyqtSignal()
    bring_front_requested = pyqtSignal()
    send_back_requested = pyqtSignal()
    move_up_requested = pyqtSignal()
    move_down_requested = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._selected_count = 0
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)

        self.doc_info = QLabel("画布信息")
        self.doc_info.setObjectName("CanvasInfo")
        self.doc_info.setWordWrap(True)
        root.addWidget(self.doc_info)

        self.selection_info = QLabel("未选中对象")
        self.selection_info.setObjectName("CanvasSelectionInfo")
        self.selection_info.setWordWrap(True)
        root.addWidget(self.selection_info)

        self.single_box = QFrame()
        self.single_box.setObjectName("CanvasSingleBox")
        single_layout = QVBoxLayout(self.single_box)
        single_layout.setContentsMargins(10, 10, 10, 10)
        single_layout.setSpacing(8)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.name_edit = QLineEdit()
        self.type_label = QLabel("source")
        self.x_spin = QSpinBox()
        self.y_spin = QSpinBox()
        self.w_spin = QSpinBox()
        self.h_spin = QSpinBox()
        self.rotation_spin = QDoubleSpinBox()
        self.rotation_spin.setRange(-360.0, 360.0)
        self.rotation_spin.setDecimals(1)
        self.opacity_spin = QDoubleSpinBox()
        self.opacity_spin.setRange(0.0, 1.0)
        self.opacity_spin.setDecimals(2)
        self.opacity_spin.setSingleStep(0.05)
        self.z_spin = QSpinBox()
        self.visible_check = QCheckBox("显示")
        self.visible_check.setChecked(True)
        self.locked_check = QCheckBox("锁定")
        self.crop_left = QSpinBox()
        self.crop_top = QSpinBox()
        self.crop_right = QSpinBox()
        self.crop_bottom = QSpinBox()
        for spin in (self.x_spin, self.y_spin, self.w_spin, self.h_spin):
            spin.setRange(-100000, 100000)
        for spin in (self.crop_left, self.crop_top, self.crop_right, self.crop_bottom):
            spin.setRange(0, 10000)
        self.z_spin.setRange(-10000, 10000)
        form.addRow("名称", self.name_edit)
        form.addRow("类型", self.type_label)
        form.addRow("位置 X", self.x_spin)
        form.addRow("位置 Y", self.y_spin)
        form.addRow("宽度", self.w_spin)
        form.addRow("高度", self.h_spin)
        form.addRow("旋转", self.rotation_spin)
        form.addRow("透明度", self.opacity_spin)
        form.addRow("层级", self.z_spin)
        form.addRow("裁剪 左", self.crop_left)
        form.addRow("裁剪 上", self.crop_top)
        form.addRow("裁剪 右", self.crop_right)
        form.addRow("裁剪 下", self.crop_bottom)
        single_layout.addLayout(form)

        audio_box = QFrame()
        audio_box.setObjectName("CanvasAudioBox")
        audio_layout = QVBoxLayout(audio_box)
        audio_layout.setContentsMargins(10, 10, 10, 10)
        audio_layout.setSpacing(6)
        audio_layout.addWidget(QLabel("音频"))
        audio_form = QFormLayout()
        audio_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.volume_spin = QDoubleSpinBox()
        self.amplitude_spin = QDoubleSpinBox()
        self.low_gain_spin = QDoubleSpinBox()
        self.mid_gain_spin = QDoubleSpinBox()
        self.high_gain_spin = QDoubleSpinBox()
        for spin in (self.volume_spin, self.amplitude_spin, self.low_gain_spin, self.mid_gain_spin, self.high_gain_spin):
            spin.setRange(0.0, 4.0)
            spin.setDecimals(2)
            spin.setSingleStep(0.05)
        self.muted_check = QCheckBox("静音")
        audio_form.addRow("音量", self.volume_spin)
        audio_form.addRow("幅度", self.amplitude_spin)
        audio_form.addRow("低频", self.low_gain_spin)
        audio_form.addRow("中频", self.mid_gain_spin)
        audio_form.addRow("高频", self.high_gain_spin)
        audio_form.addRow("", self.muted_check)
        audio_layout.addLayout(audio_form)
        single_layout.addWidget(audio_box)

        filter_box = QFrame()
        filter_box.setObjectName("CanvasFilterBox")
        filter_layout = QVBoxLayout(filter_box)
        filter_layout.setContentsMargins(10, 10, 10, 10)
        filter_layout.setSpacing(6)
        filter_layout.addWidget(QLabel("滤镜"))
        filter_form = QFormLayout()
        filter_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.saturation_spin = QDoubleSpinBox()
        self.saturation_spin.setRange(0.0, 3.0)
        self.saturation_spin.setDecimals(2)
        self.saturation_spin.setSingleStep(0.05)
        self.contrast_spin = QDoubleSpinBox()
        self.contrast_spin.setRange(0.0, 3.0)
        self.contrast_spin.setDecimals(2)
        self.contrast_spin.setSingleStep(0.05)
        self.color_temp_spin = QSpinBox()
        self.color_temp_spin.setRange(-100, 100)
        self.mosaic_spin = QSpinBox()
        self.mosaic_spin.setRange(0, 100)
        self.onnx_style_combo = QComboBox()
        self.onnx_style_combo.addItem("不使用 ONNX 风格", "none")
        self.onnx_style_combo.addItem("卡通化", "cartoon")
        self.onnx_style_combo.addItem("莫奈风格", "monet")
        self.onnx_style_combo.addItem("梵高风格", "vangogh")
        filter_form.addRow("饱和度", self.saturation_spin)
        filter_form.addRow("对比度", self.contrast_spin)
        filter_form.addRow("色温", self.color_temp_spin)
        filter_form.addRow("马赛克", self.mosaic_spin)
        filter_form.addRow("ONNX 风格", self.onnx_style_combo)
        filter_layout.addLayout(filter_form)
        single_layout.addWidget(filter_box)

        ai_box = QFrame()
        ai_box.setObjectName("CanvasABox")
        ai_layout = QVBoxLayout(ai_box)
        ai_layout.setContentsMargins(10, 10, 10, 10)
        ai_layout.setSpacing(6)
        ai_layout.addWidget(QLabel("智能增强"))
        ai_form = QFormLayout()
        ai_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.face_enabled_check = QCheckBox("启用人脸识别")
        self.face_effect_combo = QComboBox()
        self.face_effect_combo.addItem("狗鼻子", "dog_nose")
        self.face_effect_combo.addItem("猫耳", "cat_ears")
        self.face_effect_combo.addItem("卡通眼睛", "cartoon_eyes")
        self.face_scale_spin = QSpinBox()
        self.face_scale_spin.setRange(50, 200)
        self.face_smoothing_spin = QSpinBox()
        self.face_smoothing_spin.setRange(0, 100)
        self.virtual_bg_enabled_check = QCheckBox("启用虚拟背景")
        self.virtual_bg_mode_combo = QComboBox()
        self.virtual_bg_mode_combo.addItem("背景图", "image")
        self.virtual_bg_mode_combo.addItem("背景模糊", "blur")
        self.virtual_bg_blur_spin = QSpinBox()
        self.virtual_bg_blur_spin.setRange(0, 100)
        ai_form.addRow("", self.face_enabled_check)
        ai_form.addRow("贴纸类型", self.face_effect_combo)
        ai_form.addRow("缩放系数", self.face_scale_spin)
        ai_form.addRow("跟踪平滑", self.face_smoothing_spin)
        ai_form.addRow("", self.virtual_bg_enabled_check)
        ai_form.addRow("背景模式", self.virtual_bg_mode_combo)
        ai_form.addRow("模糊强度", self.virtual_bg_blur_spin)
        ai_layout.addLayout(ai_form)
        single_layout.addWidget(ai_box)

        state_row = QHBoxLayout()
        state_row.addWidget(self.visible_check)
        state_row.addWidget(self.locked_check)
        state_row.addStretch(1)
        single_layout.addLayout(state_row)

        action_row = QHBoxLayout()
        self.apply_btn = QPushButton("应用修改")
        self.apply_btn.setProperty("role", "primary")
        self.duplicate_btn = QPushButton("复制")
        self.delete_btn = QPushButton("删除")
        self.delete_btn.setProperty("role", "danger")
        action_row.addWidget(self.apply_btn)
        action_row.addWidget(self.duplicate_btn)
        action_row.addWidget(self.delete_btn)
        single_layout.addLayout(action_row)

        z_row = QHBoxLayout()
        self.front_btn = QPushButton("置顶")
        self.back_btn = QPushButton("置底")
        self.up_btn = QPushButton("上移")
        self.down_btn = QPushButton("下移")
        for btn in (self.front_btn, self.back_btn, self.up_btn, self.down_btn):
            z_row.addWidget(btn)
        single_layout.addLayout(z_row)

        self.multi_box = QFrame()
        self.multi_box.setObjectName("CanvasMultiBox")
        multi_layout = QVBoxLayout(self.multi_box)
        multi_layout.setContentsMargins(10, 10, 10, 10)
        multi_layout.setSpacing(8)
        self.multi_label = QLabel("已选中 0 个对象")
        self.multi_label.setWordWrap(True)
        multi_layout.addWidget(self.multi_label)

        align_row = QHBoxLayout()
        self.align_left_btn = QPushButton("左对齐")
        self.align_center_btn = QPushButton("居中对齐")
        self.align_right_btn = QPushButton("右对齐")
        align_row.addWidget(self.align_left_btn)
        align_row.addWidget(self.align_center_btn)
        align_row.addWidget(self.align_right_btn)
        multi_layout.addLayout(align_row)

        distribute_row = QHBoxLayout()
        self.distribute_h_btn = QPushButton("横向分布")
        self.distribute_v_btn = QPushButton("纵向分布")
        self.group_btn = QPushButton("组合")
        self.ungroup_btn = QPushButton("取消组合")
        distribute_row.addWidget(self.distribute_h_btn)
        distribute_row.addWidget(self.distribute_v_btn)
        distribute_row.addWidget(self.group_btn)
        distribute_row.addWidget(self.ungroup_btn)
        multi_layout.addLayout(distribute_row)

        bulk_row = QHBoxLayout()
        self.bulk_lock_btn = QPushButton("批量锁定")
        self.bulk_hide_btn = QPushButton("批量隐藏")
        bulk_row.addWidget(self.bulk_lock_btn)
        bulk_row.addWidget(self.bulk_hide_btn)
        multi_layout.addLayout(bulk_row)

        canvas_ops = QFrame()
        canvas_ops.setObjectName("CanvasOpsBox")
        ops_layout = QVBoxLayout(canvas_ops)
        ops_layout.setContentsMargins(10, 10, 10, 10)
        ops_layout.setSpacing(8)
        self.canvas_ops_title = QLabel("画布与输出")
        ops_layout.addWidget(self.canvas_ops_title)
        target_form = QFormLayout()
        target_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.scene_target_combo = QComboBox()
        target_form.addRow("写入场景", self.scene_target_combo)
        ops_layout.addLayout(target_form)
        global_row = QHBoxLayout()
        self.import_scene_btn = QPushButton("导入当前场景")
        self.apply_scene_btn = QPushButton("应用到当前场景")
        self.save_new_scene_btn = QPushButton("保存为新场景")
        self.export_preview_btn = QPushButton("输出到预览")
        self.export_program_btn = QPushButton("输出到节目")
        self.export_program_btn.setProperty("role", "primary")
        global_row.addWidget(self.import_scene_btn)
        global_row.addWidget(self.apply_scene_btn)
        global_row.addWidget(self.save_new_scene_btn)
        global_row.addWidget(self.export_preview_btn)
        global_row.addWidget(self.export_program_btn)
        ops_layout.addLayout(global_row)
        fit_row = QHBoxLayout()
        self.fit_doc_btn = QPushButton("适配画布")
        self.fit_sel_btn = QPushButton("适配选中")
        fit_row.addWidget(self.fit_doc_btn)
        fit_row.addWidget(self.fit_sel_btn)
        ops_layout.addLayout(fit_row)

        root.addWidget(self.single_box)
        root.addWidget(self.multi_box)
        root.addWidget(canvas_ops)
        self._canvas_ops_box = canvas_ops
        self.set_selection([])

    def set_document_info(self, document: CanvasDocument, zoom_percent: int, item_count: int) -> None:
        self.doc_info.setText(
            f"{document.name} | 输出 {document.output_frame.width}×{document.output_frame.height} | 缩放 {zoom_percent}% | 对象 {item_count}"
        )

    def set_selection(self, models: list[CanvasItemModel]) -> None:
        self._selected_count = len(models)
        if self._selected_count == 0:
            self.selection_info.setText("未选中对象")
            self.single_box.setVisible(False)
            self.multi_box.setVisible(False)
            self._canvas_ops_box.setVisible(True)
            return
        self._canvas_ops_box.setVisible(True)
        if self._selected_count == 1:
            model = models[0]
            self.selection_info.setText(f"已选中：{model.name}")
            self.single_box.setVisible(True)
            self.multi_box.setVisible(False)
            self.name_edit.setText(model.name)
            self.type_label.setText(model.type.upper())
            self.x_spin.setValue(int(model.x))
            self.y_spin.setValue(int(model.y))
            self.w_spin.setValue(int(model.width))
            self.h_spin.setValue(int(model.height))
            self.rotation_spin.setValue(float(model.rotation))
            self.opacity_spin.setValue(float(model.opacity))
            self.z_spin.setValue(int(model.z_index))
            crop = model.crop or {}
            self.crop_left.setValue(int(crop.get("left", 0)))
            self.crop_top.setValue(int(crop.get("top", 0)))
            self.crop_right.setValue(int(crop.get("right", 0)))
            self.crop_bottom.setValue(int(crop.get("bottom", 0)))
            self.visible_check.setChecked(bool(model.visible))
            self.locked_check.setChecked(bool(model.locked))
            audio = model.audio or {}
            filters = model.filters or {}
            chroma_key = model.chroma_key or {}
            self.volume_spin.setValue(float(audio.get("volume", 1.0)))
            self.amplitude_spin.setValue(float(audio.get("amplitude", 1.0)))
            self.low_gain_spin.setValue(float(audio.get("low_gain", 1.0)))
            self.mid_gain_spin.setValue(float(audio.get("mid_gain", 1.0)))
            self.high_gain_spin.setValue(float(audio.get("high_gain", 1.0)))
            self.muted_check.setChecked(bool(audio.get("muted", False)))
            self.saturation_spin.setValue(float(filters.get("saturation", 1.0)))
            self.contrast_spin.setValue(float(filters.get("contrast", 1.0)))
            self.color_temp_spin.setValue(int(filters.get("color_temp", 0)))
            self.mosaic_spin.setValue(int(filters.get("mosaic", 0)))
            self._set_combo_by_data(self.onnx_style_combo, str(filters.get("onnx_style", "none") or "none"))
            self.face_enabled_check.setChecked(bool(chroma_key.get("face_enabled", False)))
            self._set_combo_by_data(self.face_effect_combo, str(chroma_key.get("effect_type", "dog_nose") or "dog_nose"))
            self.face_scale_spin.setValue(int(chroma_key.get("face_scale_percent", 100)))
            self.face_smoothing_spin.setValue(int(chroma_key.get("face_smoothing", 60)))
            self.virtual_bg_enabled_check.setChecked(bool(chroma_key.get("virtual_bg_enabled", False)))
            self._set_combo_by_data(self.virtual_bg_mode_combo, str(chroma_key.get("virtual_bg_mode", "image") or "image"))
            self.virtual_bg_blur_spin.setValue(int(chroma_key.get("virtual_bg_blur_strength", 55)))
        else:
            self.selection_info.setText(f"已选中 {self._selected_count} 个对象")
            self.single_box.setVisible(False)
            self.multi_box.setVisible(True)
            self.multi_label.setText(f"已选中 {self._selected_count} 个对象，可执行对齐、分布、组合和批量状态控制。")

    def selected_count(self) -> int:
        return self._selected_count

    def set_scene_targets(self, scenes: list[Scene], current_scene_id: str | None) -> None:
        """刷新写回目标列表：既可覆盖已有场景，也可保存为新场景。"""
        self.scene_target_combo.blockSignals(True)
        self.scene_target_combo.clear()
        for scene in scenes:
            if scene.is_placeholder:
                continue
            self.scene_target_combo.addItem(scene.name, scene.id)
        self.scene_target_combo.addItem("新建场景", "__new__")
        if current_scene_id:
            index = self.scene_target_combo.findData(current_scene_id)
            if index >= 0:
                self.scene_target_combo.setCurrentIndex(index)
        self.scene_target_combo.blockSignals(False)

    def selected_scene_target(self) -> tuple[str | None, bool]:
        data = self.scene_target_combo.currentData()
        if data == "__new__":
            return None, True
        return (str(data) if data else None), False

    @staticmethod
    def _set_combo_by_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(index if index >= 0 else 0)

    def collect_single_update(self) -> dict[str, Any]:
        return {
            "name": self.name_edit.text().strip(),
            "x": int(self.x_spin.value()),
            "y": int(self.y_spin.value()),
            "width": max(1, int(self.w_spin.value())),
            "height": max(1, int(self.h_spin.value())),
            "rotation": float(self.rotation_spin.value()),
            "opacity": float(self.opacity_spin.value()),
            "z_index": int(self.z_spin.value()),
            "visible": bool(self.visible_check.isChecked()),
            "locked": bool(self.locked_check.isChecked()),
            "crop": {
                "left": int(self.crop_left.value()),
                "top": int(self.crop_top.value()),
                "right": int(self.crop_right.value()),
                "bottom": int(self.crop_bottom.value()),
            },
            "audio": {
                "volume": float(self.volume_spin.value()),
                "muted": bool(self.muted_check.isChecked()),
                "amplitude": float(self.amplitude_spin.value()),
                "low_gain": float(self.low_gain_spin.value()),
                "mid_gain": float(self.mid_gain_spin.value()),
                "high_gain": float(self.high_gain_spin.value()),
            },
            "filters": {
                "saturation": float(self.saturation_spin.value()),
                "contrast": float(self.contrast_spin.value()),
                "color_temp": int(self.color_temp_spin.value()),
                "mosaic": int(self.mosaic_spin.value()),
                "onnx_style": str(self.onnx_style_combo.currentData() or "none"),
            },
            "chroma_key": {
                "face_enabled": bool(self.face_enabled_check.isChecked()),
                "effect_type": str(self.face_effect_combo.currentData() or "dog_nose"),
                "face_scale_percent": int(self.face_scale_spin.value()),
                "face_smoothing": int(self.face_smoothing_spin.value()),
                "virtual_bg_enabled": bool(self.virtual_bg_enabled_check.isChecked()),
                "virtual_bg_mode": str(self.virtual_bg_mode_combo.currentData() or "image"),
                "virtual_bg_blur_strength": int(self.virtual_bg_blur_spin.value()),
            },
        }


class CanvasLayerEffectDialog(QDialog):
    """图层特效子面板，仅修改滤镜与智能增强参数。"""

    def __init__(self, model: CanvasItemModel, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("图层特效")
        self.setModal(True)
        self.setMinimumSize(480, 620)
        self.setStyleSheet(HAULIX_APP_QSS)
        self._model = model.clone()
        self._build_ui()
        self._load_model(self._model)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        title = QLabel("在这里调整当前图层的滤镜、ONNX 风格迁移、虚拟背景和 AR 参数")
        title.setWordWrap(True)
        root.addWidget(title)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.saturation_spin = QDoubleSpinBox()
        self.saturation_spin.setRange(0.0, 3.0)
        self.saturation_spin.setDecimals(2)
        self.saturation_spin.setSingleStep(0.05)

        self.contrast_spin = QDoubleSpinBox()
        self.contrast_spin.setRange(0.0, 3.0)
        self.contrast_spin.setDecimals(2)
        self.contrast_spin.setSingleStep(0.05)

        self.color_temp_spin = QSpinBox()
        self.color_temp_spin.setRange(-100, 100)

        self.mosaic_spin = QSpinBox()
        self.mosaic_spin.setRange(0, 100)

        self.onnx_style_combo = QComboBox()
        self.onnx_style_combo.addItem("不使用 ONNX 风格", "none")
        self.onnx_style_combo.addItem("卡通化", "cartoon")
        self.onnx_style_combo.addItem("莫奈风格", "monet")
        self.onnx_style_combo.addItem("梵高风格", "vangogh")

        self.face_enabled_check = QCheckBox("启用人脸识别")
        self.face_effect_combo = QComboBox()
        self.face_effect_combo.addItem("狗鼻子", "dog_nose")
        self.face_effect_combo.addItem("猫耳朵", "cat_ears")
        self.face_effect_combo.addItem("卡通眼睛", "cartoon_eyes")
        self.face_scale_spin = QSpinBox()
        self.face_scale_spin.setRange(50, 200)
        self.face_smoothing_spin = QSpinBox()
        self.face_smoothing_spin.setRange(0, 100)

        self.virtual_bg_enabled_check = QCheckBox("启用虚拟背景")
        self.virtual_bg_mode_combo = QComboBox()
        self.virtual_bg_mode_combo.addItem("背景图", "image")
        self.virtual_bg_mode_combo.addItem("背景模糊", "blur")
        self.virtual_bg_blur_spin = QSpinBox()
        self.virtual_bg_blur_spin.setRange(0, 100)

        form.addRow("饱和度", self.saturation_spin)
        form.addRow("对比度", self.contrast_spin)
        form.addRow("色温", self.color_temp_spin)
        form.addRow("马赛克", self.mosaic_spin)
        form.addRow("ONNX 风格", self.onnx_style_combo)
        form.addRow("", self.face_enabled_check)
        form.addRow("贴纸类型", self.face_effect_combo)
        form.addRow("缩放系数", self.face_scale_spin)
        form.addRow("跟踪平滑", self.face_smoothing_spin)
        form.addRow("", self.virtual_bg_enabled_check)
        form.addRow("背景模式", self.virtual_bg_mode_combo)
        form.addRow("模糊强度", self.virtual_bg_blur_spin)
        root.addLayout(form)

        button_row = QHBoxLayout()
        self.apply_btn = QPushButton("应用")
        self.apply_btn.setProperty("role", "primary")
        self.cancel_btn = QPushButton("取消")
        button_row.addWidget(self.apply_btn)
        button_row.addWidget(self.cancel_btn)
        root.addLayout(button_row)

        self.apply_btn.clicked.connect(self.accept)
        self.cancel_btn.clicked.connect(self.reject)

    def _load_model(self, model: CanvasItemModel) -> None:
        filters = dict(model.filters or {})
        chroma_key = dict(model.chroma_key or {})
        self.saturation_spin.setValue(float(filters.get("saturation", 1.0)))
        self.contrast_spin.setValue(float(filters.get("contrast", 1.0)))
        self.color_temp_spin.setValue(int(filters.get("color_temp", 0)))
        self.mosaic_spin.setValue(int(filters.get("mosaic", 0)))
        CanvasPropertyPanel._set_combo_by_data(self.onnx_style_combo, str(filters.get("onnx_style", "none") or "none"))
        self.face_enabled_check.setChecked(bool(chroma_key.get("face_enabled", False)))
        CanvasPropertyPanel._set_combo_by_data(self.face_effect_combo, str(chroma_key.get("effect_type", "dog_nose") or "dog_nose"))
        self.face_scale_spin.setValue(int(chroma_key.get("face_scale_percent", 100)))
        self.face_smoothing_spin.setValue(int(chroma_key.get("face_smoothing", 60)))
        self.virtual_bg_enabled_check.setChecked(bool(chroma_key.get("virtual_bg_enabled", False)))
        CanvasPropertyPanel._set_combo_by_data(self.virtual_bg_mode_combo, str(chroma_key.get("virtual_bg_mode", "image") or "image"))
        self.virtual_bg_blur_spin.setValue(int(chroma_key.get("virtual_bg_blur_strength", 55)))

    def collect_update(self) -> dict[str, Any]:
        return {
            "filters": {
                "saturation": float(self.saturation_spin.value()),
                "contrast": float(self.contrast_spin.value()),
                "color_temp": int(self.color_temp_spin.value()),
                "mosaic": int(self.mosaic_spin.value()),
                "onnx_style": str(self.onnx_style_combo.currentData() or "none"),
            },
            "chroma_key": {
                "face_enabled": bool(self.face_enabled_check.isChecked()),
                "effect_type": str(self.face_effect_combo.currentData() or "dog_nose"),
                "face_scale_percent": int(self.face_scale_spin.value()),
                "face_smoothing": int(self.face_smoothing_spin.value()),
                "virtual_bg_enabled": bool(self.virtual_bg_enabled_check.isChecked()),
                "virtual_bg_mode": str(self.virtual_bg_mode_combo.currentData() or "image"),
                "virtual_bg_blur_strength": int(self.virtual_bg_blur_spin.value()),
            },
        }

class InfiniteCanvasDialog(QDialog):
    """无限画布工作区。"""

    scene_committed = pyqtSignal(str)
    canvas_changed = pyqtSignal()
    closed = pyqtSignal()

    def __init__(
        self,
        state,
        canvas_width: int,
        canvas_height: int,
        parent=None,
        *,
        audio_controller=None,
        ai_settings=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("无限画布模式")
        self.setMinimumSize(960, 620)
        self.setStyleSheet(HAULIX_APP_QSS)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self.state = state
        self.audio_controller = audio_controller
        self.ai_settings = ai_settings
        self.canvas_width = max(1, int(canvas_width))
        self.canvas_height = max(1, int(canvas_height))
        self.bridge = DirectorCanvasBridge(state)
        self.export_service = CanvasExportService(self.bridge)
        self.history = CanvasHistoryManager()
        self.selection_manager = CanvasSelectionManager()
        self._loading_document = False
        self._current_scene_id: str | None = None
        self._current_document = CanvasDocument()
        self._capture_quality_key = "standard"
        self._audio_mixer_dialog: AudioMixerDialog | None = None
        self._audio_mixer_timer = QTimer(self)
        self._audio_mixer_timer.setInterval(140)
        self._audio_mixer_timer.timeout.connect(self._update_audio_mixer_level)
        self._ai_model_dialog: AIModelWorkbenchDialog | None = None

        self._build_ui()
        self._wire_signals()
        self._fit_initial_geometry()
        self._load_current_scene()

    def _fit_initial_geometry(self) -> None:
        screen = self.screen()
        if screen is not None:
            available = screen.availableGeometry()
            width = min(1500, max(1180, int(available.width() * 0.88)))
            height = min(980, max(720, int(available.height() * 0.82)))
            self.resize(width, height)
            self.move(
                available.center().x() - self.width() // 2,
                available.center().y() - self.height() // 2,
            )
        else:
            self.resize(1400, 860)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        top_row = QHBoxLayout()
        self.back_btn = QPushButton("返回导播台")
        self.canvas_title = QLabel("无限画布模式")
        self.canvas_title.setObjectName("CanvasTitle")
        self.zoom_label = QLabel("100%")
        self.zoom_label.setObjectName("StatusLabel")
        self.scene_list_btn = QPushButton("场景列表")
        self.scene_list_btn.setCheckable(True)
        self.scene_list_btn.setProperty("role", "toolbar")
        self.layer_list_btn = QPushButton("图层列表")
        self.layer_list_btn.setCheckable(True)
        self.layer_list_btn.setProperty("role", "toolbar")
        self.source_menu_btn = QPushButton("添加输入源")
        self.source_menu_btn.setProperty("role", "toolbar")
        self.effect_btn = QPushButton("特效")
        self.effect_btn.setProperty("role", "toolbar")
        self.effect_btn.setEnabled(False)
        self.audio_mixer_btn = QPushButton("调音台")
        self.audio_mixer_btn.setProperty("role", "toolbar")
        self.ai_model_btn = QPushButton("大模型")
        self.ai_model_btn.setProperty("role", "primary")
        self.fit_doc_btn = QPushButton("适配")
        self.fit_sel_btn = QPushButton("适配选中")
        self.fit_doc_btn.setProperty("role", "toolbar")
        self.fit_sel_btn.setProperty("role", "toolbar")
        self.undo_btn = QPushButton("撤销")
        self.redo_btn = QPushButton("重做")
        self.undo_btn.setProperty("role", "toolbar")
        self.redo_btn.setProperty("role", "toolbar")
        self.apply_btn = QPushButton("应用到当前场景")
        self.apply_btn.setProperty("role", "primary")
        self.save_new_btn = QPushButton("保存为新场景")
        self.save_project_btn = QPushButton("保存工程")
        self.load_project_btn = QPushButton("加载工程")
        self.preview_btn = QPushButton("输出到预览")
        self.program_btn = QPushButton("输出到节目")
        self.low_perf_btn = QPushButton("低性能模式")
        self.low_perf_btn.setCheckable(True)
        self.low_perf_btn.setProperty("role", "toolbar")
        self.toggle_props_btn = QPushButton("属性面板")
        self.toggle_props_btn.setCheckable(True)
        self.toggle_props_btn.setProperty("role", "toolbar")
        for widget in (
            self.back_btn,
            self.canvas_title,
            self.fit_doc_btn,
            self.undo_btn,
            self.redo_btn,
            self.low_perf_btn,
        ):
            top_row.addWidget(widget)
        top_row.addStretch(1)
        top_row.addWidget(QLabel("缩放"))
        top_row.addWidget(self.zoom_label)
        top_row.addWidget(self.scene_list_btn)
        top_row.addWidget(self.layer_list_btn)
        top_row.addWidget(self.source_menu_btn)
        top_row.addWidget(self.effect_btn)
        top_row.addWidget(self.audio_mixer_btn)
        top_row.addWidget(self.ai_model_btn)
        root.addLayout(top_row)

        body = QSplitter(Qt.Orientation.Horizontal)
        body.setChildrenCollapsible(True)
        self.body_splitter = body
        root.addWidget(body, 1)

        left_panel = QFrame()
        self.left_panel = left_panel
        left_panel.setObjectName("CanvasDockPanel")
        left_panel.setMinimumWidth(180)
        left_panel.setMaximumWidth(220)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(6)

        self.scene_section_btn = QPushButton("\u573a\u666f\u5217\u8868 \u25be")
        self.scene_section_btn.setCheckable(True)
        self.scene_section_btn.setChecked(True)
        self.scene_section_btn.setVisible(False)
        left_layout.addWidget(self.scene_section_btn)
        self.scene_section = QFrame()
        scene_section_layout = QVBoxLayout(self.scene_section)
        scene_section_layout.setContentsMargins(0, 0, 0, 0)
        scene_section_layout.setSpacing(6)
        self.scene_list_widget = CanvasResourceListWidget()
        self.scene_list_widget.setMinimumHeight(160)
        scene_section_layout.addWidget(self.scene_list_widget, 1)
        scene_btn_row = QHBoxLayout()
        self.scene_add_btn = QPushButton("\u52a0\u5165\u753b\u5e03")
        self.scene_remove_btn = QPushButton("\u79fb\u51fa\u753b\u5e03")
        self.scene_ratio_btn = QPushButton("\u573a\u666f\u5c5e\u6027")
        self.scene_add_btn.setVisible(False)
        self.scene_remove_btn.setVisible(False)
        self.scene_ratio_btn.setVisible(False)
        scene_btn_row.addWidget(self.scene_add_btn)
        scene_btn_row.addWidget(self.scene_remove_btn)
        scene_btn_row.addWidget(self.scene_ratio_btn)
        scene_section_layout.addLayout(scene_btn_row)
        left_layout.addWidget(self.scene_section, 1)

        self.layer_section_btn = QPushButton("\u56fe\u5c42\u5217\u8868 \u25be")
        self.layer_section_btn.setCheckable(True)
        self.layer_section_btn.setChecked(True)
        self.layer_section_btn.setVisible(False)
        left_layout.addWidget(self.layer_section_btn)
        self.layer_section = QFrame()
        layer_section_layout = QVBoxLayout(self.layer_section)
        layer_section_layout.setContentsMargins(0, 0, 0, 0)
        layer_section_layout.setSpacing(6)
        self.layer_list_widget = CanvasResourceListWidget()
        self.layer_list_widget.setMinimumHeight(180)
        layer_section_layout.addWidget(self.layer_list_widget, 1)
        layer_btn_row = QHBoxLayout()
        self.layer_add_btn = QPushButton("\u52a0\u5165\u753b\u5e03")
        self.layer_remove_btn = QPushButton("\u79fb\u51fa\u753b\u5e03")
        self.layer_props_btn = QPushButton("\u56fe\u5c42\u5c5e\u6027")
        self.layer_add_btn.setVisible(False)
        self.layer_remove_btn.setVisible(False)
        self.layer_props_btn.setVisible(False)
        layer_btn_row.addWidget(self.layer_add_btn)
        layer_btn_row.addWidget(self.layer_remove_btn)
        layer_btn_row.addWidget(self.layer_props_btn)
        layer_section_layout.addLayout(layer_btn_row)

        add_source_box = QFrame()
        add_source_box.setObjectName("CanvasSourceButtons")
        self.add_source_box = add_source_box
        add_source_layout = QVBoxLayout(add_source_box)
        add_source_layout.setContentsMargins(0, 0, 0, 0)
        add_source_layout.setSpacing(6)
        add_source_layout.addWidget(QLabel("\u6dfb\u52a0\u8f93\u5165\u6e90"))
        add_source_row1 = QHBoxLayout()
        add_source_row2 = QHBoxLayout()
        self.add_camera_btn = QPushButton("\u6444\u50cf\u5934")
        self.add_screen_btn = QPushButton("\u5c4f\u5e55")
        self.add_window_btn = QPushButton("\u7a97\u53e3")
        self.add_image_btn = QPushButton("\u56fe\u7247")
        self.add_network_btn = QPushButton("\u7f51\u7edc\u6d41")
        add_source_row1.addWidget(self.add_camera_btn)
        add_source_row1.addWidget(self.add_screen_btn)
        add_source_row1.addWidget(self.add_window_btn)
        add_source_row2.addWidget(self.add_image_btn)
        add_source_row2.addWidget(self.add_network_btn)
        add_source_layout.addLayout(add_source_row1)
        add_source_layout.addLayout(add_source_row2)
        layer_section_layout.addWidget(add_source_box)
        add_source_box.setVisible(False)
        left_layout.addWidget(self.layer_section, 1)
        body.addWidget(left_panel)

        self.view = InfiniteCanvasView()
        body.addWidget(self.view)

        self.right_panel = QFrame()
        self.right_panel.setObjectName("CanvasDockPanel")
        self.right_panel.setMinimumWidth(280)
        self.right_panel.setMaximumWidth(360)
        right_layout = QVBoxLayout(self.right_panel)
        right_layout.setContentsMargins(8, 8, 8, 8)
        right_layout.setSpacing(6)
        right_title = QLabel("\u5c5e\u6027\u9762\u677f")
        right_layout.addWidget(right_title)
        self.property_scroll = QScrollArea()
        self.property_scroll.setWidgetResizable(True)
        self.property_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.property_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.property_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.property_panel = CanvasPropertyPanel()
        self.property_scroll.setWidget(self.property_panel)
        right_layout.addWidget(self.property_scroll, 1)
        self.right_panel.setVisible(False)
        body.addWidget(self.right_panel)

        body.setSizes([0, 1320, 0])

        status_row = QHBoxLayout()
        self.status_label = QLabel("已加载当前场景。")
        self.status_label.setObjectName("StatusLabel")
        status_row.addWidget(self.status_label, 1)
        status_frame = QFrame()
        status_frame.setObjectName("CanvasStatusBar")
        status_layout = QHBoxLayout(status_frame)
        status_layout.setContentsMargins(8, 2, 8, 2)
        status_layout.addLayout(status_row)
        root.addWidget(status_frame)

    def _wire_signals(self) -> None:
        self.back_btn.clicked.connect(self.close)
        self.fit_doc_btn.clicked.connect(self.view.fit_to_document)
        self.fit_sel_btn.clicked.connect(self.view.fit_to_selection)
        self.undo_btn.clicked.connect(self._undo)
        self.redo_btn.clicked.connect(self._redo)
        self.apply_btn.clicked.connect(self._apply_to_current_scene)
        self.save_new_btn.clicked.connect(self._save_as_new_scene)
        self.save_project_btn.clicked.connect(self._save_project)
        self.load_project_btn.clicked.connect(self._load_project)
        self.preview_btn.clicked.connect(self._export_to_preview)
        self.program_btn.clicked.connect(self._export_to_program)
        self.low_perf_btn.toggled.connect(self.view.set_low_performance_mode)
        self.scene_list_btn.clicked.connect(lambda: self._toggle_resource_panel("scene"))
        self.layer_list_btn.clicked.connect(lambda: self._toggle_resource_panel("layer"))
        self.source_menu_btn.clicked.connect(self._show_source_menu)
        self.effect_btn.clicked.connect(self._open_effect_editor)
        self.audio_mixer_btn.clicked.connect(self._open_audio_mixer_dialog)
        self.ai_model_btn.clicked.connect(self._open_ai_model_dialog)
        self.view.selection_models_changed.connect(self._on_selection_changed)
        self.view.document_changed.connect(self._on_document_changed)
        self.view.zoom_changed.connect(self._on_zoom_changed)
        self.view.undo_requested.connect(self._undo)
        self.view.redo_requested.connect(self._redo)
        self.view.item_drop_requested.connect(self._on_item_drop_requested)
        self.toggle_props_btn.toggled.connect(self.right_panel.setVisible)
        self.scene_list_widget.resource_activated.connect(self._on_resource_activated)
        self.layer_list_widget.resource_activated.connect(self._on_resource_activated)
        self.scene_list_widget.itemClicked.connect(lambda item: self._on_scene_list_selected(item, None))
        self.layer_list_widget.itemClicked.connect(lambda item: self._on_layer_list_selected(item, None))
        self.scene_add_btn.clicked.connect(self._add_selected_scene_to_canvas)
        self.scene_remove_btn.clicked.connect(self._remove_selected_scene_from_canvas)
        self.scene_ratio_btn.clicked.connect(self._edit_selected_scene_ratio)
        self.layer_add_btn.clicked.connect(self._add_selected_layer_to_canvas)
        self.layer_remove_btn.clicked.connect(self._remove_selected_layer_from_canvas)
        self.layer_props_btn.clicked.connect(self._open_selected_properties)
        self.add_camera_btn.clicked.connect(self._add_camera_source)
        self.add_screen_btn.clicked.connect(self._add_screen_source)
        self.add_window_btn.clicked.connect(self._add_window_source)
        self.add_image_btn.clicked.connect(self._add_image_source)
        self.add_network_btn.clicked.connect(self._add_network_source)

        self.property_panel.apply_btn.clicked.connect(self._apply_selected_item)
        self.property_panel.delete_btn.clicked.connect(self._delete_selected)
        self.property_panel.duplicate_btn.clicked.connect(self._duplicate_selected)
        self.property_panel.front_btn.clicked.connect(self.view.bring_to_front)
        self.property_panel.back_btn.clicked.connect(self.view.send_to_back)
        self.property_panel.up_btn.clicked.connect(self.view.move_up_one)
        self.property_panel.down_btn.clicked.connect(self.view.move_down_one)
        self.property_panel.align_left_btn.clicked.connect(lambda: self.view.align_selected("left"))
        self.property_panel.align_center_btn.clicked.connect(lambda: self.view.align_selected("center"))
        self.property_panel.align_right_btn.clicked.connect(lambda: self.view.align_selected("right"))
        self.property_panel.distribute_h_btn.clicked.connect(lambda: self.view.distribute_selected("horizontal"))
        self.property_panel.distribute_v_btn.clicked.connect(lambda: self.view.distribute_selected("vertical"))
        self.property_panel.group_btn.clicked.connect(self.view.group_selected)
        self.property_panel.ungroup_btn.clicked.connect(self.view.ungroup_selected)
        self.property_panel.bulk_lock_btn.clicked.connect(self._toggle_selected_lock)
        self.property_panel.bulk_hide_btn.clicked.connect(self._toggle_selected_visible)
        self.property_panel.import_scene_btn.clicked.connect(self._import_current_scene)
        self.property_panel.apply_scene_btn.clicked.connect(self._apply_to_current_scene)
        self.property_panel.save_new_scene_btn.clicked.connect(self._save_as_new_scene)
        self.property_panel.export_preview_btn.clicked.connect(self._export_to_preview)
        self.property_panel.export_program_btn.clicked.connect(self._export_to_program)
        self.property_panel.fit_doc_btn.clicked.connect(self.view.fit_to_document)
        self.property_panel.fit_sel_btn.clicked.connect(self.view.fit_to_selection)
        self._set_resource_panel(None)

    def _set_resource_panel(self, mode: str | None) -> None:
        show_scene = mode == "scene"
        show_layer = mode == "layer"
        self.left_panel.setVisible(show_scene or show_layer)
        self.scene_section.setVisible(show_scene)
        self.layer_section.setVisible(show_layer)
        if hasattr(self, "body_splitter"):
            left_size = 210 if (show_scene or show_layer) else 0
            right_size = 320 if self.right_panel.isVisible() else 0
            canvas_size = max(900, self.width() - left_size - right_size - 80)
            self.body_splitter.setSizes([left_size, canvas_size, right_size])
        self.scene_list_btn.blockSignals(True)
        self.layer_list_btn.blockSignals(True)
        self.scene_list_btn.setChecked(show_scene)
        self.layer_list_btn.setChecked(show_layer)
        self.scene_list_btn.blockSignals(False)
        self.layer_list_btn.blockSignals(False)

    def _toggle_resource_panel(self, mode: str) -> None:
        current_scene = self.left_panel.isVisible() and self.scene_section.isVisible()
        current_layer = self.left_panel.isVisible() and self.layer_section.isVisible()
        if (mode == "scene" and current_scene) or (mode == "layer" and current_layer):
            self._set_resource_panel(None)
            return
        self._set_resource_panel(mode)

    def _show_source_menu(self) -> None:
        menu = QMenu(self)
        for text, handler in (
            ("添加摄像头", self._add_camera_source),
            ("添加屏幕", self._add_screen_source),
            ("添加窗口", self._add_window_source),
            ("添加图片", self._add_image_source),
            ("添加网络流", self._add_network_source),
        ):
            action = menu.addAction(text)
            action.triggered.connect(lambda _checked=False, callback=handler: callback())
        menu.exec(self.source_menu_btn.mapToGlobal(self.source_menu_btn.rect().bottomLeft()))

    def _notify(self, text: str, is_error: bool = False) -> None:
        if is_error:
            self.status_label.setText(f"错误：{text}")
        else:
            self.status_label.setText(text)

    def _audio_scene_id(self) -> str | None:
        """画布调音台优先跟随当前画布场景，没有时退回导播台活动场景。"""
        return self._current_scene_id or self.state.get_active_scene_id()

    def _apply_audio_source_now(self) -> None:
        if self.audio_controller is None:
            return
        scene_id = self._audio_scene_id()
        track = self.state.resolve_audio_track_profile(scene_id)
        strict = self.state.audio_isolation_requested(scene_id)
        self.audio_controller.set_track_profile(track, strict_isolation=strict)

    def _sync_audio_mixer_dialog(self, level: float | None = None) -> None:
        if self._audio_mixer_dialog is None:
            return
        scene_id = self._audio_scene_id()
        tracks = self.state.list_audio_tracks(scene_id)
        active_track = self.state.resolve_audio_track_profile(scene_id)
        self._audio_mixer_dialog.set_tracks(tracks, active_track.id)
        if level is not None:
            self._audio_mixer_dialog.update_level(active_track.id, level)

    def _update_audio_mixer_level(self) -> None:
        if self._audio_mixer_dialog is None or self.audio_controller is None:
            self._audio_mixer_timer.stop()
            return
        try:
            diag = self.audio_controller.get_diagnostics()
            self._sync_audio_mixer_dialog(level=float(getattr(diag, "level", 0.0)))
        except Exception:
            self._sync_audio_mixer_dialog(level=0.0)

    def _open_audio_mixer_dialog(self) -> None:
        if self._audio_mixer_dialog is None:
            dialog = AudioMixerDialog(self)
            dialog.track_params_changed.connect(self._on_canvas_mixer_track_params_changed)
            dialog.track_selected.connect(self._on_canvas_mixer_track_selected)
            dialog.closed.connect(self._on_audio_mixer_closed)
            self._audio_mixer_dialog = dialog
        self._sync_audio_mixer_dialog()
        self._audio_mixer_dialog.show()
        self._audio_mixer_dialog.raise_()
        self._audio_mixer_dialog.activateWindow()
        if self.audio_controller is not None and not self._audio_mixer_timer.isActive():
            self._audio_mixer_timer.start()
        self._notify("已打开画布调音台。")

    def _on_audio_mixer_closed(self) -> None:
        self._audio_mixer_timer.stop()
        self._audio_mixer_dialog = None

    def _on_canvas_mixer_track_params_changed(
        self,
        track_id: str,
        volume: float,
        muted: bool,
        amplitude: float,
        low_gain: float,
        mid_gain: float,
        high_gain: float,
    ) -> None:
        self.state.set_audio_track_params(
            track_id,
            volume=volume,
            muted=muted,
            amplitude=amplitude,
            low_gain=low_gain,
            mid_gain=mid_gain,
            high_gain=high_gain,
        )
        self._apply_audio_source_now()
        self._sync_audio_mixer_dialog()

    def _on_canvas_mixer_track_selected(self, track_id: str) -> None:
        self.state.set_audio_capture_source(track_id)
        self._apply_audio_source_now()
        self._sync_audio_mixer_dialog()
        self._notify("画布调音台已切换采集音轨。")

    def _ai_current_frame_image(self, source: str):
        """给大模型工作台提供当前画布截图，避免访问主窗口内部控件。"""
        viewport = self.view.viewport()
        if viewport is None:
            return None
        image = viewport.grab().toImage()
        return image if not image.isNull() else None

    def _add_ai_result_to_canvas(self, image_path: str) -> bool:
        path = str(Path(image_path))
        if not Path(path).exists():
            self._notify("AI 结果图片不存在，无法加入画布。", is_error=True)
            return False
        return self.add_ai_image_to_canvas(path, name=Path(path).stem)

    def _sync_ai_result_to_selected_canvas_item(self, image_path: str) -> bool:
        path = str(Path(image_path))
        if not Path(path).exists():
            self._notify("AI 结果图片不存在，无法同步。", is_error=True)
            return False
        item = self._selected_single_item()
        if item is None or item.model.type == "scene":
            self._notify("请先选择一个可替换的画布图层。", is_error=True)
            return False
        model = item.to_model()
        model.type = LayerType.PNG.value
        model.name = f"AI图片: {Path(path).stem}"
        metadata = dict(model.metadata or {})
        source_snapshot = dict(metadata.get("source_snapshot") or {})
        source_snapshot.update({"image_path": path, "ai_generated": True, "synced_from_ai": True})
        metadata["source_snapshot"] = source_snapshot
        metadata["layer_type"] = LayerType.PNG.value
        model.metadata = metadata
        item.apply_model(model)
        self.view._refresh_parent_assignment(item)
        self.view.document_changed.emit()
        self._center_canvas_item(model.item_id)
        self._notify("AI 图片已同步到选中画布图层。")
        return True

    def _open_ai_model_dialog(self) -> None:
        if self.ai_settings is None:
            self._notify("画布未接入 AI 配置，无法打开大模型工作台。", is_error=True)
            return
        if self._ai_model_dialog is None:
            dialog = AIModelWorkbenchDialog(
                settings=self.ai_settings,
                output_root=Path("outputs") / "ai_generated",
                current_frame_provider=self._ai_current_frame_image,
                add_image_layer=self._add_ai_result_to_canvas,
                sync_selected_layer=self._sync_ai_result_to_selected_canvas_item,
                send_to_canvas=self._add_ai_result_to_canvas,
                parent=self,
            )
            dialog.finished.connect(lambda _code: self._on_ai_model_closed())
            self._ai_model_dialog = dialog
        self._ai_model_dialog.show()
        self._ai_model_dialog.raise_()
        self._ai_model_dialog.activateWindow()
        self._notify("已打开画布大模型图像工作台。")

    def _on_ai_model_closed(self) -> None:
        self._ai_model_dialog = None

    def _scene_list(self) -> list[Scene]:
        return self.state.snapshot_scenes()

    def _active_scene(self) -> Scene | None:
        scene_id = self.state.get_active_scene_id()
        if not scene_id:
            return None
        return self.state.get_scene_by_id(scene_id)

    def _load_current_scene(self) -> None:
        scene = self._active_scene()
        if scene is None:
            document = CanvasDocument(
                name="场景画布",
                output_frame=replace(self._current_document.output_frame, width=self.canvas_width, height=self.canvas_height),
            )
            self._set_document(document, scene_id=None)
            self._refresh_resources()
            self._refresh_scene_target_combo()
            self._refresh_object_list()
            return
        document = self.bridge.build_document_from_scene(scene.id, self.canvas_width, self.canvas_height)
        self._set_document(document, scene_id=scene.id)
        self._refresh_resources()
        self._refresh_object_list()
        self._notify(f"已载入场景：{scene.name}")

    def refresh_from_state(self, reload_scene: bool = False) -> None:
        if reload_scene:
            self._load_current_scene()
            return
        self.view.sync_scene_snapshots(lambda scene_id: self.state.get_scene_by_id(scene_id), self.state.get_active_scene_id())
        self._refresh_resources()
        self._refresh_scene_target_combo()
        self._refresh_object_list()
        self.property_panel.set_document_info(self.view.document(), self.view.zoom_percent(), len(self.view.document().items))

    def _set_document(self, document: CanvasDocument, scene_id: str | None) -> None:
        self._loading_document = True
        self._current_document = document.clone()
        self._current_scene_id = scene_id
        self.view.set_document(document)
        self.view.set_canvas_name(document.name)
        self.property_panel.set_document_info(document, self.view.zoom_percent(), len(document.items))
        self.property_panel.set_selection([])
        self.selection_manager.clear()
        self.history.reset(self.view.document())
        self.view.center_to_document()
        self._loading_document = False
        self._on_zoom_changed(self.view._zoom)
        self._refresh_scene_target_combo()
        self._refresh_object_list()

    def _refresh_scene_target_combo(self) -> None:
        self.property_panel.set_scene_targets(self._scene_list(), self._current_scene_id)

    def _capture_quality_settings(self) -> dict[str, int | str]:
        presets = {
            "low": {"width": 960, "height": 540, "fps": 24},
            "standard": {"width": 1280, "height": 720, "fps": 30},
            "high": {"width": 1920, "height": 1080, "fps": 30},
        }
        key = self._capture_quality_key if self._capture_quality_key in presets else "standard"
        meta = presets[key]
        return {
            "capture_quality": key,
            "capture_width": int(meta["width"]),
            "capture_height": int(meta["height"]),
            "capture_fps": int(meta["fps"]),
        }

    def _with_capture_quality(self, source: dict[str, Any]) -> dict[str, Any]:
        merged = dict(source or {})
        merged.update(self._capture_quality_settings())
        return merged

    def _default_rect(self) -> tuple[int, int, int, int]:
        width = min(640, max(320, self.canvas_width // 2))
        height = min(360, max(180, self.canvas_height // 2))
        x = (self.canvas_width - width) // 2
        y = (self.canvas_height - height) // 2
        return x, y, width, height

    def _create_source_canvas_item(
        self,
        *,
        name: str,
        layer_type: LayerType,
        source: dict[str, Any],
    ) -> CanvasItemModel:
        x, y, w, h = self._default_rect()
        temp_layer = Layer(
            id=new_id("layer"),
            name=name,
            layer_type=layer_type,
            x=x,
            y=y,
            width=w,
            height=h,
            source=source,
        )
        return SceneCanvasAdapter.layer_to_item(temp_layer, scene_id=self._current_scene_id)

    def _append_canvas_item(self, item: CanvasItemModel) -> None:
        self.view.add_item_model(item)
        self._center_canvas_item(item.item_id)
        self._refresh_scene_target_combo()

    def _add_camera_source(self) -> None:
        idx, ok = QInputDialog.getInt(self, "添加相机", "摄像头索引:", 0, 0, 32, 1)
        if not ok:
            return
        item = self._create_source_canvas_item(
            name=f"相机 {idx}",
            layer_type=LayerType.CAMERA,
            source=self._with_capture_quality({"camera_index": idx}),
        )
        self._append_canvas_item(item)
        self._notify(f"已添加到画布：{item.name}")

    def _add_screen_source(self) -> None:
        idx, ok = QInputDialog.getInt(self, "添加屏幕", "显示器索引(通常从1开始):", 1, 0, 16, 1)
        if not ok:
            return
        item = self._create_source_canvas_item(
            name=f"屏幕 {idx}",
            layer_type=LayerType.SCREEN,
            source=self._with_capture_quality({"monitor_index": idx}),
        )
        item.x = 0
        item.y = 0
        item.width = max(1, self.canvas_width)
        item.height = max(1, self.canvas_height)
        self._append_canvas_item(item)
        self._notify(f"已添加到画布：{item.name}")

    def _add_window_source(self) -> None:
        wins = enum_windows()
        if not wins:
            self._notify("未检测到可采集窗口，或 pywin32 未安装。", is_error=True)
            return
        labels = [f"{w['title']}  (PID:{w.get('pid')}, HWND:{w['hwnd']})" for w in wins]
        label, ok = QInputDialog.getItem(self, "添加窗口源", "选择目标窗口:", labels, 0, False)
        if not ok:
            return
        idx = labels.index(label)
        win = wins[idx]
        item = self._create_source_canvas_item(
            name=f"窗口: {win['title'][:16]}",
            layer_type=LayerType.WINDOW,
            source=self._with_capture_quality(
                {
                    "hwnd": int(win["hwnd"]),
                    "title": win["title"],
                    "pid": win.get("pid"),
                    "process_name": win.get("process_name"),
                }
            ),
        )
        self._append_canvas_item(item)
        self._notify(f"已添加到画布：{item.name}")

    def _add_image_source(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择静态图片",
            str(Path.cwd()),
            "图片文件 (*.png *.jpg *.jpeg *.bmp);;PNG 图片 (*.png);;JPEG 图片 (*.jpg *.jpeg);;BMP 图片 (*.bmp);;全部文件 (*.*)",
        )
        if not path:
            return
        item = self._create_source_canvas_item(
            name=f"图片: {Path(path).name}",
            layer_type=LayerType.PNG,
            source={"image_path": path},
        )
        self._append_canvas_item(item)
        self._notify(f"已添加到画布：{item.name}")

    def _add_network_source(self) -> None:
        url, ok = QInputDialog.getText(self, "添加网络流", "输入网络流地址（RTMP/RTSP/HTTP）:")
        if not ok:
            return
        url = url.strip()
        if not url:
            self._notify("网络流 URL 不能为空。", is_error=True)
            return
        item = self._create_source_canvas_item(
            name="网络流",
            layer_type=LayerType.NETWORK,
            source=self._with_capture_quality({"url": url}),
        )
        self._append_canvas_item(item)
        self._notify(f"已添加到画布：{item.name}")

    def _on_selection_changed(self, models: list[CanvasItemModel] | object) -> None:
        if isinstance(models, list):
            selected_models = [model for model in models if isinstance(model, CanvasItemModel)]
        else:
            selected_models = []
        self.selection_manager.set_selected_ids([model.item_id for model in selected_models])
        self.property_panel.set_selection(selected_models)
        can_edit_effects = len(selected_models) == 1 and selected_models[0].type != "scene"
        self.effect_btn.setEnabled(can_edit_effects)

    def _on_document_changed(self) -> None:
        if self._loading_document:
            return
        document = self.view.document()
        self._current_document = document.clone()
        self._current_document.touch()
        self.property_panel.set_document_info(document, self.view.zoom_percent(), len(document.items))
        self._refresh_scene_target_combo()
        self._refresh_scene_list()
        self._refresh_layer_list()
        self._push_history()
        self._sync_canvas_scene_frames_to_state()

    def _on_zoom_changed(self, zoom: float) -> None:
        self.zoom_label.setText(f"{int(round(zoom * 100))}%")
        document = self.view.document()
        self.property_panel.set_document_info(document, self.view.zoom_percent(), len(document.items))

    def _push_history(self) -> None:
        if self._loading_document:
            return
        self.history.push(self.view.document())

    def _add_list_header(self, widget: QListWidget, text: str) -> None:
        item = QListWidgetItem(text)
        item.setFlags(Qt.ItemFlag.ItemIsEnabled)
        item.setData(Qt.ItemDataRole.UserRole, None)
        widget.addItem(item)

    def _refresh_scene_list(self) -> None:
        self.scene_list_widget.blockSignals(True)
        self.scene_list_widget.clear()
        scenes = self._scene_list()
        active_id = self.state.get_active_scene_id()

        self._add_list_header(self.scene_list_widget, "\u5bfc\u64ad\u53f0\u573a\u666f")
        for scene in scenes:
            if scene.is_placeholder:
                continue
            label = scene.name
            if scene.id == active_id:
                label += " [\u5f53\u524d]"
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, {"kind": "scene_resource", "scene_id": scene.id})
            self.scene_list_widget.addItem(item)

        new_scene_item = QListWidgetItem("\u65b0\u5efa\u573a\u666f")
        new_scene_item.setData(Qt.ItemDataRole.UserRole, {"kind": "new_scene"})
        self.scene_list_widget.addItem(new_scene_item)

        self._add_list_header(self.scene_list_widget, "\u753b\u5e03\u573a\u666f\u6846")
        for scene_item in sorted((item.to_model() for item in self.view._items.values() if item.model.type == "scene"), key=lambda it: (it.z_index, it.item_id)):
            scene = self.state.get_scene_by_id(scene_item.scene_ref) if scene_item.scene_ref else None
            status = str(scene_item.metadata.get("director_status") or ("\u5f53\u524d\u5bfc\u64ad" if scene and scene.id == active_id else "\u573a\u666f\u6846"))
            item = QListWidgetItem(f"{scene_item.name} | {status}")
            item.setData(Qt.ItemDataRole.UserRole, {"kind": "canvas_item", "item_id": scene_item.item_id, "item_type": "scene"})
            if scene_item.scene_ref and scene and scene.id == active_id:
                item.setBackground(QColor(25, 39, 57))
            self.scene_list_widget.addItem(item)

        self.scene_list_widget.blockSignals(False)

    def _refresh_layer_list(self) -> None:
        self.layer_list_widget.blockSignals(True)
        self.layer_list_widget.clear()
        scenes = self._scene_list()
        active_id = self.state.get_active_scene_id()

        self._add_list_header(self.layer_list_widget, "\u5bfc\u64ad\u53f0\u56fe\u5c42")
        for scene in scenes:
            if scene.is_placeholder:
                continue
            scene_header = QListWidgetItem(f"[{scene.name}]")
            scene_header.setFlags(Qt.ItemFlag.ItemIsEnabled)
            scene_header.setData(Qt.ItemDataRole.UserRole, None)
            self.layer_list_widget.addItem(scene_header)
            for layer in sorted(scene.layers, key=lambda item: item.priority, reverse=True):
                label = f"{layer.layer_type.value.upper()} | {layer.name}"
                if scene.id == active_id:
                    label += " [\u5f53\u524d\u573a\u666f]"
                item = QListWidgetItem(label)
                item.setData(Qt.ItemDataRole.UserRole, {"kind": "layer_resource", "scene_id": scene.id, "layer_id": layer.id})
                self.layer_list_widget.addItem(item)

        self._add_list_header(self.layer_list_widget, "\u753b\u5e03\u56fe\u5c42")
        for model in sorted(self.view.document().items, key=lambda item: (-item.z_index, item.item_id)):
            if model.type == "scene":
                continue
            state_bits = []
            if model.locked:
                state_bits.append("\u9501\u5b9a")
            if not model.visible:
                state_bits.append("\u9690\u85cf")
            if model.parent_item_id:
                state_bits.append("\u5f52\u5165:" + str(model.metadata.get("parent_scene_name") or "\u573a\u666f"))
            elif model.scene_ref:
                state_bits.append("\u573a\u666f\u5185")
            elif model.source_ref:
                state_bits.append("\u6765\u6e90")
            suffix = f" [{' / '.join(state_bits)}]" if state_bits else ""
            item = QListWidgetItem(f"{model.z_index:02d} | {model.name}{suffix}")
            item.setData(Qt.ItemDataRole.UserRole, {"kind": "canvas_item", "item_id": model.item_id, "item_type": model.type})
            self.layer_list_widget.addItem(item)

        self.layer_list_widget.blockSignals(False)

    def _refresh_resources(self) -> None:
        self._refresh_scene_list()
        self._refresh_layer_list()

    def _refresh_object_list(self) -> None:
        self._refresh_layer_list()

    def _scene_canvas_item_by_scene_id(self, scene_id: str | None) -> CanvasGraphicsItem | None:
        if not scene_id:
            return None
        for item in self.view._items.values():
            if item.model.type == "scene" and item.model.scene_ref == scene_id:
                return item
        return None

    def _canvas_scene_items(self) -> list[CanvasGraphicsItem]:
        return [item for item in self.view._items.values() if item.model.type == "scene"]

    def _center_canvas_item(self, item_id: str | None) -> bool:
        if self.view.center_on_item(item_id):
            model = self.view.item_model(item_id)
            if model is not None:
                self._on_selection_changed([model])
            return True
        return False

    def _canvas_layer_item_by_layer_ref(self, layer_id: str, scene_id: str | None = None) -> CanvasGraphicsItem | None:
        """根据原始图层 ID 查找画布对象；优先匹配所属场景，兜底匹配已脱离场景的同源图层。"""
        if not layer_id:
            return None
        exact = next(
            (
                item
                for item in self.view._items.values()
                if item.model.type != "scene"
                and item.model.source_ref == layer_id
                and (scene_id is None or item.model.scene_ref == scene_id)
            ),
            None,
        )
        if exact is not None:
            return exact
        return next(
            (
                item
                for item in self.view._items.values()
                if item.model.type != "scene" and item.model.source_ref == layer_id
            ),
            None,
        )

    def _center_or_add_scene_resource(self, scene_id: str) -> bool:
        scene_item = self._scene_canvas_item_by_scene_id(scene_id)
        if scene_item is not None:
            return self._center_canvas_item(scene_item.model.item_id)
        scene = self.state.get_scene_by_id(scene_id)
        if scene is None:
            self._notify("场景已失效，无法定位。", is_error=True)
            return False
        return self._add_scene_frame_to_canvas(scene) is not None

    def _center_or_add_layer_resource(self, scene_id: str, layer_id: str) -> bool:
        canvas_item = self._canvas_layer_item_by_layer_ref(layer_id, scene_id)
        if canvas_item is not None:
            return self._center_canvas_item(canvas_item.model.item_id)
        layer = self.state.find_layer(layer_id, scene_id=scene_id)
        if layer is None:
            self._notify("图层已失效，无法定位。", is_error=True)
            return False
        item = SceneCanvasAdapter.layer_to_item(layer, scene_id=scene_id)
        center = self.view.mapToScene(self.view.viewport().rect().center())
        item.x = int(round(center.x() - item.width / 2))
        item.y = int(round(center.y() - item.height / 2))
        self.view.add_item_model(item)
        self._refresh_scene_target_combo()
        self._refresh_scene_list()
        self._refresh_layer_list()
        self._center_canvas_item(item.item_id)
        self._notify(f"已将图层加入画布并定位：{layer.name}")
        return True

    def _center_or_add_resource_payload(self, payload: dict[str, Any]) -> bool:
        kind = str(payload.get("kind") or "")
        if kind == "canvas_item":
            return self._center_canvas_item(payload.get("item_id"))
        if kind == "scene_resource":
            return self._center_or_add_scene_resource(str(payload.get("scene_id") or ""))
        if kind == "layer_resource":
            return self._center_or_add_layer_resource(
                str(payload.get("scene_id") or ""),
                str(payload.get("layer_id") or ""),
            )
        if kind == "new_scene":
            return self._create_blank_scene_frame() is not None
        return False

    def _selected_item_payload(self, list_widget: QListWidget) -> dict[str, Any] | None:
        item = list_widget.currentItem()
        if item is None:
            return None
        payload = item.data(Qt.ItemDataRole.UserRole)
        return payload if isinstance(payload, dict) else None

    def _on_scene_list_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        payload = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(payload, dict):
            return
        self._center_or_add_resource_payload(payload)

    def _on_layer_list_selected(self, current: QListWidgetItem | None, _previous: QListWidgetItem | None) -> None:
        if current is None:
            return
        payload = current.data(Qt.ItemDataRole.UserRole)
        if not isinstance(payload, dict):
            return
        self._center_or_add_resource_payload(payload)

    def _selected_scene_resource(self) -> dict[str, Any] | None:
        return self._selected_item_payload(self.scene_list_widget)

    def _selected_layer_resource(self) -> dict[str, Any] | None:
        return self._selected_item_payload(self.layer_list_widget)

    def _sync_canvas_scene_frames_to_state(self) -> None:
        document = self.view.document()
        changed_scene_ids: list[str] = []
        removed_by_scene_ref: dict[str, set[str]] = {}
        for item in document.items:
            if item.type == "scene":
                continue
            removed_scene_ref = str(item.metadata.get("removed_from_scene_ref") or "")
            removed_layer_id = str(item.source_ref or item.item_id or "")
            if removed_scene_ref and removed_layer_id:
                removed_by_scene_ref.setdefault(removed_scene_ref, set()).add(removed_layer_id)
        for scene_item in sorted((item.to_model() for item in self.view._items.values() if item.model.type == "scene" and item.model.scene_ref), key=lambda it: (it.z_index, it.item_id)):
            scene = self.state.get_scene_by_id(scene_item.scene_ref or "")
            if scene is None or scene.is_placeholder:
                continue
            children = [item.to_model() for item in self.view._items.values() if item.model.parent_item_id == scene_item.item_id]
            new_layers = SceneCanvasAdapter.scene_frame_to_layers(scene_item, children, document, removed_by_scene_ref.get(scene_item.scene_ref or "", set()))
            current_layers = scene.layers
            current_payload = [SceneCanvasAdapter._layer_to_dict(layer) for layer in current_layers]
            new_payload = [SceneCanvasAdapter._layer_to_dict(layer) for layer in new_layers]
            if current_payload == new_payload:
                continue
            self.state.clear_scene_layers(scene.id)
            for layer in new_layers:
                self.state.add_layer(layer, scene_id=scene.id)
            changed_scene_ids.append(scene.id)
        if changed_scene_ids:
            self.scene_committed.emit(changed_scene_ids[0])

    def _scene_frame_for_scene(self, scene: Scene, scene_pos: QPointF | None = None) -> CanvasItemModel:
        item = SceneCanvasAdapter.scene_to_item(scene, self.canvas_width, self.canvas_height)
        bottom_z = min([int(round(obj.zValue())) for obj in self.view._items.values()], default=0) - 1
        item.z_index = min(int(item.z_index), bottom_z, -1000)
        center = scene_pos if scene_pos is not None else self.view.mapToScene(self.view.viewport().rect().center())
        item.x = int(round(center.x() - item.width / 2))
        item.y = int(round(center.y() - item.height / 2))
        item.metadata = dict(item.metadata or {})
        item.metadata["director_status"] = "\u5f53\u524d\u5bfc\u64ad" if scene.id == self.state.get_active_scene_id() else ("\u5360\u4f4d\u573a\u666f" if scene.is_placeholder else "\u666e\u901a\u573a\u666f")
        return item

    def _add_scene_frame_to_canvas(self, scene: Scene, scene_pos: QPointF | None = None, *, allow_existing: bool = False) -> CanvasItemModel | None:
        if not allow_existing and self._scene_canvas_item_by_scene_id(scene.id) is not None:
            self._notify("\u8be5\u573a\u666f\u5df2\u5728\u753b\u5e03\u4e2d\u3002")
            return None
        item = self._scene_frame_for_scene(scene, scene_pos)
        self.view.add_item_model(item)
        self._refresh_scene_target_combo()
        self._refresh_scene_list()
        self._refresh_layer_list()
        self._center_canvas_item(item.item_id)
        self._notify(f"\u5df2\u5c06\u573a\u666f\u52a0\u5165\u753b\u5e03\uff1a{scene.name}")
        return item

    def _create_blank_scene_frame(self, scene_pos: QPointF | None = None) -> CanvasItemModel | None:
        scene = self.state.add_scene("\u753b\u5e03\u65b0\u573a\u666f")
        self.scene_committed.emit(scene.id)
        return self._add_scene_frame_to_canvas(scene, scene_pos, allow_existing=True)

    def _create_scene_frame_from_selected_scene(self) -> None:
        payload = self._selected_scene_resource()
        if not payload:
            return
        if str(payload.get("kind") or "") == "new_scene":
            self._create_blank_scene_frame()
            return
        scene_id = str(payload.get("scene_id") or "")
        scene = self.state.get_scene_by_id(scene_id)
        if scene is None:
            self._notify("\u573a\u666f\u5df2\u5931\u6548\uff0c\u65e0\u6cd5\u521b\u5efa\u573a\u666f\u6846\u3002", is_error=True)
            return
        self._add_scene_frame_to_canvas(scene)

    def _add_selected_scene_to_canvas(self) -> None:
        payload = self._selected_scene_resource()
        if not payload:
            return
        self._center_or_add_resource_payload(payload)

    def _remove_selected_scene_from_canvas(self) -> None:
        payload = self._selected_scene_resource()
        if not payload:
            return
        kind = str(payload.get("kind") or "")
        item_id = str(payload.get("item_id") or "") if kind == "canvas_item" else ""
        if kind == "scene_resource":
            scene_item = self._scene_canvas_item_by_scene_id(str(payload.get("scene_id") or ""))
            item_id = scene_item.model.item_id if scene_item is not None else ""
        if not item_id:
            self._notify("\u8be5\u573a\u666f\u5f53\u524d\u4e0d\u5728\u753b\u5e03\u4e2d\u3002")
            return
        if self.view.remove_item_by_id(item_id):
            self._refresh_scene_target_combo()
            self._refresh_scene_list()
            self._refresh_layer_list()
            self._notify("\u5df2\u4ece\u753b\u5e03\u79fb\u51fa\u573a\u666f\u6846\u3002")

    def _edit_selected_scene_ratio(self) -> None:
        payload = self._selected_scene_resource()
        if not payload:
            return
        item = None
        if payload.get("kind") == "canvas_item":
            item = self.view._items.get(str(payload.get("item_id") or ""))
        elif payload.get("kind") == "scene_resource":
            item = self._scene_canvas_item_by_scene_id(str(payload.get("scene_id") or ""))
        if item is None:
            self._notify("\u8bf7\u5148\u9009\u62e9\u753b\u5e03\u4e2d\u7684\u573a\u666f\u6846\u3002", is_error=True)
            return
        model = item.to_model()
        ratios = ["4:3", "16:9", "16:10", "21:9"]
        current_ratio = SceneCanvasAdapter._aspect_ratio(model.width, model.height)
        ratio, ok = QInputDialog.getItem(self, "\u573a\u666f\u6bd4\u4f8b", "\u9009\u62e9\u573a\u666f\u6bd4\u4f8b", ratios, ratios.index(current_ratio) if current_ratio in ratios else 1, False)
        if not ok:
            return
        ratio_map = {"4:3": 4 / 3, "16:9": 16 / 9, "16:10": 16 / 10, "21:9": 21 / 9}
        model.height = max(1, int(round(max(1, model.width) / ratio_map[ratio])))
        item.apply_model(model)
        self.view._sync_scene_children(model.item_id)
        self._sync_canvas_scene_frames_to_state()
        self._refresh_scene_list()
        self._refresh_layer_list()
        self._notify(f"\u5df2\u8c03\u6574\u573a\u666f\u6bd4\u4f8b\uff1a{ratio}")

    def _add_selected_layer_to_canvas(self) -> None:
        payload = self._selected_layer_resource()
        if not payload:
            return
        self._center_or_add_resource_payload(payload)

    def _remove_selected_layer_from_canvas(self) -> None:
        payload = self._selected_layer_resource()
        if not payload:
            return
        kind = str(payload.get("kind") or "")
        item_id = str(payload.get("item_id") or "") if kind == "canvas_item" else ""
        if kind == "layer_resource":
            scene_id = str(payload.get("scene_id") or "")
            layer_id = str(payload.get("layer_id") or "")
            canvas_item = next((obj for obj in self.view._items.values() if obj.model.source_ref == layer_id and obj.model.scene_ref == scene_id), None)
            item_id = canvas_item.model.item_id if canvas_item is not None else ""
        if not item_id:
            self._notify("\u8be5\u56fe\u5c42\u5f53\u524d\u4e0d\u5728\u753b\u5e03\u4e2d\u3002")
            return
        if self.view.remove_item_by_id(item_id):
            self._sync_canvas_scene_frames_to_state()
            self._refresh_scene_target_combo()
            self._refresh_scene_list()
            self._refresh_layer_list()
            self._notify("\u5df2\u4ece\u753b\u5e03\u79fb\u51fa\u56fe\u5c42\u3002")

    def _open_selected_properties(self) -> None:
        self.right_panel.setVisible(True)
        self.toggle_props_btn.setChecked(True)
        left_size = 210 if self.left_panel.isVisible() else 0
        self.body_splitter.setSizes([left_size, max(900, self.width() - left_size - 400), 320])
        self._notify("\u5df2\u5c55\u5f00\u5c5e\u6027\u9762\u677f\u3002")

    def _open_effect_editor(self) -> None:
        item = self._selected_single_item()
        if item is None or item.model.type == "scene":
            self._notify("请先选择一个可调节的图层。", is_error=True)
            return
        dialog = CanvasLayerEffectDialog(item.to_model(), self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        model = item.to_model()
        update = dialog.collect_update()
        model.filters = dict(update.get("filters") or {})
        model.chroma_key = dict(update.get("chroma_key") or {})
        item.apply_model(model)
        self.view._refresh_parent_assignment(item)
        self.view.document_changed.emit()
        self._refresh_scene_target_combo()
        self._refresh_scene_list()
        self._refresh_layer_list()
        self._notify(f"已更新图层特效：{model.name}")

    def _build_item_from_resource(self, payload: dict[str, Any], scene_pos: QPointF) -> CanvasItemModel | None:
        kind = str(payload.get("kind") or "")
        if kind in {"scene", "scene_resource"}:
            scene = self.state.get_scene_by_id(str(payload.get("scene_id") or ""))
            return None if scene is None else self._scene_frame_for_scene(scene, scene_pos)
        if kind == "new_scene":
            scene = self.state.add_scene("\u753b\u5e03\u65b0\u573a\u666f")
            self.scene_committed.emit(scene.id)
            return self._scene_frame_for_scene(scene, scene_pos)
        if kind in {"layer", "layer_resource"}:
            scene_id = str(payload.get("scene_id") or "")
            layer_id = str(payload.get("layer_id") or "")
            layer = self.state.find_layer(layer_id, scene_id=scene_id)
            if layer is None:
                return None
            item = SceneCanvasAdapter.layer_to_item(layer, scene_id=scene_id)
            item.x = int(round(scene_pos.x()))
            item.y = int(round(scene_pos.y()))
            return item
        if kind == "ai_image":
            image_path = str(payload.get("image_path") or "").strip()
            if not image_path:
                return None
            name = str(payload.get("name") or Path(image_path).stem or "AI 图片")
            temp_layer = Layer(
                id=new_id("layer"),
                name=f"AI图片: {name}",
                layer_type=LayerType.PNG,
                x=int(round(scene_pos.x())),
                y=int(round(scene_pos.y())),
                width=640,
                height=360,
                source={"image_path": image_path, "ai_generated": True},
            )
            return SceneCanvasAdapter.layer_to_item(temp_layer, scene_id=self._current_scene_id)
        return None

    def add_ai_image_to_canvas(self, image_path: str, name: str | None = None) -> bool:
        """供主界面 AI 子窗口调用，把生成结果放到画布中心。"""
        payload = {"kind": "ai_image", "image_path": image_path, "name": name or Path(image_path).stem}
        item = self._build_item_from_resource(payload, self.view.mapToScene(self.view.viewport().rect().center()))
        if item is None:
            self._notify("AI 图片无法加入画布。", is_error=True)
            return False
        self.view.add_item_model(item)
        self._refresh_scene_target_combo()
        self._refresh_scene_list()
        self._refresh_layer_list()
        self._center_canvas_item(item.item_id)
        self._notify(f"已加入 AI 图片：{item.name}")
        return True

    def _on_item_drop_requested(self, payload, scene_pos) -> None:
        if not isinstance(payload, dict):
            return
        if payload.get("kind") == "canvas_item":
            self._center_canvas_item(payload.get("item_id"))
            return
        item = self._build_item_from_resource(payload, scene_pos)
        if item is None:
            self._notify("\u8d44\u6e90\u5df2\u5931\u6548\uff0c\u65e0\u6cd5\u52a0\u5165\u753b\u5e03\u3002", is_error=True)
            return
        self.view.add_item_model(item)
        self._refresh_scene_target_combo()
        self._refresh_scene_list()
        self._refresh_layer_list()
        self._center_canvas_item(item.item_id)
        self._notify(f"\u5df2\u52a0\u5165\u753b\u5e03\uff1a{item.name}")

    def _on_resource_activated(self, payload) -> None:
        if not isinstance(payload, dict):
            return
        if self._center_or_add_resource_payload(payload):
            return
        item = self._build_item_from_resource(payload, self.view.mapToScene(self.view.viewport().rect().center()))
        if item is None:
            self._notify("\u8d44\u6e90\u65e0\u6cd5\u5bfc\u5165\u753b\u5e03\u3002", is_error=True)
            return
        self.view.add_item_model(item)
        self._refresh_scene_target_combo()
        self._refresh_scene_list()
        self._refresh_layer_list()
        self._center_canvas_item(item.item_id)
        self._notify(f"\u5df2\u52a0\u5165\u753b\u5e03\uff1a{item.name}")

    def _selected_single_item(self) -> CanvasGraphicsItem | None:
        items = [item for item in self.view._scene.selectedItems() if isinstance(item, CanvasGraphicsItem)]
        return items[0] if len(items) == 1 else None

    def _apply_selected_item(self) -> None:
        item = self._selected_single_item()
        if item is None:
            self._notify("请先选择一个对象再应用修改。", is_error=True)
            return
        data = self.property_panel.collect_single_update()
        model = item.to_model()
        model.name = data["name"] or model.name
        model.x = int(data["x"])
        model.y = int(data["y"])
        model.width = int(data["width"])
        model.height = int(data["height"])
        model.rotation = float(data["rotation"])
        model.opacity = float(data["opacity"])
        model.z_index = int(data["z_index"])
        model.visible = bool(data["visible"])
        model.locked = bool(data["locked"])
        model.crop = dict(data["crop"])
        model.audio = dict(data.get("audio") or {})
        model.filters = dict(data.get("filters") or {})
        model.chroma_key = dict(data.get("chroma_key") or {})
        item.apply_model(model)
        self.view._refresh_parent_assignment(item)
        self._push_history()
        self._sync_canvas_scene_frames_to_state()
        self._refresh_scene_target_combo()
        self._refresh_scene_list()
        self._refresh_layer_list()
        self._notify(f"已更新对象：{model.name}")
        self._on_selection_changed([model])

    def _delete_selected(self) -> None:
        self.view.delete_selected_items()
        self._push_history()
        self._sync_canvas_scene_frames_to_state()

    def _duplicate_selected(self) -> None:
        self.view.duplicate_selected_items()
        self._push_history()
        self._sync_canvas_scene_frames_to_state()

    def _toggle_selected_lock(self) -> None:
        models = self.view.selected_models()
        if not models:
            return
        for model in models:
            model.locked = not model.locked
            item = self.view._items.get(model.item_id)
            if item is not None:
                item.apply_model(model)
        self._push_history()
        self._sync_canvas_scene_frames_to_state()

    def _toggle_selected_visible(self) -> None:
        models = self.view.selected_models()
        if not models:
            return
        for model in models:
            model.visible = not model.visible
            item = self.view._items.get(model.item_id)
            if item is not None:
                item.apply_model(model)
        self._push_history()
        self._sync_canvas_scene_frames_to_state()

    def _document_for_export(self) -> CanvasDocument:
        doc = self.view.document()
        doc.name = self._current_document.name or doc.name
        doc.output_frame.width = self.canvas_width
        doc.output_frame.height = self.canvas_height
        return doc

    def _apply_document_to_scene(
        self,
        *,
        target_scene_id: str | None = None,
        create_new_scene: bool = False,
        activate: bool = False,
    ) -> tuple[bool, str, str | None]:
        document = self._document_for_export()
        target_scene_id = target_scene_id if not create_new_scene else None
        if target_scene_id is None and not create_new_scene:
            target_scene_id = self._current_scene_id
        ok, msg, scene_id = self.export_service.export_document(
            document,
            target_scene_id=target_scene_id,
            create_new_scene=create_new_scene,
            activate=activate,
        )
        if ok:
            self.scene_committed.emit(scene_id or "")
            self._notify(msg)
        else:
            self._notify(msg, is_error=True)
        return ok, msg, scene_id

    def _apply_to_current_scene(self) -> None:
        target_scene_id, create_new = self.property_panel.selected_scene_target()
        if target_scene_id is None and not create_new:
            self._notify("请选择一个要写回的场景。", is_error=True)
            return
        self._apply_document_to_scene(target_scene_id=target_scene_id, create_new_scene=create_new, activate=False)

    def _save_as_new_scene(self) -> None:
        self._apply_document_to_scene(target_scene_id=None, create_new_scene=True, activate=True)

    def _save_project(self) -> None:
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存画布工程",
            "canvas_project.json",
            "画布工程 (*.json);;所有文件 (*.*)",
        )
        if not file_path:
            return
        document = self._document_for_export()
        if self.export_service.save_document_to_file(document, file_path):
            self._notify(f"画布工程已保存：{Path(file_path).name}")
        else:
            self._notify("保存画布工程失败。", is_error=True)

    def _load_project(self) -> None:
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "加载画布工程",
            "",
            "画布工程 (*.json);;所有文件 (*.*)",
        )
        if not file_path:
            return
        document = self.export_service.load_document_from_file(file_path)
        if document is None:
            self._notify("加载画布工程失败。", is_error=True)
            return
        document.output_frame.width = self.canvas_width
        document.output_frame.height = self.canvas_height
        self._set_document(document, scene_id=self._current_scene_id)
        self._notify(f"画布工程已加载：{Path(file_path).name}")

    def _export_to_preview(self) -> None:
        target_scene_id, create_new = self.property_panel.selected_scene_target()
        ok, msg, _scene_id = self._apply_document_to_scene(target_scene_id=target_scene_id, create_new_scene=create_new, activate=False)
        if ok:
            self._notify(f"{msg}，并已同步到预览。")

    def _export_to_program(self) -> None:
        target_scene_id, create_new = self.property_panel.selected_scene_target()
        ok, msg, _scene_id = self._apply_document_to_scene(target_scene_id=target_scene_id, create_new_scene=create_new, activate=True)
        if ok:
            self._notify(f"{msg}，并已同步到节目。")

    def _import_current_scene(self) -> None:
        self._load_current_scene()

    def _undo(self) -> None:
        doc = self.history.undo()
        if doc is None:
            return
        self._loading_document = True
        self.view.set_document(doc)
        self._loading_document = False
        self._notify("已撤销。")

    def _redo(self) -> None:
        doc = self.history.redo()
        if doc is None:
            return
        self._loading_document = True
        self.view.set_document(doc)
        self._loading_document = False
        self._notify("已重做。")

    def closeEvent(self, event):  # noqa: N802
        self._audio_mixer_timer.stop()
        if self._audio_mixer_dialog is not None:
            self._audio_mixer_dialog.close()
            self._audio_mixer_dialog = None
        if self._ai_model_dialog is not None:
            self._ai_model_dialog.close()
            self._ai_model_dialog = None
        self.closed.emit()
        super().closeEvent(event)

