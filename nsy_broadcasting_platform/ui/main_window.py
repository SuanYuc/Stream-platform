from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSize, Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QButtonGroup,
    QCheckBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QListView,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSlider,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
    QComboBox,
)

from nsy_broadcasting_platform.audio.audio_controller import AudioController
from nsy_broadcasting_platform.ai_models import AISettingsStore
from nsy_broadcasting_platform.capture.source_manager import SourceManager
from nsy_broadcasting_platform.capture.window_capture_win import enum_windows
from nsy_broadcasting_platform.canvas.workspace import InfiniteCanvasDialog
from nsy_broadcasting_platform.config import AppConfig
from nsy_broadcasting_platform.core.state import AppState
from nsy_broadcasting_platform.models import Layer, LayerType, Scene, TransitionConfig, new_id
from nsy_broadcasting_platform.output.adaptive_bitrate import AdaptiveBitrateController
from nsy_broadcasting_platform.output.output_manager import OutputManager
from nsy_broadcasting_platform.preferences import PreferenceStore, UserPreferences
from nsy_broadcasting_platform.render.compositor import Compositor
from nsy_broadcasting_platform.render.render_thread import RenderThread
from nsy_broadcasting_platform.semantic_director import SemanticRecommendationWorker, SemanticSceneFrame
from nsy_broadcasting_platform.ui.ai_feature_dialogs import (
    ARFeatureDialog,
    AnomalyDetectionDialog,
    FaceEffectDialog,
    SemanticDirectorDialog,
    VirtualAdDialog,
    VirtualBackgroundDialog,
)
from nsy_broadcasting_platform.ui.ai_model_dialog import AIModelWorkbenchDialog
from nsy_broadcasting_platform.ui.audio_mixer_dialog import AudioMixerDialog
from nsy_broadcasting_platform.ui.layer_item_widget import LayerItemWidget
from nsy_broadcasting_platform.ui.preview_widget import PreviewWidget
from nsy_broadcasting_platform.ui.scene_item_widget import SceneItemWidget
from nsy_broadcasting_platform.ui.theme import HAULIX_APP_QSS, status_badge_qss
from nsy_broadcasting_platform.utils import (
    ar_effect_label,
    canonical_ar_effect_type,
    canonical_onnx_style,
    default_ar_sticker_path,
    get_face_effect_status,
    is_default_ar_sticker_path,
    onnx_style_label,
    preload_onnx_style_filter,
    prewarm_mediapipe_components,
)


def _refresh_custom_list_widgets(
    list_widget: QListWidget,
    selected_id: str | None = None,
    *,
    min_height: int = 0,
) -> None:
    """重新计算 QListWidget 内自定义卡片高度，避免新增后被首次布局裁切。"""
    try:
        selected_item: QListWidgetItem | None = None
        for i in range(list_widget.count()):
            item = list_widget.item(i)
            widget = list_widget.itemWidget(item)
            if widget is None:
                continue
            if min_height > 0:
                widget.setMinimumHeight(max(widget.minimumHeight(), min_height))
            widget.adjustSize()
            widget.updateGeometry()
            hint = widget.sizeHint()
            if hint.isValid():
                item.setSizeHint(hint.expandedTo(QSize(0, min_height)))
            if selected_id is not None and item.data(Qt.ItemDataRole.UserRole) == selected_id:
                selected_item = item

        if selected_item is not None:
            was_blocked = list_widget.blockSignals(True)
            list_widget.setCurrentItem(selected_item)
            list_widget.blockSignals(was_blocked)
            list_widget.scrollToItem(selected_item, QAbstractItemView.ScrollHint.PositionAtCenter)

        list_widget.doItemsLayout()
        list_widget.viewport().update()
        list_widget.updateGeometry()
    except RuntimeError:
        # 弹窗关闭时，延迟布局回调可能遇到已释放的 Qt 对象，直接忽略即可。
        return


def _schedule_custom_list_widgets_refresh(
    list_widget: QListWidget,
    selected_id: str | None = None,
    *,
    min_height: int = 0,
) -> None:
    """当前事件循环和下一事件循环各刷新一次，覆盖 QFileDialog 返回后的延迟布局。"""
    _refresh_custom_list_widgets(list_widget, selected_id, min_height=min_height)
    QTimer.singleShot(
        0,
        lambda: _refresh_custom_list_widgets(list_widget, selected_id, min_height=min_height),
    )
    QTimer.singleShot(
        80,
        lambda: _refresh_custom_list_widgets(list_widget, selected_id, min_height=min_height),
    )


class PreviewPopoutWindow(QDialog):
    """独立预览窗口，复用 PreviewWidget 的编辑交互能力。"""

    closed = pyqtSignal()

    def __init__(
        self,
        canvas_width: int,
        canvas_height: int,
        parent=None,
        default_rtmp_url: str = "rtmp://localhost/live/stream",
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("预览与节目输出 - 独立窗口")
        self.resize(1180, 680)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setStyleSheet(HAULIX_APP_QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        director_row = QHBoxLayout()
        director_row.addWidget(QLabel("显示场景:"))
        self.scene_combo = QComboBox()
        director_row.addWidget(self.scene_combo, 2)
        director_row.addSpacing(12)
        director_row.addWidget(QLabel("输出比例:"))
        self.aspect_combo = QComboBox()
        for label in ("4:3", "16:9", "16:10", "21:9"):
            self.aspect_combo.addItem(label, label)
        director_row.addWidget(self.aspect_combo)
        director_row.addSpacing(12)
        director_row.addWidget(QLabel("输出质量:"))
        self.output_quality_combo = QComboBox()
        for key, meta in MainWindow.OUTPUT_QUALITY_PRESETS.items():
            self.output_quality_combo.addItem(meta["label"], key)
        director_row.addWidget(self.output_quality_combo)
        self.adaptive_bitrate_check = QCheckBox("网络自适应")
        self.adaptive_bitrate_label = QLabel("ABR: 待机")
        self.adaptive_bitrate_label.setObjectName("StatusLabel")
        director_row.addWidget(self.adaptive_bitrate_check)
        director_row.addWidget(self.adaptive_bitrate_label)
        director_row.addSpacing(12)
        director_row.addWidget(QLabel("节目延时(ms):"))
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 5000)
        director_row.addWidget(self.delay_spin)
        self.btn_transition_settings = QPushButton("转场设置")
        self.btn_transition_settings.setProperty("role", "toolbar")
        self.btn_emergency_placeholder = QPushButton("紧急占位")
        self.btn_emergency_placeholder.setCheckable(True)
        self.btn_emergency_placeholder.setProperty("role", "danger")
        self.btn_choose_placeholder_video = QPushButton("设置占位视频")
        self.btn_choose_placeholder_video.setProperty("role", "toolbar")
        director_row.addWidget(self.btn_transition_settings)
        director_row.addWidget(self.btn_emergency_placeholder)
        director_row.addWidget(self.btn_choose_placeholder_video)
        director_row.addStretch(1)
        root.addLayout(director_row)

        edit_mode_row = QHBoxLayout()
        edit_mode_row.addWidget(QLabel("编辑模式:"))
        self.btn_edit_position = QPushButton("位置编辑")
        self.btn_edit_size = QPushButton("大小编辑")
        self.btn_edit_lock = QPushButton("锁定")
        self.edit_mode_group = QButtonGroup(self)
        self.edit_mode_group.setExclusive(True)
        for button in (self.btn_edit_position, self.btn_edit_size, self.btn_edit_lock):
            button.setCheckable(True)
            button.setProperty("role", "toggle")
            self.edit_mode_group.addButton(button)
            edit_mode_row.addWidget(button)
        edit_mode_row.addStretch(1)
        root.addLayout(edit_mode_row)

        preview_row = QHBoxLayout()
        preview_row.setSpacing(8)
        self.edit_preview = PreviewWidget("编辑预览", canvas_width, canvas_height, editable=True)
        self.program_preview = PreviewWidget("节目输出", canvas_width, canvas_height, editable=False)
        self.edit_preview.setMinimumSize(520, 360)
        self.program_preview.setMinimumSize(520, 360)
        preview_row.addWidget(self.edit_preview, 1)
        preview_row.addWidget(self.program_preview, 1)
        root.addLayout(preview_row, 1)

        output_row = QHBoxLayout()
        output_row.addWidget(QLabel("RTMP 地址:"))
        self.rtmp_edit = QLineEdit(default_rtmp_url)
        self.btn_stream_start = QPushButton("开始推流")
        self.btn_stream_stop = QPushButton("停止推流")
        self.btn_record_start = QPushButton("开始录制")
        self.btn_record_stop = QPushButton("停止录制")
        self.btn_stream_start.setProperty("role", "primary")
        self.btn_record_start.setProperty("role", "primary")
        self.btn_stream_stop.setProperty("role", "danger")
        self.btn_record_stop.setProperty("role", "danger")
        output_row.addWidget(self.rtmp_edit, 1)
        output_row.addWidget(self.btn_stream_start)
        output_row.addWidget(self.btn_stream_stop)
        output_row.addWidget(self.btn_record_start)
        output_row.addWidget(self.btn_record_stop)
        root.addLayout(output_row)

        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("推流状态:"))
        self.stream_status_badge = QLabel("未运行")
        self.stream_status_badge.setObjectName("StreamStatusBadge")
        status_row.addWidget(self.stream_status_badge)
        status_row.addSpacing(16)
        status_row.addWidget(QLabel("录制状态:"))
        self.record_status_badge = QLabel("未运行")
        self.record_status_badge.setObjectName("RecordStatusBadge")
        status_row.addWidget(self.record_status_badge)
        status_row.addSpacing(16)
        self.encoder_status_label = QLabel("编码: 自动 CPU+GPU")
        self.encoder_status_label.setObjectName("StatusLabel")
        status_row.addWidget(self.encoder_status_label)
        status_row.addStretch(1)
        root.addLayout(status_row)

        monitor_row = QHBoxLayout()
        self.btn_audio_monitor = QPushButton("开启监听")
        self.btn_audio_monitor.setCheckable(True)
        self.btn_audio_monitor.setProperty("role", "toggle")
        self.monitor_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.monitor_volume_slider.setRange(0, 200)
        self.monitor_volume_slider.setValue(60)
        self.monitor_volume_value = QLabel("60%")
        self.monitor_status_label = QLabel("监听: 关闭")
        monitor_row.addWidget(self.btn_audio_monitor)
        monitor_row.addWidget(QLabel("监听音量:"))
        monitor_row.addWidget(self.monitor_volume_slider, 1)
        monitor_row.addWidget(self.monitor_volume_value)
        monitor_row.addWidget(self.monitor_status_label)
        root.addLayout(monitor_row)

    def closeEvent(self, event):  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)


class SceneGridPopoutWindow(QDialog):
    """独立场景网格窗口，用更大的缩略画布做导播场景选择。"""

    closed = pyqtSignal()
    scene_selected = pyqtSignal(str)
    grid_changed = pyqtSignal(int)
    clear_scene_clicked = pyqtSignal()
    transition_clicked = pyqtSignal()
    emergency_toggled = pyqtSignal(bool)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("场景管理 - 独立窗口")
        self.resize(1220, 820)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._grid_columns = 3
        self._scene_item_widgets: dict[str, SceneItemWidget] = {}

        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        header = QHBoxLayout()
        self.current_scene_label = QLabel("当前场景: 未选择")
        self.current_scene_label.setObjectName("CurrentSceneLabel")
        header.addWidget(self.current_scene_label, 1)
        header.addWidget(QLabel("布局:"))
        self.grid_combo = QComboBox()
        self.grid_combo.addItem("2×2", 2)
        self.grid_combo.addItem("3×3", 3)
        self.grid_combo.addItem("4×4", 4)
        self.grid_combo.setFixedWidth(92)
        header.addWidget(self.grid_combo)
        self.btn_clear_scene = QPushButton("清空选中场景")
        self.btn_clear_scene.setProperty("role", "danger")
        self.btn_transition_settings = QPushButton("转场设置")
        self.btn_transition_settings.setProperty("role", "toolbar")
        self.btn_emergency_placeholder = QPushButton("紧急占位")
        self.btn_emergency_placeholder.setCheckable(True)
        self.btn_emergency_placeholder.setProperty("role", "danger")
        header.addWidget(self.btn_clear_scene)
        header.addWidget(self.btn_transition_settings)
        header.addWidget(self.btn_emergency_placeholder)
        root.addLayout(header)

        status_row = QHBoxLayout()
        self.transition_status_label = QLabel("转场: 硬切")
        self.transition_status_label.setObjectName("StatusLabel")
        self.placeholder_status_label = QLabel("占位: 未启用")
        self.placeholder_status_label.setObjectName("StatusLabel")
        status_row.addWidget(self.transition_status_label, 1)
        status_row.addWidget(self.placeholder_status_label, 1)
        root.addLayout(status_row)

        self.scene_list = QListWidget()
        self.scene_list.setObjectName("SceneList")
        self.scene_list.setViewMode(QListView.ViewMode.IconMode)
        self.scene_list.setFlow(QListView.Flow.LeftToRight)
        self.scene_list.setWrapping(True)
        self.scene_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.scene_list.setMovement(QListView.Movement.Static)
        self.scene_list.setSpacing(4)
        root.addWidget(self.scene_list, 1)

        self.grid_combo.currentIndexChanged.connect(self._on_grid_combo_changed)
        self.scene_list.currentItemChanged.connect(self._on_scene_item_changed)
        self.btn_clear_scene.clicked.connect(self.clear_scene_clicked.emit)
        self.btn_transition_settings.clicked.connect(self.transition_clicked.emit)
        self.btn_emergency_placeholder.toggled.connect(self.emergency_toggled.emit)
        self.set_grid_columns(3)

    def set_grid_columns(self, columns: int) -> None:
        self._grid_columns = max(2, min(4, int(columns)))
        index = self.grid_combo.findData(self._grid_columns)
        self.grid_combo.blockSignals(True)
        self.grid_combo.setCurrentIndex(max(0, index))
        self.grid_combo.blockSignals(False)
        self._apply_grid_layout()

    def set_current_scene_name(self, name: str) -> None:
        self.current_scene_label.setText(f"当前场景: {name or '未选择'}")

    def set_transition_text(self, text: str) -> None:
        self.transition_status_label.setText(text)

    def set_emergency_state(self, active: bool, button_text: str, status_text: str) -> None:
        self.btn_emergency_placeholder.blockSignals(True)
        self.btn_emergency_placeholder.setChecked(active)
        self.btn_emergency_placeholder.setText(button_text)
        self.btn_emergency_placeholder.blockSignals(False)
        self.placeholder_status_label.setText(status_text)

    def set_scenes(self, scenes: list[Scene], active_scene_id: str | None, preview_cache: dict[str, object]) -> None:
        self.scene_list.blockSignals(True)
        self.scene_list.clear()
        self._scene_item_widgets.clear()
        active_row = 0
        for index, scene in enumerate(scenes):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, scene.id)
            widget = SceneItemWidget(scene.name)
            widget.thumb_label.setFixedSize(self._scene_thumb_size())
            image = preview_cache.get(scene.id)
            if image is not None:
                widget.set_preview_image(image)
            item.setSizeHint(self.scene_list.gridSize())
            self.scene_list.addItem(item)
            self.scene_list.setItemWidget(item, widget)
            self._scene_item_widgets[scene.id] = widget
            if scene.id == active_scene_id:
                active_row = index
        if self.scene_list.count() > 0:
            self.scene_list.setCurrentRow(active_row)
        self.scene_list.blockSignals(False)
        self._highlight_scene_items()
        active_scene = next((scene for scene in scenes if scene.id == active_scene_id), None)
        self.set_current_scene_name(active_scene.name if active_scene is not None else "")

    def update_scene_preview(self, scene_id: str, image) -> None:
        widget = self._scene_item_widgets.get(scene_id)
        if widget is not None:
            widget.set_preview_image(image)

    def _scene_thumb_size(self) -> QSize:
        cell = self.scene_list.gridSize()
        return QSize(max(120, cell.width() - 18), max(68, cell.height() - 50))

    def _apply_grid_layout(self) -> None:
        columns = max(1, self._grid_columns)
        viewport_width = self.scene_list.viewport().width() or self.scene_list.width()
        viewport_height = self.scene_list.viewport().height() or self.scene_list.height()
        spacing = self.scene_list.spacing()
        available_w = max(520, viewport_width - spacing * (columns + 1) - 8)
        available_h = max(420, viewport_height - spacing * (columns + 1) - 8)
        cell_w = max(160, int(available_w / columns))
        cell_h = max(124, int(available_h / columns))
        self.scene_list.setGridSize(QSize(cell_w, cell_h))
        thumb_size = self._scene_thumb_size()
        for i in range(self.scene_list.count()):
            item = self.scene_list.item(i)
            item.setSizeHint(self.scene_list.gridSize())
            widget = self.scene_list.itemWidget(item)
            if not isinstance(widget, SceneItemWidget):
                continue
            image = widget.current_preview_image()
            widget.thumb_label.setFixedSize(thumb_size)
            if image is not None:
                widget.set_preview_image(image)

    def _highlight_scene_items(self) -> None:
        current = self.scene_list.currentItem()
        current_id = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        for i in range(self.scene_list.count()):
            item = self.scene_list.item(i)
            widget = self.scene_list.itemWidget(item)
            if isinstance(widget, SceneItemWidget):
                widget.set_selected(item.data(Qt.ItemDataRole.UserRole) == current_id)

    def _on_grid_combo_changed(self, _index: int) -> None:
        self.grid_changed.emit(int(self.grid_combo.currentData() or 3))

    def _on_scene_item_changed(self, current: QListWidgetItem | None, _prev: QListWidgetItem | None) -> None:
        self._highlight_scene_items()
        if current is None:
            return
        self.scene_selected.emit(str(current.data(Qt.ItemDataRole.UserRole)))

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply_grid_layout()

    def closeEvent(self, event):  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)


class PlaceholderSceneDialog(QDialog):
    """紧急占位场景独立编辑窗口，不占用普通导播场景网格。"""

    closed = pyqtSignal()
    add_camera_clicked = pyqtSignal()
    add_screen_clicked = pyqtSignal()
    add_window_clicked = pyqtSignal()
    add_image_clicked = pyqtSignal()
    add_network_clicked = pyqtSignal()
    choose_video_clicked = pyqtSignal()
    layer_selected = pyqtSignal(str)
    layer_transform_changed = pyqtSignal(str, int, int, int, int)
    layer_deleted = pyqtSignal(str)
    layer_lock_changed = pyqtSignal(str, bool)
    layer_enabled_changed = pyqtSignal(str, bool)
    layer_priority_changed = pyqtSignal(str, int)
    layer_volume_changed = pyqtSignal(str, float)
    layer_saturation_changed = pyqtSignal(str, float)
    layer_contrast_changed = pyqtSignal(str, float)
    layer_color_temp_changed = pyqtSignal(str, int)
    layer_mosaic_changed = pyqtSignal(str, int)
    layer_onnx_style_changed = pyqtSignal(str, str)
    layer_face_enabled_changed = pyqtSignal(str, bool)
    layer_face_effect_changed = pyqtSignal(str, str)
    layer_face_scale_changed = pyqtSignal(str, int)
    layer_face_smoothing_changed = pyqtSignal(str, int)
    layer_virtual_bg_enabled_changed = pyqtSignal(str, bool)
    layer_virtual_bg_mode_changed = pyqtSignal(str, str)
    layer_virtual_bg_blur_changed = pyqtSignal(str, int)

    def __init__(self, canvas_width: int, canvas_height: int, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("紧急占位场景编辑")
        self.resize(980, 720)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._selected_layer_id: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        header = QHBoxLayout()
        self.title_label = QLabel("紧急占位场景")
        self.title_label.setObjectName("CurrentSceneLabel")
        self.btn_choose_video = QPushButton("设置循环占位视频")
        self.btn_choose_video.setProperty("role", "toolbar")
        header.addWidget(self.title_label, 1)
        header.addWidget(self.btn_choose_video)
        root.addLayout(header)

        self.preview = PreviewWidget("占位场景预览", canvas_width, canvas_height, editable=True)
        self.preview.setMinimumSize(520, 300)
        root.addWidget(self.preview, 1)

        add_row = QHBoxLayout()
        self.btn_add_camera = QPushButton("添加相机")
        self.btn_add_screen = QPushButton("添加屏幕")
        self.btn_add_window = QPushButton("添加窗口")
        self.btn_add_image = QPushButton("添加图片")
        self.btn_add_network = QPushButton("添加网络流")
        for button in (
            self.btn_add_camera,
            self.btn_add_screen,
            self.btn_add_window,
            self.btn_add_image,
            self.btn_add_network,
        ):
            button.setProperty("role", "toolbar")
            add_row.addWidget(button)
        root.addLayout(add_row)

        self.layer_list = QListWidget()
        self.layer_list.setSpacing(6)
        self.layer_list.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        root.addWidget(self.layer_list, 1)

        self.preview.layer_selected.connect(self._on_preview_layer_selected)
        self.preview.layer_transform_changed.connect(self.layer_transform_changed.emit)
        self.layer_list.currentItemChanged.connect(self._on_layer_item_selected)
        self.btn_choose_video.clicked.connect(self.choose_video_clicked.emit)
        self.btn_add_camera.clicked.connect(self.add_camera_clicked.emit)
        self.btn_add_screen.clicked.connect(self.add_screen_clicked.emit)
        self.btn_add_window.clicked.connect(self.add_window_clicked.emit)
        self.btn_add_image.clicked.connect(self.add_image_clicked.emit)
        self.btn_add_network.clicked.connect(self.add_network_clicked.emit)

    def set_canvas_size(self, width: int, height: int) -> None:
        self.preview.set_canvas_size(width, height)

    def set_selected_layer(self, layer_id: str | None) -> None:
        self._selected_layer_id = layer_id
        self.preview.set_selected_layer(layer_id)
        for i in range(self.layer_list.count()):
            item = self.layer_list.item(i)
            widget = self.layer_list.itemWidget(item)
            if isinstance(widget, LayerItemWidget):
                widget.set_selected(item.data(Qt.ItemDataRole.UserRole) == layer_id)
        _schedule_custom_list_widgets_refresh(self.layer_list, layer_id, min_height=132)

    def set_scene(self, scene, preview_image=None) -> None:
        self.title_label.setText(scene.name if scene is not None else "紧急占位场景")
        self.preview.set_scene(scene)
        self.preview.set_scene_name(scene.name if scene is not None else "")
        if preview_image is not None:
            self.preview.set_frame(preview_image)
        self._refresh_layers(scene)

    def _refresh_layers(self, scene) -> None:
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        if scene is not None:
            max_priority = max(1, len(scene.layers))
            for layer in sorted(scene.layers, key=lambda item: item.priority, reverse=True):
                item = QListWidgetItem()
                item.setData(Qt.ItemDataRole.UserRole, layer.id)
                widget = LayerItemWidget(layer, max_priority=max_priority)
                widget.set_selected(layer.id == self._selected_layer_id)
                widget.lock_changed.connect(self.layer_lock_changed.emit)
                widget.delete_clicked.connect(self.layer_deleted.emit)
                widget.enabled_changed.connect(self.layer_enabled_changed.emit)
                widget.priority_changed.connect(self.layer_priority_changed.emit)
                widget.volume_changed.connect(self.layer_volume_changed.emit)
                widget.saturation_changed.connect(self.layer_saturation_changed.emit)
                widget.contrast_changed.connect(self.layer_contrast_changed.emit)
                widget.color_temp_changed.connect(self.layer_color_temp_changed.emit)
                widget.mosaic_changed.connect(self.layer_mosaic_changed.emit)
                widget.onnx_style_changed.connect(self.layer_onnx_style_changed.emit)
                widget.face_enabled_changed.connect(self.layer_face_enabled_changed.emit)
                widget.face_effect_changed.connect(self.layer_face_effect_changed.emit)
                widget.face_scale_changed.connect(self.layer_face_scale_changed.emit)
                widget.face_smoothing_changed.connect(self.layer_face_smoothing_changed.emit)
                widget.virtual_bg_enabled_changed.connect(self.layer_virtual_bg_enabled_changed.emit)
                widget.virtual_bg_mode_changed.connect(self.layer_virtual_bg_mode_changed.emit)
                widget.virtual_bg_blur_changed.connect(self.layer_virtual_bg_blur_changed.emit)
                item.setSizeHint(widget.sizeHint())
                self.layer_list.addItem(item)
                self.layer_list.setItemWidget(item, widget)
        self.layer_list.blockSignals(False)
        _schedule_custom_list_widgets_refresh(self.layer_list, self._selected_layer_id, min_height=132)

    def _on_preview_layer_selected(self, layer_id: str) -> None:
        self.set_selected_layer(layer_id)
        self.layer_selected.emit(layer_id)

    def _on_layer_item_selected(self, current: QListWidgetItem | None, _prev: QListWidgetItem | None) -> None:
        if current is None:
            self.set_selected_layer(None)
            return
        layer_id = str(current.data(Qt.ItemDataRole.UserRole))
        self.set_selected_layer(layer_id)
        self.layer_selected.emit(layer_id)

    def closeEvent(self, event):  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)


class TransitionDialog(QDialog):
    """导播转场设置窗口，保存节目输出链路使用的转场参数。"""

    config_applied = pyqtSignal(object)
    closed = pyqtSignal()

    MODES = (
        ("硬切", "cut"),
        ("化像 / 叠化", "dissolve"),
        ("划像", "wipe"),
        ("DVE 数字视频特效", "dve"),
        ("自定义图片/视频", "media"),
    )
    WIPE_SHAPES = (
        ("水平线", "horizontal"),
        ("垂直线", "vertical"),
        ("圆形", "circle"),
        ("对角线", "diagonal"),
    )
    DVE_MODES = (
        ("推拉", "push"),
        ("旋转缩放", "rotate"),
        ("翻页", "page"),
        ("挤压切换", "squeeze"),
    )

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("转场设置")
        self.resize(560, 360)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        title = QLabel("导播转场配置")
        title.setObjectName("StatusLabel")
        root.addWidget(title)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("转场模式:"))
        self.mode_combo = QComboBox()
        for label, value in self.MODES:
            self.mode_combo.addItem(label, value)
        mode_row.addWidget(self.mode_combo, 1)
        root.addLayout(mode_row)

        duration_row = QHBoxLayout()
        duration_row.addWidget(QLabel("持续时间(ms):"))
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(0, 5000)
        self.duration_spin.setSingleStep(100)
        duration_row.addWidget(self.duration_spin)
        duration_row.addStretch(1)
        root.addLayout(duration_row)

        wipe_row = QHBoxLayout()
        wipe_row.addWidget(QLabel("划像形状:"))
        self.wipe_combo = QComboBox()
        for label, value in self.WIPE_SHAPES:
            self.wipe_combo.addItem(label, value)
        wipe_row.addWidget(self.wipe_combo, 1)
        root.addLayout(wipe_row)

        dve_row = QHBoxLayout()
        dve_row.addWidget(QLabel("DVE 类型:"))
        self.dve_combo = QComboBox()
        for label, value in self.DVE_MODES:
            self.dve_combo.addItem(label, value)
        dve_row.addWidget(self.dve_combo, 1)
        root.addLayout(dve_row)

        media_row = QHBoxLayout()
        media_row.addWidget(QLabel("自定义素材:"))
        self.media_edit = QLineEdit()
        self.media_edit.setPlaceholderText("可选择 PNG/JPG/BMP 或 MP4/MOV/AVI 等视频")
        self.btn_choose_media = QPushButton("选择素材")
        self.btn_choose_media.setProperty("role", "toolbar")
        media_row.addWidget(self.media_edit, 1)
        media_row.addWidget(self.btn_choose_media)
        root.addLayout(media_row)

        action_row = QHBoxLayout()
        action_row.addStretch(1)
        self.btn_apply = QPushButton("应用转场")
        self.btn_apply.setProperty("role", "primary")
        self.btn_close = QPushButton("关闭")
        action_row.addWidget(self.btn_apply)
        action_row.addWidget(self.btn_close)
        root.addLayout(action_row)

        self.mode_combo.currentIndexChanged.connect(self._sync_mode_controls)
        self.btn_choose_media.clicked.connect(self._choose_media)
        self.btn_apply.clicked.connect(self._apply)
        self.btn_close.clicked.connect(self.close)
        self._sync_mode_controls()

    def set_config(self, config: TransitionConfig) -> None:
        self.mode_combo.blockSignals(True)
        index = self.mode_combo.findData(config.mode)
        self.mode_combo.setCurrentIndex(max(0, index))
        self.mode_combo.blockSignals(False)
        self.duration_spin.setValue(max(0, min(5000, int(config.duration_ms))))
        index = self.wipe_combo.findData(config.wipe_shape)
        self.wipe_combo.setCurrentIndex(max(0, index))
        index = self.dve_combo.findData(config.dve_mode)
        self.dve_combo.setCurrentIndex(max(0, index))
        self.media_edit.setText(config.media_path)
        self._sync_mode_controls()

    def _current_config(self) -> TransitionConfig:
        return TransitionConfig(
            mode=str(self.mode_combo.currentData() or "cut"),
            duration_ms=int(self.duration_spin.value()),
            wipe_shape=str(self.wipe_combo.currentData() or "horizontal"),
            dve_mode=str(self.dve_combo.currentData() or "push"),
            media_path=self.media_edit.text().strip(),
        )

    def _sync_mode_controls(self) -> None:
        mode = str(self.mode_combo.currentData() or "cut")
        self.wipe_combo.setEnabled(mode == "wipe")
        self.dve_combo.setEnabled(mode == "dve")
        self.media_edit.setEnabled(mode == "media")
        self.btn_choose_media.setEnabled(mode == "media")
        self.duration_spin.setEnabled(mode != "cut")

    def _choose_media(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择自定义转场素材",
            str(Path.cwd()),
            "转场素材 (*.png *.jpg *.jpeg *.bmp *.mp4 *.mov *.avi *.mkv *.flv *.wmv);;全部文件 (*.*)",
        )
        if path:
            self.media_edit.setText(path)
            index = self.mode_combo.findData("media")
            if index >= 0:
                self.mode_combo.setCurrentIndex(index)

    def _apply(self) -> None:
        self.config_applied.emit(self._current_config())

    def closeEvent(self, event):  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)


