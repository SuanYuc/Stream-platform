from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from nsy_broadcasting_platform.models import Layer, LayerType
from nsy_broadcasting_platform.ui.theme import LAYER_CARD_NORMAL_QSS, LAYER_CARD_SELECTED_QSS


class LayerItemWidget(QWidget):
    lock_changed = pyqtSignal(str, bool)
    delete_clicked = pyqtSignal(str)
    volume_changed = pyqtSignal(str, float)
    enabled_changed = pyqtSignal(str, bool)
    saturation_changed = pyqtSignal(str, float)
    contrast_changed = pyqtSignal(str, float)
    color_temp_changed = pyqtSignal(str, int)
    mosaic_changed = pyqtSignal(str, int)
    onnx_style_changed = pyqtSignal(str, str)
    priority_changed = pyqtSignal(str, int)
    face_enabled_changed = pyqtSignal(str, bool)
    face_effect_changed = pyqtSignal(str, str)
    face_scale_changed = pyqtSignal(str, int)
    face_smoothing_changed = pyqtSignal(str, int)
    virtual_bg_enabled_changed = pyqtSignal(str, bool)
    virtual_bg_mode_changed = pyqtSignal(str, str)
    virtual_bg_blur_changed = pyqtSignal(str, int)

    _MATTING_SUPPORTED = {
        LayerType.CAMERA,
        LayerType.SCREEN,
        LayerType.WINDOW,
        LayerType.NETWORK,
        LayerType.VIDEO,
    }

    def __init__(self, layer: Layer, max_priority: int | None = None) -> None:
        super().__init__()
        self.layer_id = layer.id
        self.layer_type = layer.layer_type
        self.layer_name = layer.name
        self.max_priority = max(1, int(max_priority or layer.priority or 1))
        self._volume_value = max(0, min(200, int(layer.volume * 100)))
        self._saturation_value = max(0, min(200, int(layer.saturation * 100)))
        self._contrast_value = max(0, min(200, int(layer.contrast * 100)))
        self._color_temp_value = max(-100, min(100, int(layer.color_temp)))
        self._mosaic_value = max(0, min(100, int(layer.mosaic)))
        self._source = dict(layer.source or {})
        self._onnx_style_value = self._canonical_onnx_style(self._source.get("onnx_style", "none"))
        self._matting_supported = layer.layer_type in self._MATTING_SUPPORTED
        self.setObjectName("LayerCard")
        self.metrics_label: QLabel | None = None
        self._build_ui(layer)
        self.set_selected(False)

    def _build_ui(self, layer: Layer) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(6)

        header = QHBoxLayout()
        self.name_label = QLabel(f"{layer.name} [{layer.layer_type.value}]")
        self.name_label.setObjectName("LayerTitle")
        self.lock_box = QCheckBox("锁定")
        self.lock_box.setChecked(layer.locked)
        self.priority_spin = QSpinBox()
        self.priority_spin.setRange(1, self.max_priority)
        self.priority_spin.setValue(max(1, min(self.max_priority, int(layer.priority or 1))))
        self.priority_spin.setToolTip("图层优先级范围为 1 到当前图层数量，编号越大越靠上")
        self.priority_spin.setFixedWidth(74)
        self.del_btn = QPushButton("删除")
        self.del_btn.setProperty("role", "danger")
        self.del_btn.setFixedWidth(58)
        header.addWidget(self.name_label, 1)
        header.addWidget(QLabel("优先级"))
        header.addWidget(self.priority_spin)
        header.addWidget(self.lock_box)
        header.addWidget(self.del_btn)
        root.addLayout(header)

        row2 = QHBoxLayout()
        self.enable_box = QCheckBox("启用")
        self.enable_box.setChecked(layer.enabled)
        row2.addWidget(self.enable_box)
        row2.addStretch(1)
        root.addLayout(row2)

        param_row = QHBoxLayout()
        param_row.setSpacing(6)
        self.btn_audio = QPushButton("音频")
        self.btn_filter = QPushButton("滤镜")
        self.btn_color = QPushButton("色彩校正")
        self.btn_ai = QPushButton("智能增强")
        for button in (self.btn_audio, self.btn_filter, self.btn_color, self.btn_ai):
            button.setProperty("role", "toolbar")
            button.setMinimumHeight(32)
            param_row.addWidget(button, 1)
        self.btn_ai.setEnabled(self._matting_supported)
        self.btn_ai.setToolTip("" if self._matting_supported else "当前图层不支持虚拟背景或 AR 贴纸")
        root.addLayout(param_row)

        if self._matting_supported:
            self.metrics_label = QLabel(self._default_metrics_text(layer))
            self.metrics_label.setObjectName("LayerMetrics")
            self.metrics_label.setWordWrap(True)
            root.addWidget(self.metrics_label)

        self.lock_box.toggled.connect(lambda value: self.lock_changed.emit(self.layer_id, value))
        self.del_btn.clicked.connect(lambda: self.delete_clicked.emit(self.layer_id))
        self.enable_box.toggled.connect(lambda value: self.enabled_changed.emit(self.layer_id, value))
        self.priority_spin.valueChanged.connect(lambda value: self.priority_changed.emit(self.layer_id, int(value)))
        self.btn_audio.clicked.connect(self._open_audio_panel)
        self.btn_filter.clicked.connect(self._open_filter_panel)
        self.btn_color.clicked.connect(self._open_color_panel)
        self.btn_ai.clicked.connect(self._open_ai_panel)

    def _slider_row(
        self,
        root: QVBoxLayout,
        text: str,
        min_v: int,
        max_v: int,
        val: int,
        suffix: str = "",
    ) -> tuple[QSlider, QLabel]:
        row = QHBoxLayout()
        label = QLabel(text)
        label.setObjectName("LayerField")
        label.setFixedWidth(84)
        value_label = QLabel(f"{val}{suffix}")
        value_label.setObjectName("LayerField")
        value_label.setFixedWidth(58)
        value_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        slider = QSlider(Qt.Orientation.Horizontal)
        slider.setRange(min_v, max_v)
        slider.setValue(val)
        row.addWidget(label)
        row.addWidget(slider, 1)
        row.addWidget(value_label)
        root.addLayout(row)
        return slider, value_label

    def _make_panel(self, title: str) -> tuple[QDialog, QVBoxLayout]:
        dialog = QDialog(self)
        dialog.setWindowTitle(title)
        dialog.setModal(True)
        dialog.setMinimumWidth(360)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)
        return dialog, layout

    def _add_close_button(self, layout: QVBoxLayout, dialog: QDialog) -> None:
        row = QHBoxLayout()
        row.addStretch(1)
        close_btn = QPushButton("关闭")
        close_btn.setMinimumWidth(92)
        close_btn.clicked.connect(dialog.accept)
        row.addWidget(close_btn)
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
        if not self._matting_supported:
            return
        dialog, layout = self._make_panel("智能增强")

        virtual_bg_enabled = bool(self._source.get("virtual_bg_enabled", False))
        virtual_bg_mode = str(self._source.get("virtual_bg_mode", "image") or "image").strip().lower()
        if virtual_bg_mode not in {"image", "blur"}:
            virtual_bg_mode = "image"
        blur_strength = int(max(0, min(100, self._source.get("virtual_bg_blur_strength", 55))))
        face_enabled = bool(self._source.get("face_enabled", False))
        effect_type = str(self._source.get("effect_type", "dog_nose") or "dog_nose").strip()
        face_scale = int(max(50, min(200, self._source.get("face_scale_percent", 100))))
        face_smoothing = int(max(0, min(100, self._source.get("face_smoothing", 60))))

        bg_enable = QCheckBox("启用虚拟背景")
        bg_enable.setChecked(virtual_bg_enabled)
        layout.addWidget(bg_enable)

        bg_row = QHBoxLayout()
        bg_row.addWidget(QLabel("背景模式"))
        bg_mode = QComboBox()
        bg_mode.addItem("背景图片", "image")
        bg_mode.addItem("背景模糊", "blur")
        bg_mode.setCurrentIndex(max(0, bg_mode.findData(virtual_bg_mode)))
        bg_row.addWidget(bg_mode, 1)
        layout.addLayout(bg_row)

        blur_slider, blur_value = self._slider_row(layout, "模糊强度", 0, 100, blur_strength, "%")

        face_enable = QCheckBox("启用 AR 贴纸")
        face_enable.setChecked(face_enabled)
        layout.addWidget(face_enable)

        effect_row = QHBoxLayout()
        effect_row.addWidget(QLabel("贴纸类型"))
        effect_combo = QComboBox()
        effect_combo.addItem("狗鼻子", "dog_nose")
        effect_combo.addItem("猫耳朵", "cat_ears")
        effect_combo.addItem("卡通眼睛", "cartoon_eyes")
        effect_combo.setCurrentIndex(max(0, effect_combo.findData(effect_type)))
        effect_row.addWidget(effect_combo, 1)
        layout.addLayout(effect_row)

        scale_slider, scale_value = self._slider_row(layout, "贴纸缩放", 50, 200, face_scale, "%")
        smooth_slider, smooth_value = self._slider_row(layout, "跟踪平滑", 0, 100, face_smoothing, "%")

        def on_bg_enabled(value: bool) -> None:
            self._source["virtual_bg_enabled"] = bool(value)
            self.virtual_bg_enabled_changed.emit(self.layer_id, bool(value))

        def on_bg_mode(_index: int) -> None:
            value = str(bg_mode.currentData() or "image")
            self._source["virtual_bg_mode"] = value
            self.virtual_bg_mode_changed.emit(self.layer_id, value)

        def on_blur(value: int) -> None:
            value = int(value)
            self._source["virtual_bg_blur_strength"] = value
            blur_value.setText(f"{value}%")
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
            scale_value.setText(f"{value}%")
            self.face_scale_changed.emit(self.layer_id, value)

        def on_smoothing(value: int) -> None:
            value = int(value)
            self._source["face_smoothing"] = value
            smooth_value.setText(f"{value}%")
            self.face_smoothing_changed.emit(self.layer_id, value)

        bg_enable.toggled.connect(on_bg_enabled)
        bg_mode.currentIndexChanged.connect(on_bg_mode)
        blur_slider.valueChanged.connect(on_blur)
        face_enable.toggled.connect(on_face_enabled)
        effect_combo.currentIndexChanged.connect(on_effect)
        scale_slider.valueChanged.connect(on_scale)
        smooth_slider.valueChanged.connect(on_smoothing)
        self._add_close_button(layout, dialog)
        dialog.exec()

    def _default_metrics_text(self, layer: Layer) -> str:
        if not bool(layer.source.get("virtual_bg_enabled", False)):
            return "算法: MediaPipe | 抠像未启用"
        return "算法: MediaPipe | 等待渲染指标..."

    def set_matting_engine(self, _engine_key: str) -> None:
        # 当前仅保留 MediaPipe 引擎入口。
        return

    def set_matting_metrics(self, metrics: dict[str, object] | None) -> None:
        if self.metrics_label is None:
            return
        if not metrics:
            self.metrics_label.setText("算法: MediaPipe | 暂无指标")
            self.metrics_label.setToolTip("")
            return
        engine_label = str(metrics.get("engine_label") or "MediaPipe")
        inference_time_ms = float(metrics.get("inference_time_ms") or 0.0)
        estimated_fps = float(metrics.get("estimated_fps") or 0.0)
        note = str(metrics.get("note") or "").strip()
        provider = str(metrics.get("provider") or "").strip()
        if note:
            self.metrics_label.setText(f"算法: {engine_label} | 状态: {note}")
        else:
            self.metrics_label.setText(
                f"算法: {engine_label} | 延迟: {inference_time_ms:.1f} ms | 估算 FPS: {estimated_fps:.0f}"
            )
        self.metrics_label.setToolTip(provider)

    def set_selected(self, selected: bool) -> None:
        if selected:
            self.setStyleSheet(LAYER_CARD_SELECTED_QSS)
            return
        self.setStyleSheet(LAYER_CARD_NORMAL_QSS)
