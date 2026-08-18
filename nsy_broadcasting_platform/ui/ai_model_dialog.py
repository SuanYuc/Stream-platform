from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QMimeData, QPoint, Qt
from PyQt6.QtGui import QDrag, QImage, QPixmap
from PyQt6.QtWidgets import (
    QComboBox,
    QDialog,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from nsy_broadcasting_platform.ai_models import (
    AI_TASK_ANALYZE_IMAGE,
    AI_TASK_EDIT_IMAGE,
    AI_TASK_GENERATE_IMAGE,
    AI_TASK_PROMPT_ASSIST,
    AIResult,
    AISettingsStore,
    AITask,
    AIWorker,
)
from nsy_broadcasting_platform.ui.theme import AI_DIALOG_QSS, HaulixTokens


class AIResultImageLabel(QLabel):
    """AI 结果预览图。用户可以拖到无限画布中生成图片图层。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._image_path = ""
        self._drag_start = QPoint()
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(320, 210)
        self.setText("AI 结果图片会显示在这里")
        self.setStyleSheet(
            f"background:{HaulixTokens.bg_soft};border:1px solid {HaulixTokens.border};"
            f"border-radius:18px;color:{HaulixTokens.text_weak};"
        )

    def set_image_path(self, path: str) -> None:
        self._image_path = path
        if not path:
            self.setPixmap(QPixmap())
            self.setText("AI 结果图片会显示在这里")
            return
        pixmap = QPixmap(path)
        if pixmap.isNull():
            self.setText("结果图片无法读取")
            return
        self.setPixmap(
            pixmap.scaled(
                self.size(),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.setText("")

    def resizeEvent(self, event):  # noqa: N802
        super().resizeEvent(event)
        if self._image_path:
            self.set_image_path(self._image_path)

    def mousePressEvent(self, event):  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_start = event.position().toPoint()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):  # noqa: N802
        if not self._image_path or not (event.buttons() & Qt.MouseButton.LeftButton):
            return super().mouseMoveEvent(event)
        if (event.position().toPoint() - self._drag_start).manhattanLength() < 8:
            return
        mime = QMimeData()
        payload = {
            "kind": "ai_image",
            "image_path": self._image_path,
            "name": Path(self._image_path).stem,
        }
        mime.setData("application/x-nsy-ai-result", json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        pixmap = QPixmap(self._image_path)
        if not pixmap.isNull():
            drag.setPixmap(
                pixmap.scaled(
                    160,
                    100,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            )
        drag.exec(Qt.DropAction.CopyAction)


class AIModelWorkbenchDialog(QDialog):
    """大模型图像工作台：负责提交任务、预览结果，并将结果转成导播图层。"""

    def __init__(
        self,
        *,
        settings: AISettingsStore,
        output_root: str | Path,
        current_frame_provider: Callable[[str], QImage | None],
        add_image_layer: Callable[[str], bool],
        sync_selected_layer: Callable[[str], bool],
        send_to_canvas: Callable[[str], bool],
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("AI 大模型图像工作台")
        self.resize(920, 720)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setStyleSheet(AI_DIALOG_QSS)
        self.settings = settings
        self.current_frame_provider = current_frame_provider
        self.add_image_layer = add_image_layer
        self.sync_selected_layer = sync_selected_layer
        self.send_to_canvas = send_to_canvas
        self.worker = AIWorker(settings, output_root)
        self.worker.task_started.connect(self._on_task_started)
        self.worker.task_finished.connect(self._on_task_finished)
        self._last_result: AIResult | None = None
        self._input_image_path = ""
        self._build_ui()
        self._load_provider_settings()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(18, 18, 18, 18)
        root.setSpacing(12)

        header = QFrame()
        header.setObjectName("AIFeatureHeader")
        header_layout = QVBoxLayout(header)
        title = QLabel("AI 大模型图像工作台")
        title.setStyleSheet(
            f"font-size:19px;font-weight:800;letter-spacing:-0.2px;color:{HaulixTokens.text};"
        )
        desc = QLabel("Gemini 用于图片生成和图片编辑；DeepSeek 用于提示词、文本分析和导播建议。所有联网任务都在后台线程中执行。")
        desc.setWordWrap(True)
        desc.setStyleSheet(f"color:{HaulixTokens.text_muted};")
        header_layout.addWidget(title)
        header_layout.addWidget(desc)
        root.addWidget(header)

        form_card = QFrame()
        form_card.setObjectName("AIFeatureCard")
        form = QFormLayout(form_card)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.provider_combo = QComboBox()
        self.provider_combo.addItem("Gemini", "gemini")
        self.provider_combo.addItem("DeepSeek", "deepseek")
        form.addRow("模型供应商", self.provider_combo)

        self.task_combo = QComboBox()
        self.task_combo.addItem("文本生成图片", AI_TASK_GENERATE_IMAGE)
        self.task_combo.addItem("根据当前画面编辑图片", AI_TASK_EDIT_IMAGE)
        self.task_combo.addItem("分析当前画面", AI_TASK_ANALYZE_IMAGE)
        self.task_combo.addItem("提示词优化 / 场景建议", AI_TASK_PROMPT_ASSIST)
        form.addRow("任务类型", self.task_combo)

        self.model_edit = QLineEdit()
        form.addRow("模型名称", self.model_edit)

        self.base_url_edit = QLineEdit()
        form.addRow("接口地址", self.base_url_edit)

        self.key_edit = QLineEdit()
        self.key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow("API Key", self.key_edit)

        key_row = QHBoxLayout()
        self.save_key_btn = QPushButton("保存 API Key")
        self.save_key_btn.setProperty("role", "primary")
        key_row.addWidget(self.save_key_btn)
        key_row.addStretch(1)
        form.addRow("", key_row)

        self.frame_source_combo = QComboBox()
        self.frame_source_combo.addItem("编辑预览画面", "edit")
        self.frame_source_combo.addItem("节目输出画面", "program")
        form.addRow("输入画面", self.frame_source_combo)

        input_row = QHBoxLayout()
        self.capture_frame_btn = QPushButton("捕获当前画面")
        self.choose_image_btn = QPushButton("选择本地图片")
        self.input_path_label = QLabel("未选择输入图")
        self.input_path_label.setWordWrap(True)
        input_row.addWidget(self.capture_frame_btn)
        input_row.addWidget(self.choose_image_btn)
        input_row.addWidget(self.input_path_label, 1)
        form.addRow("输入图片", input_row)
        root.addWidget(form_card)

        prompt_card = QFrame()
        prompt_card.setObjectName("AIFeatureCard")
        prompt_layout = QVBoxLayout(prompt_card)
        prompt_layout.addWidget(QLabel("提示词"))
        self.prompt_edit = QTextEdit()
        self.prompt_edit.setPlaceholderText("例如：生成一张科技感直播背景；或：把当前画面背景改成蓝色演播室风格。")
        self.prompt_edit.setMinimumHeight(110)
        prompt_layout.addWidget(self.prompt_edit)
        run_row = QHBoxLayout()
        self.run_btn = QPushButton("开始执行")
        self.run_btn.setObjectName("PrimaryAction")
        self.status_label = QLabel("等待任务。")
        self.status_label.setObjectName("AIFeatureStatus")
        run_row.addWidget(self.run_btn)
        run_row.addWidget(self.status_label, 1)
        prompt_layout.addLayout(run_row)
        root.addWidget(prompt_card)

        result_card = QFrame()
        result_card.setObjectName("AIFeatureCard")
        result_layout = QHBoxLayout(result_card)
        self.preview = AIResultImageLabel()
        result_layout.addWidget(self.preview, 1)

        right = QVBoxLayout()
        right.addWidget(QLabel("文本结果 / 提示词建议"))
        self.result_text = QTextEdit()
        self.result_text.setReadOnly(True)
        right.addWidget(self.result_text, 1)

        action_row = QHBoxLayout()
        self.add_layer_btn = QPushButton("加入当前场景")
        self.sync_layer_btn = QPushButton("同步到选中图层")
        self.canvas_btn = QPushButton("发送到画布")
        self.add_layer_btn.setProperty("role", "primary")
        self.sync_layer_btn.setProperty("role", "toolbar")
        self.canvas_btn.setProperty("role", "toolbar")
        action_row.addWidget(self.add_layer_btn)
        action_row.addWidget(self.sync_layer_btn)
        action_row.addWidget(self.canvas_btn)
        right.addLayout(action_row)
        result_layout.addLayout(right, 1)
        root.addWidget(result_card, 1)

        self.provider_combo.currentIndexChanged.connect(self._load_provider_settings)
        self.save_key_btn.clicked.connect(self._save_provider_settings)
        self.capture_frame_btn.clicked.connect(self._capture_current_frame)
        self.choose_image_btn.clicked.connect(self._choose_input_image)
        self.run_btn.clicked.connect(self._submit_task)
        self.add_layer_btn.clicked.connect(self._add_result_to_scene)
        self.sync_layer_btn.clicked.connect(self._sync_result_to_selected_layer)
        self.canvas_btn.clicked.connect(self._send_result_to_canvas)
        self._set_result_actions_enabled(False)

    def _provider_key(self) -> str:
        return str(self.provider_combo.currentData() or "gemini")

    def _load_provider_settings(self) -> None:
        settings = self.settings.get(self._provider_key())
        self.model_edit.setText(settings.model)
        self.base_url_edit.setText(settings.base_url)
        self.key_edit.setText(settings.api_key)

    def _save_provider_settings(self) -> None:
        self.settings.update(
            self._provider_key(),
            api_key=self.key_edit.text().strip(),
            model=self.model_edit.text().strip(),
            base_url=self.base_url_edit.text().strip(),
        )
        self.status_label.setText("API Key 和模型配置已保存。")

    def _set_result_actions_enabled(self, enabled: bool) -> None:
        for button in (self.add_layer_btn, self.sync_layer_btn, self.canvas_btn):
            button.setEnabled(enabled)

    def _capture_current_frame(self) -> str:
        image = self.current_frame_provider(str(self.frame_source_combo.currentData() or "edit"))
        if image is None or image.isNull():
            QMessageBox.warning(self, "提示", "当前没有可用画面。")
            return ""
        out_dir = Path("outputs") / "ai_inputs" / time.strftime("%Y%m%d")
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"input_{int(time.time() * 1000)}.png"
        if not image.save(str(path), "PNG"):
            QMessageBox.warning(self, "提示", "当前画面保存失败。")
            return ""
        self._input_image_path = str(path)
        self.input_path_label.setText(self._input_image_path)
        return self._input_image_path

    def _choose_input_image(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self,
            "选择输入图片",
            str(Path.cwd()),
            "图片文件 (*.png *.jpg *.jpeg *.bmp *.webp);;全部文件 (*.*)",
        )
        if not path:
            return
        self._input_image_path = path
        self.input_path_label.setText(path)

    def _submit_task(self) -> None:
        self._save_provider_settings()
        task_type = str(self.task_combo.currentData() or AI_TASK_GENERATE_IMAGE)
        if task_type in {AI_TASK_EDIT_IMAGE, AI_TASK_ANALYZE_IMAGE} and not self._input_image_path:
            self._capture_current_frame()
        prompt = self.prompt_edit.toPlainText().strip()
        if not prompt:
            QMessageBox.warning(self, "提示", "请输入提示词。")
            return
        task = AITask(
            provider=self._provider_key(),
            task_type=task_type,
            prompt=prompt,
            input_image_path=self._input_image_path,
            model=self.model_edit.text().strip(),
        )
        self.worker.submit(task)

    def _on_task_started(self, task: AITask) -> None:
        self.run_btn.setEnabled(False)
        self.status_label.setText(f"正在执行：{task.provider} / {task.task_type}")
        self._set_result_actions_enabled(False)

    def _on_task_finished(self, result: AIResult) -> None:
        self.run_btn.setEnabled(True)
        self._last_result = result
        state = "完成" if result.ok else "失败"
        self.status_label.setText(f"{state} | {result.message} | {result.elapsed_ms:.0f} ms")
        self.result_text.setPlainText(result.text or result.message)
        first_image = result.first_image_path
        self.preview.set_image_path(first_image)
        self._set_result_actions_enabled(bool(first_image))

    def _result_image_path(self) -> str:
        return self._last_result.first_image_path if self._last_result is not None else ""

    def _add_result_to_scene(self) -> None:
        path = self._result_image_path()
        if path and self.add_image_layer(path):
            self.status_label.setText("AI 图片已作为新图层加入当前场景。")

    def _sync_result_to_selected_layer(self) -> None:
        path = self._result_image_path()
        if path and self.sync_selected_layer(path):
            self.status_label.setText("AI 图片已同步到选中图层。")

    def _send_result_to_canvas(self) -> None:
        path = self._result_image_path()
        if path and self.send_to_canvas(path):
            self.status_label.setText("AI 图片已发送到无限画布。")

    def closeEvent(self, event):  # noqa: N802
        try:
            self.worker.shutdown()
        except Exception:
            pass
        super().closeEvent(event)

