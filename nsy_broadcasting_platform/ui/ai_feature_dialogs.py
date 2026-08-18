from __future__ import annotations

from PyQt6.QtCore import QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QProgressBar,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from nsy_broadcasting_platform.ui.theme import AI_DIALOG_QSS, HaulixTokens


class _FeatureShell(QDialog):
    def __init__(self, title: str, subtitle: str, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 560)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setObjectName("AIFeatureDialog")
        self.setStyleSheet(AI_DIALOG_QSS)

        root = QVBoxLayout(self)
        root.setContentsMargins(22, 22, 22, 22)
        root.setSpacing(12)

        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(0)
        shadow.setOffset(6, 6)
        shadow.setColor(QColor(0, 0, 0, 160))
        self.setGraphicsEffect(shadow)

        title_label = QLabel(title)
        title_label.setStyleSheet(
            f"font-size: 19px; font-weight: 800; letter-spacing: -0.2px; color: {HaulixTokens.text};"
        )
        subtitle_label = QLabel(subtitle)
        subtitle_label.setWordWrap(True)
        subtitle_label.setStyleSheet(f"color: {HaulixTokens.text_muted};")

        header_card = QFrame()
        header_card.setObjectName("AIFeatureHeader")
        header_layout = QVBoxLayout(header_card)
        header_layout.setContentsMargins(14, 14, 14, 14)
        header_layout.setSpacing(6)
        header_layout.addWidget(title_label)
        header_layout.addWidget(subtitle_label)

        self.body = QVBoxLayout()
        self.body.setSpacing(10)

        self.status_label = QLabel("当前为独立功能界面占位，后续可以在这里接入真实 AI 工作流。")
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("AIFeatureStatus")

        close_row = QHBoxLayout()
        close_row.addStretch(1)
        close_btn = QPushButton("返回主界面")
        close_btn.clicked.connect(self.close)
        close_row.addWidget(close_btn)

        root.addWidget(header_card)
        root.addLayout(self.body, 1)
        root.addWidget(self.status_label)
        root.addLayout(close_row)

    def _add_card(self, title: str, description: str | None = None) -> QVBoxLayout:
        card = QFrame()
        card.setObjectName("AIFeatureCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        label = QLabel(title)
        label.setStyleSheet(f"font-weight: 800; font-size: 14px; color: {HaulixTokens.text};")
        layout.addWidget(label)

        if description:
            desc_label = QLabel(description)
            desc_label.setWordWrap(True)
            desc_label.setStyleSheet(f"color: {HaulixTokens.text_muted};")
            layout.addWidget(desc_label)

        self.body.addWidget(card)
        return layout


class SemanticResultCard(QFrame):
    highlight_requested = pyqtSignal(str)
    switch_requested = pyqtSignal(str)

    def __init__(
        self,
        *,
        scene_id: str,
        scene_name: str,
        score: float,
        reason: str,
        inference_ms: float,
        image=None,
        is_best: bool = False,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.scene_id = scene_id
        self.setObjectName("AIFeatureCard")
        self.setMinimumHeight(116)

        root = QHBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(12)

        self.thumb_label = QLabel()
        self.thumb_label.setFixedSize(144, 82)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setStyleSheet(
            f"background:{HaulixTokens.bg_soft}; border:1px solid {HaulixTokens.border}; "
            "border-radius:14px; color:{0};".format(HaulixTokens.text_weak)
        )
        if image is not None and not (hasattr(image, "isNull") and image.isNull()):
            pixmap = QPixmap.fromImage(image).scaled(
                self.thumb_label.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
            self.thumb_label.setPixmap(pixmap)
        else:
            self.thumb_label.setText("暂无预览")
        root.addWidget(self.thumb_label)

        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 0, 0, 0)
        info_layout.setSpacing(6)
        title = QLabel(("最佳匹配 · " if is_best else "") + scene_name)
        title.setStyleSheet(f"color:{HaulixTokens.text}; font-size:14px; font-weight:900;")
        score_label = QLabel(f"相似度 {score:.3f} · 推理 {inference_ms:.0f} ms")
        score_label.setStyleSheet(f"color:{HaulixTokens.accent_hover}; font-weight:800;")
        reason_label = QLabel(reason or "FG-CLIP2 图文相似度命中")
        reason_label.setWordWrap(True)
        reason_label.setStyleSheet(f"color:{HaulixTokens.text_muted};")
        info_layout.addWidget(title)
        info_layout.addWidget(score_label)
        info_layout.addWidget(reason_label)
        root.addLayout(info_layout, 1)

        action_layout = QVBoxLayout()
        action_layout.setContentsMargins(0, 0, 0, 0)
        action_layout.setSpacing(8)
        locate_btn = QPushButton("定位")
        locate_btn.setProperty("role", "toolbar")
        apply_btn = QPushButton("应用到节目")
        apply_btn.setProperty("role", "primary")
        locate_btn.clicked.connect(lambda: self.highlight_requested.emit(self.scene_id))
        apply_btn.clicked.connect(lambda: self.switch_requested.emit(self.scene_id))
        action_layout.addWidget(locate_btn)
        action_layout.addWidget(apply_btn)
        action_layout.addStretch(1)
        root.addLayout(action_layout)

        border = HaulixTokens.accent if is_best else HaulixTokens.border
        self.setStyleSheet(
            f"QFrame#AIFeatureCard {{ background:{HaulixTokens.panel_raised}; "
            f"border:1px solid {border}; border-radius:18px; }}"
        )

    def sizeHint(self) -> QSize:  # noqa: D401
        return QSize(620, 122)


class SemanticDirectorDialog(_FeatureShell):
    recommendation_requested = pyqtSignal(str, float)
    recommendation_stopped = pyqtSignal()
    switch_requested = pyqtSignal(str)
    highlight_requested = pyqtSignal(str)

    def __init__(self, query: str, parent=None) -> None:
        super().__init__(
            "语义智能导播",
            "开启后输入关键词，系统会语义检索所有场景缩略图，并把符合描述的场景整理成可直接上节目输出的预览面板。",
            parent=parent,
        )
        self._best_scene_id: str | None = None
        self._selected_scene_id: str | None = None
        self._scene_previews: dict[str, object] = {}
        layout = self._add_card("推荐模式")

        row = QHBoxLayout()
        row.addWidget(QLabel("搜索词"))
        self.query_edit = QLineEdit(query)
        self.query_edit.setPlaceholderText("例如：足球、篮球、主持人、红色队服、拿麦克风的人")
        row.addWidget(self.query_edit, 1)
        layout.addLayout(row)

        threshold_row = QHBoxLayout()
        threshold_row.addWidget(QLabel("推荐阈值"))
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(-1.0, 1.0)
        self.threshold_spin.setDecimals(2)
        self.threshold_spin.setSingleStep(0.02)
        self.threshold_spin.setValue(0.10)
        self.threshold_spin.setToolTip("FG-CLIP2 余弦相似度阈值，过高可能没有推荐结果。")
        threshold_row.addWidget(self.threshold_spin)
        self.provider_label = QLabel("推理设备: 未加载")
        self.provider_label.setObjectName("AIFeatureStatus")
        threshold_row.addWidget(self.provider_label, 1)
        layout.addLayout(threshold_row)

        action_row = QHBoxLayout()
        self.start_btn = QPushButton("开启智能导播")
        self.start_btn.setObjectName("PrimaryAction")
        self.stop_btn = QPushButton("关闭模式")
        self.stop_btn.setEnabled(False)
        self.highlight_btn = QPushButton("定位最佳匹配")
        self.highlight_btn.setEnabled(False)
        self.switch_btn = QPushButton("应用最佳到节目输出")
        self.switch_btn.setProperty("role", "primary")
        self.switch_btn.setEnabled(False)
        action_row.addWidget(self.start_btn)
        action_row.addWidget(self.stop_btn)
        action_row.addWidget(self.highlight_btn)
        action_row.addWidget(self.switch_btn)
        layout.addLayout(action_row)

        preview_title = QLabel("匹配场景预览")
        preview_title.setStyleSheet(f"font-weight:900; color:{HaulixTokens.text};")
        layout.addWidget(preview_title)

        self.result_list = QListWidget()
        self.result_list.setMinimumHeight(300)
        layout.addWidget(self.result_list)

        self.status_label.setText("输入关键词后点击“开启智能导播”。模型会按需加载，首次启动可能稍慢。")

        self.start_btn.clicked.connect(self._emit_start)
        self.stop_btn.clicked.connect(self._emit_stop)
        self.highlight_btn.clicked.connect(self._emit_highlight)
        self.switch_btn.clicked.connect(self._emit_switch)
        self.result_list.currentItemChanged.connect(self._on_result_selected)

    def _emit_start(self) -> None:
        query = self.query_edit.text().strip()
        if not query:
            self.status_label.setText("请输入语义搜索词。")
            return
        self.start_btn.setEnabled(False)
        self.stop_btn.setEnabled(True)
        self.recommendation_requested.emit(query, float(self.threshold_spin.value()))

    def _emit_stop(self) -> None:
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        self.recommendation_stopped.emit()

    def _emit_highlight(self) -> None:
        scene_id = self._selected_scene_id or self._best_scene_id
        if scene_id:
            self.highlight_requested.emit(scene_id)

    def _emit_switch(self) -> None:
        scene_id = self._selected_scene_id or self._best_scene_id
        if scene_id:
            self.switch_requested.emit(scene_id)

    def _on_result_selected(self, current: QListWidgetItem | None, _prev: QListWidgetItem | None) -> None:
        self._selected_scene_id = current.data(Qt.ItemDataRole.UserRole) if current is not None else None
        has_scene = bool(self._selected_scene_id or self._best_scene_id)
        self.highlight_btn.setEnabled(has_scene)
        self.switch_btn.setEnabled(has_scene)

    def set_status(self, text: str) -> None:
        self.status_label.setText(text or "")

    def set_provider_text(self, text: str) -> None:
        self.provider_label.setText(f"推理设备: {text or '未知'}")

    def set_scene_previews(self, previews: dict[str, object] | None) -> None:
        self._scene_previews = dict(previews or {})

    def set_results(self, result, previews: dict[str, object] | None = None) -> None:
        if previews is not None:
            self.set_scene_previews(previews)
        self.result_list.clear()
        self._best_scene_id = getattr(result, "best_scene_id", None)
        self._selected_scene_id = None
        self.set_provider_text(getattr(result, "provider", "未知"))
        error = getattr(result, "error", "")
        if error:
            self.status_label.setText(error)
            self.start_btn.setEnabled(True)
            self.stop_btn.setEnabled(False)
            self.highlight_btn.setEnabled(False)
            self.switch_btn.setEnabled(False)
            return

        threshold = float(self.threshold_spin.value())
        scores = [
            item
            for item in (list(getattr(result, "scores", []) or []))
            if float(getattr(item, "score", -1.0)) >= threshold
        ]
        if not scores:
            self.status_label.setText("没有超过当前阈值的匹配场景。可以降低阈值或等待场景缩略图刷新后重试。")
        for index, item in enumerate(scores, start=1):
            score = float(getattr(item, "score", 0.0))
            scene_id = str(getattr(item, "scene_id", ""))
            scene_name = str(getattr(item, "scene_name", scene_id))
            reason = str(getattr(item, "reason", ""))
            infer_ms = float(getattr(item, "inference_ms", 0.0))
            row = QListWidgetItem()
            row.setData(Qt.ItemDataRole.UserRole, scene_id)
            card = SemanticResultCard(
                scene_id=scene_id,
                scene_name=f"{index}. {scene_name}",
                score=score,
                reason=reason,
                inference_ms=infer_ms,
                image=self._scene_previews.get(scene_id),
                is_best=scene_id == self._best_scene_id,
            )
            card.highlight_requested.connect(self.highlight_requested.emit)
            card.switch_requested.connect(self.switch_requested.emit)
            row.setSizeHint(card.sizeHint())
            self.result_list.addItem(row)
            self.result_list.setItemWidget(row, card)
        if self.result_list.count() > 0:
            self.result_list.setCurrentRow(0)
        elapsed = float(getattr(result, "elapsed_ms", 0.0))
        self.status_label.setText(f"智能导播检索完成：{len(scores)} 个匹配场景，耗时 {elapsed:.0f} ms。")
        self.start_btn.setEnabled(True)
        self.stop_btn.setEnabled(False)
        has_best = bool(self._best_scene_id)
        self.highlight_btn.setEnabled(has_best)
        self.switch_btn.setEnabled(has_best)


class FaceEffectDialog(_FeatureShell):
    def __init__(
        self,
        enabled: bool,
        effect_text: str,
        sticker_path: str,
        scale_percent: int,
        smoothing_percent: int,
        target_text: str,
        status_text: str,
        progress_value: int,
        parent=None,
    ) -> None:
        super().__init__(
            "人脸识别与特效",
            "这里集中展示人脸识别开关、贴纸特效和工作状态。当前保留界面与状态联动入口。",
            parent=parent,
        )
        layout = self._add_card("识别与特效配置", "可以在这里集中操作人脸识别、特效类型和本地 AR 素材。")

        top = QHBoxLayout()
        self.enable_btn = QPushButton("关闭识别" if enabled else "开启识别")
        self.enable_btn.setCheckable(True)
        self.enable_btn.setChecked(enabled)
        self.enable_btn.setObjectName("PrimaryAction")
        self.nose_btn = QPushButton("狗鼻子")
        self.hat_btn = QPushButton("猫耳")
        self.eyes_btn = QPushButton("卡通眼睛")
        top.addWidget(self.enable_btn)
        top.addWidget(self.nose_btn)
        top.addWidget(self.hat_btn)
        top.addWidget(self.eyes_btn)
        top.addStretch(1)
        layout.addLayout(top)

        effect_row = QHBoxLayout()
        effect_row.addWidget(QLabel("特效类型"))
        self.effect_combo = QComboBox()
        self.effect_combo.addItem("无", "")
        self.effect_combo.addItem("狗鼻子", "dog_nose")
        self.effect_combo.addItem("猫耳", "cat_ears")
        self.effect_combo.addItem("卡通眼睛", "cartoon_eyes")
        legacy_effects = {"nose": "dog_nose", "hat": "cat_ears", "eyes": "cartoon_eyes"}
        effect_index = self.effect_combo.findText(effect_text)
        if effect_index < 0:
            effect_index = self.effect_combo.findData(legacy_effects.get(effect_text.lower(), effect_text.lower()))
        if effect_index >= 0:
            self.effect_combo.setCurrentIndex(effect_index)
        effect_row.addWidget(self.effect_combo, 1)
        layout.addLayout(effect_row)

        sticker_row = QHBoxLayout()
        sticker_row.addWidget(QLabel("AR素材"))
        self.sticker_edit = QLineEdit(sticker_path)
        self.sticker_edit.setReadOnly(True)
        self.sticker_edit.setPlaceholderText("这里显示本地 PNG AR 素材路径")
        self.sticker_btn = QPushButton("导入AR素材")
        sticker_row.addWidget(self.sticker_edit, 1)
        sticker_row.addWidget(self.sticker_btn)
        layout.addLayout(sticker_row)

        scale_row = QHBoxLayout()
        scale_row.addWidget(QLabel("贴纸缩放"))
        self.scale_slider = QSlider(Qt.Orientation.Horizontal)
        self.scale_slider.setRange(50, 200)
        self.scale_slider.setValue(max(50, min(200, int(scale_percent))))
        self.scale_value_label = QLabel(f"{self.scale_slider.value()}%")
        self.scale_value_label.setMinimumWidth(54)
        scale_row.addWidget(self.scale_slider, 1)
        scale_row.addWidget(self.scale_value_label)
        layout.addLayout(scale_row)

        smooth_row = QHBoxLayout()
        smooth_row.addWidget(QLabel("跟踪平滑"))
        self.smoothing_slider = QSlider(Qt.Orientation.Horizontal)
        self.smoothing_slider.setRange(0, 100)
        self.smoothing_slider.setValue(max(0, min(100, int(smoothing_percent))))
        self.smoothing_value_label = QLabel(f"{self.smoothing_slider.value()}%")
        self.smoothing_value_label.setMinimumWidth(54)
        smooth_row.addWidget(self.smoothing_slider, 1)
        smooth_row.addWidget(self.smoothing_value_label)
        layout.addLayout(smooth_row)

        self.target_label = QLabel(target_text or "目标图层: 未选择")
        layout.addWidget(self.target_label)

        self.runtime_status_label = QLabel(status_text or "人脸识别状态: 未启用")
        self.runtime_status_label.setWordWrap(True)
        layout.addWidget(self.runtime_status_label)

        self.status_bar = QProgressBar()
        self.status_bar.setRange(0, 100)
        self.status_bar.setValue(max(0, min(100, progress_value)))
        self.status_bar.setTextVisible(False)
        self.status_bar.setFixedHeight(10)
        layout.addWidget(self.status_bar)


class VirtualBackgroundDialog(_FeatureShell):
    def __init__(
        self,
        enabled: bool,
        mode_text: str,
        blur_strength_percent: int,
        asset_path: str,
        target_text: str,
        status_text: str,
        parent=None,
    ) -> None:
        super().__init__(
            "虚拟背景",
            "这里集中配置人像抠图、背景图替换和背景模糊。当前会与主界面实时联动。",
            parent=parent,
        )
        layout = self._add_card("虚拟背景配置", "可对当前选中的视频图层启用背景替换或背景模糊。")

        top = QHBoxLayout()
        self.enable_btn = QPushButton("关闭虚拟背景" if enabled else "开启虚拟背景")
        self.enable_btn.setCheckable(True)
        self.enable_btn.setChecked(enabled)
        self.enable_btn.setObjectName("PrimaryAction")
        top.addWidget(self.enable_btn)
        top.addStretch(1)
        layout.addLayout(top)

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("处理模式"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("背景图替换", "image")
        self.mode_combo.addItem("背景模糊", "blur")
        mode_index = self.mode_combo.findText(mode_text)
        if mode_index < 0:
            mode_index = self.mode_combo.findData(mode_text.lower())
        if mode_index < 0:
            mode_index = 0
        self.mode_combo.setCurrentIndex(mode_index)
        mode_row.addWidget(self.mode_combo, 1)
        layout.addLayout(mode_row)

        blur_row = QHBoxLayout()
        blur_row.addWidget(QLabel("模糊强度"))
        self.blur_slider = QSlider(Qt.Orientation.Horizontal)
        self.blur_slider.setRange(0, 100)
        self.blur_slider.setValue(max(0, min(100, int(blur_strength_percent))))
        self.blur_value_label = QLabel(f"{self.blur_slider.value()}%")
        self.blur_value_label.setMinimumWidth(54)
        blur_row.addWidget(self.blur_slider, 1)
        blur_row.addWidget(self.blur_value_label)
        layout.addLayout(blur_row)

        asset_row = QHBoxLayout()
        asset_row.addWidget(QLabel("背景图片"))
        self.asset_edit = QLineEdit(asset_path)
        self.asset_edit.setReadOnly(True)
        self.asset_edit.setPlaceholderText("背景图替换模式下可选择 JPG / PNG / BMP")
        self.asset_choose_btn = QPushButton("选择背景")
        self.asset_clear_btn = QPushButton("清除背景")
        asset_row.addWidget(self.asset_edit, 1)
        asset_row.addWidget(self.asset_choose_btn)
        asset_row.addWidget(self.asset_clear_btn)
        layout.addLayout(asset_row)

        self.target_label = QLabel(target_text or "目标图层: 未选择")
        layout.addWidget(self.target_label)

        self.runtime_status_label = QLabel(status_text or "虚拟背景状态: 未启用")
        self.runtime_status_label.setWordWrap(True)
        layout.addWidget(self.runtime_status_label)


class AnomalyDetectionDialog(_FeatureShell):
    def __init__(self, enabled: bool, query: str, parent=None) -> None:
        super().__init__(
            "舞台异常检测",
            "这里预留给异常动作检测、趣味镜头抓取和安全事件提示。当前只提供界面入口。",
            parent=parent,
        )
        layout = self._add_card("异常搜索", "可以把文本提示、事件规则和结果列表都集中在这个独立界面里。")

        top = QHBoxLayout()
        self.enable_btn = QPushButton("已启动" if enabled else "未启动")
        self.enable_btn.setCheckable(True)
        self.enable_btn.setChecked(enabled)
        top.addWidget(self.enable_btn)
        top.addWidget(QLabel("异常描述"))
        self.query_edit = QLineEdit(query)
        self.query_edit.setPlaceholderText("例如：演员跌倒、某个孩子裤子掉了、观众闯入舞台")
        top.addWidget(self.query_edit, 1)
        layout.addLayout(top)

        action_row = QHBoxLayout()
        search_btn = QPushButton("搜索异常镜头")
        search_btn.setObjectName("PrimaryAction")
        action_row.addWidget(search_btn)
        action_row.addWidget(QPushButton("加入监控规则"))
        action_row.addWidget(QPushButton("清空结果"))
        layout.addLayout(action_row)


class VirtualAdDialog(_FeatureShell):
    def __init__(self, enabled: bool, position_text: str, asset_path: str, parent=None) -> None:
        super().__init__(
            "虚拟广告",
            "这里预留给广告素材管理、广告位编排和自动投放配置。当前只保留界面入口。",
            parent=parent,
        )
        layout = self._add_card("广告位配置", "可以把广告计划、素材预览和投放策略放到这个页面。")

        top = QHBoxLayout()
        self.enable_btn = QPushButton("已启用" if enabled else "未启用")
        self.enable_btn.setCheckable(True)
        self.enable_btn.setChecked(enabled)
        top.addWidget(self.enable_btn)
        top.addWidget(QLabel("广告位置"))
        self.position_combo = QComboBox()
        for text in ["右下角", "左上角", "右上角", "中下三分之一", "舞台中央"]:
            self.position_combo.addItem(text)
        index = self.position_combo.findText(position_text)
        if index >= 0:
            self.position_combo.setCurrentIndex(index)
        top.addWidget(self.position_combo)
        top.addStretch(1)
        layout.addLayout(top)

        asset_row = QHBoxLayout()
        asset_row.addWidget(QLabel("广告素材"))
        self.asset_edit = QLineEdit(asset_path)
        self.asset_edit.setReadOnly(True)
        self.asset_edit.setPlaceholderText("这里显示广告素材路径")
        asset_row.addWidget(self.asset_edit, 1)
        asset_row.addWidget(QPushButton("选择素材"))
        layout.addLayout(asset_row)

        action_row = QHBoxLayout()
        apply_btn = QPushButton("应用到当前场景")
        apply_btn.setObjectName("PrimaryAction")
        action_row.addWidget(apply_btn)
        action_row.addWidget(QPushButton("保存广告模板"))
        action_row.addWidget(QPushButton("移除广告"))
        layout.addLayout(action_row)


class ARFeatureDialog(_FeatureShell):
    def __init__(
        self,
        enabled: bool,
        mode_text: str,
        target_text: str,
        asset_path: str,
        effect_text: str = "",
        parent=None,
    ) -> None:
        super().__init__(
            "AR 功能",
            "人物贴纸 AR 已接入人脸跟随渲染，可直接在默认三种效果中选择。",
            parent=parent,
        )
        layout = self._add_card("AR 配置", "可选择狗鼻子、猫耳和卡通眼睛，未导入素材时使用内置 PNG 贴纸。")

        top = QHBoxLayout()
        self.enable_btn = QPushButton("已启用" if enabled else "未启用")
        self.enable_btn.setCheckable(True)
        self.enable_btn.setChecked(enabled)
        top.addWidget(self.enable_btn)
        top.addWidget(QLabel("AR 模式"))
        self.mode_combo = QComboBox()
        for text in ["人物贴纸 AR", "舞台标签 AR", "虚拟道具 AR"]:
            self.mode_combo.addItem(text)
        index = self.mode_combo.findText(mode_text)
        if index >= 0:
            self.mode_combo.setCurrentIndex(index)
        top.addWidget(self.mode_combo)
        top.addStretch(1)
        layout.addLayout(top)

        target_label = QLabel(target_text or "AR 目标图层：未选择")
        target_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(target_label)

        effect_row = QHBoxLayout()
        effect_row.addWidget(QLabel("默认特效"))
        self.effect_combo = QComboBox()
        self.effect_combo.addItem("狗鼻子", "dog_nose")
        self.effect_combo.addItem("猫耳", "cat_ears")
        self.effect_combo.addItem("卡通眼睛", "cartoon_eyes")
        legacy_effects = {"nose": "dog_nose", "hat": "cat_ears", "eyes": "cartoon_eyes"}
        effect_index = self.effect_combo.findText(effect_text)
        if effect_index < 0:
            effect_index = self.effect_combo.findData(legacy_effects.get(effect_text.lower(), effect_text.lower()))
        if effect_index >= 0:
            self.effect_combo.setCurrentIndex(effect_index)
        effect_row.addWidget(self.effect_combo, 1)
        layout.addLayout(effect_row)

        asset_row = QHBoxLayout()
        asset_row.addWidget(QLabel("AR 素材"))
        self.asset_edit = QLineEdit(asset_path)
        self.asset_edit.setReadOnly(True)
        self.asset_edit.setPlaceholderText("这里显示 AR 素材路径")
        asset_row.addWidget(self.asset_edit, 1)
        self.asset_btn = QPushButton("选择素材")
        asset_row.addWidget(self.asset_btn)
        layout.addLayout(asset_row)

        action_row = QHBoxLayout()
        self.apply_btn = QPushButton("应用到选中图层")
        self.apply_btn.setObjectName("PrimaryAction")
        self.clear_btn = QPushButton("清除 AR")
        action_row.addWidget(self.apply_btn)
        action_row.addWidget(QPushButton("保存 AR 模板"))
        action_row.addWidget(self.clear_btn)
        layout.addLayout(action_row)

