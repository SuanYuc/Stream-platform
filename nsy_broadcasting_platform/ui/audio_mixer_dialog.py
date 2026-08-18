from __future__ import annotations

import math
from collections import deque
from typing import Callable

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSlider,
    QVBoxLayout,
    QWidget,
)

from nsy_broadcasting_platform.models import AudioTrack
from nsy_broadcasting_platform.ui.theme import HAULIX_APP_QSS, T


class AudioWaveformWidget(QWidget):
    """轻量声纹显示，只重绘自身，不参与音频处理链路。"""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setMinimumSize(72, 78)
        self._history: deque[float] = deque([0.0] * 32, maxlen=32)
        self._active = False

    def set_level(self, level: float, active: bool) -> None:
        value = max(0.0, min(1.0, float(level)))
        self._active = bool(active)
        self._history.append(value if active else value * 0.18)
        self.update()

    def paintEvent(self, event):  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        rect = self.rect().adjusted(4, 4, -4, -4)
        painter.fillRect(rect, QColor(T.field))
        painter.setPen(QPen(QColor(T.border), 1))
        painter.drawRoundedRect(rect, 8, 8)

        values = list(self._history)
        if not values:
            return
        center_y = rect.center().y()
        bar_w = max(2.0, rect.width() / max(1, len(values)) * 0.58)
        gap = max(1.0, rect.width() / max(1, len(values)) * 0.42)
        x = rect.left() + 5
        active_color = QColor(T.amber if self._active else T.green)
        idle_color = QColor(T.border)
        for i, level in enumerate(values):
            shaped = math.sqrt(max(0.0, level))
            half_h = max(2.0, shaped * rect.height() * 0.42)
            color = QColor(active_color if self._active or i > len(values) - 5 else idle_color)
            color.setAlpha(210 if self._active else 135)
            painter.setPen(QPen(color, bar_w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(int(x), int(center_y - half_h), int(x), int(center_y + half_h))
            x += bar_w + gap


class MixerChannelStrip(QFrame):
    """单条音轨通道条：声纹、电平、推子、EQ、静音与采集选择。"""

    params_changed = pyqtSignal(str, float, bool, float, float, float, float)
    selected_requested = pyqtSignal(str)

    def __init__(self, track: AudioTrack, active: bool = False, parent=None) -> None:
        super().__init__(parent)
        self.track_id = track.id
        self._track = track.clone()
        self._active = active
        self.setObjectName("MixerChannel")
        self.setMinimumWidth(164)
        self.setMaximumWidth(190)
        self._build_ui()
        self.set_track(track, active)

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        self.name_label = QLabel("音轨")
        self.name_label.setObjectName("MixerChannelTitle")
        self.name_label.setWordWrap(True)
        root.addWidget(self.name_label)

        self.kind_label = QLabel("来源")
        self.kind_label.setObjectName("StatusLabel")
        root.addWidget(self.kind_label)

        self.waveform = AudioWaveformWidget()
        root.addWidget(self.waveform)

        self.level_label = QLabel("-∞ dB")
        self.level_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        root.addWidget(self.level_label)

        fader_row = QHBoxLayout()
        fader_row.setSpacing(8)
        self.volume_slider = QSlider(Qt.Orientation.Vertical)
        self.volume_slider.setRange(0, 200)
        self.volume_slider.setMinimumHeight(168)
        self.amp_slider = QSlider(Qt.Orientation.Vertical)
        self.amp_slider.setRange(0, 200)
        self.amp_slider.setMinimumHeight(168)
        fader_row.addWidget(self._vertical_slider_box("音量", self.volume_slider))
        fader_row.addWidget(self._vertical_slider_box("幅度", self.amp_slider))
        root.addLayout(fader_row)

        eq_grid = QGridLayout()
        eq_grid.setHorizontalSpacing(6)
        eq_grid.setVerticalSpacing(4)
        self.low_slider = self._eq_slider()
        self.mid_slider = self._eq_slider()
        self.high_slider = self._eq_slider()
        for col, (label, slider) in enumerate((("低", self.low_slider), ("中", self.mid_slider), ("高", self.high_slider))):
            eq_grid.addWidget(QLabel(label), 0, col, alignment=Qt.AlignmentFlag.AlignCenter)
            eq_grid.addWidget(slider, 1, col)
        root.addLayout(eq_grid)

        self.mute_check = QCheckBox("静音")
        root.addWidget(self.mute_check)

        self.select_btn = QPushButton("设为采集")
        self.select_btn.setProperty("role", "primary")
        root.addWidget(self.select_btn)

        for slider in (self.volume_slider, self.amp_slider, self.low_slider, self.mid_slider, self.high_slider):
            slider.valueChanged.connect(self._emit_params)
        self.mute_check.toggled.connect(self._emit_params)
        self.select_btn.clicked.connect(lambda: self.selected_requested.emit(self.track_id))

    @staticmethod
    def _eq_slider() -> QSlider:
        slider = QSlider(Qt.Orientation.Vertical)
        slider.setRange(0, 200)
        slider.setMinimumHeight(92)
        return slider

    @staticmethod
    def _vertical_slider_box(title: str, slider: QSlider) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(title)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(label)
        layout.addWidget(slider, 1)
        return box

    def set_track(self, track: AudioTrack, active: bool) -> None:
        self._track = track.clone()
        self._active = bool(active)
        self.setProperty("active", self._active)
        self.style().unpolish(self)
        self.style().polish(self)
        self.name_label.setText(track.name)
        self.kind_label.setText("当前采集" if active else track.kind.value)
        self._set_slider(self.volume_slider, track.volume)
        self._set_slider(self.amp_slider, track.amplitude)
        self._set_slider(self.low_slider, track.low_gain)
        self._set_slider(self.mid_slider, track.mid_gain)
        self._set_slider(self.high_slider, track.high_gain)
        self.mute_check.blockSignals(True)
        self.mute_check.setChecked(track.muted)
        self.mute_check.blockSignals(False)

    @staticmethod
    def _set_slider(slider: QSlider, value: float) -> None:
        slider.blockSignals(True)
        slider.setValue(max(0, min(200, int(round(float(value) * 100)))))
        slider.blockSignals(False)

    def _emit_params(self) -> None:
        self.params_changed.emit(
            self.track_id,
            self.volume_slider.value() / 100.0,
            self.mute_check.isChecked(),
            self.amp_slider.value() / 100.0,
            self.low_slider.value() / 100.0,
            self.mid_slider.value() / 100.0,
            self.high_slider.value() / 100.0,
        )

    def set_level(self, level: float, active: bool) -> None:
        level = max(0.0, min(1.0, float(level)))
        self.waveform.set_level(level, active)
        if active and level > 0.0001:
            db = 20.0 * math.log10(max(level, 0.0001))
            self.level_label.setText(f"{db:0.1f} dB")
        elif active:
            self.level_label.setText("-∞ dB")
        else:
            self.level_label.setText("待机")


class AudioMixerDialog(QDialog):
    """数字调音台窗口，展示所有可采集音轨并集中调节参数。"""

    closed = pyqtSignal()
    track_params_changed = pyqtSignal(str, float, bool, float, float, float, float)
    track_selected = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("音轨数字调音台")
        self.resize(980, 620)
        self.setMinimumSize(760, 460)
        self.setModal(False)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setStyleSheet(HAULIX_APP_QSS + MIXER_QSS)
        self._strips: dict[str, MixerChannelStrip] = {}
        self._active_track_id = ""
        self._build_ui()

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        header = QHBoxLayout()
        title = QLabel("数字音轨调音台")
        title.setObjectName("MixerTitle")
        self.active_label = QLabel("当前采集: 未选择")
        self.active_label.setObjectName("StatusLabel")
        header.addWidget(title)
        header.addStretch(1)
        header.addWidget(self.active_label)
        root.addLayout(header)

        self.tip_label = QLabel("每条通道可独立设置音量、幅度、三段 EQ 和静音；声纹显示当前采集音轨的实时电平。")
        self.tip_label.setObjectName("MixerHint")
        self.tip_label.setWordWrap(True)
        root.addWidget(self.tip_label)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setObjectName("MixerScrollArea")
        self.channel_host = QWidget()
        self.channel_layout = QHBoxLayout(self.channel_host)
        self.channel_layout.setContentsMargins(8, 8, 8, 8)
        self.channel_layout.setSpacing(10)
        self.channel_layout.addStretch(1)
        scroll.setWidget(self.channel_host)
        root.addWidget(scroll, 1)

    def set_tracks(self, tracks: list[AudioTrack], active_track_id: str) -> None:
        self._active_track_id = active_track_id or ""
        seen = set()
        for track in tracks:
            seen.add(track.id)
            strip = self._strips.get(track.id)
            active = track.id == self._active_track_id
            if strip is None:
                strip = MixerChannelStrip(track, active)
                strip.params_changed.connect(self.track_params_changed.emit)
                strip.selected_requested.connect(self.track_selected.emit)
                self._strips[track.id] = strip
                self.channel_layout.insertWidget(max(0, self.channel_layout.count() - 1), strip)
            else:
                strip.set_track(track, active)
        for track_id in list(self._strips):
            if track_id in seen:
                continue
            strip = self._strips.pop(track_id)
            self.channel_layout.removeWidget(strip)
            strip.deleteLater()
        active_name = next((track.name for track in tracks if track.id == self._active_track_id), "未选择")
        self.active_label.setText(f"当前采集: {active_name}")

    def update_level(self, active_track_id: str, level: float) -> None:
        self._active_track_id = active_track_id or self._active_track_id
        for track_id, strip in self._strips.items():
            strip.set_level(level if track_id == self._active_track_id else 0.0, track_id == self._active_track_id)

    def closeEvent(self, event):  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)


MIXER_QSS = f"""
QDialog {{
    background: {T.bg};
}}
QLabel#MixerTitle {{
    color: {T.text};
    font-size: 19px;
    font-weight: 900;
    letter-spacing: -0.2px;
}}
QLabel#MixerHint {{
    background: {T.panel_raised};
    border: 1px solid {T.border};
    border-radius: 16px;
    color: {T.text_muted};
    padding: 10px 12px;
}}
QFrame#MixerChannel {{
    background: {T.panel};
    border: 1px solid {T.border};
    border-radius: 20px;
}}
QFrame#MixerChannel[active="true"] {{
    background: {T.panel_warm};
    border: 2px solid {T.accent};
}}
QLabel#MixerChannelTitle {{
    color: {T.text};
    font-size: 14px;
    font-weight: 900;
}}
QScrollArea#MixerScrollArea {{
    background: {T.panel};
    border: 1px solid {T.border};
    border-radius: 18px;
}}
"""