class LayerPriorityRow(QWidget):
    priority_changed = pyqtSignal(str, int)
    selected = pyqtSignal(str)
    deleted = pyqtSignal(str)
    volume_changed = pyqtSignal(str, float)
    saturation_changed = pyqtSignal(str, float)
    contrast_changed = pyqtSignal(str, float)
    color_temp_changed = pyqtSignal(str, int)
    mosaic_changed = pyqtSignal(str, int)
    onnx_style_changed = pyqtSignal(str, str)
    face_enabled_changed = pyqtSignal(str, bool)
    face_effect_changed = pyqtSignal(str, str)
    face_scale_changed = pyqtSignal(str, int)
    face_smoothing_changed = pyqtSignal(str, int)
    virtual_bg_enabled_changed = pyqtSignal(str, bool)
    virtual_bg_mode_changed = pyqtSignal(str, str)
    virtual_bg_blur_changed = pyqtSignal(str, int)

    _SMART_SUPPORTED = {
        LayerType.CAMERA,
        LayerType.SCREEN,
        LayerType.WINDOW,
        LayerType.NETWORK,
        LayerType.VIDEO,
    }

    def __init__(self, layer: Layer, max_priority: int) -> None:
        super().__init__()
        self.layer_id = layer.id
        self.layer_type = layer.layer_type
        self.max_priority = max(1, int(max_priority))
        self._volume_value = max(0, min(200, int(layer.volume * 100)))
        self._saturation_value = max(0, min(200, int(layer.saturation * 100)))
        self._contrast_value = max(0, min(200, int(layer.contrast * 100)))
        self._color_temp_value = max(-100, min(100, int(layer.color_temp)))
        self._mosaic_value = max(0, min(100, int(layer.mosaic)))
        self._source = dict(layer.source or {})
        self._onnx_style_value = self._canonical_onnx_style(self._source.get("onnx_style", "none"))
        self._smart_supported = layer.layer_type in self._SMART_SUPPORTED

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 6, 8, 6)
        root.setSpacing(6)

        header = QHBoxLayout()
        self.name_label = QLabel(f"{layer.name} [{layer.layer_type.value}]")
        self.name_label.setToolTip("优先级范围为 1 到当前场景图层数量，编号越大，画面越靠上")
        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(1, self.max_priority)
        self.priority_spin.setValue(max(1, min(self.max_priority, int(layer.priority or 1))))
        self.priority_spin.setFixedWidth(86)
        self.priority_spin.setFixedHeight(34)
        self.btn_select = QPushButton("选中")
        self.btn_select.setMinimumSize(82, 36)
        self.btn_delete = QPushButton("删除")
        self.btn_delete.setProperty("role", "danger")
        self.btn_delete.setMinimumSize(82, 36)

        header.addWidget(self.name_label, 1)
        header.addWidget(QLabel("优先级"))
        header.addWidget(self.priority_spin)
        header.addWidget(self.btn_select)
        header.addWidget(self.btn_delete)
        root.addLayout(header)

        param_row = QHBoxLayout()
        param_row.setSpacing(8)
        self.btn_audio = QPushButton("音频")
        self.btn_filter = QPushButton("滤镜")
        self.btn_color = QPushButton("色彩校正")
        self.btn_ai = QPushButton("智能增强")
        for button in (self.btn_audio, self.btn_filter, self.btn_color, self.btn_ai):
            button.setProperty("role", "toolbar")
            button.setMinimumHeight(38)
            param_row.addWidget(button, 1)
        self.btn_ai.setEnabled(self._smart_supported)
        self.btn_ai.setToolTip("" if self._smart_supported else "当前图层不支持智能增强")
        root.addLayout(param_row)

        self.priority_spin.valueChanged.connect(lambda value: self.priority_changed.emit(self.layer_id, int(value)))
        self.btn_select.clicked.connect(lambda: self.selected.emit(self.layer_id))
        self.btn_delete.clicked.connect(lambda: self.deleted.emit(self.layer_id))
        self.btn_audio.clicked.connect(self._open_audio_panel)
        self.btn_filter.clicked.connect(self._open_filter_panel)
        self.btn_color.clicked.connect(self._open_color_panel)
        self.btn_ai.clicked.connect(self._open_ai_panel)

    @staticmethod
    def _build_slider(min_v: int, max_v: int, value: int) -> QSlider:
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(min_v, max_v)
        slider.setValue(max(min_v, min(max_v, int(value))))
        slider.setMinimumWidth(86)
        return slider

    def _slider_row(
        self,
        layout: QVBoxLayout,
        label_text: str,
        min_v: int,
        max_v: int,
        value: int,
        suffix: str = "",
    ) -> tuple[QSlider, QLabel]:
        row = QHBoxLayout()
        label = QLabel(label_text)
        label.setFixedWidth(84)
        value_label = QLabel(f"{value}{suffix}")
        value_label.setFixedWidth(58)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        slider = self._build_slider(min_v, max_v, value)
        row.addWidget(label)
        row.addWidget(slider, 1)
        row.addWidget(value_label)
        layout.addLayout(row)
        return slider, value_label

    def _make_panel(self, title: str) -> tuple[QDialog, QVBoxLayout]:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setMinimumWidth(380)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        return dialog, layout

    @staticmethod
    def _add_close_button(layout: QVBoxLayout, dialog: QDialog) -> None:
        row = QHBoxLayout()
        row.addStretch(1)
        btn = QPushButton("关闭")
        btn.setMinimumWidth(96)
        btn.clicked.connect(dialog.accept)
        row.addWidget(btn)
        layout.addLayout(row)

    def _open_audio_panel(self) -> None:
        dialog, layout = self._make_panel("音频调节")
        label = "窗口音量" if self.layer_type == LayerType.WINDOW else "图层音量"
        slider, value_label = self._slider_row(layout, label, 0, 200, self._volume_value, "%")

        def on_changed(value: int) -> None:
            self._volume_value = int(value)
            value_label.setText(f"{self._volume_value}%")
            self.volume_changed.emit(self.layer_id, self._volume_value / 100.0)

        slider.valueChanged.connect(on_changed)
        self._add_close_button(layout, dialog)
        dialog.exec()

    def _open_filter_panel(self) -> None:
        dialog, layout = self._make_panel("滤镜")
        slider, value_label = self._slider_row(layout, "马赛克", 0, 100, self._mosaic_value, "%")

        style_row = QHBoxLayout()
        style_row.addWidget(QLabel("ONNX风格"))
        style_combo = QComboBox()
        style_combo.addItem("关闭", "none")
        style_combo.addItem("卡通化", "cartoon")
        style_combo.addItem("莫奈风格", "monet")
        style_combo.addItem("梵高风格", "vangogh")
        style_combo.setCurrentIndex(max(0, style_combo.findData(self._onnx_style_value)))
        style_row.addWidget(style_combo, 1)
        apply_style_btn = QPushButton("应用")
        apply_style_btn.setProperty("role", "primary")
        apply_style_btn.setMinimumWidth(76)
        style_row.addWidget(apply_style_btn)
        layout.addLayout(style_row)

        def on_changed(value: int) -> None:
            self._mosaic_value = int(value)
            value_label.setText(f"{self._mosaic_value}%")
            self.mosaic_changed.emit(self.layer_id, self._mosaic_value)

        def apply_style() -> None:
            self._onnx_style_value = self._canonical_onnx_style(style_combo.currentData())
            self._source["onnx_style"] = self._onnx_style_value
            self.onnx_style_changed.emit(self.layer_id, self._onnx_style_value)

        slider.valueChanged.connect(on_changed)
        apply_style_btn.clicked.connect(apply_style)
        self._add_close_button(layout, dialog)
        dialog.exec()

    @staticmethod
    def _canonical_onnx_style(value) -> str:
        raw = str(value or "none").strip().lower()
        return raw if raw in {"none", "cartoon", "monet", "vangogh"} else "none"

    def _open_color_panel(self) -> None:
        dialog, layout = self._make_panel("色彩校正")
        sat_slider, sat_value = self._slider_row(layout, "饱和度", 0, 200, self._saturation_value, "%")
        con_slider, con_value = self._slider_row(layout, "对比度", 0, 200, self._contrast_value, "%")
        temp_slider, temp_value = self._slider_row(layout, "色温", -100, 100, self._color_temp_value)

        def on_sat_changed(value: int) -> None:
            self._saturation_value = int(value)
            sat_value.setText(f"{self._saturation_value}%")
            self.saturation_changed.emit(self.layer_id, self._saturation_value / 100.0)

        def on_con_changed(value: int) -> None:
            self._contrast_value = int(value)
            con_value.setText(f"{self._contrast_value}%")
            self.contrast_changed.emit(self.layer_id, self._contrast_value / 100.0)

        def on_temp_changed(value: int) -> None:
            self._color_temp_value = int(value)
            temp_value.setText(str(self._color_temp_value))
            self.color_temp_changed.emit(self.layer_id, self._color_temp_value)

        sat_slider.valueChanged.connect(on_sat_changed)
        con_slider.valueChanged.connect(on_con_changed)
        temp_slider.valueChanged.connect(on_temp_changed)
        self._add_close_button(layout, dialog)
        dialog.exec()

    def _open_ai_panel(self) -> None:
        if not self._smart_supported:
            return
        dialog, layout = self._make_panel("智能增强")
        bg_enabled = bool(self._source.get("virtual_bg_enabled", False))
        bg_mode_value = str(self._source.get("virtual_bg_mode", "image") or "image").strip().lower()
        if bg_mode_value not in {"image", "blur"}:
            bg_mode_value = "image"
        blur_strength = int(max(0, min(100, self._source.get("virtual_bg_blur_strength", 55))))
        face_enabled = bool(self._source.get("face_enabled", False))
        effect_type = str(self._source.get("effect_type", "dog_nose") or "dog_nose").strip()
        face_scale = int(max(50, min(200, self._source.get("face_scale_percent", 100))))
        smoothing = int(max(0, min(100, self._source.get("face_smoothing", 60))))

        bg_box = QCheckBox("启用虚拟背景")
        bg_box.setChecked(bg_enabled)
        layout.addWidget(bg_box)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("背景模式"))
        mode_combo = QComboBox()
        mode_combo.addItem("背景图片", "image")
        mode_combo.addItem("背景模糊", "blur")
        mode_combo.setCurrentIndex(max(0, mode_combo.findData(bg_mode_value)))
        mode_row.addWidget(mode_combo, 1)
        layout.addLayout(mode_row)
        blur_slider, blur_label = self._slider_row(layout, "模糊强度", 0, 100, blur_strength, "%")

        face_box = QCheckBox("启用 AR 贴纸")
        face_box.setChecked(face_enabled)
        layout.addWidget(face_box)

        effect_row = QHBoxLayout()
        effect_row.addWidget(QLabel("贴纸类型"))
        effect_combo = QComboBox()
        effect_combo.addItem("狗鼻子", "dog_nose")
        effect_combo.addItem("猫耳朵", "cat_ears")
        effect_combo.addItem("卡通眼睛", "cartoon_eyes")
        effect_combo.setCurrentIndex(max(0, effect_combo.findData(effect_type)))
        effect_row.addWidget(effect_combo, 1)
        layout.addLayout(effect_row)
        scale_slider, scale_label = self._slider_row(layout, "贴纸缩放", 50, 200, face_scale, "%")
        smooth_slider, smooth_label = self._slider_row(layout, "跟踪平滑", 0, 100, smoothing, "%")

        def on_bg_enabled(value: bool) -> None:
            self._source["virtual_bg_enabled"] = bool(value)
            self.virtual_bg_enabled_changed.emit(self.layer_id, bool(value))

        def on_bg_mode(_index: int) -> None:
            value = str(mode_combo.currentData() or "image")
            self._source["virtual_bg_mode"] = value
            self.virtual_bg_mode_changed.emit(self.layer_id, value)

        def on_blur(value: int) -> None:
            value = int(value)
            self._source["virtual_bg_blur_strength"] = value
            blur_label.setText(f"{value}%")
            self.virtual_bg_blur_changed.emit(self.layer_id, value)

        def on_face_enabled(value: bool) -> None:
            self._source["face_enabled"] = bool(value)
            self.face_enabled_changed.emit(self.layer_id, bool(value))

        def on_effect(_index: int) -> None:
            value = str(effect_combo.currentData() or "dog_nose")
            self._source["effect_type"] = value
            self.face_effect_changed.emit(self.layer_id, value)

        def on_scale(value: int) -> None:
            value = int(value)
            self._source["face_scale_percent"] = value
            scale_label.setText(f"{value}%")
            self.face_scale_changed.emit(self.layer_id, value)

        def on_smoothing(value: int) -> None:
            value = int(value)
            self._source["face_smoothing"] = value
            smooth_label.setText(f"{value}%")
            self.face_smoothing_changed.emit(self.layer_id, value)

        bg_box.toggled.connect(on_bg_enabled)
        mode_combo.currentIndexChanged.connect(
            on_bg_mode
        )
        blur_slider.valueChanged.connect(on_blur)
        face_box.toggled.connect(on_face_enabled)
        effect_combo.currentIndexChanged.connect(on_effect)
        scale_slider.valueChanged.connect(on_scale)
        smooth_slider.valueChanged.connect(on_smoothing)
        self._add_close_button(layout, dialog)
        dialog.exec()


class LayerManagerDialog(QDialog):
    """独立图层管理窗口，通过优先级编号控制遮挡关系。"""

    closed = pyqtSignal()
    scene_selected = pyqtSignal(str)
    layer_selected = pyqtSignal(str)
    layer_deleted = pyqtSignal(str, str)
    layer_priority_changed = pyqtSignal(str, str, int)
    layer_volume_changed = pyqtSignal(str, str, float)
    layer_saturation_changed = pyqtSignal(str, str, float)
    layer_contrast_changed = pyqtSignal(str, str, float)
    layer_color_temp_changed = pyqtSignal(str, str, int)
    layer_mosaic_changed = pyqtSignal(str, str, int)
    layer_onnx_style_changed = pyqtSignal(str, str, str)
    layer_face_enabled_changed = pyqtSignal(str, str, bool)
    layer_face_effect_changed = pyqtSignal(str, str, str)
    layer_face_scale_changed = pyqtSignal(str, str, int)
    layer_face_smoothing_changed = pyqtSignal(str, str, int)
    layer_virtual_bg_enabled_changed = pyqtSignal(str, str, bool)
    layer_virtual_bg_mode_changed = pyqtSignal(str, str, str)
    layer_virtual_bg_blur_changed = pyqtSignal(str, str, int)
    add_camera_clicked = pyqtSignal()
    add_screen_clicked = pyqtSignal()
    add_window_clicked = pyqtSignal()
    add_image_clicked = pyqtSignal()
    add_network_clicked = pyqtSignal()
    placeholder_scene_clicked = pyqtSignal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("图层管理")
        self.resize(820, 680)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self._current_scene_id: str | None = None

        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        scene_row = QHBoxLayout()
        scene_row.addWidget(QLabel("管理场景:"))
        self.scene_combo = QComboBox()
        scene_row.addWidget(self.scene_combo, 1)
        self.btn_placeholder_scene = QPushButton("占位场景编辑")
        self.btn_placeholder_scene.setProperty("role", "danger")
        self.btn_placeholder_scene.setMinimumHeight(36)
        scene_row.addWidget(self.btn_placeholder_scene)
        root.addLayout(scene_row)

        add_row = QHBoxLayout()
        self.btn_add_camera = QPushButton("添加相机")
        self.btn_add_screen = QPushButton("添加屏幕")
        self.btn_add_window = QPushButton("添加窗口")
        self.btn_add_image = QPushButton("添加图片")
        self.btn_add_network = QPushButton("添加网络流")
        for button in (
            self.btn_add_camera,
            self.btn_add_screen,
            self.btn_add_window,
            self.btn_add_image,
            self.btn_add_network,
        ):
            button.setProperty("role", "toolbar")
            button.setMinimumHeight(38)
            button.setMinimumWidth(108)
            add_row.addWidget(button)
        root.addLayout(add_row)

        self.layer_list = QListWidget()
        self.layer_list.setSpacing(6)
        self.layer_list.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        root.addWidget(self.layer_list, 1)

        self.scene_combo.currentIndexChanged.connect(self._on_scene_changed)
        self.btn_add_camera.clicked.connect(self._on_add_camera_clicked)
        self.btn_add_screen.clicked.connect(self._on_add_screen_clicked)
        self.btn_add_window.clicked.connect(self._on_add_window_clicked)
        self.btn_add_image.clicked.connect(self._on_add_image_clicked)
        self.btn_add_network.clicked.connect(self._on_add_network_clicked)
        self.btn_placeholder_scene.clicked.connect(self.placeholder_scene_clicked.emit)

    def set_placeholder_editor_open(self, opened: bool) -> None:
        self.btn_placeholder_scene.setText("占位已开" if opened else "占位场景编辑")

    def current_scene_id(self) -> str | None:
        value = self.scene_combo.currentData()
        return str(value) if value else self._current_scene_id

    def set_selected_layer(self, layer_id: str | None) -> None:
        for i in range(self.layer_list.count()):
            item = self.layer_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) != layer_id:
                continue
            self.layer_list.blockSignals(True)
            self.layer_list.setCurrentItem(item)
            self.layer_list.blockSignals(False)
            self.layer_list.scrollToItem(item)
            _schedule_custom_list_widgets_refresh(self.layer_list, layer_id, min_height=118)
            return
        self.layer_list.blockSignals(True)
        self.layer_list.clearSelection()
        self.layer_list.blockSignals(False)
        _schedule_custom_list_widgets_refresh(self.layer_list, None, min_height=118)

    def set_scenes(
        self,
        scenes: list[Scene],
        active_scene_id: str | None,
        focus_scene_id: str | None = None,
    ) -> None:
        preferred_scene_id = focus_scene_id or self._current_scene_id or active_scene_id
        self.scene_combo.blockSignals(True)
        self.scene_combo.clear()
        for scene in scenes:
            self.scene_combo.addItem(scene.name, scene.id)
        index = self.scene_combo.findData(preferred_scene_id)
        if index < 0:
            index = 0
        self.scene_combo.setCurrentIndex(index)
        self.scene_combo.blockSignals(False)

        self._current_scene_id = self.scene_combo.currentData()
        scene = next((item for item in scenes if item.id == self._current_scene_id), None)
        self._refresh_layers(scene)

    def _on_scene_changed(self, _index: int) -> None:
        scene_id = self.scene_combo.currentData()
        if not scene_id:
            return
        self._current_scene_id = str(scene_id)
        self.scene_selected.emit(str(scene_id))

    def _emit_current_scene_before_add(self) -> None:
        if self._current_scene_id:
            self.scene_selected.emit(self._current_scene_id)

    def _on_add_camera_clicked(self) -> None:
        self._emit_current_scene_before_add()
        self.add_camera_clicked.emit()

    def _on_add_screen_clicked(self) -> None:
        self._emit_current_scene_before_add()
        self.add_screen_clicked.emit()

    def _on_add_window_clicked(self) -> None:
        self._emit_current_scene_before_add()
        self.add_window_clicked.emit()

    def _on_add_image_clicked(self) -> None:
        self._emit_current_scene_before_add()
        self.add_image_clicked.emit()

    def _on_add_network_clicked(self) -> None:
        self._emit_current_scene_before_add()
        self.add_network_clicked.emit()

    def _refresh_layers(self, scene: Scene | None) -> None:
        self.layer_list.clear()
        if scene is None:
            return
        self._current_scene_id = scene.id
        max_priority = max(1, len(scene.layers))
        for layer in sorted(scene.layers, key=lambda item: item.priority, reverse=True):
            item = QListWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, layer.id)
            row = LayerPriorityRow(layer, max_priority=max_priority)
            row.priority_changed.connect(
                lambda layer_id, value, scene_id=scene.id: self.layer_priority_changed.emit(scene_id, layer_id, value)
            )
            row.selected.connect(self.layer_selected.emit)
            row.deleted.connect(
                lambda layer_id, scene_id=scene.id: self.layer_deleted.emit(scene_id, layer_id)
            )
            row.volume_changed.connect(
                lambda layer_id, value, scene_id=scene.id: self.layer_volume_changed.emit(scene_id, layer_id, value)
            )
            row.saturation_changed.connect(
                lambda layer_id, value, scene_id=scene.id: self.layer_saturation_changed.emit(scene_id, layer_id, value)
            )
            row.contrast_changed.connect(
                lambda layer_id, value, scene_id=scene.id: self.layer_contrast_changed.emit(scene_id, layer_id, value)
            )
            row.color_temp_changed.connect(
                lambda layer_id, value, scene_id=scene.id: self.layer_color_temp_changed.emit(scene_id, layer_id, value)
            )
            row.mosaic_changed.connect(
                lambda layer_id, value, scene_id=scene.id: self.layer_mosaic_changed.emit(scene_id, layer_id, value)
            )
            row.onnx_style_changed.connect(
                lambda layer_id, value, scene_id=scene.id: self.layer_onnx_style_changed.emit(scene_id, layer_id, value)
            )
            row.face_enabled_changed.connect(
                lambda layer_id, value, scene_id=scene.id: self.layer_face_enabled_changed.emit(scene_id, layer_id, value)
            )
            row.face_effect_changed.connect(
                lambda layer_id, value, scene_id=scene.id: self.layer_face_effect_changed.emit(scene_id, layer_id, value)
            )
            row.face_scale_changed.connect(
                lambda layer_id, value, scene_id=scene.id: self.layer_face_scale_changed.emit(scene_id, layer_id, value)
            )
            row.face_smoothing_changed.connect(
                lambda layer_id, value, scene_id=scene.id: self.layer_face_smoothing_changed.emit(scene_id, layer_id, value)
            )
            row.virtual_bg_enabled_changed.connect(
                lambda layer_id, value, scene_id=scene.id: self.layer_virtual_bg_enabled_changed.emit(scene_id, layer_id, value)
            )
            row.virtual_bg_mode_changed.connect(
                lambda layer_id, value, scene_id=scene.id: self.layer_virtual_bg_mode_changed.emit(scene_id, layer_id, value)
            )
            row.virtual_bg_blur_changed.connect(
                lambda layer_id, value, scene_id=scene.id: self.layer_virtual_bg_blur_changed.emit(scene_id, layer_id, value)
            )
            item.setSizeHint(row.sizeHint())
            self.layer_list.addItem(item)
            self.layer_list.setItemWidget(item, row)
        _schedule_custom_list_widgets_refresh(self.layer_list, None, min_height=118)

    def closeEvent(self, event):  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)


