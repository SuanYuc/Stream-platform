from __future__ import annotations

from PyQt6.QtCore import QSize, Qt
from PyQt6.QtGui import QImage, QPixmap
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QVBoxLayout, QWidget

from nsy_broadcasting_platform.ui.theme import (
    SCENE_CARD_NORMAL_QSS,
    SCENE_CARD_RECOMMENDED_QSS,
    SCENE_CARD_SELECTED_QSS,
)


class SceneItemWidget(QWidget):
    def __init__(self, scene_name: str) -> None:
        super().__init__()
        self.setObjectName("SceneCard")
        self._preview_image: QImage | None = None
        self._selected = False
        self._ai_score: float | None = None
        self._ai_reason = ""
        self.thumb_label = QLabel()
        self.thumb_label.setObjectName("SceneThumb")
        self.thumb_label.setFixedSize(160, 90)
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.name_label = QLabel(scene_name)
        self.name_label.setObjectName("SceneName")
        self.name_label.setWordWrap(True)
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)

        self.badge_label = QLabel("当前")
        self.badge_label.setObjectName("SceneBadge")
        self.badge_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.badge_label.hide()

        self.ai_badge_label = QLabel("AI 推荐")
        self.ai_badge_label.setObjectName("SceneAiBadge")
        self.ai_badge_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.ai_badge_label.hide()

        root = QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        root.addWidget(self.thumb_label, 0, Qt.AlignmentFlag.AlignCenter)
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addWidget(self.name_label, 1)
        footer.addWidget(self.ai_badge_label, 0, Qt.AlignmentFlag.AlignRight)
        footer.addWidget(self.badge_label, 0, Qt.AlignmentFlag.AlignRight)
        root.addLayout(footer)

        self.set_selected(False)

    def set_scene_name(self, name: str) -> None:
        self.name_label.setText(name)

    def set_preview_image(self, image: QImage | None) -> None:
        self._preview_image = None if image is None or image.isNull() else image.copy()
        if image is None or image.isNull():
            self.thumb_label.clear()
            self.thumb_label.setText("暂无预览")
            return
        pix = QPixmap.fromImage(image).scaled(
            self.thumb_label.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.thumb_label.setText("")
        self.thumb_label.setPixmap(pix)

    def current_preview_image(self) -> QImage | None:
        if self._preview_image is None or self._preview_image.isNull():
            return None
        return self._preview_image.copy()

    def sizeHint(self) -> QSize:  # noqa: D401
        return QSize(208, 156)

    def set_selected(self, selected: bool) -> None:
        self._selected = bool(selected)
        self.badge_label.setVisible(selected)
        self._apply_state_style()

    def set_ai_recommendation(self, score: float | None, reason: str = "") -> None:
        self._ai_score = score if score is not None and score >= 0 else None
        self._ai_reason = reason or ""
        if self._ai_score is None:
            self.ai_badge_label.hide()
            self.ai_badge_label.setToolTip("")
        else:
            self.ai_badge_label.setText(f"推荐 {self._ai_score * 100:.0f}%")
            self.ai_badge_label.setToolTip(self._ai_reason)
            self.ai_badge_label.show()
        self._apply_state_style()

    def _apply_state_style(self) -> None:
        if self._selected:
            self.setStyleSheet(SCENE_CARD_SELECTED_QSS)
        elif self._ai_score is not None:
            self.setStyleSheet(SCENE_CARD_RECOMMENDED_QSS)
        else:
            self.setStyleSheet(SCENE_CARD_NORMAL_QSS)