class MainWindow(QMainWindow):
    ASPECT_SIZES = {
        "4:3": (1280, 960),
        "16:9": (1280, 720),
        "16:10": (1280, 800),
        "21:9": (1680, 720),
    }
    CAPTURE_QUALITY_PRESETS = {
        "smooth": {"label": "流畅 480p / 15fps", "width": 854, "height": 480, "fps": 15},
        "standard": {"label": "标准 720p / 30fps", "width": 1280, "height": 720, "fps": 30},
        "balanced": {"label": "平衡 720p / 60fps", "width": 1280, "height": 720, "fps": 60},
        "high": {"label": "高清 1080p / 30fps", "width": 1920, "height": 1080, "fps": 30},
        "ultra": {"label": "超清 1080p / 60fps", "width": 1920, "height": 1080, "fps": 60},
    }
    OUTPUT_QUALITY_PRESETS = {
        "standard_720p30": {
            "label": "标准 720p / 30fps",
            "width": 1280,
            "height": 720,
            "fps": 30,
            "bitrate": "3500k",
            "record_bitrate": "6000k",
            "encoder": "cpu",
            "capture_quality": "standard",
            "thumb_interval": 1.0,
            "semantic_interval": 1600,
            "performance": "轻量",
        },
        "balanced_720p60": {
            "label": "平衡 720p / 60fps",
            "width": 1280,
            "height": 720,
            "fps": 60,
            "bitrate": "5000k",
            "record_bitrate": "8000k",
            "encoder": "gpu",
            "capture_quality": "balanced",
            "thumb_interval": 1.8,
            "semantic_interval": 2200,
            "performance": "推荐",
        },
        "high_1080p30": {
            "label": "高清 1080p / 30fps",
            "width": 1920,
            "height": 1080,
            "fps": 30,
            "bitrate": "8000k",
            "record_bitrate": "12000k",
            "encoder": "gpu",
            "capture_quality": "high",
            "thumb_interval": 1.4,
            "semantic_interval": 2000,
            "performance": "高清",
        },
        "ultra_1080p60": {
            "label": "极清 1080p / 60fps",
            "width": 1920,
            "height": 1080,
            "fps": 60,
            "bitrate": "12000k",
            "record_bitrate": "16000k",
            "encoder": "gpu",
            "capture_quality": "ultra",
            "thumb_interval": 2.4,
            "semantic_interval": 2800,
            "performance": "高负载",
        },
    }

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self.config = config
        self._preference_store = PreferenceStore()
        self._preferences: UserPreferences = self._preference_store.load()
        self._preferences.apply_to_config(self.config)
        self.setWindowTitle("Nsy_Broadcasting_platform 类 OBS 导播台")
        self.resize(1520, 920)
        self.setMinimumSize(980, 620)
        self._current_aspect_ratio = self._aspect_from_size(config.canvas_width, config.canvas_height)

        self.state = AppState()
        self.source_manager = SourceManager()
        self.audio_controller = AudioController(
            sample_rate=config.audio_sample_rate,
            channels=config.audio_channels,
            chunk_size=config.audio_chunk,
        )
        self.output_manager = OutputManager(
            audio_controller=self.audio_controller,
            width=config.canvas_width,
            height=config.canvas_height,
            fps=config.render_fps,
            sample_rate=config.audio_sample_rate,
            channels=config.audio_channels,
            record_bitrate=config.default_record_bitrate,
            stream_bitrate=config.default_stream_bitrate,
            record_encoder=config.default_record_encoder,
            stream_encoder=config.default_stream_encoder,
        )

        self.selected_layer_id: str | None = None
        self._preview_edit_mode = "position"
        self._last_unlocked_preview_edit_mode = "position"
        self._scene_item_widgets: dict[str, SceneItemWidget] = {}
        self._scene_preview_cache: dict[str, object] = {}
        self._semantic_recommendations: dict[str, object] = {}
        self._semantic_best_scene_id: str | None = None
        self._semantic_query = ""
        self._semantic_threshold = 0.10
        self._semantic_recommendation_enabled = False
        self._layer_metrics_cache: dict[str, dict[str, object]] = {}
        self._ai_dialogs: dict[str, object] = {}
        self._ai_settings = AISettingsStore()
        self._preview_popout: PreviewPopoutWindow | None = None
        self._layer_manager_dialog: LayerManagerDialog | None = None
        self._transition_dialog: TransitionDialog | None = None
        self._audio_mixer_dialog: AudioMixerDialog | None = None
        self._placeholder_dialog: PlaceholderSceneDialog | None = None
        self._scene_popout: SceneGridPopoutWindow | None = None
        self._canvas_workspace: InfiniteCanvasDialog | None = None
        self._scene_grid_columns = 3
        self._capture_quality_key = getattr(config, "default_capture_quality", "balanced")
        self._output_performance_hint = "平衡"
        self._output_quality_key = getattr(config, "default_output_quality", "balanced_720p60")
        self._adaptive_bitrate_enabled = bool(getattr(config, "adaptive_bitrate_enabled", True))
        self._adaptive_bitrate = AdaptiveBitrateController(getattr(config, "adaptive_bitrate_min", "2500k"))
        self._adaptive_pending_bitrate = ""
        self._adaptive_bitrate_text = "ABR: 待机"
        self._adaptive_bitrate_kind = "idle"

        self._build_ui()
        self._apply_scene_grid_layout()
        self._apply_modern_theme()
        self._wire_signals()
        self._apply_output_quality_profile(initial=True)
        self._refresh_scene_list()
        self._refresh_layer_list()
        self._refresh_audio_device_combo()
        self._refresh_audio_source_combo()

        self.audio_controller.start()

        self.render_thread = RenderThread(
            state=self.state,
            source_manager=self.source_manager,
            audio_controller=self.audio_controller,
            output_manager=self.output_manager,
            width=config.canvas_width,
            height=config.canvas_height,
            fps=config.render_fps,
            delay_ms=config.program_delay_ms,
        )
        self.render_thread.edit_frame_ready.connect(self._on_edit_frame_ready)
        self.render_thread.program_frame_ready.connect(self._on_program_frame_ready)
        self.render_thread.scene_preview_ready.connect(self._on_scene_preview_ready)
        self.render_thread.layer_metrics_ready.connect(self._on_layer_metrics_ready)
        self._update_output_protection(self._output_quality_meta(self._output_quality_key))
        self.render_thread.start()

        self.diag_timer = QTimer(self)
        self.diag_timer.setInterval(500)
        self.diag_timer.timeout.connect(self._update_diag_panel)
        self.diag_timer.start()

        self._semantic_worker = SemanticRecommendationWorker(self)
        self._semantic_worker.result_ready.connect(self._on_semantic_recommendation_ready)
        self._semantic_worker.status_changed.connect(self._on_semantic_recommendation_status)
        self._semantic_timer = QTimer(self)
        self._semantic_timer.setInterval(1600)
        self._semantic_timer.timeout.connect(self._submit_semantic_recommendation)
        self._update_output_protection(self._output_quality_meta(self._output_quality_key))

    @classmethod
    def _aspect_from_size(cls, width: int, height: int) -> str:
        if height <= 0:
            return "16:9"
        ratio = width / height
        return min(cls.ASPECT_SIZES, key=lambda key: abs((cls.ASPECT_SIZES[key][0] / cls.ASPECT_SIZES[key][1]) - ratio))

    @classmethod
    def _canvas_size_for_aspect(cls, aspect: str) -> tuple[int, int]:
        return cls.ASPECT_SIZES.get(str(aspect), cls.ASPECT_SIZES["16:9"])

    @classmethod
    def _populate_aspect_combo(cls, combo: QComboBox) -> None:
        combo.clear()
        for label in ("4:3", "16:9", "16:10", "21:9"):
            combo.addItem(label, label)

    @staticmethod
    def _set_combo_data(combo: QComboBox, value: str) -> None:
        index = combo.findData(value)
        combo.setCurrentIndex(max(0, index))

    def _capture_quality_settings(self) -> dict[str, int | str]:
        key = str(self._capture_quality_key or "standard")
        if key not in self.CAPTURE_QUALITY_PRESETS:
            key = "standard"
        meta = self.CAPTURE_QUALITY_PRESETS[key]
        return {
            "capture_quality": key,
            "capture_width": int(meta["width"]),
            "capture_height": int(meta["height"]),
            "capture_fps": int(meta["fps"]),
        }

    def _with_capture_quality(self, source: dict) -> dict:
        merged = dict(source)
        merged.update(self._capture_quality_settings())
        return merged

    @staticmethod
    def _normal_scene_list(scenes) -> list:
        return [scene for scene in scenes if not scene.is_placeholder]

    def _apply_scene_grid_layout(self) -> None:
        if not hasattr(self, "scene_list"):
            return
        columns = int(getattr(self, "_scene_grid_columns", 3) or 3)
        viewport_width = self.scene_list.viewport().width() or self.scene_list.width()
        viewport_height = self.scene_list.viewport().height() or self.scene_list.height()
        spacing = self.scene_list.spacing()
        available_w = max(360, viewport_width - spacing * (columns + 1) - 8)
        available_h = max(160, viewport_height - spacing * (columns + 1) - 8)
        cell_w = max(120, int(available_w / max(1, columns)))
        cell_h = max(86, int(available_h / max(1, columns)))
        self.scene_list.setGridSize(QSize(cell_w, cell_h))
        self.scene_list.setMinimumHeight(120)
        self._resize_scene_item_widgets()

    def _scene_thumb_size(self) -> QSize:
        cell = self.scene_list.gridSize()
        return QSize(max(86, cell.width() - 18), max(48, cell.height() - 48))

    def _resize_scene_item_widgets(self) -> None:
        if not hasattr(self, "scene_list"):
            return
        thumb_size = self._scene_thumb_size()
        for i in range(self.scene_list.count()):
            item = self.scene_list.item(i)
            item.setSizeHint(self.scene_list.gridSize())
            widget = self.scene_list.itemWidget(item)
            if not isinstance(widget, SceneItemWidget):
                continue
            image = widget.current_preview_image()
            widget.thumb_label.setFixedSize(thumb_size)
            if image is not None:
                widget.set_preview_image(image)

    def _sync_scene_grid_controls(self) -> None:
        if hasattr(self, "scene_grid_combo"):
            self.scene_grid_combo.blockSignals(True)
            index = self.scene_grid_combo.findData(self._scene_grid_columns)
            self.scene_grid_combo.setCurrentIndex(max(0, index))
            self.scene_grid_combo.blockSignals(False)
        if self._scene_popout is not None:
            self._scene_popout.set_grid_columns(self._scene_grid_columns)

    def _set_scene_grid_columns(self, columns: int, notify: bool = True) -> None:
        self._scene_grid_columns = max(2, min(4, int(columns)))
        target_count = self._scene_grid_columns * self._scene_grid_columns
        self.state.set_normal_scene_count(target_count)
        valid_scene_ids = {scene.id for scene in self.state.snapshot_scenes()}
        for scene_id in list(self._scene_preview_cache):
            if scene_id not in valid_scene_ids:
                self._scene_preview_cache.pop(scene_id, None)
        self.selected_layer_id = None
        self._sync_scene_grid_controls()
        self._apply_scene_grid_layout()
        self._refresh_scene_list()
        self._refresh_layer_list()
        if notify:
            self._notify(f"场景布局已切换为 {self._scene_grid_columns}×{self._scene_grid_columns}，普通场景数量已同步为 {target_count} 个。")

    def _on_scene_grid_changed(self, _index: int) -> None:
        self._set_scene_grid_columns(int(self.scene_grid_combo.currentData() or 3))

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        self._apply_scene_grid_layout()

    def _apply_modern_theme(self) -> None:
        self.setStyleSheet(HAULIX_APP_QSS)

    def _make_collapsed_panel(self, title: str, detail: str, action_text: str, action) -> QGroupBox:
        """弹出独立窗口后，用一个轻量占位条替代主界面大面板。"""
        box = QGroupBox(title)
        box.setObjectName("CollapsedPanel")
        box.setMaximumHeight(78)
        box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        layout = QHBoxLayout(box)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        label = QLabel(detail)
        label.setWordWrap(True)
        button = QPushButton(action_text)
        button.setProperty("role", "primary")
        button.setMinimumWidth(92)
        button.clicked.connect(action)
        layout.addWidget(label, 1)
        layout.addWidget(button)
        return box

    def _set_panel_popped(self, panel_key: str, popped: bool) -> None:
        panel_pairs = {
            "scene": ("scene_box", "scene_collapsed_panel"),
            "preview": ("preview_box", "preview_collapsed_panel"),
            "output": ("output_box", "output_collapsed_panel"),
            "layer": ("layer_box", "layer_collapsed_panel"),
        }
        pair = panel_pairs.get(panel_key)
        if not pair:
            return
        panel = getattr(self, pair[0], None)
        collapsed = getattr(self, pair[1], None)
        if panel is not None:
            panel.setVisible(not popped)
        if collapsed is not None:
            collapsed.setVisible(popped)
        if panel_key == "scene" and not popped:
            self._apply_scene_grid_layout()

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("RootPanel")
        self.setCentralWidget(root)
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(10, 10, 10, 10)
        root_layout.setSpacing(8)

        hero = QFrame()
        hero.setObjectName("MainHero")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(16, 12, 16, 12)
        hero_layout.setSpacing(10)
        title_stack = QVBoxLayout()
        title_stack.setContentsMargins(0, 0, 0, 0)
        title_stack.setSpacing(2)
        hero_title = QLabel("Nsy Broadcasting Platform")
        hero_title.setObjectName("HeroTitle")
        hero_subtitle = QLabel("AI intelligent broadcast console")
        hero_subtitle.setObjectName("HeroSubtitle")
        title_stack.addWidget(hero_title)
        title_stack.addWidget(hero_subtitle)
        hero_layout.addLayout(title_stack, 1)
        self.hero_scene_chip = QLabel("Scene: 未选择")
        self.hero_scene_chip.setObjectName("HeroChip")
        self.hero_quality_chip = QLabel(f"Quality: {self._output_quality_label()}")
        self.hero_quality_chip.setObjectName("HeroChip")
        self.hero_stream_chip = QLabel("Stream: 未运行")
        self.hero_stream_chip.setObjectName("HeroChip")
        self.hero_record_chip = QLabel("Record: 未运行")
        self.hero_record_chip.setObjectName("HeroChip")
        self.btn_canvas_workspace = QPushButton("画布模式")
        self.btn_canvas_workspace.setProperty("role", "primary")
        self.btn_canvas_workspace.setMinimumWidth(96)
        for chip in (
            self.hero_scene_chip,
            self.hero_quality_chip,
            self.hero_stream_chip,
            self.hero_record_chip,
        ):
            hero_layout.addWidget(chip)
        hero_layout.addWidget(self.btn_canvas_workspace)
        root_layout.addWidget(hero)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(True)
        self.main_splitter = splitter
        root_layout.addWidget(splitter, 1)

        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.setSpacing(6)
        left_splitter = QSplitter(Qt.Orientation.Vertical)
        left_splitter.setChildrenCollapsible(True)
        left_layout.addWidget(left_splitter)
        self.left_splitter = left_splitter

        scene_box = QGroupBox("场景")
        scene_layout = QVBoxLayout(scene_box)
        self.current_scene_label = QLabel("当前场景: 未选择")
        self.current_scene_label.setObjectName("CurrentSceneLabel")
        scene_top_row = QHBoxLayout()
        scene_top_row.addWidget(self.current_scene_label, 1)
        scene_top_row.addWidget(QLabel("布局:"))
        self.scene_grid_combo = QComboBox()
        self.scene_grid_combo.addItem("2×2", 2)
        self.scene_grid_combo.addItem("3×3", 3)
        self.scene_grid_combo.addItem("4×4", 4)
        self.scene_grid_combo.setCurrentIndex(1)
        self.scene_grid_combo.setMinimumWidth(78)
        self.scene_grid_combo.setMaximumWidth(110)
        scene_top_row.addWidget(self.scene_grid_combo)
        self.btn_scene_popout = QPushButton("弹出")
        self.btn_scene_popout.setProperty("role", "compact")
        self.btn_scene_popout.setMinimumSize(58, 28)
        self.btn_scene_popout.setMaximumWidth(76)
        scene_top_row.addWidget(self.btn_scene_popout)
        self.btn_clear_scene = QPushButton("清空")
        self.btn_clear_scene.setProperty("role", "danger")
        self.btn_clear_scene.setMinimumSize(58, 28)
        self.btn_clear_scene.setMaximumWidth(76)
        self.btn_clear_scene.setToolTip("清空选中场景中的全部图层")
        scene_top_row.addWidget(self.btn_clear_scene)
        scene_layout.addLayout(scene_top_row)
        self.scene_list = QListWidget()
        self.scene_list.setObjectName("SceneList")
        self.scene_list.setViewMode(QListView.ViewMode.IconMode)
        self.scene_list.setFlow(QListView.Flow.LeftToRight)
        self.scene_list.setWrapping(True)
        self.scene_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.scene_list.setMovement(QListView.Movement.Static)
        self.scene_list.setSpacing(4)
        self.scene_list.setMinimumHeight(120)
        scene_layout.addWidget(self.scene_list)
        self.scene_box = scene_box
        self.scene_collapsed_panel = self._make_collapsed_panel(
            "场景已弹出",
            "场景管理已在独立窗口中显示，主界面释放这块空间。",
            "定位窗口",
            self._open_scene_popout,
        )
        self.scene_collapsed_panel.hide()
        left_splitter.addWidget(scene_box)
        left_splitter.addWidget(self.scene_collapsed_panel)

        layer_box = QGroupBox("图层（优先级 1-N，编号越大 = 画面越靠上）")
        layer_layout = QVBoxLayout(layer_box)
        layer_header_row = QHBoxLayout()
        layer_hint = QLabel("当前场景图层")
        self.btn_placeholder_scene_editor = QPushButton("占位场景")
        self.btn_placeholder_scene_editor.setProperty("role", "danger")
        self.btn_placeholder_scene_editor.setMinimumSize(74, 28)
        self.btn_placeholder_scene_editor.setMaximumWidth(98)
        self.btn_layer_manager = QPushButton("管理")
        self.btn_layer_manager.setProperty("role", "primary")
        self.btn_layer_manager.setMinimumSize(58, 28)
        self.btn_layer_manager.setMaximumWidth(76)
        layer_header_row.addWidget(layer_hint)
        layer_header_row.addStretch(1)
        layer_header_row.addWidget(self.btn_placeholder_scene_editor)
        layer_header_row.addWidget(self.btn_layer_manager)
        layer_layout.addLayout(layer_header_row)

        add_source_row = QHBoxLayout()
        add_source_row.setContentsMargins(0, 0, 0, 0)
        add_source_row.setSpacing(6)
        add_source_row.addWidget(QLabel("输入源:"))
        self.source_type_combo = QComboBox()
        self.source_type_combo.addItem("相机", "camera")
        self.source_type_combo.addItem("屏幕", "screen")
        self.source_type_combo.addItem("窗口", "window")
        self.source_type_combo.addItem("图片", "image")
        self.source_type_combo.addItem("网络流", "network")
        self.btn_add_selected_source = QPushButton("添加输入源")
        self.btn_add_selected_source.setProperty("role", "primary")
        add_source_row.addWidget(self.source_type_combo, 1)
        add_source_row.addWidget(self.btn_add_selected_source)
        layer_layout.addLayout(add_source_row)

        self.layer_list = QListWidget()
        self.layer_list.setObjectName("LayerList")
        self.layer_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.layer_list.setDragDropMode(QAbstractItemView.DragDropMode.NoDragDrop)
        self.layer_list.setSpacing(6)
        layer_layout.addWidget(self.layer_list, 1)
        self.layer_box = layer_box
        self.layer_collapsed_panel = self._make_collapsed_panel(
            "图层管理已弹出",
            "图层参数在独立窗口中编辑，主界面暂时收起图层面板。",
            "定位窗口",
            self._open_layer_manager_dialog,
        )
        self.layer_collapsed_panel.hide()

        face_box = QGroupBox("人脸识别与特效")
        face_layout = QVBoxLayout(face_box)

        face_row1 = QHBoxLayout()
        self.btn_face_enable = QPushButton("开启识别")
        self.btn_face_enable.setProperty("role", "toggle")
        self.btn_face_enable.setCheckable(True)
        self.btn_face_nose = QPushButton("狗鼻子")
        self.btn_face_hat = QPushButton("猫耳")
        self.btn_face_eyes = QPushButton("卡通眼睛")
        face_row1.addWidget(self.btn_face_enable)
        face_row1.addWidget(self.btn_face_nose)
        face_row1.addWidget(self.btn_face_hat)
        face_row1.addWidget(self.btn_face_eyes)
        face_layout.addLayout(face_row1)

        face_row2 = QHBoxLayout()
        face_row2.addWidget(QLabel("特效类型:"))
        self.face_effect_combo = QComboBox()
        self.face_effect_combo.addItem("无", "")
        self.face_effect_combo.addItem("狗鼻子", "dog_nose")
        self.face_effect_combo.addItem("猫耳", "cat_ears")
        self.face_effect_combo.addItem("卡通眼睛", "cartoon_eyes")
        face_row2.addWidget(self.face_effect_combo, 1)
        face_layout.addLayout(face_row2)

        face_row3 = QHBoxLayout()
        face_row3.addWidget(QLabel("AR素材:"))
        self.face_sticker_edit = QLineEdit()
        self.face_sticker_edit.setPlaceholderText("未导入时自动使用内置默认 AR PNG 素材")
        self.face_sticker_edit.setReadOnly(True)
        self.btn_face_sticker = QPushButton("导入AR素材")
        face_row3.addWidget(self.face_sticker_edit, 1)
        face_row3.addWidget(self.btn_face_sticker)
        face_layout.addLayout(face_row3)

        face_row4 = QHBoxLayout()
        face_row4.addWidget(QLabel("贴纸缩放:"))
        self.face_scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.face_scale_slider.setRange(50, 200)
        self.face_scale_slider.setValue(100)
        self.face_scale_value = QLabel("100%")
        self.face_scale_value.setMinimumWidth(54)
        face_row4.addWidget(self.face_scale_slider, 1)
        face_row4.addWidget(self.face_scale_value)
        face_layout.addLayout(face_row4)

        face_row5 = QHBoxLayout()
        face_row5.addWidget(QLabel("跟踪平滑:"))
        self.face_smoothing_slider = QSlider(Qt.Orientation.Horizontal)
        self.face_smoothing_slider.setRange(0, 100)
        self.face_smoothing_slider.setValue(60)
        self.face_smoothing_value = QLabel("60%")
        self.face_smoothing_value.setMinimumWidth(54)
        face_row5.addWidget(self.face_smoothing_slider, 1)
        face_row5.addWidget(self.face_smoothing_value)
        face_layout.addLayout(face_row5)

        self.face_target_label = QLabel("目标图层: 未选择")
        face_layout.addWidget(self.face_target_label)

        self.face_status_text = QLabel("人脸识别状态: 未启用")
        self.face_status_text.setObjectName("FaceStatusText")
        self.face_status_bar = QProgressBar()
        self.face_status_bar.setObjectName("FaceStatusBar")
        self.face_status_bar.setRange(0, 100)
        self.face_status_bar.setValue(0)
        self.face_status_bar.setTextVisible(False)
        self.face_status_bar.setFixedHeight(10)
        face_layout.addWidget(self.face_status_text)
        face_layout.addWidget(self.face_status_bar)

        virtual_bg_box = QGroupBox("虚拟背景")
        virtual_bg_layout = QVBoxLayout(virtual_bg_box)

        virtual_bg_row1 = QHBoxLayout()
        self.btn_virtual_bg_enable = QPushButton("开启虚拟背景")
        self.btn_virtual_bg_enable.setProperty("role", "toggle")
        self.btn_virtual_bg_enable.setCheckable(True)
        virtual_bg_row1.addWidget(self.btn_virtual_bg_enable)
        virtual_bg_row1.addStretch(1)
        virtual_bg_layout.addLayout(virtual_bg_row1)

        virtual_bg_row_mode = QHBoxLayout()
        virtual_bg_row_mode.addWidget(QLabel("处理模式:"))
        self.virtual_bg_mode_combo = QComboBox()
        self.virtual_bg_mode_combo.addItem("背景图替换", "image")
        self.virtual_bg_mode_combo.addItem("背景模糊", "blur")
        virtual_bg_row_mode.addWidget(self.virtual_bg_mode_combo, 1)
        virtual_bg_layout.addLayout(virtual_bg_row_mode)

        virtual_bg_row_strength = QHBoxLayout()
        virtual_bg_row_strength.addWidget(QLabel("模糊强度:"))
        self.virtual_bg_blur_slider = QSlider(Qt.Orientation.Horizontal)
        self.virtual_bg_blur_slider.setRange(0, 100)
        self.virtual_bg_blur_slider.setValue(55)
        self.virtual_bg_blur_value = QLabel("55%")
        self.virtual_bg_blur_value.setMinimumWidth(54)
        virtual_bg_row_strength.addWidget(self.virtual_bg_blur_slider, 1)
        virtual_bg_row_strength.addWidget(self.virtual_bg_blur_value)
        virtual_bg_layout.addLayout(virtual_bg_row_strength)

        virtual_bg_row2 = QHBoxLayout()
        virtual_bg_row2.addWidget(QLabel("背景图片:"))
        self.virtual_bg_edit = QLineEdit()
        self.virtual_bg_edit.setReadOnly(True)
        self.virtual_bg_edit.setPlaceholderText("背景图替换模式下可选择 JPG / PNG / BMP")
        self.btn_virtual_bg_choose = QPushButton("选择背景")
        self.btn_virtual_bg_clear = QPushButton("清除背景")
        virtual_bg_row2.addWidget(self.virtual_bg_edit, 1)
        virtual_bg_row2.addWidget(self.btn_virtual_bg_choose)
        virtual_bg_row2.addWidget(self.btn_virtual_bg_clear)
        virtual_bg_layout.addLayout(virtual_bg_row2)

        self.virtual_bg_target_label = QLabel("目标图层: 未选择")
        virtual_bg_layout.addWidget(self.virtual_bg_target_label)

        self.virtual_bg_status_label = QLabel("虚拟背景状态: 未启用")
        self.virtual_bg_status_label.setWordWrap(True)
        virtual_bg_layout.addWidget(self.virtual_bg_status_label)

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(0, 0, 0, 0)
        right_layout.setSpacing(6)
        right_splitter = QSplitter(Qt.Orientation.Vertical)
        right_splitter.setChildrenCollapsible(True)
        right_layout.addWidget(right_splitter)
        self.right_splitter = right_splitter

        preview_box = QGroupBox("预览与节目输出")
        preview_box.setMinimumHeight(150)
        preview_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        preview_root_layout = QVBoxLayout(preview_box)
        preview_root_layout.setContentsMargins(8, 8, 8, 8)
        preview_root_layout.setSpacing(6)
        edit_mode_row = QHBoxLayout()
        edit_mode_row.setContentsMargins(0, 0, 0, 0)
        edit_mode_row.setSpacing(6)
        edit_mode_row.addWidget(QLabel("\u7f16\u8f91\u6a21\u5f0f:"))
        self.btn_edit_position = QPushButton("\u4f4d\u7f6e\u7f16\u8f91")
        self.btn_edit_size = QPushButton("\u5927\u5c0f\u7f16\u8f91")
        self.btn_edit_lock = QPushButton("\u9501\u5b9a")
        self.edit_mode_group = QButtonGroup(self)
        self.edit_mode_group.setExclusive(True)
        for btn in (self.btn_edit_position, self.btn_edit_size, self.btn_edit_lock):
            btn.setCheckable(True)
            btn.setProperty("role", "toggle")
            self.edit_mode_group.addButton(btn)
            edit_mode_row.addWidget(btn)
        edit_mode_row.addSpacing(10)
        edit_mode_row.addWidget(QLabel("输出比例:"))
        self.aspect_combo = QComboBox()
        self._populate_aspect_combo(self.aspect_combo)
        self._set_combo_data(self.aspect_combo, self._current_aspect_ratio)
        self.aspect_combo.setMinimumWidth(78)
        self.aspect_combo.setMaximumWidth(108)
        edit_mode_row.addWidget(self.aspect_combo)
        edit_mode_row.addStretch(1)
        self.btn_preview_popout = QPushButton("弹出")
        self.btn_preview_popout.setProperty("role", "primary")
        self.btn_preview_popout.setMinimumSize(58, 28)
        self.btn_preview_popout.setMaximumWidth(76)
        edit_mode_row.addWidget(self.btn_preview_popout)
        preview_root_layout.addLayout(edit_mode_row)
        preview_layout = QHBoxLayout()
        preview_layout.setContentsMargins(0, 0, 0, 0)
        preview_layout.setSpacing(6)
        self.edit_preview = PreviewWidget("编辑预览", self.config.canvas_width, self.config.canvas_height, editable=True)
        self.program_preview = PreviewWidget("节目输出", self.config.canvas_width, self.config.canvas_height, editable=False)
        preview_layout.addWidget(self.edit_preview, 1)
        preview_layout.addWidget(self.program_preview, 1)
        preview_root_layout.addLayout(preview_layout, 1)
        self.preview_box = preview_box
        self.preview_collapsed_panel = self._make_collapsed_panel(
            "预览与节目输出已弹出",
            "编辑预览和节目输出在独立窗口中显示，主界面释放预览空间。",
            "定位窗口",
            self._open_preview_popout,
        )
        self.preview_collapsed_panel.hide()
        right_splitter.addWidget(preview_box)
        right_splitter.addWidget(self.preview_collapsed_panel)

        ai_box = QGroupBox("AI 智能功能")
        ai_box.setObjectName("AIFocusBox")
        ai_layout = QVBoxLayout(ai_box)
        ai_box.setMinimumHeight(64)
        ai_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        ai_layout.setContentsMargins(8, 8, 8, 8)
        ai_layout.setSpacing(0)
        ai_layout.addWidget(face_box, 0)
        ai_layout.addWidget(virtual_bg_box, 0)

        semantic_box = QGroupBox("语义智能导播")
        semantic_layout = QVBoxLayout(semantic_box)
        semantic_desc = QLabel("输入搜索词后进入独立界面，后续可以在该页面接入语义检索、镜头切换与重点高亮。")
        semantic_desc.setWordWrap(True)
        semantic_layout.addWidget(semantic_desc)
        self.btn_semantic_window = QPushButton("进入语义智能导播界面")
        self.btn_semantic_window.setProperty("role", "primary")
        semantic_layout.addWidget(self.btn_semantic_window)
        self.semantic_query_edit = QLineEdit()
        self.semantic_query_edit.setPlaceholderText("例如：戴帽子的人、红色外套、主持人")
        self.semantic_query_edit.hide()
        self.btn_semantic_switch = QPushButton("切到主显示")
        self.btn_semantic_switch.setProperty("role", "primary")
        self.btn_semantic_switch.hide()
        self.btn_semantic_highlight = QPushButton("高亮匹配镜头")
        self.btn_semantic_highlight.hide()
        self.semantic_status_label = QLabel("当前为规则匹配演示模式，后续可对接 CLIP / GroundingDINO / 多模态模型。")
        self.semantic_status_label.hide()
        ai_layout.addWidget(semantic_box)

        anomaly_box = QGroupBox("舞台异常检测")
        anomaly_layout = QVBoxLayout(anomaly_box)
        anomaly_desc = QLabel("进入独立界面后可继续配置异常提示词、异常类型和趣味镜头抓取相关参数。")
        anomaly_desc.setWordWrap(True)
        anomaly_layout.addWidget(anomaly_desc)
        self.btn_anomaly_window = QPushButton("进入舞台异常检测界面")
        self.btn_anomaly_window.setProperty("role", "primary")
        anomaly_layout.addWidget(self.btn_anomaly_window)
        self.btn_anomaly_enable = QPushButton("启动异常检测")
        self.btn_anomaly_enable.setCheckable(True)
        self.btn_anomaly_enable.setProperty("role", "toggle")
        self.btn_anomaly_enable.hide()
        self.anomaly_query_edit = QLineEdit()
        self.anomaly_query_edit.setPlaceholderText("例如：某个孩子裤子掉了、演员跌倒、舞台上突然跑入")
        self.anomaly_query_edit.hide()
        self.btn_anomaly_search = QPushButton("搜索异常镜头")
        self.btn_anomaly_search.hide()
        self.btn_anomaly_clear = QPushButton("清空异常高亮")
        self.btn_anomaly_clear.hide()
        self.anomaly_status_label = QLabel("异常检测未启动。启动后可基于文本提示做镜头筛选演示。")
        self.anomaly_status_label.hide()
        ai_layout.addWidget(anomaly_box)

        ad_box = QGroupBox("虚拟广告")
        ad_layout = QVBoxLayout(ad_box)
        ad_desc = QLabel("主界面仅保留入口，进入独立界面后可以继续设置广告位、素材和投放方式。")
        ad_desc.setWordWrap(True)
        ad_layout.addWidget(ad_desc)
        self.btn_virtual_ad_window = QPushButton("进入虚拟广告界面")
        self.btn_virtual_ad_window.setProperty("role", "primary")
        ad_layout.addWidget(self.btn_virtual_ad_window)
        self.btn_virtual_ad_enable = QPushButton("启用虚拟广告")
        self.btn_virtual_ad_enable.setCheckable(True)
        self.btn_virtual_ad_enable.setProperty("role", "toggle")
        self.btn_virtual_ad_enable.hide()
        self.virtual_ad_position_combo = QComboBox()
        self.virtual_ad_position_combo.addItem("右下角", "bottom_right")
        self.virtual_ad_position_combo.addItem("左上角", "top_left")
        self.virtual_ad_position_combo.addItem("右上角", "top_right")
        self.virtual_ad_position_combo.addItem("中下三分之一", "lower_third")
        self.virtual_ad_position_combo.addItem("舞台中央", "center")
        self.virtual_ad_position_combo.hide()
        self.virtual_ad_asset_edit = QLineEdit()
        self.virtual_ad_asset_edit.setReadOnly(True)
        self.virtual_ad_asset_edit.setPlaceholderText("请选择 PNG / 带透明通道广告素材")
        self.virtual_ad_asset_edit.hide()
        self.btn_virtual_ad_asset = QPushButton("选择素材")
        self.btn_virtual_ad_asset.hide()
        self.btn_virtual_ad_apply = QPushButton("应用到当前场景")
        self.btn_virtual_ad_apply.hide()
        self.btn_virtual_ad_remove = QPushButton("移除广告")
        self.btn_virtual_ad_remove.setProperty("role", "danger")
        self.btn_virtual_ad_remove.hide()
        self.virtual_ad_status_label = QLabel("可将虚拟广告作为独立 PNG 图层插入当前场景。")
        self.virtual_ad_status_label.hide()
        ai_layout.addWidget(ad_box)

        ar_box = QGroupBox("AR 功能")
        ar_layout = QVBoxLayout(ar_box)
        ar_desc = QLabel("主界面只保留进入按钮，AR 模式、目标图层和素材配置放到独立界面里展示。")
        ar_desc.setWordWrap(True)
        ar_layout.addWidget(ar_desc)
        self.btn_ar_window = QPushButton("进入 AR 功能界面")
        self.btn_ar_window.setProperty("role", "primary")
        ar_layout.addWidget(self.btn_ar_window)
        self.btn_ar_enable = QPushButton("启用 AR")
        self.btn_ar_enable.setCheckable(True)
        self.btn_ar_enable.setProperty("role", "toggle")
        self.btn_ar_enable.hide()
        self.ar_mode_combo = QComboBox()
        self.ar_mode_combo.addItem("人物贴纸 AR", "face_sticker")
        self.ar_mode_combo.addItem("舞台标签 AR", "stage_label")
        self.ar_mode_combo.addItem("虚拟道具 AR", "prop_overlay")
        self.ar_mode_combo.hide()
        self.ar_asset_edit = QLineEdit()
        self.ar_asset_edit.setReadOnly(True)
        self.ar_asset_edit.setPlaceholderText("请选择 AR 贴纸/标签素材")
        self.ar_asset_edit.hide()
        self.btn_ar_asset = QPushButton("选择 AR 素材")
        self.btn_ar_asset.hide()
        self.ar_target_label = QLabel("AR 目标图层: 未选择")
        self.ar_target_label.hide()
        self.btn_ar_apply = QPushButton("应用到选中图层")
        self.btn_ar_apply.hide()
        self.btn_ar_clear = QPushButton("清除 AR")
        self.btn_ar_clear.hide()
        self.ar_status_label = QLabel("人物贴纸 AR 会复用现有人脸贴纸通道，其它 AR 模式保留接口等待模型接入。")
        self.ar_status_label.hide()
        ai_layout.addWidget(ar_box)

        self.btn_face_window = QPushButton("人脸识别")
        self.btn_virtual_bg_window = QPushButton("虚拟背景")
        self.btn_ai_model_window = QPushButton("大模型图像")
        toolbar_buttons = [
            (self.btn_face_window, "人脸识别与特效"),
            (self.btn_virtual_bg_window, "虚拟背景"),
            (self.btn_ai_model_window, "Gemini / DeepSeek 图像生成、编辑和分析"),
            (self.btn_semantic_window, "智能导播模式：按关键词检索场景并一键切到节目输出"),
            (self.btn_anomaly_window, "舞台异常检测"),
            (self.btn_virtual_ad_window, "虚拟广告"),
            (self.btn_ar_window, "AR 功能"),
        ]
        self.btn_virtual_bg_window.setText("虚拟背景")
        self.btn_ai_model_window.setText("大模型图像")
        self.btn_semantic_window.setText("智能导播")
        self.btn_anomaly_window.setText("异常检测")
        self.btn_virtual_ad_window.setText("虚拟广告")
        self.btn_ar_window.setText("AR功能")

        ai_toolbar = QHBoxLayout()
        ai_toolbar.setContentsMargins(0, 0, 0, 0)
        ai_toolbar.setSpacing(6)
        for button, tooltip in toolbar_buttons:
            button.setProperty("role", "primary")
            button.setProperty("accent", "ai")
            button.setToolTip(tooltip)
            button.setMinimumHeight(32)
            button.setMaximumHeight(38)
            button.setMinimumWidth(88)
            ai_toolbar.addWidget(button)
        ai_toolbar.addStretch(1)
        ai_layout.insertLayout(0, ai_toolbar)

        for hidden_box in (face_box, virtual_bg_box, semantic_box, anomaly_box, ad_box, ar_box):
            hidden_box.hide()

        self.ai_box = ai_box
        right_splitter.addWidget(ai_box)

        output_box = QGroupBox("输出控制")
        output_layout = QVBoxLayout(output_box)
        output_layout.setContentsMargins(10, 10, 10, 10)
        output_layout.setSpacing(7)

        row1 = QHBoxLayout()
        row1.addWidget(QLabel("节目延时(ms):"))
        self.delay_spin = QSpinBox()
        self.delay_spin.setRange(0, 5000)
        self.delay_spin.setValue(self.config.program_delay_ms)
        row1.addWidget(self.delay_spin)
        row1.addSpacing(12)
        row1.addWidget(QLabel("采集质量:"))
        self.capture_quality_combo = QComboBox()
        for key, meta in self.CAPTURE_QUALITY_PRESETS.items():
            self.capture_quality_combo.addItem(meta["label"], key)
        self.capture_quality_combo.setCurrentIndex(max(0, self.capture_quality_combo.findData(self._capture_quality_key)))
        row1.addWidget(self.capture_quality_combo)
        row1.addSpacing(12)
        row1.addWidget(QLabel("输出质量:"))
        self.output_quality_combo = QComboBox()
        for key, meta in self.OUTPUT_QUALITY_PRESETS.items():
            self.output_quality_combo.addItem(meta["label"], key)
        self.output_quality_combo.setCurrentIndex(max(0, self.output_quality_combo.findData(self._output_quality_key)))
        row1.addWidget(self.output_quality_combo)
        self.adaptive_bitrate_check = QCheckBox("网络自适应")
        self.adaptive_bitrate_check.setChecked(self._adaptive_bitrate_enabled)
        self.adaptive_bitrate_label = QLabel(self._adaptive_bitrate_text)
        self.adaptive_bitrate_label.setObjectName("StatusLabel")
        row1.addWidget(self.adaptive_bitrate_check)
        row1.addWidget(self.adaptive_bitrate_label)
        row1.addSpacing(12)
        row1.addWidget(QLabel("音频回采设备:"))
        self.audio_device_combo = QComboBox()
        self.btn_refresh_devices = QPushButton("刷新设备")
        self.btn_refresh_devices.setProperty("role", "compact")
        self.btn_apply_device = QPushButton("应用设备")
        self.btn_apply_device.setProperty("role", "compact")
        row1.addWidget(self.audio_device_combo, 1)
        row1.addWidget(self.btn_refresh_devices)
        row1.addWidget(self.btn_apply_device)
        output_layout.addLayout(row1)

        emergency_row = QHBoxLayout()
        self.btn_transition_settings = QPushButton("转场设置")
        self.btn_transition_settings.setProperty("role", "toolbar")
        self.btn_emergency_placeholder = QPushButton("紧急占位")
        self.btn_emergency_placeholder.setCheckable(True)
        self.btn_emergency_placeholder.setProperty("role", "danger")
        self.btn_choose_placeholder_video = QPushButton("设置占位视频")
        self.btn_choose_placeholder_video.setProperty("role", "toolbar")
        self.placeholder_status_label = QLabel("占位: 未启用")
        self.placeholder_status_label.setObjectName("StatusLabel")
        self.transition_status_label = QLabel("转场: 硬切")
        self.transition_status_label.setObjectName("StatusLabel")
        emergency_row.addWidget(self.btn_transition_settings)
        emergency_row.addWidget(self.btn_emergency_placeholder)
        emergency_row.addWidget(self.btn_choose_placeholder_video)
        emergency_row.addWidget(self.transition_status_label, 1)
        emergency_row.addWidget(self.placeholder_status_label, 1)
        output_layout.addLayout(emergency_row)

        audio_source_row = QHBoxLayout()
        audio_source_row.addWidget(QLabel("采集音轨:"))
        self.audio_source_combo = QComboBox()
        self.btn_refresh_audio_sources = QPushButton("刷新音轨")
        self.btn_refresh_audio_sources.setProperty("role", "compact")
        self.btn_audio_mixer = QPushButton("音轨调音台")
        self.btn_audio_mixer.setProperty("role", "toolbar")
        self.audio_level_bar = QProgressBar()
        self.audio_level_bar.setRange(0, 100)
        self.audio_level_bar.setValue(0)
        self.audio_level_bar.setTextVisible(False)
        self.audio_level_bar.setMinimumWidth(80)
        self.audio_level_bar.setMaximumWidth(130)
        audio_source_row.addWidget(self.audio_source_combo, 2)
        audio_source_row.addWidget(self.btn_refresh_audio_sources)
        audio_source_row.addWidget(self.btn_audio_mixer)
        audio_source_row.addSpacing(8)
        audio_source_row.addWidget(QLabel("电平:"))
        audio_source_row.addWidget(self.audio_level_bar)
        output_layout.addLayout(audio_source_row)

        self.audio_status_label = QLabel("音频: 等待回采启动")
        self.audio_status_label.setObjectName("StatusLabel")
        output_layout.addWidget(self.audio_status_label)

        monitor_row = QHBoxLayout()
        self.btn_audio_monitor = QPushButton("开启监听")
        self.btn_audio_monitor.setCheckable(True)
        self.btn_audio_monitor.setProperty("role", "toggle")
        self.monitor_volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.monitor_volume_slider.setRange(0, 200)
        self.monitor_volume_slider.setValue(60)
        self.monitor_volume_value = QLabel("60%")
        self.monitor_status_label = QLabel("监听: 关闭")
        monitor_row.addWidget(self.btn_audio_monitor)
        monitor_row.addWidget(QLabel("监听音量:"))
        monitor_row.addWidget(self.monitor_volume_slider, 1)
        monitor_row.addWidget(self.monitor_volume_value)
        monitor_row.addWidget(self.monitor_status_label)
        output_layout.addLayout(monitor_row)

        encoder_row = QHBoxLayout()
        encoder_row.addWidget(QLabel("推流编码:"))
        self.stream_encoder_combo = QComboBox()
        self.stream_encoder_combo.addItem("自动 CPU+GPU", "auto")
        self.stream_encoder_combo.addItem("优先 GPU/NVENC", "gpu")
        self.stream_encoder_combo.addItem("仅 CPU/x264", "cpu")
        stream_encoder_index = self.stream_encoder_combo.findData(self.config.default_stream_encoder)
        self.stream_encoder_combo.setCurrentIndex(max(0, stream_encoder_index))
        encoder_row.addWidget(self.stream_encoder_combo)
        encoder_row.addWidget(QLabel("录制编码:"))
        self.record_encoder_combo = QComboBox()
        self.record_encoder_combo.addItem("自动 CPU+GPU", "auto")
        self.record_encoder_combo.addItem("优先 GPU/NVENC", "gpu")
        self.record_encoder_combo.addItem("仅 CPU/x264", "cpu")
        record_encoder_index = self.record_encoder_combo.findData(self.config.default_record_encoder)
        self.record_encoder_combo.setCurrentIndex(max(0, record_encoder_index))
        encoder_row.addWidget(self.record_encoder_combo)
        encoder_row.addWidget(QLabel("推流码率:"))
        self.stream_bitrate_combo = QComboBox()
        self.stream_bitrate_combo.setEditable(True)
        for value in ("3500k", "4500k", "6000k", "8000k", "10000k"):
            self.stream_bitrate_combo.addItem(value)
        self.stream_bitrate_combo.setCurrentText(self.config.default_stream_bitrate)
        encoder_row.addWidget(self.stream_bitrate_combo)
        encoder_row.addWidget(QLabel("录制码率:"))
        self.record_bitrate_combo = QComboBox()
        self.record_bitrate_combo.setEditable(True)
        for value in ("6000k", "8000k", "12000k", "16000k", "24000k"):
            self.record_bitrate_combo.addItem(value)
        self.record_bitrate_combo.setCurrentText(self.config.default_record_bitrate)
        encoder_row.addWidget(self.record_bitrate_combo)
        output_layout.addLayout(encoder_row)

        row2 = QHBoxLayout()
        row2.addWidget(QLabel("RTMP 地址:"))
        self.rtmp_edit = QLineEdit(self.config.default_rtmp_url)
        self.btn_stream_start = QPushButton("开始推流")
        self.btn_stream_stop = QPushButton("停止推流")
        self.btn_record_start = QPushButton("开始录制")
        self.btn_record_stop = QPushButton("停止录制")
        self.btn_stream_start.setProperty("role", "primary")
        self.btn_record_start.setProperty("role", "primary")
        self.btn_stream_stop.setProperty("role", "danger")
        self.btn_record_stop.setProperty("role", "danger")
        row2.addWidget(self.rtmp_edit, 1)
        row2.addWidget(self.btn_stream_start)
        row2.addWidget(self.btn_stream_stop)
        row2.addWidget(self.btn_record_start)
        row2.addWidget(self.btn_record_stop)
        output_layout.addLayout(row2)

        self.status_label = QLabel("状态: 就绪")
        self.status_label.setObjectName("StatusLabel")
        output_layout.addWidget(self.status_label)
        self.encoder_status_label = QLabel("编码: 自动 CPU+GPU")
        self.encoder_status_label.setObjectName("StatusLabel")
        output_layout.addWidget(self.encoder_status_label)
        status_row = QHBoxLayout()
        status_row.addWidget(QLabel("推流状态:"))
        self.stream_status_badge = QLabel("未运行")
        self.stream_status_badge.setObjectName("StreamStatusBadge")
        status_row.addWidget(self.stream_status_badge)
        status_row.addSpacing(16)
        status_row.addWidget(QLabel("录制状态:"))
        self.record_status_badge = QLabel("未运行")
        self.record_status_badge.setObjectName("RecordStatusBadge")
        status_row.addWidget(self.record_status_badge)
        status_row.addStretch(1)
        output_layout.addLayout(status_row)

        self.output_box = output_box
        self.output_collapsed_panel = self._make_collapsed_panel(
            "输出控制已弹出",
            "延时、推流、录制和监听控制已移到预览子界面。",
            "定位窗口",
            self._open_preview_popout,
        )
        self.output_collapsed_panel.hide()
        left_splitter.addWidget(output_box)
        left_splitter.addWidget(self.output_collapsed_panel)
        right_splitter.addWidget(layer_box)
        right_splitter.addWidget(self.layer_collapsed_panel)

        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([640, 880])
        splitter.setStretchFactor(0, 1)
        splitter.setStretchFactor(1, 1)
        left_splitter.setSizes([520, 0, 260, 0])
        left_splitter.setStretchFactor(0, 3)
        left_splitter.setStretchFactor(1, 0)
        left_splitter.setStretchFactor(2, 1)
        left_splitter.setStretchFactor(3, 0)
        right_splitter.setSizes([320, 0, 86, 440, 0])
        right_splitter.setStretchFactor(0, 2)
        right_splitter.setStretchFactor(1, 0)
        right_splitter.setStretchFactor(2, 0)
        right_splitter.setStretchFactor(3, 2)
        right_splitter.setStretchFactor(4, 0)
        self._update_output_status_badges(
            {
                "stream": "未运行",
                "record": "未运行",
                "stream_error": "",
                "record_error": "",
            }
        )
        self._sync_adaptive_bitrate_controls()
        self._sync_emergency_placeholder_controls()
        self._sync_transition_controls()

    def _wire_signals(self) -> None:
        self.btn_clear_scene.clicked.connect(self._clear_selected_scene)
        self.scene_list.currentItemChanged.connect(self._on_scene_selected)
        self.scene_grid_combo.currentIndexChanged.connect(self._on_scene_grid_changed)
        self.btn_placeholder_scene_editor.clicked.connect(self._open_placeholder_scene_dialog)
        self.btn_scene_popout.clicked.connect(self._open_scene_popout)
        self.btn_canvas_workspace.clicked.connect(self._open_canvas_workspace)

        self.btn_add_selected_source.clicked.connect(self._add_selected_source_layer)
        self.btn_layer_manager.clicked.connect(self._open_layer_manager_dialog)

        self.layer_list.currentItemChanged.connect(self._on_layer_item_selected)

        self.edit_preview.layer_selected.connect(self._select_layer)
        self.edit_preview.layer_transform_changed.connect(self._update_layer_rect)
        self.btn_edit_position.clicked.connect(lambda checked: self._on_preview_edit_mode_clicked("position", checked))
        self.btn_edit_size.clicked.connect(lambda checked: self._on_preview_edit_mode_clicked("size", checked))
        self.btn_edit_lock.clicked.connect(lambda checked: self._on_preview_edit_mode_clicked("lock", checked))
        self.aspect_combo.currentIndexChanged.connect(self._on_aspect_ratio_changed)
        self.btn_preview_popout.clicked.connect(self._open_preview_popout)

        self.delay_spin.valueChanged.connect(self._on_delay_changed)
        self.capture_quality_combo.currentIndexChanged.connect(self._on_capture_quality_changed)
        self.output_quality_combo.currentIndexChanged.connect(self._on_output_quality_changed)
        self.adaptive_bitrate_check.toggled.connect(self._on_adaptive_bitrate_toggled)
        self.btn_transition_settings.clicked.connect(self._open_transition_dialog)
        self.btn_emergency_placeholder.toggled.connect(self._on_emergency_placeholder_toggled)
        self.btn_choose_placeholder_video.clicked.connect(self._choose_placeholder_video)
        self.rtmp_edit.textChanged.connect(self._on_main_rtmp_changed)
        self.btn_refresh_devices.clicked.connect(self._refresh_audio_device_combo)
        self.btn_apply_device.clicked.connect(self._apply_audio_device)
        self.audio_source_combo.currentIndexChanged.connect(self._on_audio_source_changed)
        self.btn_refresh_audio_sources.clicked.connect(self._refresh_audio_source_combo)
        self.btn_audio_mixer.clicked.connect(self._open_audio_mixer_dialog)
        self.btn_audio_monitor.toggled.connect(self._on_audio_monitor_toggled)
        self.monitor_volume_slider.valueChanged.connect(self._on_monitor_volume_changed)
        self.stream_encoder_combo.currentIndexChanged.connect(self._apply_encoding_profile)
        self.record_encoder_combo.currentIndexChanged.connect(self._apply_encoding_profile)
        self.stream_bitrate_combo.currentTextChanged.connect(self._apply_encoding_profile)
        self.record_bitrate_combo.currentTextChanged.connect(self._apply_encoding_profile)
        self.btn_record_start.clicked.connect(self._start_record)
        self.btn_record_stop.clicked.connect(self._stop_record)
        self.btn_stream_start.clicked.connect(self._start_stream)
        self.btn_stream_stop.clicked.connect(self._stop_stream)
        self.btn_face_enable.toggled.connect(self._on_face_enabled_toggled)
        self.btn_face_nose.clicked.connect(lambda: self._set_face_effect_type("dog_nose"))
        self.btn_face_hat.clicked.connect(lambda: self._set_face_effect_type("cat_ears"))
        self.btn_face_eyes.clicked.connect(lambda: self._set_face_effect_type("cartoon_eyes"))
        self.face_effect_combo.currentIndexChanged.connect(self._on_face_effect_combo_changed)
        self.btn_face_sticker.clicked.connect(self._on_choose_face_sticker)
        self.face_scale_slider.valueChanged.connect(self._on_face_scale_changed)
        self.face_smoothing_slider.valueChanged.connect(self._on_face_smoothing_changed)
        self.btn_face_window.clicked.connect(self._open_face_dialog)
        self.btn_virtual_bg_enable.toggled.connect(self._on_virtual_bg_enabled_toggled)
        self.virtual_bg_mode_combo.currentIndexChanged.connect(self._on_virtual_bg_mode_changed)
        self.virtual_bg_blur_slider.valueChanged.connect(self._on_virtual_bg_blur_strength_changed)
        self.btn_virtual_bg_choose.clicked.connect(self._on_choose_virtual_bg_image)
        self.btn_virtual_bg_clear.clicked.connect(self._clear_virtual_bg_image)
        self.btn_virtual_bg_window.clicked.connect(self._open_virtual_bg_dialog)
        self.btn_ai_model_window.clicked.connect(self._open_ai_model_dialog)
        self.btn_semantic_switch.clicked.connect(self._on_semantic_switch_clicked)
        self.btn_semantic_highlight.clicked.connect(self._on_semantic_highlight_clicked)
        self.btn_semantic_window.clicked.connect(self._open_semantic_dialog)
        self.btn_anomaly_enable.toggled.connect(self._on_anomaly_toggle)
        self.btn_anomaly_search.clicked.connect(self._on_anomaly_search_clicked)
        self.btn_anomaly_clear.clicked.connect(self._clear_ai_highlight)
        self.btn_anomaly_window.clicked.connect(self._open_anomaly_dialog)
        self.btn_virtual_ad_enable.toggled.connect(self._on_virtual_ad_toggled)
        self.btn_virtual_ad_asset.clicked.connect(self._on_choose_virtual_ad_asset)
        self.btn_virtual_ad_apply.clicked.connect(self._apply_virtual_ad)
        self.btn_virtual_ad_remove.clicked.connect(self._remove_virtual_ad)
        self.btn_virtual_ad_window.clicked.connect(self._open_virtual_ad_dialog)
        self.btn_ar_enable.toggled.connect(self._on_ar_toggle)
        self.btn_ar_asset.clicked.connect(self._on_choose_ar_asset)
        self.btn_ar_apply.clicked.connect(self._apply_ar_to_selected_layer)
        self.btn_ar_clear.clicked.connect(self._clear_ar_from_selected_layer)
        self.btn_ar_window.clicked.connect(self._open_ar_dialog)

    def _show_ai_dialog(self, key: str, dialog) -> None:
        existing = self._ai_dialogs.pop(key, None)
        if existing is not None:
            try:
                existing.close()
            except Exception:
                pass
        self._ai_dialogs[key] = dialog
        dialog.finished.connect(lambda _result, dialog_key=key: self._ai_dialogs.pop(dialog_key, None))
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _sync_face_dialog(self) -> None:
        dialog = self._ai_dialogs.get("face")
        if not isinstance(dialog, FaceEffectDialog):
            return

        widgets = [
            dialog.enable_btn,
            dialog.nose_btn,
            dialog.hat_btn,
            dialog.eyes_btn,
            dialog.effect_combo,
            dialog.sticker_btn,
            dialog.scale_slider,
            dialog.smoothing_slider,
        ]
        enabled = self.btn_face_enable.isEnabled()
        for widget in widgets:
            widget.setEnabled(enabled)

        dialog.enable_btn.blockSignals(True)
        dialog.enable_btn.setChecked(self.btn_face_enable.isChecked())
        dialog.enable_btn.setText(self.btn_face_enable.text())
        dialog.enable_btn.blockSignals(False)

        dialog.effect_combo.blockSignals(True)
        dialog.effect_combo.setCurrentIndex(self.face_effect_combo.currentIndex())
        dialog.effect_combo.blockSignals(False)

        dialog.scale_slider.blockSignals(True)
        dialog.scale_slider.setValue(self.face_scale_slider.value())
        dialog.scale_slider.blockSignals(False)
        dialog.scale_value_label.setText(f"{self.face_scale_slider.value()}%")

        dialog.smoothing_slider.blockSignals(True)
        dialog.smoothing_slider.setValue(self.face_smoothing_slider.value())
        dialog.smoothing_slider.blockSignals(False)
        dialog.smoothing_value_label.setText(f"{self.face_smoothing_slider.value()}%")

        dialog.sticker_edit.setText(self.face_sticker_edit.text())
        dialog.target_label.setText(self.face_target_label.text())
        dialog.runtime_status_label.setText(self.face_status_text.text())
        dialog.status_bar.setValue(self.face_status_bar.value())

    def _open_face_dialog(self) -> None:
        dialog = FaceEffectDialog(
            self.btn_face_enable.isChecked(),
            self.face_effect_combo.currentText(),
            self.face_sticker_edit.text().strip(),
            self.face_scale_slider.value(),
            self.face_smoothing_slider.value(),
            self.face_target_label.text(),
            self.face_status_text.text(),
            self.face_status_bar.value(),
            parent=self,
        )
        dialog.enable_btn.toggled.connect(self.btn_face_enable.setChecked)
        dialog.nose_btn.clicked.connect(lambda: self._set_face_effect_type("dog_nose"))
        dialog.hat_btn.clicked.connect(lambda: self._set_face_effect_type("cat_ears"))
        dialog.eyes_btn.clicked.connect(lambda: self._set_face_effect_type("cartoon_eyes"))
        dialog.effect_combo.currentIndexChanged.connect(self.face_effect_combo.setCurrentIndex)
        dialog.sticker_btn.clicked.connect(self._on_choose_face_sticker)
        dialog.scale_slider.valueChanged.connect(self.face_scale_slider.setValue)
        dialog.smoothing_slider.valueChanged.connect(self.face_smoothing_slider.setValue)
        self._show_ai_dialog("face", dialog)
        self._sync_face_dialog()
        self._notify("已打开人脸识别与特效界面。")

    def _sync_virtual_bg_dialog(self) -> None:
        dialog = self._ai_dialogs.get("virtual_bg")
        if not isinstance(dialog, VirtualBackgroundDialog):
            return

        widgets = [
            dialog.enable_btn,
            dialog.mode_combo,
            dialog.blur_slider,
            dialog.asset_choose_btn,
            dialog.asset_clear_btn,
        ]
        enabled = self.btn_virtual_bg_enable.isEnabled()
        for widget in widgets:
            widget.setEnabled(enabled)

        dialog.enable_btn.blockSignals(True)
        dialog.enable_btn.setChecked(self.btn_virtual_bg_enable.isChecked())
        dialog.enable_btn.setText(self.btn_virtual_bg_enable.text())
        dialog.enable_btn.blockSignals(False)

        dialog.mode_combo.blockSignals(True)
        dialog.mode_combo.setCurrentIndex(self.virtual_bg_mode_combo.currentIndex())
        dialog.mode_combo.blockSignals(False)

        dialog.blur_slider.blockSignals(True)
        dialog.blur_slider.setValue(self.virtual_bg_blur_slider.value())
        dialog.blur_slider.blockSignals(False)
        dialog.blur_value_label.setText(f"{self.virtual_bg_blur_slider.value()}%")
        dialog.asset_edit.setText(self.virtual_bg_edit.text())
        dialog.target_label.setText(self.virtual_bg_target_label.text())
        dialog.runtime_status_label.setText(self.virtual_bg_status_label.text())
        use_bg_image = str(self.virtual_bg_mode_combo.currentData() or "image") == "image"
        dialog.blur_slider.setEnabled(enabled and not use_bg_image)
        dialog.asset_edit.setEnabled(enabled and use_bg_image)
        dialog.asset_choose_btn.setEnabled(enabled and use_bg_image)
        dialog.asset_clear_btn.setEnabled(enabled and use_bg_image)

    def _open_virtual_bg_dialog(self) -> None:
        dialog = VirtualBackgroundDialog(
            self.btn_virtual_bg_enable.isChecked(),
            self.virtual_bg_mode_combo.currentText(),
            self.virtual_bg_blur_slider.value(),
            self.virtual_bg_edit.text().strip(),
            self.virtual_bg_target_label.text(),
            self.virtual_bg_status_label.text(),
            parent=self,
        )
        dialog.enable_btn.toggled.connect(self.btn_virtual_bg_enable.setChecked)
        dialog.mode_combo.currentIndexChanged.connect(self.virtual_bg_mode_combo.setCurrentIndex)
        dialog.blur_slider.valueChanged.connect(self.virtual_bg_blur_slider.setValue)
        dialog.asset_choose_btn.clicked.connect(self._on_choose_virtual_bg_image)
        dialog.asset_clear_btn.clicked.connect(self._clear_virtual_bg_image)
        self._show_ai_dialog("virtual_bg", dialog)
        self._sync_virtual_bg_dialog()
        self._notify("已打开虚拟背景界面。")

    def _ai_current_frame_image(self, source: str):
        widget = self.program_preview if source == "program" else self.edit_preview
        return widget.current_frame_image()

    def _add_ai_image_layer(self, image_path: str) -> bool:
        path = str(Path(image_path))
        if not Path(path).exists():
            self._notify("AI 结果图片不存在，无法加入场景。", is_error=True)
            return False
        x, y, w, h = self._default_rect()
        layer = Layer(
            id=new_id("layer"),
            name=f"AI图片: {Path(path).stem}",
            layer_type=LayerType.PNG,
            x=x,
            y=y,
            width=w,
            height=h,
            source={"image_path": path, "ai_generated": True},
        )
        self._add_layer_common(layer)
        self._notify("AI 图片已作为新图层加入当前场景。")
        return True

    def _sync_ai_image_to_selected_layer(self, image_path: str) -> bool:
        if not self.selected_layer_id:
            self._notify("请先选择一个要同步的图层。", is_error=True)
            return False
        path = str(Path(image_path))
        if not Path(path).exists():
            self._notify("AI 结果图片不存在，无法同步。", is_error=True)
            return False

        def updater(layer: Layer) -> None:
            layer.name = f"AI图片: {Path(path).stem}"
            layer.layer_type = LayerType.PNG
            layer.source = {"image_path": path, "ai_generated": True, "synced_from_ai": True}

        if not self.state.update_layer(self.selected_layer_id, updater):
            self._notify("选中图层已失效，无法同步。", is_error=True)
            return False
        self._refresh_layer_list()
        self._refresh_preview_scene()
        self._sync_layer_manager_dialog(selected_layer_id=self.selected_layer_id)
        self._sync_canvas_workspace_from_state(reload_scene=False)
        self._notify("AI 图片已同步到选中图层。")
        return True

    def _send_ai_image_to_canvas(self, image_path: str) -> bool:
        path = str(Path(image_path))
        if not Path(path).exists():
            self._notify("AI 结果图片不存在，无法发送到画布。", is_error=True)
            return False
        self._open_canvas_workspace(reload_existing=False)
        if self._canvas_workspace is None:
            return False
        return self._canvas_workspace.add_ai_image_to_canvas(path, name=Path(path).stem)

    def _open_ai_model_dialog(self) -> None:
        dialog = AIModelWorkbenchDialog(
            settings=self._ai_settings,
            output_root=Path("outputs") / "ai_generated",
            current_frame_provider=self._ai_current_frame_image,
            add_image_layer=self._add_ai_image_layer,
            sync_selected_layer=self._sync_ai_image_to_selected_layer,
            send_to_canvas=self._send_ai_image_to_canvas,
            parent=self,
        )
        self._show_ai_dialog("ai_model", dialog)
        self._notify("已打开 AI 大模型图像工作台。")

    def _open_semantic_dialog(self) -> None:
        dialog = SemanticDirectorDialog(self.semantic_query_edit.text().strip(), parent=self)
        dialog.recommendation_requested.connect(self._start_semantic_recommendation)
        dialog.recommendation_stopped.connect(self._stop_semantic_recommendation)
        dialog.highlight_requested.connect(self._highlight_semantic_scene)
        dialog.switch_requested.connect(self._switch_to_semantic_scene)
        dialog.set_provider_text(self._semantic_worker.provider_label() if hasattr(self, "_semantic_worker") else "未加载")
        dialog.set_scene_previews(self._semantic_preview_images())
        if self._semantic_recommendations:
            pseudo = type(
                "SemanticUiResult",
                (),
                {
                    "scores": list(self._semantic_recommendations.values()),
                    "best_scene_id": self._semantic_best_scene_id,
                    "provider": self._semantic_worker.provider_label() if hasattr(self, "_semantic_worker") else "未知",
                    "elapsed_ms": 0.0,
                    "error": "",
                },
            )()
            dialog.set_results(pseudo, self._semantic_preview_images())
        self._show_ai_dialog("semantic", dialog)
        self._notify("已打开智能导播模式。")

    def _open_anomaly_dialog(self) -> None:
        dialog = AnomalyDetectionDialog(
            self.btn_anomaly_enable.isChecked(),
            self.anomaly_query_edit.text().strip(),
            parent=self,
        )
        self._show_ai_dialog("anomaly", dialog)
        self._notify("已打开舞台异常检测界面。")

    def _open_virtual_ad_dialog(self) -> None:
        dialog = VirtualAdDialog(
            self.btn_virtual_ad_enable.isChecked(),
            self.virtual_ad_position_combo.currentText(),
            self.virtual_ad_asset_edit.text().strip(),
            parent=self,
        )
        self._show_ai_dialog("virtual_ad", dialog)
        self._notify("已打开虚拟广告界面。")

    def _open_ar_dialog(self) -> None:
        dialog = ARFeatureDialog(
            self.btn_ar_enable.isChecked(),
            self.ar_mode_combo.currentText(),
            self.ar_target_label.text(),
            self.ar_asset_edit.text().strip(),
            self.face_effect_combo.currentText(),
            parent=self,
        )
        dialog.enable_btn.toggled.connect(self.btn_ar_enable.setChecked)
        dialog.mode_combo.currentIndexChanged.connect(self.ar_mode_combo.setCurrentIndex)
        dialog.effect_combo.currentIndexChanged.connect(
            lambda index: self._set_face_effect_type(str(dialog.effect_combo.itemData(index) or "dog_nose"))
        )
        dialog.asset_btn.clicked.connect(self._on_choose_ar_asset)
        dialog.apply_btn.clicked.connect(self._apply_ar_to_selected_layer)
        dialog.clear_btn.clicked.connect(self._clear_ar_from_selected_layer)
        self._show_ai_dialog("ar", dialog)
        self._notify("已打开 AR 功能界面。")

    def _notify(self, text: str, is_error: bool = False) -> None:
        self.status_label.setText(f"状态: {text}")
        self.statusBar().showMessage(text, 5000)
        if is_error:
            QMessageBox.warning(self, "提示", text)

    def _open_preview_popout(self) -> None:
        if self._preview_popout is None:
            dialog = PreviewPopoutWindow(
                self.config.canvas_width,
                self.config.canvas_height,
                self,
                self.config.default_rtmp_url,
            )
            dialog.edit_preview.layer_selected.connect(self._select_layer)
            dialog.edit_preview.layer_transform_changed.connect(self._update_layer_rect)
            dialog.btn_edit_position.clicked.connect(
                lambda checked: self._on_preview_edit_mode_clicked("position", checked)
            )
            dialog.btn_edit_size.clicked.connect(lambda checked: self._on_preview_edit_mode_clicked("size", checked))
            dialog.btn_edit_lock.clicked.connect(lambda checked: self._on_preview_edit_mode_clicked("lock", checked))
            dialog.aspect_combo.currentIndexChanged.connect(self._on_preview_popout_aspect_changed)
            dialog.output_quality_combo.currentIndexChanged.connect(self._on_output_quality_changed)
            dialog.adaptive_bitrate_check.toggled.connect(self._on_adaptive_bitrate_toggled)
            dialog.scene_combo.currentIndexChanged.connect(self._on_preview_popout_scene_changed)
            dialog.delay_spin.valueChanged.connect(self._on_preview_popout_delay_changed)
            dialog.btn_transition_settings.clicked.connect(self._open_transition_dialog)
            dialog.btn_emergency_placeholder.toggled.connect(self._on_emergency_placeholder_toggled)
            dialog.btn_choose_placeholder_video.clicked.connect(self._choose_placeholder_video)
            dialog.rtmp_edit.textChanged.connect(self._on_preview_popout_rtmp_changed)
            dialog.btn_stream_start.clicked.connect(self._start_stream)
            dialog.btn_stream_stop.clicked.connect(self._stop_stream)
            dialog.btn_record_start.clicked.connect(self._start_record)
            dialog.btn_record_stop.clicked.connect(self._stop_record)
            dialog.btn_audio_monitor.toggled.connect(self._on_audio_monitor_toggled)
            dialog.monitor_volume_slider.valueChanged.connect(self._on_monitor_volume_changed)
            dialog.closed.connect(self._on_preview_popout_closed)
            self._preview_popout = dialog
        self._sync_preview_popout_controls()
        self._sync_monitor_controls()
        self._refresh_preview_scene()
        self._sync_preview_edit_controls()
        self._preview_popout.show()
        self._preview_popout.raise_()
        self._preview_popout.activateWindow()
        self.btn_preview_popout.setText("已开")
        self._set_panel_popped("preview", True)
        self._set_panel_popped("output", True)

    def _on_preview_popout_closed(self) -> None:
        self.btn_preview_popout.setText("弹出")
        self._preview_popout = None
        self._set_panel_popped("preview", False)
        self._set_panel_popped("output", False)

    def _open_scene_popout(self) -> None:
        if self._scene_popout is None:
            dialog = SceneGridPopoutWindow(self)
            dialog.scene_selected.connect(self._on_scene_popout_selected)
            dialog.grid_changed.connect(lambda columns: self._set_scene_grid_columns(columns))
            dialog.clear_scene_clicked.connect(self._clear_selected_scene)
            dialog.transition_clicked.connect(self._open_transition_dialog)
            dialog.emergency_toggled.connect(self._on_emergency_placeholder_toggled)
            dialog.closed.connect(self._on_scene_popout_closed)
            self._scene_popout = dialog
        self._sync_scene_popout()
        self._scene_popout.show()
        self._scene_popout.raise_()
        self._scene_popout.activateWindow()
        self.btn_scene_popout.setText("已开")
        self._set_panel_popped("scene", True)

    def _on_scene_popout_closed(self) -> None:
        self.btn_scene_popout.setText("弹出")
        self._scene_popout = None
        self._set_panel_popped("scene", False)

    def _open_canvas_workspace(self, checked: bool = False, *, reload_existing: bool = True) -> None:
        if self._canvas_workspace is None:
            dialog = InfiniteCanvasDialog(
                self.state,
                self.config.canvas_width,
                self.config.canvas_height,
                self,
                audio_controller=self.audio_controller,
                ai_settings=self._ai_settings,
            )
            dialog.scene_committed.connect(self._on_canvas_scene_committed)
            dialog.closed.connect(self._on_canvas_workspace_closed)
            self._canvas_workspace = dialog
        elif reload_existing:
            self._canvas_workspace.refresh_from_state(reload_scene=True)
        self._canvas_workspace.show()
        self._canvas_workspace.raise_()
        self._canvas_workspace.activateWindow()
        self.btn_canvas_workspace.setText("已开")

    def _on_canvas_workspace_closed(self) -> None:
        self.btn_canvas_workspace.setText("画布模式")
        self._canvas_workspace = None

    def _sync_canvas_workspace_from_state(self, reload_scene: bool = False) -> None:
        if self._canvas_workspace is None:
            return
        self._canvas_workspace.refresh_from_state(reload_scene=reload_scene)

    def _on_canvas_scene_committed(self, scene_id: str) -> None:
        if not scene_id:
            return
        self.selected_layer_id = None
        self._refresh_scene_list()
        self._refresh_layer_list()
        self._refresh_preview_scene()
        self._sync_scene_popout()
        self._sync_layer_manager_dialog()
        self._notify("画布内容已写回导播场景。")

    def _sync_scene_popout(self) -> None:
        if self._scene_popout is None:
            return
        scenes = self._normal_scene_list(self.state.snapshot_scenes())
        active_id = self.state.get_active_scene_id()
        self._scene_popout.set_grid_columns(self._scene_grid_columns)
        self._scene_popout.set_scenes(scenes, active_id, self._scene_preview_cache)
        self._sync_transition_controls()
        self._sync_emergency_placeholder_controls()

    def _on_scene_popout_selected(self, scene_id: str) -> None:
        if scene_id == self.state.get_active_scene_id():
            return
        if self.state.set_active_scene(scene_id):
            self.selected_layer_id = None
            self._refresh_scene_list()
            self._refresh_layer_list()
            scene = self.state.get_scene_by_id(scene_id)
            self._notify(f"已切换到场景: {scene.name if scene is not None else scene_id}")

    def _open_transition_dialog(self) -> None:
        if self._transition_dialog is None:
            dialog = TransitionDialog(self)
            dialog.config_applied.connect(self._apply_transition_config)
            dialog.closed.connect(self._on_transition_dialog_closed)
            self._transition_dialog = dialog
        self._transition_dialog.set_config(self.state.get_transition_config())
        self._transition_dialog.show()
        self._transition_dialog.raise_()
        self._transition_dialog.activateWindow()

    def _on_transition_dialog_closed(self) -> None:
        self._transition_dialog = None

    def _apply_transition_config(self, config: TransitionConfig) -> None:
        self.state.set_transition_config(config)
        self._sync_transition_controls()
        self._notify(f"转场已设置为: {self._transition_label(config)}")

    @staticmethod
    def _transition_label(config: TransitionConfig) -> str:
        mode_labels = {
            "cut": "硬切",
            "dissolve": "化像/叠化",
            "wipe": "划像",
            "dve": "DVE",
            "media": "自定义素材",
        }
        wipe_labels = {
            "horizontal": "水平线",
            "vertical": "垂直线",
            "circle": "圆形",
            "diagonal": "对角线",
        }
        dve_labels = {
            "push": "推拉",
            "rotate": "旋转缩放",
            "page": "翻页",
            "squeeze": "挤压",
        }
        mode = str(config.mode or "cut")
        label = mode_labels.get(mode, "硬切")
        if mode == "wipe":
            label += f" / {wipe_labels.get(config.wipe_shape, '水平线')}"
        elif mode == "dve":
            label += f" / {dve_labels.get(config.dve_mode, '推拉')}"
        elif mode == "media" and config.media_path:
            label += f" / {Path(config.media_path).name}"
        return label

    def _sync_transition_controls(self) -> None:
        config = self.state.get_transition_config()
        duration = "" if config.mode == "cut" else f" | {config.duration_ms}ms"
        transition_text = f"转场: {self._transition_label(config)}{duration}"
        self.transition_status_label.setText(transition_text)
        if self._transition_dialog is not None:
            self._transition_dialog.set_config(config)
        if self._scene_popout is not None:
            self._scene_popout.set_transition_text(transition_text)

    def _open_layer_manager_dialog(self) -> None:
        if self._layer_manager_dialog is None:
            dialog = LayerManagerDialog(self)
            dialog.scene_selected.connect(self._on_layer_manager_scene_selected)
            dialog.layer_selected.connect(self._select_layer)
            dialog.layer_deleted.connect(self._delete_layer_from_dialog)
            dialog.layer_priority_changed.connect(self._set_layer_priority_from_dialog)
            dialog.layer_volume_changed.connect(self._set_layer_volume_from_dialog)
            dialog.layer_saturation_changed.connect(self._set_layer_saturation_from_dialog)
            dialog.layer_contrast_changed.connect(self._set_layer_contrast_from_dialog)
            dialog.layer_color_temp_changed.connect(self._set_layer_temp_from_dialog)
            dialog.layer_mosaic_changed.connect(self._set_layer_mosaic_from_dialog)
            dialog.layer_onnx_style_changed.connect(
                lambda scene_id, layer_id, value: self._set_layer_source_value_from_dialog(
                    scene_id, layer_id, "onnx_style", self._canonical_onnx_style_value(value)
                )
            )
            dialog.layer_face_enabled_changed.connect(
                lambda scene_id, layer_id, value: self._set_layer_source_value_from_dialog(
                    scene_id, layer_id, "face_enabled", value
                )
            )
            dialog.layer_face_effect_changed.connect(self._set_layer_face_effect_type_from_dialog)
            dialog.layer_face_scale_changed.connect(
                lambda scene_id, layer_id, value: self._set_layer_source_value_from_dialog(
                    scene_id, layer_id, "face_scale_percent", int(max(50, min(200, value)))
                )
            )
            dialog.layer_face_smoothing_changed.connect(
                lambda scene_id, layer_id, value: self._set_layer_source_value_from_dialog(
                    scene_id, layer_id, "face_smoothing", int(max(0, min(100, value)))
                )
            )
            dialog.layer_virtual_bg_enabled_changed.connect(
                lambda scene_id, layer_id, value: self._set_layer_source_value_from_dialog(
                    scene_id, layer_id, "virtual_bg_enabled", value, clear_metrics=True
                )
            )
            dialog.layer_virtual_bg_mode_changed.connect(
                lambda scene_id, layer_id, value: self._set_layer_source_value_from_dialog(
                    scene_id, layer_id, "virtual_bg_mode", "blur" if value == "blur" else "image", clear_metrics=True
                )
            )
            dialog.layer_virtual_bg_blur_changed.connect(
                lambda scene_id, layer_id, value: self._set_layer_source_value_from_dialog(
                    scene_id, layer_id, "virtual_bg_blur_strength", int(max(0, min(100, value))), clear_metrics=True
                )
            )
            dialog.add_camera_clicked.connect(lambda: self._add_camera_layer(dialog.current_scene_id()))
            dialog.add_screen_clicked.connect(lambda: self._add_screen_layer(dialog.current_scene_id()))
            dialog.add_window_clicked.connect(lambda: self._add_window_layer(dialog.current_scene_id()))
            dialog.add_image_clicked.connect(lambda: self._add_png_layer(dialog.current_scene_id()))
            dialog.add_network_clicked.connect(lambda: self._add_network_layer(dialog.current_scene_id()))
            dialog.placeholder_scene_clicked.connect(self._open_placeholder_scene_dialog)
            dialog.closed.connect(self._on_layer_manager_closed)
            self._layer_manager_dialog = dialog
        self._sync_layer_manager_dialog()
        self._layer_manager_dialog.set_placeholder_editor_open(self._placeholder_dialog is not None)
        self._layer_manager_dialog.show()
        self._layer_manager_dialog.raise_()
        self._layer_manager_dialog.activateWindow()
        self.btn_layer_manager.setText("已开")

    def _on_layer_manager_closed(self) -> None:
        self.btn_layer_manager.setText("管理")
        self._layer_manager_dialog = None

    def _placeholder_scene_id(self) -> str | None:
        return self.state.get_placeholder_scene_id()

    def _placeholder_scene(self):
        scene_id = self._placeholder_scene_id()
        return self.state.get_scene_by_id(scene_id) if scene_id else None

    def _open_placeholder_scene_dialog(self) -> None:
        scene_id = self._placeholder_scene_id()
        if not scene_id:
            self._notify("未找到紧急占位场景。", is_error=True)
            return
        if self._placeholder_dialog is None:
            dialog = PlaceholderSceneDialog(self.config.canvas_width, self.config.canvas_height, self)
            dialog.add_camera_clicked.connect(lambda: self._add_camera_layer(scene_id))
            dialog.add_screen_clicked.connect(lambda: self._add_screen_layer(scene_id))
            dialog.add_window_clicked.connect(lambda: self._add_window_layer(scene_id))
            dialog.add_image_clicked.connect(lambda: self._add_png_layer(scene_id))
            dialog.add_network_clicked.connect(lambda: self._add_network_layer(scene_id))
            dialog.choose_video_clicked.connect(self._choose_placeholder_video)
            dialog.layer_transform_changed.connect(self._update_placeholder_layer_rect)
            dialog.layer_deleted.connect(self._delete_placeholder_layer)
            dialog.layer_lock_changed.connect(lambda layer_id, locked: self._update_placeholder_layer(layer_id, "locked", locked))
            dialog.layer_enabled_changed.connect(
                lambda layer_id, enabled: self._update_placeholder_layer(layer_id, "enabled", enabled)
            )
            dialog.layer_priority_changed.connect(self._set_placeholder_layer_priority)
            dialog.layer_volume_changed.connect(lambda layer_id, value: self._update_placeholder_layer(layer_id, "volume", value))
            dialog.layer_saturation_changed.connect(
                lambda layer_id, value: self._update_placeholder_layer(layer_id, "saturation", value)
            )
            dialog.layer_contrast_changed.connect(lambda layer_id, value: self._update_placeholder_layer(layer_id, "contrast", value))
            dialog.layer_color_temp_changed.connect(
                lambda layer_id, value: self._update_placeholder_layer(layer_id, "color_temp", value)
            )
            dialog.layer_mosaic_changed.connect(lambda layer_id, value: self._update_placeholder_layer(layer_id, "mosaic", value))
            dialog.layer_onnx_style_changed.connect(
                lambda layer_id, value: self._update_placeholder_layer_source(
                    layer_id, "onnx_style", self._canonical_onnx_style_value(value)
                )
            )
            dialog.layer_face_enabled_changed.connect(
                lambda layer_id, value: self._update_placeholder_layer_source(layer_id, "face_enabled", value)
            )
            dialog.layer_face_effect_changed.connect(self._set_placeholder_layer_face_effect_type)
            dialog.layer_face_scale_changed.connect(
                lambda layer_id, value: self._update_placeholder_layer_source(layer_id, "face_scale_percent", value)
            )
            dialog.layer_face_smoothing_changed.connect(
                lambda layer_id, value: self._update_placeholder_layer_source(layer_id, "face_smoothing", value)
            )
            dialog.layer_virtual_bg_enabled_changed.connect(
                lambda layer_id, value: self._update_placeholder_layer_source(layer_id, "virtual_bg_enabled", value)
            )
            dialog.layer_virtual_bg_mode_changed.connect(
                lambda layer_id, value: self._update_placeholder_layer_source(layer_id, "virtual_bg_mode", value)
            )
            dialog.layer_virtual_bg_blur_changed.connect(
                lambda layer_id, value: self._update_placeholder_layer_source(layer_id, "virtual_bg_blur_strength", value)
            )
            dialog.closed.connect(self._on_placeholder_dialog_closed)
            self._placeholder_dialog = dialog
        self._sync_placeholder_dialog()
        self._placeholder_dialog.show()
        self._placeholder_dialog.raise_()
        self._placeholder_dialog.activateWindow()
        self.btn_placeholder_scene_editor.setText("已开")
        if self._layer_manager_dialog is not None:
            self._layer_manager_dialog.set_placeholder_editor_open(True)

    def _on_placeholder_dialog_closed(self) -> None:
        self.btn_placeholder_scene_editor.setText("占位场景")
        if self._layer_manager_dialog is not None:
            self._layer_manager_dialog.set_placeholder_editor_open(False)
        self._placeholder_dialog = None

    def _sync_placeholder_dialog(self) -> None:
        if self._placeholder_dialog is None:
            return
        scene = self._placeholder_scene()
        preview_image = self._scene_preview_cache.get(scene.id) if scene is not None else None
        self._placeholder_dialog.set_scene(scene, preview_image=preview_image)

    def _refresh_placeholder_after_edit(self) -> None:
        self._sync_placeholder_dialog()
        self._sync_emergency_placeholder_controls()
        self._refresh_preview_scene()

    def _update_placeholder_layer(self, layer_id: str, attr: str, value) -> None:
        scene_id = self._placeholder_scene_id()
        if scene_id and self.state.update_layer(layer_id, lambda layer: setattr(layer, attr, value), scene_id=scene_id):
            self._refresh_placeholder_after_edit()

    def _update_placeholder_layer_source(self, layer_id: str, key: str, value) -> None:
        scene_id = self._placeholder_scene_id()
        if not scene_id:
            return

        def updater(layer: Layer):
            layer.source[key] = value

        if self.state.update_layer(layer_id, updater, scene_id=scene_id):
            if key == "onnx_style":
                preload_onnx_style_filter(value)
            elif key == "virtual_bg_enabled" and bool(value):
                prewarm_mediapipe_components(segmentation=True)
            elif key == "face_enabled" and bool(value):
                prewarm_mediapipe_components(face_mesh=True)
            self._refresh_placeholder_after_edit()
            if key == "onnx_style":
                self._notify(f"ONNX 风格已应用：{onnx_style_label(value)}")

    def _set_placeholder_layer_face_effect_type(self, layer_id: str, effect_type: str) -> None:
        scene_id = self._placeholder_scene_id()
        if not scene_id:
            return
        effect_type = canonical_ar_effect_type(effect_type) or "dog_nose"
        default_path = default_ar_sticker_path(effect_type)

        def updater(layer: Layer):
            layer.source["effect_type"] = effect_type
            if default_path:
                layer.source["sticker_path"] = default_path

        if self.state.update_layer(layer_id, updater, scene_id=scene_id):
            self._refresh_placeholder_after_edit()

    def _update_placeholder_layer_rect(self, layer_id: str, x: int, y: int, w: int, h: int) -> None:
        scene_id = self._placeholder_scene_id()
        if not scene_id:
            return

        def updater(layer: Layer):
            layer.x = x
            layer.y = y
            layer.width = w
            layer.height = h

        if self.state.update_layer(layer_id, updater, scene_id=scene_id):
            self._refresh_placeholder_after_edit()

    def _set_placeholder_layer_priority(self, layer_id: str, priority: int) -> None:
        scene_id = self._placeholder_scene_id()
        if scene_id and self.state.set_layer_priority(layer_id, priority, scene_id=scene_id):
            self._refresh_placeholder_after_edit()

    def _delete_placeholder_layer(self, layer_id: str) -> None:
        scene_id = self._placeholder_scene_id()
        if scene_id and self.state.remove_layer(layer_id, scene_id=scene_id):
            if self._placeholder_dialog is not None:
                self._placeholder_dialog.set_selected_layer(None)
            self._refresh_placeholder_after_edit()
            self._notify("占位场景图层已删除。")

    def _sync_layer_manager_dialog(
        self,
        focus_scene_id: str | None = None,
        selected_layer_id: str | None = None,
    ) -> None:
        if self._layer_manager_dialog is None:
            return
        selected_layer_id = self.selected_layer_id if selected_layer_id is None else selected_layer_id
        self._layer_manager_dialog.set_scenes(
            self._normal_scene_list(self.state.snapshot_scenes()),
            self.state.get_active_scene_id(),
            focus_scene_id=focus_scene_id,
        )
        self._layer_manager_dialog.set_selected_layer(selected_layer_id)
        self._layer_manager_dialog.set_placeholder_editor_open(self._placeholder_dialog is not None)

    def _on_layer_manager_scene_selected(self, scene_id: str) -> None:
        if scene_id == self.state.get_active_scene_id():
            return
        if self.state.set_active_scene(scene_id):
            self.selected_layer_id = None
            self._refresh_scene_list()
            self._refresh_layer_list()
            self._notify("已切换图层管理场景。")

    def _sync_preview_popout_scene_combo(self) -> None:
        if self._preview_popout is None:
            return
        scenes = self._normal_scene_list(self.state.snapshot_scenes())
        active_id = self.state.get_active_scene_id()
        combo = self._preview_popout.scene_combo
        combo.blockSignals(True)
        combo.clear()
        for scene in scenes:
            combo.addItem(scene.name, scene.id)
        index = combo.findData(active_id)
        combo.setCurrentIndex(max(0, index))
        combo.blockSignals(False)

    def _sync_aspect_ratio_controls(self) -> None:
        self.aspect_combo.blockSignals(True)
        self._set_combo_data(self.aspect_combo, self._current_aspect_ratio)
        self.aspect_combo.blockSignals(False)
        if self._preview_popout is not None:
            self._preview_popout.aspect_combo.blockSignals(True)
            self._set_combo_data(self._preview_popout.aspect_combo, self._current_aspect_ratio)
            self._preview_popout.aspect_combo.blockSignals(False)

    def _set_all_preview_canvas_size(self, width: int, height: int) -> None:
        self.edit_preview.set_canvas_size(width, height)
        self.program_preview.set_canvas_size(width, height)
        if self._preview_popout is not None:
            self._preview_popout.edit_preview.set_canvas_size(width, height)
            self._preview_popout.program_preview.set_canvas_size(width, height)
        if self._placeholder_dialog is not None:
            self._placeholder_dialog.set_canvas_size(width, height)

    def _output_is_running(self) -> bool:
        status = self.output_manager.status()
        return status.get("record") == "录制中" or status.get("stream") == "推流中"

    def _apply_output_aspect_ratio(self, aspect: str) -> None:
        aspect = str(aspect or "16:9")
        if aspect == self._current_aspect_ratio:
            self._sync_aspect_ratio_controls()
            return
        if self._output_is_running():
            self._sync_aspect_ratio_controls()
            self._notify("请先停止推流和录制，再切换输出画面比例。", is_error=True)
            return

        old_width, old_height = self.config.canvas_width, self.config.canvas_height
        new_width, new_height = self._canvas_size_for_aspect(aspect)
        self.state.resize_canvas(old_width, old_height, new_width, new_height)
        self.config.canvas_width = new_width
        self.config.canvas_height = new_height
        self.output_manager.set_video_size(new_width, new_height)
        if hasattr(self, "render_thread"):
            self.render_thread.set_canvas_size(new_width, new_height)
        self._current_aspect_ratio = aspect
        self._set_all_preview_canvas_size(new_width, new_height)
        self._sync_aspect_ratio_controls()
        self._refresh_scene_list()
        self._refresh_layer_list()
        self._refresh_preview_scene()
        self._sync_placeholder_dialog()
        self._notify(f"输出画面比例已切换为 {aspect}（{new_width}×{new_height}）。")

    def _sync_preview_popout_output_controls(self, status: dict[str, str] | None = None) -> None:
        if self._preview_popout is None:
            return
        popout = self._preview_popout
        if hasattr(popout, "output_quality_combo"):
            popout.output_quality_combo.blockSignals(True)
            self._set_combo_data(popout.output_quality_combo, self._output_quality_key)
            popout.output_quality_combo.blockSignals(False)
        popout.delay_spin.blockSignals(True)
        popout.delay_spin.setValue(self.delay_spin.value())
        popout.delay_spin.blockSignals(False)

        if popout.rtmp_edit.text() != self.rtmp_edit.text():
            popout.rtmp_edit.blockSignals(True)
            popout.rtmp_edit.setText(self.rtmp_edit.text())
            popout.rtmp_edit.blockSignals(False)

        status = status or self.output_manager.status()
        self._set_status_badge(
            popout.stream_status_badge,
            status.get("stream", "未运行"),
            status.get("stream_error", ""),
        )
        self._set_status_badge(
            popout.record_status_badge,
            status.get("record", "未运行"),
            status.get("record_error", ""),
        )
        if hasattr(popout, "encoder_status_label"):
            popout.encoder_status_label.setText(self._encoding_status_text(status))
        self._sync_adaptive_bitrate_controls()
        self._sync_emergency_placeholder_controls()

    @staticmethod
    def _encoding_mode_label(value: str) -> str:
        labels = {
            "auto": "自动 GPU优先",
            "hybrid": "自动 GPU优先",
            "cpu+gpu": "自动 GPU优先",
            "gpu": "优先 GPU/NVENC",
            "nvenc": "优先 GPU/NVENC",
            "cpu": "仅 CPU/x264",
            "x264": "仅 CPU/x264",
        }
        return labels.get(str(value or "auto").strip().lower(), str(value or "auto"))

    @classmethod
    def _output_quality_meta(cls, key: str) -> dict[str, object]:
        return cls.OUTPUT_QUALITY_PRESETS.get(str(key or "").strip(), cls.OUTPUT_QUALITY_PRESETS["balanced_720p60"])

    def _output_quality_label(self, key: str | None = None) -> str:
        meta = self._output_quality_meta(key or self._output_quality_key)
        return str(meta["label"])

    def _apply_capture_quality_key(self, key: str, *, notify: bool = False) -> None:
        key = str(key or "standard")
        if key not in self.CAPTURE_QUALITY_PRESETS:
            key = "standard"
        if key == self._capture_quality_key:
            return
        self._capture_quality_key = key
        self.config.default_capture_quality = key
        settings = self._capture_quality_settings()
        video_types = {LayerType.CAMERA, LayerType.SCREEN, LayerType.WINDOW, LayerType.NETWORK, LayerType.VIDEO}

        def updater(layer: Layer) -> None:
            if layer.layer_type in video_types:
                layer.source.update(settings)

        for scene in self.state.snapshot_scenes():
            for layer in scene.layers:
                if layer.layer_type in video_types:
                    self.state.update_layer(layer.id, updater, scene_id=scene.id)

        if hasattr(self, "capture_quality_combo"):
            self.capture_quality_combo.blockSignals(True)
            self._set_combo_data(self.capture_quality_combo, key)
            self.capture_quality_combo.blockSignals(False)

        self._refresh_layer_list()
        self._sync_layer_manager_dialog()
        self._refresh_preview_scene()
        self._sync_canvas_workspace_from_state(reload_scene=False)
        if notify:
            self._notify(f"采集质量已切换为 {self.CAPTURE_QUALITY_PRESETS[key]['label']}。")
            self._save_output_preferences()

    def _update_output_protection(self, preset: dict[str, object]) -> None:
        thumb_interval = float(preset.get("thumb_interval") or 1.0)
        semantic_interval = int(preset.get("semantic_interval") or 1600)
        performance = str(preset.get("performance") or "平衡")
        if hasattr(self, "render_thread"):
            self.render_thread.set_thumbnail_interval(thumb_interval)
        if hasattr(self, "_semantic_timer"):
            self._semantic_timer.setInterval(max(800, semantic_interval))
        self._output_performance_hint = performance

    def _sync_output_quality_ui(self, key: str) -> None:
        preset = self._output_quality_meta(key)
        if hasattr(self, "hero_quality_chip"):
            self.hero_quality_chip.setText(f"Quality: {self._output_quality_label(key)}")
        if hasattr(self, "output_quality_combo"):
            self.output_quality_combo.blockSignals(True)
            self._set_combo_data(self.output_quality_combo, key)
            self.output_quality_combo.blockSignals(False)
        if hasattr(self, "stream_bitrate_combo"):
            bitrate = str(self.config.default_stream_bitrate or preset.get("bitrate") or "5000k")
            self.stream_bitrate_combo.blockSignals(True)
            self.stream_bitrate_combo.setCurrentText(bitrate)
            self.stream_bitrate_combo.blockSignals(False)
        if hasattr(self, "record_bitrate_combo"):
            record_bitrate = str(self.config.default_record_bitrate or preset.get("record_bitrate") or "8000k")
            self.record_bitrate_combo.blockSignals(True)
            self.record_bitrate_combo.setCurrentText(record_bitrate)
            self.record_bitrate_combo.blockSignals(False)
        if hasattr(self, "stream_encoder_combo"):
            encoder = str(self.config.default_stream_encoder or preset.get("encoder") or "auto")
            self.stream_encoder_combo.blockSignals(True)
            self._set_combo_data(self.stream_encoder_combo, encoder)
            self.stream_encoder_combo.blockSignals(False)
        if hasattr(self, "record_encoder_combo"):
            record_encoder = str(self.config.default_record_encoder or "auto")
            self.record_encoder_combo.blockSignals(True)
            self._set_combo_data(self.record_encoder_combo, record_encoder)
            self.record_encoder_combo.blockSignals(False)

    def _set_adaptive_bitrate_text(self, text: str, *, kind: str = "idle") -> None:
        self._adaptive_bitrate_text = text
        self._adaptive_bitrate_kind = kind
        style_kind = "running" if kind == "running" else "error" if kind == "warning" else "idle"
        labels = [getattr(self, "adaptive_bitrate_label", None)]
        if self._preview_popout is not None:
            labels.append(getattr(self._preview_popout, "adaptive_bitrate_label", None))
        for label in labels:
            if label is None:
                continue
            label.setText(text)
            label.setStyleSheet(status_badge_qss(style_kind))

    def _sync_adaptive_bitrate_controls(self) -> None:
        checks = [getattr(self, "adaptive_bitrate_check", None)]
        if self._preview_popout is not None:
            checks.append(getattr(self._preview_popout, "adaptive_bitrate_check", None))
        for check in checks:
            if check is None:
                continue
            check.blockSignals(True)
            check.setChecked(self._adaptive_bitrate_enabled)
            check.blockSignals(False)
        if not self._adaptive_bitrate_enabled:
            self._set_adaptive_bitrate_text("ABR: 关闭")
        else:
            self._set_adaptive_bitrate_text(self._adaptive_bitrate_text, kind=self._adaptive_bitrate_kind)

    def _on_adaptive_bitrate_toggled(self, checked: bool) -> None:
        self._adaptive_bitrate_enabled = bool(checked)
        self.config.adaptive_bitrate_enabled = self._adaptive_bitrate_enabled
        if not checked:
            self._adaptive_pending_bitrate = ""
            self._adaptive_bitrate.reset()
            self._set_adaptive_bitrate_text("ABR: 关闭")
        else:
            self._set_adaptive_bitrate_text("ABR: 已启用", kind="running")
        self._sync_adaptive_bitrate_controls()
        self._save_output_preferences()

    def _evaluate_adaptive_bitrate(self, status: dict[str, object]) -> None:
        if not hasattr(self, "adaptive_bitrate_label"):
            return
        if not self._adaptive_bitrate_enabled:
            self._set_adaptive_bitrate_text("ABR: 关闭")
            return
        stream_running = status.get("stream") == "推流中"
        stream_stats = status.get("stream_stats")
        if not isinstance(stream_stats, dict):
            stream_stats = {}
        decision = self._adaptive_bitrate.observe(
            stream_stats,
            str(status.get("stream_bitrate") or self.config.default_stream_bitrate),
            stream_running=bool(stream_running),
        )
        if decision.state == "reduce" and decision.target_bitrate:
            self._adaptive_pending_bitrate = decision.target_bitrate
            self._set_adaptive_bitrate_text(
                f"ABR: 建议 {decision.target_bitrate} 下次生效",
                kind="warning",
            )
            return
        if decision.state in {"stable", "observe"}:
            self._set_adaptive_bitrate_text(decision.text, kind="running")
            return
        self._set_adaptive_bitrate_text(decision.text)

    def _apply_pending_adaptive_bitrate_before_stream(self) -> None:
        if not self._adaptive_bitrate_enabled or not self._adaptive_pending_bitrate:
            return
        target = self._adaptive_pending_bitrate
        self._adaptive_pending_bitrate = ""
        self.stream_bitrate_combo.blockSignals(True)
        self.stream_bitrate_combo.setCurrentText(target)
        self.stream_bitrate_combo.blockSignals(False)
        self.config.default_stream_bitrate = target
        self.output_manager.set_encoding_profile(stream_bitrate=target)
        self._set_adaptive_bitrate_text(f"ABR: 已应用 {target}", kind="running")
        self._save_output_preferences()

    def _apply_output_quality_profile(self, key: str | None = None, *, initial: bool = False, notify: bool = False) -> None:
        key = str(key or self._output_quality_key or getattr(self.config, "default_output_quality", "balanced_720p60"))
        if key not in self.OUTPUT_QUALITY_PRESETS:
            key = "balanced_720p60"
        if not initial and key == self._output_quality_key:
            self._sync_output_quality_ui(key)
            self._update_output_protection(self._output_quality_meta(key))
            if hasattr(self, "encoder_status_label"):
                self.encoder_status_label.setText(self._encoding_status_text())
            return
        if not initial and self._output_is_running():
            self._sync_output_quality_ui(self._output_quality_key)
            if notify:
                self._notify("请先停止推流和录制，再切换输出质量。", is_error=True)
            return

        preset = self._output_quality_meta(key)
        new_width = int(preset["width"])
        new_height = int(preset["height"])
        new_fps = int(preset["fps"])
        capture_key = str(preset["capture_quality"])
        stream_bitrate = str(preset["bitrate"])
        record_bitrate = str(preset.get("record_bitrate") or self.config.default_record_bitrate)
        stream_encoder = str(preset["encoder"])
        record_encoder = str(self.config.default_record_encoder or "auto")
        if initial:
            capture_key = str(getattr(self.config, "default_capture_quality", capture_key) or capture_key)
            stream_bitrate = str(self.config.default_stream_bitrate or stream_bitrate)
            record_bitrate = str(self.config.default_record_bitrate or record_bitrate)
            stream_encoder = str(self.config.default_stream_encoder or stream_encoder)
            record_encoder = str(self.config.default_record_encoder or record_encoder)
        old_width, old_height = self.config.canvas_width, self.config.canvas_height

        if (new_width, new_height) != (old_width, old_height):
            self.state.resize_canvas(old_width, old_height, new_width, new_height)

        self._output_quality_key = key
        self._current_aspect_ratio = self._aspect_from_size(new_width, new_height)
        self.config.canvas_width = new_width
        self.config.canvas_height = new_height
        self.config.render_fps = new_fps
        self.config.default_output_quality = key
        self.config.default_capture_quality = capture_key
        self.config.default_stream_bitrate = stream_bitrate
        self.config.default_record_bitrate = record_bitrate
        self.config.default_stream_encoder = stream_encoder
        self.config.default_record_encoder = record_encoder
        self.output_manager.set_video_size(new_width, new_height)
        self.output_manager.set_fps(new_fps)

        if hasattr(self, "render_thread"):
            self.render_thread.set_canvas_size(new_width, new_height)
            self.render_thread.set_fps(new_fps)

        self._set_all_preview_canvas_size(new_width, new_height)
        self._sync_aspect_ratio_controls()
        self._sync_output_quality_ui(key)
        if hasattr(self, "capture_quality_combo"):
            self.capture_quality_combo.blockSignals(True)
            self._set_combo_data(self.capture_quality_combo, capture_key)
            self.capture_quality_combo.blockSignals(False)
        self._apply_capture_quality_key(capture_key, notify=False)
        self._update_output_protection(preset)
        self.output_manager.set_encoding_profile(
            record_bitrate=self.config.default_record_bitrate,
            stream_bitrate=self.config.default_stream_bitrate,
            record_encoder=self.config.default_record_encoder,
            stream_encoder=self.config.default_stream_encoder,
        )
        if hasattr(self, "encoder_status_label"):
            self.encoder_status_label.setText(self._encoding_status_text())
        self._sync_preview_popout_output_controls()
        if notify:
            self._notify(
                f"输出质量已切换为 {preset['label']}，"
                f"画布 {new_width}×{new_height} / {new_fps}fps。",
            )
        if not initial:
            self._save_output_preferences()

    def _encoding_status_text(self, status: dict[str, str] | None = None) -> str:
        status = status or self.output_manager.status()
        stream_mode = self._encoding_mode_label(status.get("stream_encoder", "auto"))
        record_mode = self._encoding_mode_label(status.get("record_encoder", "auto"))
        video_profile = status.get("video_size", f"{self.config.canvas_width}x{self.config.canvas_height}")
        fps = status.get("fps", str(self.config.render_fps))
        return (
            f"输出: {self._output_quality_label()} | {video_profile} / {fps}fps"
            f" | 推流 {stream_mode} / {status.get('stream_bitrate', self.config.default_stream_bitrate)}"
            f" | 录制 {record_mode} / {status.get('record_bitrate', self.config.default_record_bitrate)}"
            f" | 性能: {getattr(self, '_output_performance_hint', '平衡')}"
        )

    def _sync_preview_popout_controls(self) -> None:
        self._sync_preview_popout_scene_combo()
        self._sync_aspect_ratio_controls()
        self._sync_preview_popout_output_controls()

    def _program_scene_for_ui(self):
        if not self.state.get_emergency_placeholder_active():
            return self.state.get_active_scene()
        placeholder_id = self.state.get_placeholder_scene_id()
        return self.state.get_scene_by_id(placeholder_id) if placeholder_id else None

    def _sync_emergency_placeholder_controls(self) -> None:
        active = self.state.get_emergency_placeholder_active()
        placeholder_scene = self._program_scene_for_ui()
        scene_name = placeholder_scene.name if placeholder_scene is not None else "紧急占位场景"
        button_text = "退出占位" if active else "紧急占位"
        status_text = f"占位: {'已启用' if active else '未启用'} | 场景: {scene_name}"

        self.btn_emergency_placeholder.blockSignals(True)
        self.btn_emergency_placeholder.setChecked(active)
        self.btn_emergency_placeholder.setText(button_text)
        self.btn_emergency_placeholder.blockSignals(False)
        self.placeholder_status_label.setText(status_text)

        if self._preview_popout is not None:
            popout = self._preview_popout
            popout.btn_emergency_placeholder.blockSignals(True)
            popout.btn_emergency_placeholder.setChecked(active)
            popout.btn_emergency_placeholder.setText(button_text)
            popout.btn_emergency_placeholder.blockSignals(False)

        if self._scene_popout is not None:
            self._scene_popout.set_emergency_state(active, button_text, status_text)

        active_scene = self.state.get_active_scene()
        active_name = active_scene.name if active_scene is not None else ""
        self._update_current_scene_ui(active_name)

    def _on_emergency_placeholder_toggled(self, checked: bool) -> None:
        self.state.set_emergency_placeholder_active(bool(checked))
        self._sync_emergency_placeholder_controls()
        self._refresh_preview_scene()
        self._notify("节目输出已切换到紧急占位场景。" if checked else "节目输出已恢复正常场景。")

    def _choose_placeholder_video(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择循环播放的占位视频",
            str(Path.cwd()),
            "视频文件 (*.mp4 *.mov *.avi *.mkv *.flv *.wmv);;全部文件 (*.*)",
        )
        if not path:
            return
        if not self.state.set_placeholder_video(path, self.config.canvas_width, self.config.canvas_height):
            self._notify("占位视频设置失败。", is_error=True)
            return
        self._refresh_scene_list()
        self._refresh_layer_list()
        self._sync_emergency_placeholder_controls()
        self._sync_placeholder_dialog()
        self._notify("占位视频已设置，紧急占位启用后会循环播放。")

    @staticmethod
    def _set_monitor_widget_state(button, slider, value_label, status_label, status: dict[str, object]) -> None:
        running = bool(status.get("running", False))
        gain = int(round(float(status.get("gain", 0.6)) * 100))
        backend = str(status.get("backend") or "none")
        error = str(status.get("error") or "")

        button.blockSignals(True)
        button.setChecked(running)
        button.setText("关闭监听" if running else "开启监听")
        button.blockSignals(False)

        slider.blockSignals(True)
        slider.setValue(max(0, min(200, gain)))
        slider.blockSignals(False)
        value_label.setText(f"{max(0, min(200, gain))}%")

        if error:
            status_label.setText(f"监听: 异常 | {error}")
        elif running:
            status_label.setText(f"监听: 开启 | {backend} | 建议使用耳机")
        else:
            status_label.setText("监听: 关闭")

    def _sync_monitor_controls(self) -> None:
        status = self.audio_controller.monitor_status()
        self._set_monitor_widget_state(
            self.btn_audio_monitor,
            self.monitor_volume_slider,
            self.monitor_volume_value,
            self.monitor_status_label,
            status,
        )
        if self._preview_popout is not None:
            self._set_monitor_widget_state(
                self._preview_popout.btn_audio_monitor,
                self._preview_popout.monitor_volume_slider,
                self._preview_popout.monitor_volume_value,
                self._preview_popout.monitor_status_label,
                status,
            )

    def _on_audio_monitor_toggled(self, checked: bool) -> None:
        ok, msg = self.audio_controller.start_monitoring() if checked else self.audio_controller.stop_monitoring()
        self._sync_monitor_controls()
        if ok or checked:
            self._notify(msg, is_error=checked and not ok)

    def _on_monitor_volume_changed(self, value: int) -> None:
        gain = max(0.0, min(2.0, value / 100.0))
        self.audio_controller.set_monitor_gain(gain)
        self._sync_monitor_controls()

    def _on_preview_popout_scene_changed(self, _index: int) -> None:
        if self._preview_popout is None:
            return
        scene_id = self._preview_popout.scene_combo.currentData()
        if not scene_id or scene_id == self.state.get_active_scene_id():
            return
        if self.state.set_active_scene(str(scene_id)):
            self.selected_layer_id = None
            self._refresh_scene_list()
            self._refresh_layer_list()
            scene_name = self._preview_popout.scene_combo.currentText() or str(scene_id)
            self._notify(f"已切换到场景: {scene_name}")

    def _on_aspect_ratio_changed(self, _index: int) -> None:
        self._apply_output_aspect_ratio(str(self.aspect_combo.currentData() or "16:9"))

    def _on_preview_popout_aspect_changed(self, _index: int) -> None:
        if self._preview_popout is None:
            return
        self._apply_output_aspect_ratio(str(self._preview_popout.aspect_combo.currentData() or "16:9"))

    def _on_preview_popout_delay_changed(self, value: int) -> None:
        self.delay_spin.blockSignals(True)
        self.delay_spin.setValue(value)
        self.delay_spin.blockSignals(False)
        self._on_delay_changed(value)

    def _on_preview_popout_rtmp_changed(self, text: str) -> None:
        self.rtmp_edit.blockSignals(True)
        self.rtmp_edit.setText(text)
        self.rtmp_edit.blockSignals(False)

    def _on_main_rtmp_changed(self, text: str) -> None:
        if self._preview_popout is None:
            return
        self._preview_popout.rtmp_edit.blockSignals(True)
        self._preview_popout.rtmp_edit.setText(text)
        self._preview_popout.rtmp_edit.blockSignals(False)

    def _sync_ai_controls(self) -> None:
        layer = self._get_selected_layer()
        ar_supported = self._is_face_supported_layer(layer)
        self._sync_virtual_bg_controls()

        self.ar_target_label.setText(
            f"AR 目标图层: {layer.name}" if layer is not None else "AR 目标图层: 未选择"
        )
        for widget in (self.btn_ar_enable, self.ar_mode_combo, self.btn_ar_asset, self.btn_ar_apply, self.btn_ar_clear):
            widget.setEnabled(ar_supported)
        if not ar_supported:
            self.btn_ar_enable.blockSignals(True)
            self.btn_ar_enable.setChecked(False)
            self.btn_ar_enable.blockSignals(False)
            self.ar_status_label.setText("请先选择相机/窗口/屏幕/网络流图层以配置 AR 。")

    def _clear_ai_highlight(self) -> None:
        self._semantic_recommendations.clear()
        self._semantic_best_scene_id = None
        self._highlight_scene_items()
        self.semantic_status_label.setText("AI 高亮状态已清空。智能导播结果已重置。")
        self.anomaly_status_label.setText("异常高亮状态已清空。当前仅实现界面占位。")

    def _on_semantic_switch_clicked(self) -> None:
        query = self.semantic_query_edit.text().strip()
        if not query:
            self._notify("请输入语义搜索词。", is_error=True)
            return
        self._start_semantic_recommendation(query, self._semantic_threshold)

    def _on_semantic_highlight_clicked(self) -> None:
        query = self.semantic_query_edit.text().strip()
        if not query:
            self._notify("请输入语义搜索词。", is_error=True)
            return
        self._start_semantic_recommendation(query, self._semantic_threshold)

    def _start_semantic_recommendation(self, query: str, threshold: float = 0.10) -> None:
        self._semantic_query = (query or "").strip()
        self._semantic_threshold = float(threshold)
        if not self._semantic_query:
            self._notify("请输入语义搜索词。", is_error=True)
            return
        self.semantic_query_edit.setText(self._semantic_query)
        self._semantic_recommendation_enabled = True
        self.semantic_status_label.setText(f"智能导播检索中: {self._semantic_query}")
        if not self._semantic_timer.isActive():
            self._semantic_timer.start()
        self._submit_semantic_recommendation()
        self._notify("智能导播模式已启动。")

    def _stop_semantic_recommendation(self) -> None:
        self._semantic_recommendation_enabled = False
        if self._semantic_timer.isActive():
            self._semantic_timer.stop()
        self.semantic_status_label.setText("智能导播模式已停止，保留最近一次匹配结果。")
        self._notify("智能导播模式已停止。")

    def _semantic_dialog(self):
        return self._ai_dialogs.get("semantic")

    def _scene_preview_image_for_semantic(self, scene: Scene) -> object | None:
        image = self._scene_preview_cache.get(scene.id)
        if image is None:
            widget = self._scene_item_widgets.get(scene.id)
            image = widget.current_preview_image() if widget is not None else None
        if image is not None and not (hasattr(image, "isNull") and image.isNull()):
            return image.copy() if hasattr(image, "copy") else image
        if not scene.layers:
            return None
        # 只在缓存缺失时临时渲染一次，避免把“有图层但还没生成缩略图”的场景漏掉。
        compositor = Compositor(self.source_manager, width=self.config.canvas_width, height=self.config.canvas_height)
        try:
            rendered = compositor.render_scene(scene)
            frame = rendered.frame
            if frame is None:
                return None
            image = self.render_thread._frame_to_qimage(frame) if hasattr(self, "render_thread") else None
            if image is not None:
                self._scene_preview_cache[scene.id] = image.copy() if hasattr(image, "copy") else image
            return image
        except Exception:
            return None
        finally:
            compositor.close()

    def _semantic_preview_images(self) -> dict[str, object]:
        previews: dict[str, object] = {}
        for scene in self._normal_scene_list(self.state.snapshot_scenes()):
            image = self._scene_preview_image_for_semantic(scene)
            if image is None or (hasattr(image, "isNull") and image.isNull()):
                continue
            previews[scene.id] = image
        return previews

    def _semantic_scene_frames(self) -> list[SemanticSceneFrame]:
        frames: list[SemanticSceneFrame] = []
        scenes = self._normal_scene_list(self.state.snapshot_scenes())
        for scene in scenes:
            image = self._scene_preview_image_for_semantic(scene)
            if image is None or (hasattr(image, "isNull") and image.isNull()):
                continue
            frames.append(
                SemanticSceneFrame(
                    scene_id=scene.id,
                    scene_name=scene.name,
                    image=image,
                )
            )
        return frames

    def _submit_semantic_recommendation(self) -> None:
        if not self._semantic_recommendation_enabled or not self._semantic_query:
            return
        if self._semantic_worker.is_busy():
            return
        frames = self._semantic_scene_frames()
        if not frames:
            self._on_semantic_recommendation_status("等待场景缩略图生成后再进行推荐。")
            return
        self._semantic_worker.submit(self._semantic_query, frames, self._semantic_threshold)

    def _on_semantic_recommendation_status(self, text: str) -> None:
        message = text or ""
        if hasattr(self, "semantic_status_label"):
            self.semantic_status_label.setText(message)
        dialog = self._semantic_dialog()
        if dialog is not None and hasattr(dialog, "set_status"):
            dialog.set_status(message)

    def _on_semantic_recommendation_ready(self, result) -> None:
        if getattr(result, "query", "") and getattr(result, "query", "") != self._semantic_query:
            return
        if getattr(result, "error", ""):
            self._on_semantic_recommendation_status(str(result.error))
            dialog = self._semantic_dialog()
            if dialog is not None and hasattr(dialog, "set_results"):
                dialog.set_results(result, self._semantic_preview_images())
            return
        self._semantic_recommendations = {
            str(score.scene_id): score
            for score in (getattr(result, "scores", []) or [])
            if getattr(score, "score", -1.0) >= self._semantic_threshold
        }
        self._semantic_best_scene_id = getattr(result, "best_scene_id", None)
        self._highlight_scene_items()
        dialog = self._semantic_dialog()
        if dialog is not None and hasattr(dialog, "set_results"):
            dialog.set_results(result, self._semantic_preview_images())
        best = self._semantic_recommendations.get(str(self._semantic_best_scene_id))
        if best is not None:
            self.semantic_status_label.setText(
                f"智能导播推荐: {best.scene_name}，相似度 {best.score:.3f}，设备 {getattr(result, 'provider', '未知')}"
            )
        else:
            self.semantic_status_label.setText("推荐完成，但没有场景超过当前阈值。")

    def _highlight_semantic_scene(self, scene_id: str) -> None:
        if not scene_id:
            return
        for i in range(self.scene_list.count()):
            item = self.scene_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) == scene_id:
                self.scene_list.scrollToItem(item)
                self._highlight_scene_items()
                self._notify("已定位智能导播匹配场景，未切换节目输出。")
                return
        self._notify("推荐场景已不存在。", is_error=True)

    def _switch_to_semantic_scene(self, scene_id: str) -> None:
        if not scene_id:
            scene_id = self._semantic_best_scene_id or ""
        if not scene_id:
            self._notify("暂无可切换的语义推荐场景。", is_error=True)
            return
        if self.state.set_active_scene(scene_id):
            self.selected_layer_id = None
            self._refresh_scene_list()
            self._refresh_layer_list()
            scene = self.state.get_active_scene()
            self._notify(f"已将智能导播匹配场景应用到节目输出: {scene.name if scene is not None else scene_id}")
        else:
            self._notify("推荐场景无法切换，可能已被删除或是占位场景。", is_error=True)

    def _on_anomaly_toggle(self, enabled: bool) -> None:
        self.anomaly_status_label.setText(
            "异常检测界面已启动，可输入舞台异常或趣味镜头描述。"
            if enabled
            else "异常检测未启动。启动后可输入异常提示词。"
        )

    def _on_anomaly_search_clicked(self) -> None:
        if not self.btn_anomaly_enable.isChecked():
            self._notify("请先启动异常检测功能。", is_error=True)
            return
        query = self.anomaly_query_edit.text().strip()
        if not query:
            self._notify("请输入异常描述。", is_error=True)
            return
        self.anomaly_status_label.setText(f"已记录异常搜索词：{query}。当前仅实现界面展示，待接入异常检测模型。")
        self._notify("异常检测界面请求已记录。")

    def _on_virtual_ad_toggled(self, enabled: bool) -> None:
        self.virtual_ad_status_label.setText(
            "虚拟广告功能已启用。当前仅实现界面配置。"
            if enabled
            else "虚拟广告功能已关闭。"
        )

    def _on_choose_virtual_ad_asset(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "选择虚拟广告素材",
            str(Path.cwd()),
            "PNG 图片 (*.png);;全部文件 (*.*)",
        )
        if not path_str:
            return
        self.virtual_ad_asset_edit.setText(path_str)
        self.virtual_ad_status_label.setText("广告素材已选择。可继续在界面上配置位置与开关。")

    def _apply_virtual_ad(self) -> None:
        asset_path = self.virtual_ad_asset_edit.text().strip()
        if not asset_path:
            self._notify("请先选择虚拟广告素材。", is_error=True)
            return
        position_text = self.virtual_ad_position_combo.currentText()
        self.virtual_ad_status_label.setText(f"已记录虚拟广告配置：{position_text} / {Path(asset_path).name}。当前仅实现界面。")
        self._notify("虚拟广告界面配置已记录。")

    def _remove_virtual_ad(self) -> None:
        self.virtual_ad_asset_edit.clear()
        self.btn_virtual_ad_enable.blockSignals(True)
        self.btn_virtual_ad_enable.setChecked(False)
        self.btn_virtual_ad_enable.blockSignals(False)
        self.virtual_ad_status_label.setText("虚拟广告界面配置已清除。")

    def _on_ar_toggle(self, enabled: bool) -> None:
        layer = self._get_selected_layer()
        if enabled and not self._is_face_supported_layer(layer):
            self._notify("请先选择支持 AR 的视频图层。", is_error=True)
            self.btn_ar_enable.blockSignals(True)
            self.btn_ar_enable.setChecked(False)
            self.btn_ar_enable.blockSignals(False)
            return
        self.ar_status_label.setText(
            "AR 功能已启用，人物贴纸会写入选中图层的人脸特效通道。"
            if enabled
            else "AR 功能界面已关闭。"
        )

    def _on_choose_ar_asset(self) -> None:
        path_str, _ = QFileDialog.getOpenFileName(
            self,
            "选择 AR 素材",
            str(Path.cwd()),
            "PNG 图片 (*.png);;全部文件 (*.*)",
        )
        if not path_str:
            return
        self.ar_asset_edit.setText(path_str)
        dialog = self._ai_dialogs.get("ar")
        if isinstance(dialog, ARFeatureDialog):
            dialog.asset_edit.setText(path_str)
        self.ar_status_label.setText("AR 素材已选择。可继续在界面上配置 AR 模式。")

    def _apply_ar_to_selected_layer(self) -> None:
        layer = self._get_selected_layer()
        if not self._is_face_supported_layer(layer):
            self._notify("请先选择支持 AR 的视频图层。", is_error=True)
            return
        if str(self.ar_mode_combo.currentData() or "face_sticker") != "face_sticker":
            self.ar_status_label.setText(
                f"已记录 AR 模式：{self.ar_mode_combo.currentText()}。该模式暂保留接口。"
            )
            self._notify("AR 界面配置已记录。")
            return

        effect_type = canonical_ar_effect_type(self.face_effect_combo.currentData()) or "dog_nose"
        sticker_path = self.ar_asset_edit.text().strip() or default_ar_sticker_path(effect_type)
        if not sticker_path:
            self._notify("默认 AR 素材缺失，请手动选择 PNG 素材。", is_error=True)
            return

        self.btn_face_enable.blockSignals(True)
        self.btn_face_enable.setChecked(True)
        self.btn_face_enable.setText("关闭识别")
        self.btn_face_enable.blockSignals(False)
        self.btn_ar_enable.blockSignals(True)
        self.btn_ar_enable.setChecked(True)
        self.btn_ar_enable.blockSignals(False)
        self.face_sticker_edit.setText(sticker_path)

        def updater(l: Layer):
            l.source["face_enabled"] = True
            l.source["effect_type"] = effect_type
            l.source["sticker_path"] = sticker_path

        self.state.update_layer(layer.id, updater)
        self._refresh_preview_scene()
        self._sync_face_controls()
        self.ar_status_label.setText(
            f"已应用人物贴纸 AR：{ar_effect_label(effect_type)} / {Path(sticker_path).name}。"
        )
        self._notify("AR 贴纸已应用到选中图层。")

    def _clear_ar_from_selected_layer(self) -> None:
        layer = self._get_selected_layer()
        self.ar_asset_edit.clear()
        dialog = self._ai_dialogs.get("ar")
        if isinstance(dialog, ARFeatureDialog):
            dialog.asset_edit.clear()
        self.btn_ar_enable.blockSignals(True)
        self.btn_ar_enable.setChecked(False)
        self.btn_ar_enable.blockSignals(False)
        self.btn_face_enable.blockSignals(True)
        self.btn_face_enable.setChecked(False)
        self.btn_face_enable.setText("开启识别")
        self.btn_face_enable.blockSignals(False)
        if self._is_face_supported_layer(layer):
            assert layer is not None

            def updater(l: Layer):
                l.source["face_enabled"] = False

            self.state.update_layer(layer.id, updater)
            self._refresh_preview_scene()
            self._sync_face_controls()
        self.ar_status_label.setText("AR 界面配置已清除。")

    @staticmethod
    def _set_status_badge(label: QLabel, state_text: str, error_text: str) -> None:
        if error_text:
            label.setText("异常")
            label.setToolTip(error_text)
            label.setStyleSheet(status_badge_qss("error"))
            return
        if any(token in state_text for token in ("运行中", "录制中", "推流中")):
            label.setText(state_text)
            label.setToolTip("")
            label.setStyleSheet(status_badge_qss("running"))
            return
        label.setText("未运行")
        label.setToolTip("")
        label.setStyleSheet(status_badge_qss("idle"))

    def _update_output_status_badges(self, status: dict[str, str]) -> None:
        self._set_status_badge(
            self.stream_status_badge,
            status.get("stream", "未运行"),
            status.get("stream_error", ""),
        )
        self._set_status_badge(
            self.record_status_badge,
            status.get("record", "未运行"),
            status.get("record_error", ""),
        )
        if hasattr(self, "hero_stream_chip"):
            stream_text = str(status.get("stream", "未运行"))
            self.hero_stream_chip.setText(f"Stream: {stream_text}")
            self.hero_stream_chip.setStyleSheet(status_badge_qss("running" if stream_text == "推流中" else "idle"))
        if hasattr(self, "hero_record_chip"):
            record_text = str(status.get("record", "未运行"))
            self.hero_record_chip.setText(f"Record: {record_text}")
            self.hero_record_chip.setStyleSheet(status_badge_qss("running" if record_text == "录制中" else "idle"))
        if hasattr(self, "encoder_status_label"):
            self.encoder_status_label.setText(self._encoding_status_text(status))
        self._sync_preview_popout_output_controls(status)
        self._sync_emergency_placeholder_controls()

    def _refresh_scene_list(self) -> None:
        scenes = self._normal_scene_list(self.state.snapshot_scenes())
        active_id = self.state.get_active_scene_id()
        self.scene_list.blockSignals(True)
        self.scene_list.clear()
        self._scene_item_widgets.clear()
        active_row = 0
        for i, scene in enumerate(scenes):
            self._append_scene_item(scene.id, scene.name)
            if scene.id == active_id:
                active_row = i
        if self.scene_list.count() > 0:
            self.scene_list.setCurrentRow(active_row)
        self.scene_list.blockSignals(False)
        self._highlight_scene_items()
        active_scene = next((scene for scene in scenes if scene.id == active_id), None)
        self._update_current_scene_ui(active_scene.name if active_scene is not None else "")
        self._sync_preview_popout_scene_combo()
        self._sync_layer_manager_dialog()
        self._sync_scene_popout()
        self._sync_canvas_workspace_from_state(reload_scene=False)

    def _append_scene_item(self, scene_id: str, scene_name: str) -> None:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, scene_id)
        widget = SceneItemWidget(scene_name)
        widget.thumb_label.setFixedSize(self._scene_thumb_size())
        cached_image = self._scene_preview_cache.get(scene_id)
        if cached_image is not None:
            widget.set_preview_image(cached_image)
        item.setSizeHint(self.scene_list.gridSize())
        self.scene_list.addItem(item)
        self.scene_list.setItemWidget(item, widget)
        self._scene_item_widgets[scene_id] = widget

    def _highlight_scene_items(self) -> None:
        current = self.scene_list.currentItem()
        current_id = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        for i in range(self.scene_list.count()):
            item = self.scene_list.item(i)
            sid = item.data(Qt.ItemDataRole.UserRole)
            widget = self.scene_list.itemWidget(item)
            if isinstance(widget, SceneItemWidget):
                widget.set_selected(sid == current_id)
                score_item = self._semantic_recommendations.get(str(sid))
                if score_item is None:
                    widget.set_ai_recommendation(None)
                else:
                    widget.set_ai_recommendation(
                        float(getattr(score_item, "score", 0.0)),
                        str(getattr(score_item, "reason", "")),
                    )

    def _on_scene_preview_ready(self, scene_id: str, image) -> None:
        self._scene_preview_cache[scene_id] = image.copy() if hasattr(image, "copy") else image
        widget = self._scene_item_widgets.get(scene_id)
        if widget is not None:
            widget.set_preview_image(image)
        if self._scene_popout is not None:
            self._scene_popout.update_scene_preview(scene_id, image)
        placeholder_id = self.state.get_placeholder_scene_id()
        if scene_id == placeholder_id and self._placeholder_dialog is not None:
            self._placeholder_dialog.preview.set_frame(image)

    def _on_edit_frame_ready(self, frame) -> None:
        self.edit_preview.set_frame(frame)
        if self._preview_popout is not None:
            self._preview_popout.edit_preview.set_frame(frame)
        active_scene_id = self.state.get_active_scene_id()
        if not active_scene_id:
            return
        current_image = self.edit_preview.current_frame_image()
        if current_image is not None:
            self._scene_preview_cache[active_scene_id] = current_image
        widget = self._scene_item_widgets.get(active_scene_id)
        if widget is not None and current_image is not None:
            widget.set_preview_image(current_image)
        if self._scene_popout is not None and current_image is not None:
            self._scene_popout.update_scene_preview(active_scene_id, current_image)

    def _on_program_frame_ready(self, frame) -> None:
        self.program_preview.set_frame(frame)
        if self._preview_popout is not None:
            self._preview_popout.program_preview.set_frame(frame)

    def _apply_cached_scene_preview(self, scene_id: str | None) -> None:
        if not scene_id:
            return
        image = self._scene_preview_cache.get(scene_id)
        if image is None:
            widget = self._scene_item_widgets.get(scene_id)
            if widget is not None:
                image = widget.current_preview_image()
        if image is None:
            return
        self.edit_preview.set_frame(image)
        if not self.state.get_emergency_placeholder_active():
            self.program_preview.set_frame(image)
        if self._preview_popout is not None:
            self._preview_popout.edit_preview.set_frame(image)
            if not self.state.get_emergency_placeholder_active():
                self._preview_popout.program_preview.set_frame(image)

    def _refresh_layer_list(self) -> None:
        scene = self.state.get_active_scene()
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        if scene is not None:
            max_priority = max(1, len(scene.layers))
            for layer in sorted(scene.layers, key=lambda item: item.priority, reverse=True):
                self._append_layer_item(layer, max_priority=max_priority)
            if self.selected_layer_id is not None:
                for i in range(self.layer_list.count()):
                    item = self.layer_list.item(i)
                    if item.data(Qt.ItemDataRole.UserRole) == self.selected_layer_id:
                        self.layer_list.setCurrentItem(item)
                        break
        self.layer_list.blockSignals(False)
        self._refresh_preview_scene()
        self._highlight_layer_items()
        self._refresh_audio_source_combo()
        self._sync_layer_manager_dialog()
        _schedule_custom_list_widgets_refresh(self.layer_list, self.selected_layer_id, min_height=132)
        self._sync_canvas_workspace_from_state(reload_scene=False)

    def _append_layer_item(self, layer: Layer, max_priority: int) -> None:
        item = QListWidgetItem()
        item.setData(Qt.ItemDataRole.UserRole, layer.id)
        widget = LayerItemWidget(layer, max_priority=max_priority)
        widget.lock_changed.connect(self._set_layer_lock)
        widget.delete_clicked.connect(self._delete_layer)
        widget.volume_changed.connect(self._set_layer_volume)
        widget.enabled_changed.connect(self._set_layer_enabled)
        widget.priority_changed.connect(self._set_layer_priority)
        widget.saturation_changed.connect(self._set_layer_saturation)
        widget.contrast_changed.connect(self._set_layer_contrast)
        widget.color_temp_changed.connect(self._set_layer_temp)
        widget.mosaic_changed.connect(self._set_layer_mosaic)
        widget.onnx_style_changed.connect(self._set_layer_onnx_style)
        widget.face_enabled_changed.connect(self._set_layer_face_enabled)
        widget.face_effect_changed.connect(self._set_layer_face_effect_type)
        widget.face_scale_changed.connect(self._set_layer_face_scale)
        widget.face_smoothing_changed.connect(self._set_layer_face_smoothing)
        widget.virtual_bg_enabled_changed.connect(self._set_layer_virtual_bg_enabled)
        widget.virtual_bg_mode_changed.connect(self._set_layer_virtual_bg_mode)
        widget.virtual_bg_blur_changed.connect(self._set_layer_virtual_bg_blur_strength)
        cached_metrics = self._layer_metrics_cache.get(layer.id)
        if cached_metrics is not None:
            widget.set_matting_metrics(cached_metrics)
        widget.setMinimumHeight(max(widget.minimumHeight(), 132))
        item.setSizeHint(widget.sizeHint().expandedTo(QSize(0, 132)))
        self.layer_list.addItem(item)
        self.layer_list.setItemWidget(item, widget)
        _refresh_custom_list_widgets(self.layer_list, self.selected_layer_id, min_height=132)

    def _find_layer_widget(self, layer_id: str) -> LayerItemWidget | None:
        for i in range(self.layer_list.count()):
            item = self.layer_list.item(i)
            if item.data(Qt.ItemDataRole.UserRole) != layer_id:
                continue
            widget = self.layer_list.itemWidget(item)
            if isinstance(widget, LayerItemWidget):
                return widget
        return None

    def _on_layer_metrics_ready(self, metrics_map: dict[str, dict[str, object]]) -> None:
        for layer_id, metrics in metrics_map.items():
            self._layer_metrics_cache[layer_id] = dict(metrics)
            widget = self._find_layer_widget(layer_id)
            if widget is not None:
                widget.set_matting_metrics(metrics)
            if layer_id == self.selected_layer_id:
                layer = self._get_selected_layer()
                if layer is not None and bool(layer.source.get("virtual_bg_enabled", False)):
                    mode = str(layer.source.get("virtual_bg_mode", "image") or "image").strip().lower()
                    path = str(layer.source.get("virtual_bg_path", "")).strip()
                    blur_strength = int(max(0, min(100, layer.source.get("virtual_bg_blur_strength", 55))))
                    self.virtual_bg_status_label.setText(
                        self._format_virtual_bg_status_text(
                            True,
                            mode,
                            path,
                            blur_strength,
                            metrics=metrics,
                        )
                    )
                    self._sync_virtual_bg_dialog()

    def _refresh_preview_scene(self) -> None:
        scene = self.state.get_active_scene()
        program_scene = self._program_scene_for_ui()
        self.edit_preview.set_scene(scene)
        self.edit_preview.set_scene_name(scene.name if scene is not None else "")
        self.program_preview.set_scene(program_scene)
        self.program_preview.set_scene_name(program_scene.name if program_scene is not None else "")
        self.edit_preview.set_selected_layer(self.selected_layer_id)
        if self._preview_popout is not None:
            self._preview_popout.edit_preview.set_scene(scene)
            self._preview_popout.edit_preview.set_scene_name(scene.name if scene is not None else "")
            self._preview_popout.edit_preview.set_selected_layer(self.selected_layer_id)
            self._preview_popout.program_preview.set_scene(program_scene)
            self._preview_popout.program_preview.set_scene_name(program_scene.name if program_scene is not None else "")
        if scene is not None:
            self._apply_cached_scene_preview(scene.id)
        self._sync_preview_edit_controls()
        self._sync_face_controls()
        self._sync_virtual_bg_controls()
        self._sync_ai_controls()
        self._update_current_scene_ui(scene.name if scene is not None else "")

    def _on_scene_selected(self, current: QListWidgetItem | None, _prev: QListWidgetItem | None) -> None:
        self._highlight_scene_items()
        if current is None:
            return
        current_id = current.data(Qt.ItemDataRole.UserRole)
        prev_id = _prev.data(Qt.ItemDataRole.UserRole) if _prev is not None else None
        if current_id == prev_id:
            return
        if self.state.set_active_scene(current_id):
            self.selected_layer_id = None
            self._refresh_layer_list()
            widget = self.scene_list.itemWidget(current)
            scene_name = widget.name_label.text() if isinstance(widget, SceneItemWidget) else str(current_id)
            self._notify(f"已切换到场景: {scene_name}")

    def _add_scene(self) -> None:
        scene = self.state.add_scene()
        self._refresh_scene_list()
        self._refresh_layer_list()
        self._notify(f"已创建: {scene.name}")

    def _delete_scene(self) -> None:
        item = self.scene_list.currentItem()
        if item is None:
            return
        scene_id = item.data(Qt.ItemDataRole.UserRole)
        if scene_id == self.state.get_placeholder_scene_id():
            self._notify("系统自带的紧急占位场景不能删除。", is_error=True)
            return
        ok = self.state.delete_scene(scene_id)
        if not ok:
            self._notify("至少保留一个场景。", is_error=True)
            return
        self._refresh_scene_list()
        self._refresh_layer_list()

    def _clear_selected_scene(self) -> None:
        item = self.scene_list.currentItem()
        if item is None:
            self._notify("请先选择要清空的场景。", is_error=True)
            return
        scene_id = str(item.data(Qt.ItemDataRole.UserRole))
        scene = self.state.get_scene_by_id(scene_id)
        if scene is None or scene.is_placeholder:
            self._notify("该场景不能在普通场景列表中清空。", is_error=True)
            return
        if not self.state.clear_scene_layers(scene_id):
            self._notify("清空场景失败。", is_error=True)
            return
        self.selected_layer_id = None
        self._layer_metrics_cache.clear()
        self._scene_preview_cache.pop(scene_id, None)
        self._refresh_layer_list()
        self._refresh_scene_list()
        self._notify(f"已清空: {scene.name}")

    def _update_current_scene_ui(self, scene_name: str) -> None:
        label = scene_name or "未选择"
        program_scene = self._program_scene_for_ui()
        program_label = program_scene.name if program_scene is not None else label
        if self.state.get_emergency_placeholder_active():
            program_label = f"{program_label}（紧急占位）"
        self.current_scene_label.setText(f"当前场景: {label}")
        if hasattr(self, "hero_scene_chip"):
            self.hero_scene_chip.setText(f"Scene: {label}")
        self.edit_preview.set_scene_name(label if scene_name else "")
        self.program_preview.set_scene_name(program_label if program_label else "")
        if self._preview_popout is not None:
            self._preview_popout.edit_preview.set_scene_name(label if scene_name else "")
            self._preview_popout.program_preview.set_scene_name(program_label if program_label else "")

    def _default_rect(self) -> tuple[int, int, int, int]:
        w, h = 640, 360
        x = (self.config.canvas_width - w) // 2
        y = (self.config.canvas_height - h) // 2
        return x, y, w, h

    def _add_layer_common(self, layer: Layer, scene_id: str | None = None) -> None:
        if self.state.add_layer(layer, scene_id=scene_id):
            active_scene_id = self.state.get_active_scene_id()
            target_scene_id = scene_id or active_scene_id
            if target_scene_id is not None and target_scene_id != active_scene_id:
                self._sync_placeholder_dialog()
                if target_scene_id == self._placeholder_scene_id() and self._placeholder_dialog is not None:
                    self._placeholder_dialog.set_selected_layer(layer.id)
                self._sync_layer_manager_dialog(focus_scene_id=target_scene_id, selected_layer_id=layer.id)
                target_scene = self.state.get_scene_by_id(target_scene_id)
                scene_name = target_scene.name if target_scene is not None else "目标场景"
                self._notify(f"已添加到{scene_name}: {layer.name}")
                return
            self.selected_layer_id = layer.id
            self._refresh_layer_list()
            self._select_layer(layer.id)
            self._sync_layer_manager_dialog(focus_scene_id=active_scene_id, selected_layer_id=layer.id)
            _schedule_custom_list_widgets_refresh(self.layer_list, layer.id, min_height=132)

    def _add_selected_source_layer(self) -> None:
        source_type = str(self.source_type_combo.currentData() or "camera")
        actions = {
            "camera": self._add_camera_layer,
            "screen": self._add_screen_layer,
            "window": self._add_window_layer,
            "image": self._add_png_layer,
            "network": self._add_network_layer,
        }
        action = actions.get(source_type)
        if action is not None:
            action()

    def _add_camera_layer(self, scene_id: str | None = None) -> None:
        idx, ok = QInputDialog.getInt(self, "添加相机", "摄像头索引:", 0, 0, 32, 1)
        if not ok:
            return
        x, y, w, h = self._default_rect()
        layer = Layer(
            id=new_id("layer"),
            name=f"相机 {idx}",
            layer_type=LayerType.CAMERA,
            x=x,
            y=y,
            width=w,
            height=h,
            source=self._with_capture_quality({"camera_index": idx}),
        )
        self._add_layer_common(layer, scene_id=scene_id)

    def _add_screen_layer(self, scene_id: str | None = None) -> None:
        idx, ok = QInputDialog.getInt(self, "添加屏幕", "显示器索引(通常从1开始):", 1, 0, 16, 1)
        if not ok:
            return
        x, y, w, h = 0, 0, self.config.canvas_width, self.config.canvas_height
        layer = Layer(
            id=new_id("layer"),
            name=f"屏幕 {idx}",
            layer_type=LayerType.SCREEN,
            x=x,
            y=y,
            width=w,
            height=h,
            source=self._with_capture_quality({"monitor_index": idx}),
        )
        self._add_layer_common(layer, scene_id=scene_id)

    def _add_window_layer(self, scene_id: str | None = None) -> None:
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
        x, y, w, h = self._default_rect()
        layer = Layer(
            id=new_id("layer"),
            name=f"窗口: {win['title'][:16]}",
            layer_type=LayerType.WINDOW,
            x=x,
            y=y,
            width=w,
            height=h,
            source=self._with_capture_quality({
                "hwnd": int(win["hwnd"]),
                "title": win["title"],
                "pid": win.get("pid"),
                "process_name": win.get("process_name"),
            }),
        )
        self._add_layer_common(layer, scene_id=scene_id)

    def _add_png_layer(self, scene_id: str | None = None) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择静态图片",
            str(Path.cwd()),
            "图片文件 (*.png *.jpg *.jpeg *.bmp);;PNG 图片 (*.png);;JPEG 图片 (*.jpg *.jpeg);;BMP 图片 (*.bmp);;全部文件 (*.*)",
        )
        if not path:
            return
        x, y, w, h = self._default_rect()
        layer = Layer(
            id=new_id("layer"),
            name=f"图片: {Path(path).name}",
            layer_type=LayerType.PNG,
            x=x,
            y=y,
            width=w,
            height=h,
            source={"image_path": path},
        )
        self._add_layer_common(layer, scene_id=scene_id)

    def _add_network_layer(self, scene_id: str | None = None) -> None:
        url, ok = QInputDialog.getText(self, "添加网络流", "输入网络流地址（RTMP/RTSP/HTTP）:")
        if not ok:
            return
        url = url.strip()
        if not url:
            self._notify("网络流 URL 不能为空。", is_error=True)
            return
        x, y, w, h = self._default_rect()
        layer = Layer(
            id=new_id("layer"),
            name="网络流",
            layer_type=LayerType.NETWORK,
            x=x,
            y=y,
            width=w,
            height=h,
            source=self._with_capture_quality({"url": url}),
        )
        self._add_layer_common(layer, scene_id=scene_id)

    def _on_layer_item_selected(self, current: QListWidgetItem | None, _prev: QListWidgetItem | None) -> None:
        if current is None:
            self._select_layer(None)
            return
        layer_id = current.data(Qt.ItemDataRole.UserRole)
        self._select_layer(layer_id)

    def _select_layer(self, layer_id: str | None) -> None:
        self.selected_layer_id = layer_id
        self.edit_preview.set_selected_layer(layer_id)
        if self._preview_popout is not None:
            self._preview_popout.edit_preview.set_selected_layer(layer_id)

        if layer_id is not None:
            for i in range(self.layer_list.count()):
                item = self.layer_list.item(i)
                if item.data(Qt.ItemDataRole.UserRole) == layer_id:
                    self.layer_list.blockSignals(True)
                    self.layer_list.setCurrentItem(item)
                    self.layer_list.blockSignals(False)
                    break
        self._sync_preview_edit_controls()
        self._highlight_layer_items()
        self._sync_face_controls()
        self._sync_virtual_bg_controls()
        self._sync_ai_controls()
        _schedule_custom_list_widgets_refresh(self.layer_list, layer_id, min_height=132)

    def _get_selected_layer(self) -> Layer | None:
        if not self.selected_layer_id:
            return None
        return self.state.find_layer(self.selected_layer_id)

    def _sync_preview_edit_controls(self) -> None:
        layer = self._get_selected_layer()
        enabled = layer is not None
        effective_mode = self._preview_edit_mode
        if layer is not None and layer.locked:
            effective_mode = "lock"
        elif effective_mode == "lock":
            effective_mode = self._last_unlocked_preview_edit_mode
            self._preview_edit_mode = effective_mode

        button_map = {
            "position": self.btn_edit_position,
            "size": self.btn_edit_size,
            "lock": self.btn_edit_lock,
        }
        button_maps = [button_map]
        if self._preview_popout is not None:
            button_maps.append(
                {
                    "position": self._preview_popout.btn_edit_position,
                    "size": self._preview_popout.btn_edit_size,
                    "lock": self._preview_popout.btn_edit_lock,
                }
            )
        for current_map in button_maps:
            for mode, button in current_map.items():
                button.setEnabled(enabled)
                button.blockSignals(True)
                button.setChecked(enabled and effective_mode == mode)
                button.blockSignals(False)

        self.edit_preview.set_interaction_mode(effective_mode if enabled else self._last_unlocked_preview_edit_mode)
        if self._preview_popout is not None:
            self._preview_popout.edit_preview.set_interaction_mode(
                effective_mode if enabled else self._last_unlocked_preview_edit_mode
            )

    def _on_preview_edit_mode_clicked(self, mode: str, checked: bool) -> None:
        if not checked:
            self._sync_preview_edit_controls()
            return
        layer = self._get_selected_layer()
        if layer is None:
            self._sync_preview_edit_controls()
            return

        if mode == "lock":
            self._preview_edit_mode = "lock"
            if not layer.locked:
                self._set_layer_lock(layer.id, True)
                return
        else:
            self._preview_edit_mode = mode
            self._last_unlocked_preview_edit_mode = mode
            if layer.locked:
                self._set_layer_lock(layer.id, False)
                return

        self._sync_preview_edit_controls()

    @staticmethod
    def _is_face_supported_layer(layer: Layer | None) -> bool:
        if layer is None:
            return False
        return layer.layer_type in {
            LayerType.CAMERA,
            LayerType.SCREEN,
            LayerType.WINDOW,
            LayerType.NETWORK,
            LayerType.VIDEO,
        }

    def _sync_face_controls(self) -> None:
        layer = self._get_selected_layer()
        supported = self._is_face_supported_layer(layer)

        widgets = [
            self.btn_face_enable,
            self.btn_face_nose,
            self.btn_face_hat,
            self.btn_face_eyes,
            self.face_effect_combo,
            self.btn_face_sticker,
            self.face_scale_slider,
            self.face_smoothing_slider,
        ]
        for w in widgets:
            w.setEnabled(supported)

        if not supported:
            self.face_target_label.setText("目标图层: 未选择或当前图层不支持人脸特效")
            self.btn_face_enable.blockSignals(True)
            self.btn_face_enable.setChecked(False)
            self.btn_face_enable.blockSignals(False)
            self.face_effect_combo.blockSignals(True)
            self.face_effect_combo.setCurrentIndex(0)
            self.face_effect_combo.blockSignals(False)
            self.face_sticker_edit.setText("")
            self.face_scale_slider.blockSignals(True)
            self.face_scale_slider.setValue(100)
            self.face_scale_slider.blockSignals(False)
            self.face_scale_value.setText("100%")
            self.face_smoothing_slider.blockSignals(True)
            self.face_smoothing_slider.setValue(60)
            self.face_smoothing_slider.blockSignals(False)
            self.face_smoothing_value.setText("60%")
            self._sync_face_dialog()
            return

        self.face_target_label.setText(f"目标图层: {layer.name}")
        face_enabled = bool(layer.source.get("face_enabled", False))
        effect_type = canonical_ar_effect_type(layer.source.get("effect_type", ""))
        sticker_path = str(layer.source.get("sticker_path", "")).strip()
        shown_sticker_path = sticker_path or default_ar_sticker_path(effect_type)
        scale_percent = int(max(50, min(200, layer.source.get("face_scale_percent", 100))))
        smoothing_percent = int(max(0, min(100, layer.source.get("face_smoothing", 60))))

        self.btn_face_enable.blockSignals(True)
        self.btn_face_enable.setChecked(face_enabled)
        self.btn_face_enable.setText("关闭识别" if face_enabled else "开启识别")
        self.btn_face_enable.blockSignals(False)

        idx = self.face_effect_combo.findData(effect_type)
        if idx < 0:
            idx = 0
        self.face_effect_combo.blockSignals(True)
        self.face_effect_combo.setCurrentIndex(idx)
        self.face_effect_combo.blockSignals(False)
        self.face_sticker_edit.setText(shown_sticker_path)
        self.face_scale_slider.blockSignals(True)
        self.face_scale_slider.setValue(scale_percent)
        self.face_scale_slider.blockSignals(False)
        self.face_scale_value.setText(f"{scale_percent}%")
        self.face_smoothing_slider.blockSignals(True)
        self.face_smoothing_slider.setValue(smoothing_percent)
        self.face_smoothing_slider.blockSignals(False)
        self.face_smoothing_value.setText(f"{smoothing_percent}%")
        self._sync_face_dialog()

    def _sync_virtual_bg_controls(self) -> None:
        layer = self._get_selected_layer()
        supported = self._is_face_supported_layer(layer)

        widgets = [
            self.btn_virtual_bg_enable,
            self.virtual_bg_mode_combo,
            self.virtual_bg_blur_slider,
            self.btn_virtual_bg_choose,
            self.btn_virtual_bg_clear,
        ]
        for widget in widgets:
            widget.setEnabled(supported)

        if not supported:
            self.virtual_bg_target_label.setText("目标图层: 未选择或当前图层不支持虚拟背景")
            self.btn_virtual_bg_enable.blockSignals(True)
            self.btn_virtual_bg_enable.setChecked(False)
            self.btn_virtual_bg_enable.setText("开启虚拟背景")
            self.btn_virtual_bg_enable.blockSignals(False)
            self.virtual_bg_mode_combo.blockSignals(True)
            self.virtual_bg_mode_combo.setCurrentIndex(0)
            self.virtual_bg_mode_combo.blockSignals(False)
            self.virtual_bg_blur_slider.blockSignals(True)
            self.virtual_bg_blur_slider.setValue(55)
            self.virtual_bg_blur_slider.blockSignals(False)
            self.virtual_bg_blur_value.setText("55%")
            self.virtual_bg_edit.clear()
            self.virtual_bg_status_label.setText("虚拟背景状态: 未选择或当前图层不支持人像抠图")
            self._apply_virtual_bg_mode_ui("image", enabled=False)
            self._sync_virtual_bg_dialog()
            return

        self.virtual_bg_target_label.setText(f"目标图层: {layer.name}")
        enabled = bool(layer.source.get("virtual_bg_enabled", False))
        mode = str(layer.source.get("virtual_bg_mode", "image") or "image").strip().lower()
        if mode not in {"image", "blur"}:
            mode = "image"
        blur_strength = int(max(0, min(100, layer.source.get("virtual_bg_blur_strength", 55))))
        path = str(layer.source.get("virtual_bg_path", "")).strip()
        metrics = self._layer_metrics_cache.get(layer.id)

        self.btn_virtual_bg_enable.blockSignals(True)
        self.btn_virtual_bg_enable.setChecked(enabled)
        self.btn_virtual_bg_enable.setText("关闭虚拟背景" if enabled else "开启虚拟背景")
        self.btn_virtual_bg_enable.blockSignals(False)

        mode_index = self.virtual_bg_mode_combo.findData(mode)
        if mode_index < 0:
            mode_index = 0
        self.virtual_bg_mode_combo.blockSignals(True)
        self.virtual_bg_mode_combo.setCurrentIndex(mode_index)
        self.virtual_bg_mode_combo.blockSignals(False)

        self.virtual_bg_blur_slider.blockSignals(True)
        self.virtual_bg_blur_slider.setValue(blur_strength)
        self.virtual_bg_blur_slider.blockSignals(False)
        self.virtual_bg_blur_value.setText(f"{blur_strength}%")
        self.virtual_bg_edit.setText(path)
        self.virtual_bg_status_label.setText(
            self._format_virtual_bg_status_text(enabled, mode, path, blur_strength, metrics=metrics)
        )
        self._apply_virtual_bg_mode_ui(mode, enabled=supported)
        self._sync_virtual_bg_dialog()
        self._sync_selected_layer_metrics_label()

    def _sync_selected_layer_metrics_label(self) -> None:
        layer = self._get_selected_layer()
        if layer is None:
            return
        widget = self._find_layer_widget(layer.id)
        if widget is None:
            return
        metrics = self._layer_metrics_cache.get(layer.id)
        if metrics and bool(layer.source.get("virtual_bg_enabled", False)):
            widget.set_matting_metrics(metrics)
            return
        if bool(layer.source.get("virtual_bg_enabled", False)):
            note = "等待渲染指标..."
        else:
            note = "抠像未启用"
        widget.set_matting_metrics({"engine_label": "MediaPipe", "note": note})

    def _update_selected_layer_source(self, key: str, value) -> None:
        layer = self._get_selected_layer()
        if not self._is_face_supported_layer(layer):
            return
        assert layer is not None

        def updater(l: Layer):
            l.source[key] = value

        self.state.update_layer(layer.id, updater)

    def _apply_virtual_bg_mode_ui(self, mode: str, enabled: bool = True) -> None:
        use_bg_image = mode == "image"
        self.virtual_bg_blur_slider.setEnabled(enabled and not use_bg_image)
        self.virtual_bg_edit.setEnabled(enabled and use_bg_image)
        self.btn_virtual_bg_choose.setEnabled(enabled and use_bg_image)
        self.btn_virtual_bg_clear.setEnabled(enabled and use_bg_image)

    @staticmethod
    def _format_virtual_bg_status_text(
        enabled: bool,
        mode: str,
        path: str,
        blur_strength: int,
        metrics: dict[str, object] | None = None,
    ) -> str:
        if not enabled:
            return "虚拟背景状态: 未启用"
        if mode == "blur":
            base = f"虚拟背景状态: 已启用 | 模式: 背景模糊 | 强度: {int(max(0, min(100, blur_strength)))}%"
        elif path:
            base = f"虚拟背景状态: 已启用 | 背景图: {Path(path).name}"
        else:
            base = "虚拟背景状态: 已启用 | 未选择背景图，将透出下层画面"

        if not metrics:
            return base

        engine_label = str(metrics.get("engine_label") or "").strip()
        provider = str(metrics.get("provider") or "").strip()
        note = str(metrics.get("note") or "").strip()
        inference_time_ms = float(metrics.get("inference_time_ms") or 0.0)
        estimated_fps = float(metrics.get("estimated_fps") or 0.0)

        extras: list[str] = []
        if engine_label:
            extras.append(f"算法: {engine_label}")
        if provider:
            extras.append(f"后端: {provider}")
        if note:
            extras.append(f"状态: {note}")
        elif inference_time_ms > 0.0:
            extras.append(f"延迟: {inference_time_ms:.1f} ms")
            extras.append(f"FPS: {estimated_fps:.0f}")

        if not extras:
            return base
        return base + " | " + " | ".join(extras)

    def _on_face_enabled_toggled(self, checked: bool) -> None:
        self.btn_face_enable.setText("关闭识别" if checked else "开启识别")
        self._update_selected_layer_source("face_enabled", bool(checked))
        if checked:
            effect_type = canonical_ar_effect_type(self.face_effect_combo.currentData()) or "dog_nose"
            idx = self.face_effect_combo.findData(effect_type)
            if idx >= 0 and self.face_effect_combo.currentIndex() != idx:
                self.face_effect_combo.blockSignals(True)
                self.face_effect_combo.setCurrentIndex(idx)
                self.face_effect_combo.blockSignals(False)
            sticker_path = self.face_sticker_edit.text().strip()
            default_path = default_ar_sticker_path(effect_type)
            if default_path and (not sticker_path or is_default_ar_sticker_path(sticker_path)):
                self.face_sticker_edit.setText(default_path)
                self._update_selected_layer_source("sticker_path", default_path)
            self._update_selected_layer_source("effect_type", effect_type)
        self._refresh_preview_scene()
        self._sync_face_dialog()

    def _on_virtual_bg_enabled_toggled(self, checked: bool) -> None:
        self.btn_virtual_bg_enable.setText("关闭虚拟背景" if checked else "开启虚拟背景")
        self._update_selected_layer_source("virtual_bg_enabled", bool(checked))
        mode = str(self.virtual_bg_mode_combo.currentData() or "image")
        self.virtual_bg_status_label.setText(
            self._format_virtual_bg_status_text(
                checked,
                mode,
                self.virtual_bg_edit.text().strip(),
                self.virtual_bg_blur_slider.value(),
            )
        )
        self._apply_virtual_bg_mode_ui(mode, enabled=self.btn_virtual_bg_enable.isEnabled())
        self._refresh_preview_scene()
        self._sync_virtual_bg_dialog()

    def _on_virtual_bg_mode_changed(self, _index: int) -> None:
        mode = str(self.virtual_bg_mode_combo.currentData() or "image")
        self._update_selected_layer_source("virtual_bg_mode", mode)
        self.virtual_bg_status_label.setText(
            self._format_virtual_bg_status_text(
                self.btn_virtual_bg_enable.isChecked(),
                mode,
                self.virtual_bg_edit.text().strip(),
                self.virtual_bg_blur_slider.value(),
            )
        )
        self._apply_virtual_bg_mode_ui(mode, enabled=self.btn_virtual_bg_enable.isEnabled())
        self._refresh_preview_scene()
        self._sync_virtual_bg_dialog()

    def _on_virtual_bg_blur_strength_changed(self, value: int) -> None:
        blur_strength = int(max(0, min(100, value)))
        self.virtual_bg_blur_value.setText(f"{blur_strength}%")
        self._update_selected_layer_source("virtual_bg_blur_strength", blur_strength)
        mode = str(self.virtual_bg_mode_combo.currentData() or "image")
        self.virtual_bg_status_label.setText(
            self._format_virtual_bg_status_text(
                self.btn_virtual_bg_enable.isChecked(),
                mode,
                self.virtual_bg_edit.text().strip(),
                blur_strength,
            )
        )
        self._refresh_preview_scene()
        self._sync_virtual_bg_dialog()

    def _set_face_effect_type(self, effect_type: str) -> None:
        effect_type = canonical_ar_effect_type(effect_type)
        idx = self.face_effect_combo.findData(effect_type)
        if idx >= 0:
            self.face_effect_combo.blockSignals(True)
            self.face_effect_combo.setCurrentIndex(idx)
            self.face_effect_combo.blockSignals(False)
        default_path = default_ar_sticker_path(effect_type) if effect_type else ""
        self.face_sticker_edit.setText(default_path)
        self._update_selected_layer_source("effect_type", effect_type)
        self._update_selected_layer_source("sticker_path", default_path)
        self._refresh_preview_scene()
        self._sync_face_dialog()

    def _on_face_effect_combo_changed(self, _index: int) -> None:
        effect_type = canonical_ar_effect_type(self.face_effect_combo.currentData())
        default_path = default_ar_sticker_path(effect_type) if effect_type else ""
        self.face_sticker_edit.setText(default_path)
        self._update_selected_layer_source("effect_type", effect_type)
        self._update_selected_layer_source("sticker_path", default_path)
        self._refresh_preview_scene()
        self._sync_face_dialog()

    def _on_face_scale_changed(self, value: int) -> None:
        scale_percent = int(max(50, min(200, value)))
        self.face_scale_value.setText(f"{scale_percent}%")
        self._update_selected_layer_source("face_scale_percent", scale_percent)
        self._refresh_preview_scene()
        self._sync_face_dialog()

    def _on_face_smoothing_changed(self, value: int) -> None:
        smoothing = int(max(0, min(100, value)))
        self.face_smoothing_value.setText(f"{smoothing}%")
        self._update_selected_layer_source("face_smoothing", smoothing)
        self._refresh_preview_scene()
        self._sync_face_dialog()

    def _on_choose_face_sticker(self) -> None:
        layer = self._get_selected_layer()
        if not self._is_face_supported_layer(layer):
            self._notify("请先选择支持人脸特效的视频图层。", is_error=True)
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "导入AR素材 PNG",
            str(Path.cwd()),
            "PNG 图片 (*.png);;全部文件 (*.*)",
        )
        if not path:
            return
        self.face_sticker_edit.setText(path)
        self._update_selected_layer_source("sticker_path", path)
        self._refresh_preview_scene()
        self._sync_face_dialog()
        self._notify("AR素材已导入。")

    def _on_choose_virtual_bg_image(self) -> None:
        layer = self._get_selected_layer()
        if not self._is_face_supported_layer(layer):
            self._notify("请先选择支持虚拟背景的视频图层。", is_error=True)
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择虚拟背景图片",
            str(Path.cwd()),
            "图片文件 (*.png *.jpg *.jpeg *.bmp);;全部文件 (*.*)",
        )
        if not path:
            return
        self.virtual_bg_edit.setText(path)
        self._update_selected_layer_source("virtual_bg_path", path)
        if not self.btn_virtual_bg_enable.isChecked():
            self.btn_virtual_bg_enable.setChecked(True)
        else:
            mode = str(self.virtual_bg_mode_combo.currentData() or "image")
            self.virtual_bg_status_label.setText(
                self._format_virtual_bg_status_text(True, mode, path, self.virtual_bg_blur_slider.value())
            )
            self._refresh_preview_scene()
            self._sync_virtual_bg_dialog()

    def _clear_virtual_bg_image(self) -> None:
        layer = self._get_selected_layer()
        if not self._is_face_supported_layer(layer):
            return
        self.virtual_bg_edit.clear()
        self._update_selected_layer_source("virtual_bg_path", "")
        mode = str(self.virtual_bg_mode_combo.currentData() or "image")
        self.virtual_bg_status_label.setText(
            self._format_virtual_bg_status_text(
                self.btn_virtual_bg_enable.isChecked(),
                mode,
                "",
                self.virtual_bg_blur_slider.value(),
            )
        )
        self._refresh_preview_scene()
        self._sync_virtual_bg_dialog()

    def _highlight_layer_items(self) -> None:
        for i in range(self.layer_list.count()):
            item = self.layer_list.item(i)
            widget = self.layer_list.itemWidget(item)
            if isinstance(widget, LayerItemWidget):
                widget.set_selected(item.data(Qt.ItemDataRole.UserRole) == self.selected_layer_id)

    def _on_layer_reordered(self, *_args) -> None:
        # 遮挡关系已经改为由 priority 编号控制，列表拖拽不再改变图层层级。
        return

    def _update_layer_rect(self, layer_id: str, x: int, y: int, w: int, h: int) -> None:
        def updater(layer: Layer):
            layer.x = x
            layer.y = y
            layer.width = w
            layer.height = h

        self.state.update_layer(layer_id, updater)
        self._refresh_preview_scene()

    def _delete_layer(self, layer_id: str) -> None:
        self.state.remove_layer(layer_id)
        self._layer_metrics_cache.pop(layer_id, None)
        if self.selected_layer_id == layer_id:
            self.selected_layer_id = None
        self._refresh_layer_list()

    def _delete_layer_from_dialog(self, scene_id: str, layer_id: str) -> None:
        if not self.state.remove_layer(layer_id, scene_id=scene_id):
            self._notify("删除图层失败，目标图层可能已经不存在。", is_error=True)
            return
        self._layer_metrics_cache.pop(layer_id, None)
        if self.selected_layer_id == layer_id:
            self.selected_layer_id = None
        self._refresh_layer_list()
        self._notify("图层已删除。")

    def _set_layer_lock(self, layer_id: str, locked: bool) -> None:
        self.state.update_layer(layer_id, lambda l: setattr(l, "locked", locked))
        if self.selected_layer_id == layer_id:
            self._preview_edit_mode = "lock" if locked else self._last_unlocked_preview_edit_mode
        self._refresh_layer_list()
        if self.selected_layer_id is not None and self.state.find_layer(self.selected_layer_id) is not None:
            self._select_layer(self.selected_layer_id)

    def _set_layer_enabled(self, layer_id: str, enabled: bool) -> None:
        self.state.update_layer(layer_id, lambda l: setattr(l, "enabled", enabled))
        self._refresh_preview_scene()
        self._refresh_audio_source_combo()

    def _set_layer_priority(self, layer_id: str, priority: int) -> None:
        if self.state.set_layer_priority(layer_id, priority):
            self._refresh_layer_list()
            self._refresh_preview_scene()
            self._apply_audio_source_now()

    def _set_layer_priority_from_dialog(self, scene_id: str, layer_id: str, priority: int) -> None:
        if self.state.set_layer_priority(layer_id, priority, scene_id=scene_id):
            self._refresh_layer_list()
            self._refresh_preview_scene()
            self._apply_audio_source_now()

    def _after_layer_param_changed_from_dialog(self, scene_id: str, affects_audio: bool = False) -> None:
        if scene_id != self.state.get_active_scene_id():
            return
        self._refresh_preview_scene()
        if affects_audio:
            self._apply_audio_source_now()

    def _set_layer_volume_from_dialog(self, scene_id: str, layer_id: str, volume: float) -> None:
        if self.state.update_layer(
            layer_id,
            lambda l: setattr(l, "volume", max(0.0, min(2.0, volume))),
            scene_id=scene_id,
        ):
            self._after_layer_param_changed_from_dialog(scene_id, affects_audio=True)

    def _set_layer_saturation_from_dialog(self, scene_id: str, layer_id: str, value: float) -> None:
        if self.state.update_layer(
            layer_id,
            lambda l: setattr(l, "saturation", max(0.0, min(2.0, value))),
            scene_id=scene_id,
        ):
            self._after_layer_param_changed_from_dialog(scene_id)

    def _set_layer_contrast_from_dialog(self, scene_id: str, layer_id: str, value: float) -> None:
        if self.state.update_layer(
            layer_id,
            lambda l: setattr(l, "contrast", max(0.0, min(2.0, value))),
            scene_id=scene_id,
        ):
            self._after_layer_param_changed_from_dialog(scene_id)

    def _set_layer_temp_from_dialog(self, scene_id: str, layer_id: str, value: int) -> None:
        if self.state.update_layer(
            layer_id,
            lambda l: setattr(l, "color_temp", int(max(-100, min(100, value)))),
            scene_id=scene_id,
        ):
            self._after_layer_param_changed_from_dialog(scene_id)

    def _set_layer_mosaic_from_dialog(self, scene_id: str, layer_id: str, value: int) -> None:
        if self.state.update_layer(
            layer_id,
            lambda l: setattr(l, "mosaic", int(max(0, min(100, value)))),
            scene_id=scene_id,
        ):
            self._after_layer_param_changed_from_dialog(scene_id)

    def _set_layer_source_value_from_dialog(
        self,
        scene_id: str,
        layer_id: str,
        key: str,
        value,
        *,
        clear_metrics: bool = False,
    ) -> None:
        def updater(layer: Layer):
            layer.source[key] = value

        if self.state.update_layer(layer_id, updater, scene_id=scene_id):
            if key == "onnx_style":
                preload_onnx_style_filter(value)
            elif key == "virtual_bg_enabled" and bool(value):
                prewarm_mediapipe_components(segmentation=True)
            elif key == "face_enabled" and bool(value):
                prewarm_mediapipe_components(face_mesh=True)
            if clear_metrics:
                self._layer_metrics_cache.pop(layer_id, None)
            self._after_layer_param_changed_from_dialog(scene_id)
            if key == "onnx_style":
                self._notify(f"ONNX 风格已应用：{onnx_style_label(value)}")
            if layer_id == self.selected_layer_id:
                self._sync_face_controls()
                self._sync_virtual_bg_controls()
                self._sync_ai_controls()

    def _set_layer_face_effect_type_from_dialog(self, scene_id: str, layer_id: str, effect_type: str) -> None:
        effect_type = canonical_ar_effect_type(effect_type) or "dog_nose"
        default_path = default_ar_sticker_path(effect_type)

        def updater(layer: Layer):
            layer.source["effect_type"] = effect_type
            if default_path:
                layer.source["sticker_path"] = default_path

        if self.state.update_layer(layer_id, updater, scene_id=scene_id):
            self._after_layer_param_changed_from_dialog(scene_id)
            if layer_id == self.selected_layer_id:
                self._sync_face_controls()
                self._sync_ai_controls()

    def _set_layer_volume(self, layer_id: str, volume: float) -> None:
        if self.state.update_layer(layer_id, lambda l: setattr(l, "volume", max(0.0, min(2.0, volume)))):
            self._apply_audio_source_now()

    def _set_layer_saturation(self, layer_id: str, value: float) -> None:
        if self.state.update_layer(layer_id, lambda l: setattr(l, "saturation", max(0.0, min(2.0, value)))):
            self._refresh_preview_scene()

    def _set_layer_contrast(self, layer_id: str, value: float) -> None:
        if self.state.update_layer(layer_id, lambda l: setattr(l, "contrast", max(0.0, min(2.0, value)))):
            self._refresh_preview_scene()

    def _set_layer_temp(self, layer_id: str, value: int) -> None:
        if self.state.update_layer(layer_id, lambda l: setattr(l, "color_temp", int(max(-100, min(100, value))))):
            self._refresh_preview_scene()

    def _set_layer_mosaic(self, layer_id: str, value: int) -> None:
        if self.state.update_layer(layer_id, lambda l: setattr(l, "mosaic", int(max(0, min(100, value))))):
            self._refresh_preview_scene()

    @staticmethod
    def _canonical_onnx_style_value(value) -> str:
        return canonical_onnx_style(value)

    def _set_layer_onnx_style(self, layer_id: str, value: str) -> None:
        style = self._canonical_onnx_style_value(value)
        preload_onnx_style_filter(style)
        self._set_layer_source_value(layer_id, "onnx_style", style)

    def _after_layer_source_changed(self, layer_id: str, clear_metrics: bool = False) -> None:
        if clear_metrics:
            self._layer_metrics_cache.pop(layer_id, None)
            widget = self._find_layer_widget(layer_id)
            if widget is not None:
                widget.set_matting_metrics({"engine_label": "MediaPipe", "note": "等待渲染指标..."})
        self._refresh_preview_scene()
        if layer_id == self.selected_layer_id:
            self._sync_face_controls()
            self._sync_virtual_bg_controls()
            self._sync_ai_controls()

    def _set_layer_source_value(
        self,
        layer_id: str,
        key: str,
        value,
        *,
        clear_metrics: bool = False,
    ) -> None:
        def updater(layer: Layer):
            layer.source[key] = value

        if self.state.update_layer(layer_id, updater):
            self._after_layer_source_changed(layer_id, clear_metrics=clear_metrics)
            if key == "onnx_style":
                self._notify(f"ONNX 风格已应用：{onnx_style_label(value)}")

    def _set_layer_face_enabled(self, layer_id: str, enabled: bool) -> None:
        if enabled:
            prewarm_mediapipe_components(face_mesh=True)

        def updater(layer: Layer):
            layer.source["face_enabled"] = bool(enabled)
            if enabled and not canonical_ar_effect_type(layer.source.get("effect_type", "")):
                effect_type = "dog_nose"
                layer.source["effect_type"] = effect_type
                default_path = default_ar_sticker_path(effect_type)
                if default_path:
                    layer.source["sticker_path"] = default_path

        if self.state.update_layer(layer_id, updater):
            self._after_layer_source_changed(layer_id)

    def _set_layer_face_effect_type(self, layer_id: str, effect_type: str) -> None:
        effect_type = canonical_ar_effect_type(effect_type) or "dog_nose"
        default_path = default_ar_sticker_path(effect_type)

        def updater(layer: Layer):
            layer.source["effect_type"] = effect_type
            if default_path:
                layer.source["sticker_path"] = default_path

        if self.state.update_layer(layer_id, updater):
            self._after_layer_source_changed(layer_id)

    def _set_layer_face_scale(self, layer_id: str, value: int) -> None:
        scale = int(max(50, min(200, value)))
        self._set_layer_source_value(layer_id, "face_scale_percent", scale)

    def _set_layer_face_smoothing(self, layer_id: str, value: int) -> None:
        smoothing = int(max(0, min(100, value)))
        self._set_layer_source_value(layer_id, "face_smoothing", smoothing)

    def _set_layer_virtual_bg_enabled(self, layer_id: str, enabled: bool) -> None:
        if enabled:
            prewarm_mediapipe_components(segmentation=True)
        self._set_layer_source_value(layer_id, "virtual_bg_enabled", bool(enabled), clear_metrics=True)

    def _set_layer_virtual_bg_mode(self, layer_id: str, mode: str) -> None:
        mode = str(mode or "image").strip().lower()
        if mode not in {"image", "blur"}:
            mode = "image"
        self._set_layer_source_value(layer_id, "virtual_bg_mode", mode, clear_metrics=True)

    def _set_layer_virtual_bg_blur_strength(self, layer_id: str, value: int) -> None:
        blur_strength = int(max(0, min(100, value)))
        self._set_layer_source_value(layer_id, "virtual_bg_blur_strength", blur_strength, clear_metrics=True)

    def _set_layer_matting_engine(self, layer_id: str, _engine_type: str = "mediapipe") -> None:
        def updater(layer: Layer):
            layer.source["matting_engine"] = "mediapipe"

        self.state.update_layer(layer_id, updater)
        self._layer_metrics_cache.pop(layer_id, None)
        widget = self._find_layer_widget(layer_id)
        if widget is not None:
            widget.set_matting_engine("mediapipe")
            widget.set_matting_metrics({"engine_label": "MediaPipe", "note": "等待渲染指标..."})
        self._refresh_preview_scene()

    def _on_delay_changed(self, value: int) -> None:
        self.render_thread.set_delay_ms(value)
        if self._preview_popout is not None and self._preview_popout.delay_spin.value() != value:
            self._preview_popout.delay_spin.blockSignals(True)
            self._preview_popout.delay_spin.setValue(value)
            self._preview_popout.delay_spin.blockSignals(False)

    def _on_capture_quality_changed(self, _index: int) -> None:
        key = str(self.capture_quality_combo.currentData() or "standard")
        self._apply_capture_quality_key(key, notify=True)

    def _on_output_quality_changed(self, _index: int) -> None:
        sender = self.sender()
        if hasattr(sender, "currentData"):
            key = str(sender.currentData() or self._output_quality_key)
        else:
            key = str(self.output_quality_combo.currentData() or self._output_quality_key)
        self._apply_output_quality_profile(key, notify=True)

    @staticmethod
    def _audio_layer_label(layer: Layer) -> str:
        title = str(layer.source.get("title") or layer.name)
        process = str(layer.source.get("process_name") or "未知进程")
        pid = layer.source.get("pid")
        suffix = f"PID:{pid}" if pid else process
        state = "" if layer.enabled else "（图层已停用）"
        return f"{layer.name} | {title[:28]} | {suffix}{state}"

    def _refresh_audio_source_combo(self) -> None:
        if not hasattr(self, "audio_source_combo"):
            return
        selected_key = self.state.get_audio_capture_source()
        scene = self.state.get_active_scene()

        self.audio_source_combo.blockSignals(True)
        self.audio_source_combo.clear()
        tracks = self.state.list_audio_tracks(scene.id if scene is not None else None)
        for track in tracks:
            self.audio_source_combo.addItem(track.name, track.id)
            self.audio_source_combo.setItemData(self.audio_source_combo.count() - 1, track.note, Qt.ItemDataRole.ToolTipRole)

        index = self.audio_source_combo.findData(selected_key)
        if index < 0:
            index = 0
            self.state.set_audio_capture_source("auto")
        self.audio_source_combo.setCurrentIndex(index)
        self.audio_source_combo.blockSignals(False)
        self._apply_audio_source_now(notify=False)
        self._sync_audio_mixer_dialog()

    def _on_audio_source_changed(self, _index: int) -> None:
        source_key = str(self.audio_source_combo.currentData() or "auto")
        self.state.set_audio_capture_source(source_key)
        self._apply_audio_source_now(notify=True)

    def _apply_audio_source_now(self, notify: bool = False) -> None:
        scene = self.state.get_active_scene()
        scene_id = scene.id if scene is not None else None
        track = self.state.resolve_audio_track_profile(scene_id)
        strict_isolation = self.state.audio_isolation_requested(scene_id)
        self.audio_controller.set_track_profile(track, strict_isolation=strict_isolation)
        status = "独立窗口音轨隔离中" if strict_isolation else "已锁定目标音轨"
        self.audio_status_label.setText(f"音频: {track.name} | {track.note or status}")
        if notify:
            self._notify(f"音频采集已切换: {track.name}")

    def _update_audio_status(self, diag) -> None:
        scene = self.state.get_active_scene()
        scene_id = scene.id if scene is not None else None
        track = self.state.resolve_audio_track_profile(scene_id)
        strict_isolation = self.state.audio_isolation_requested(scene_id)
        level = max(0, min(100, int(diag.level * 180)))
        self.audio_level_bar.setValue(level)

        if diag.backend == "none":
            status = "未找到音频后端"
        elif diag.chunk_empty:
            status = "等待声音输入"
        elif diag.target_pid is not None or diag.target_process:
            if strict_isolation:
                status = "独立窗口音轨" if diag.session_hit else "等待目标窗口声音"
            else:
                status = "目标窗口命中" if diag.session_hit else "系统声音保底"
        else:
            status = "系统声音采集中"

        detail = track.note or diag.note or "正常"
        self.audio_status_label.setText(f"音频: {track.name} | {status} | {detail}")
        self._sync_audio_mixer_dialog(level=diag.level)

    def _refresh_audio_device_combo(self) -> None:
        loopback_devices = self.audio_controller.list_devices()
        input_devices = self.audio_controller.list_input_devices()
        self.audio_device_combo.clear()
        self.audio_device_combo.addItem("默认系统回采设备", {"kind": "loopback", "index": None})
        for dev in loopback_devices:
            label = f"{dev['index']} - {dev['name']}"
            if dev.get("is_loopback"):
                label += " [loopback]"
            self.audio_device_combo.addItem(label, {"kind": "loopback", "index": int(dev["index"])})
        self.audio_device_combo.insertSeparator(self.audio_device_combo.count())
        self.audio_device_combo.addItem("默认麦克风输入", {"kind": "input", "index": None})
        for dev in input_devices:
            self.audio_device_combo.addItem(
                f"{dev['index']} - {dev['name']} [mic]",
                {"kind": "input", "index": int(dev["index"])},
            )

    def _apply_audio_device(self) -> None:
        data = self.audio_device_combo.currentData()
        if isinstance(data, dict):
            kind = str(data.get("kind") or "loopback")
            index = data.get("index")
        else:
            kind = "loopback"
            index = data
        if kind == "input":
            self.audio_controller.restart_with_input_device(index)
            target_name = "麦克风输入设备"
        else:
            self.audio_controller.restart_with_device(index)
            target_name = "系统回采设备"
        self._apply_audio_source_now()
        self._notify(f"已应用{target_name}。")

    def _open_audio_mixer_dialog(self) -> None:
        if self._audio_mixer_dialog is None:
            dialog = AudioMixerDialog(self)
            dialog.track_params_changed.connect(self._on_mixer_track_params_changed)
            dialog.track_selected.connect(self._on_mixer_track_selected)
            dialog.closed.connect(self._on_audio_mixer_dialog_closed)
            self._audio_mixer_dialog = dialog
        self._sync_audio_mixer_dialog()
        self._audio_mixer_dialog.show()
        self._audio_mixer_dialog.raise_()
        self._audio_mixer_dialog.activateWindow()

    def _on_audio_mixer_dialog_closed(self) -> None:
        self._audio_mixer_dialog = None

    def _sync_audio_mixer_dialog(self, level: float | None = None) -> None:
        if self._audio_mixer_dialog is None:
            return
        scene = self.state.get_active_scene()
        scene_id = scene.id if scene is not None else None
        tracks = self.state.list_audio_tracks(scene_id)
        active_track = self.state.resolve_audio_track_profile(scene_id)
        self._audio_mixer_dialog.set_tracks(tracks, active_track.id)
        if level is not None:
            self._audio_mixer_dialog.update_level(active_track.id, max(0.0, min(1.0, float(level))))

    def _on_mixer_track_params_changed(
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
        self._apply_audio_source_now(notify=False)

    def _on_mixer_track_selected(self, track_id: str) -> None:
        self.state.set_audio_capture_source(track_id)
        index = self.audio_source_combo.findData(track_id)
        if index >= 0:
            self.audio_source_combo.blockSignals(True)
            self.audio_source_combo.setCurrentIndex(index)
            self.audio_source_combo.blockSignals(False)
        self._apply_audio_source_now(notify=True)
        self._sync_audio_mixer_dialog()

    def _save_output_preferences(self) -> None:
        if not hasattr(self, "_preference_store"):
            return
        self._preferences = UserPreferences(
            output_quality=str(getattr(self, "_output_quality_key", self.config.default_output_quality)),
            capture_quality=str(getattr(self, "_capture_quality_key", self.config.default_capture_quality)),
            stream_bitrate=str(self.config.default_stream_bitrate),
            record_bitrate=str(self.config.default_record_bitrate),
            stream_encoder=str(self.config.default_stream_encoder),
            record_encoder=str(self.config.default_record_encoder),
            adaptive_bitrate_enabled=bool(getattr(self, "_adaptive_bitrate_enabled", True)),
            adaptive_bitrate_min=str(getattr(self.config, "adaptive_bitrate_min", "2500k")),
        )
        try:
            self._preference_store.save(self._preferences)
        except Exception as exc:
            self.statusBar().showMessage(f"输出偏好保存失败：{exc}", 5000)

    def _apply_encoding_profile(self, *_args) -> None:
        if not hasattr(self, "stream_encoder_combo"):
            return
        stream_mode = str(self.stream_encoder_combo.currentData() or "auto")
        record_mode = str(self.record_encoder_combo.currentData() or "auto")
        stream_bitrate = self.stream_bitrate_combo.currentText().strip() or self.config.default_stream_bitrate
        record_bitrate = self.record_bitrate_combo.currentText().strip() or self.config.default_record_bitrate
        self.output_manager.set_encoding_profile(
            record_bitrate=record_bitrate,
            stream_bitrate=stream_bitrate,
            record_encoder=record_mode,
            stream_encoder=stream_mode,
        )
        self.config.default_record_bitrate = record_bitrate
        self.config.default_stream_bitrate = stream_bitrate
        self.config.default_record_encoder = record_mode
        self.config.default_stream_encoder = stream_mode
        status = self.output_manager.status()
        if hasattr(self, "encoder_status_label"):
            self.encoder_status_label.setText(self._encoding_status_text(status))
        self._sync_preview_popout_output_controls(status)
        self._save_output_preferences()

    def _start_record(self) -> None:
        self._apply_encoding_profile()
        path = self.output_manager.build_record_path(self.config.output_dir)
        ok, msg = self.output_manager.start_record(path)
        self._notify(msg, is_error=not ok)

    def _stop_record(self) -> None:
        ok, msg = self.output_manager.stop_record()
        self._notify(msg, is_error=not ok and "未启动" not in msg)

    def _start_stream(self) -> None:
        self._apply_pending_adaptive_bitrate_before_stream()
        self._apply_encoding_profile()
        url = self.rtmp_edit.text().strip()
        if not url:
            self._notify("请输入 RTMP 地址。", is_error=True)
            return
        ok, msg = self.output_manager.start_stream(url)
        self._notify(msg, is_error=not ok)

    def _stop_stream(self) -> None:
        ok, msg = self.output_manager.stop_stream()
        if ok:
            self._apply_pending_adaptive_bitrate_before_stream()
        self._notify(msg, is_error=not ok and "未启动" not in msg)

    def _update_diag_panel(self) -> None:
        diag = self.audio_controller.get_diagnostics()
        status = self.output_manager.status()
        self._update_output_status_badges(status)
        self._evaluate_adaptive_bitrate(status)
        self._update_audio_status(diag)
        face_status = get_face_effect_status()
        selected_layer = self._get_selected_layer()
        selected_enabled = bool(selected_layer and selected_layer.source.get("face_enabled", False))
        selected_effect = str(selected_layer.source.get("effect_type", "")) if selected_layer else ""

        if not selected_enabled:
            self.face_status_text.setText("人脸识别状态: 已关闭")
            self.face_status_bar.setValue(0)
        else:
            effect_label = ar_effect_label(selected_effect) or "未设置特效"
            self.face_status_text.setText(
                f"人脸识别状态: {face_status.note} | 特效: {effect_label} | 目标层: {face_status.layer_id or 'N/A'}"
            )
            if face_status.detected:
                self.face_status_bar.setValue(100)
            elif face_status.running:
                self.face_status_bar.setValue(45)
            else:
                self.face_status_bar.setValue(20)
        self._sync_face_dialog()
        self._sync_virtual_bg_dialog()
        self._sync_monitor_controls()

    def closeEvent(self, event):  # noqa: N802
        if self._preview_popout is not None:
            self._preview_popout.close()
        if self._layer_manager_dialog is not None:
            self._layer_manager_dialog.close()
        if self._transition_dialog is not None:
            self._transition_dialog.close()
        if self._audio_mixer_dialog is not None:
            self._audio_mixer_dialog.close()
        if self._placeholder_dialog is not None:
            self._placeholder_dialog.close()
        if self._scene_popout is not None:
            self._scene_popout.close()
        if self._canvas_workspace is not None:
            self._canvas_workspace.close()
        if hasattr(self, "_semantic_timer"):
            self._semantic_timer.stop()
        if hasattr(self, "_semantic_worker"):
            self._semantic_worker.stop()
        self.diag_timer.stop()
        self.render_thread.stop()
        self.output_manager.stop_all()
        self.audio_controller.stop()
        self.source_manager.stop_all()
        super().closeEvent(event)


def launch(config: AppConfig) -> None:
    app = QApplication.instance() or QApplication([])
    win = MainWindow(config)
    win.show()
    app.exec()



