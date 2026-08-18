from __future__ import annotations

"""
Quickish-inspired professional dashboard theme.

This token set mirrors the public quickish.website visual language:
deep graphite backgrounds, one clear green primary accent, crisp borders,
pill controls, compact status chips, and hard-edged card lift without blur.
"""


class QuickishTokens:
    sans = '"Hanken Grotesk", "Segoe UI", "Microsoft YaHei UI", "PingFang SC", sans-serif'
    mono = '"JetBrains Mono", "Cascadia Mono", "SFMono-Regular", Consolas, monospace'
    bg = "#0D0D0F"
    bg_soft = "#111214"
    panel = "#141416"
    panel_raised = "#1C1C1F"
    panel_warm = "#202226"
    field = "#101113"
    border = "#2B2D31"
    border_soft = "#1F2126"
    text = "#FBFBF9"
    text_muted = "#8A8A8A"
    text_weak = "#686B70"
    accent = "#4AC777"
    accent_hover = "#5FD78E"
    accent_deep = "#22864A"
    accent_text = "#4AC777"
    amber = accent
    amber_hover = accent_hover
    amber_dark = accent_deep
    blue = "#5C7DB7"
    blue_hover = "#7E9DD7"
    green = "#6FA56D"
    danger = "#A35655"
    danger_dark = "#3E1E1E"


HaulixTokens = QuickishTokens
T = QuickishTokens


QUICKISH_APP_QSS = f"""
QMainWindow, QDialog, QWidget#RootPanel {{
    background: {T.bg};
    color: {T.text};
    font-size: 13px;
    font-family: {T.sans};
}}
QWidget {{
    selection-background-color: {T.accent};
    selection-color: {T.text};
}}
QSplitter::handle {{
    background: {T.border_soft};
    width: 6px;
}}
QSplitter::handle:vertical {{
    height: 6px;
}}
QSplitter::handle:hover {{
    background: {T.accent_deep};
}}
QFrame#MainHero, QFrame#CanvasDockPanel, QFrame#CanvasStatusBar,
QGroupBox, QFrame#CanvasSingleBox, QFrame#CanvasMultiBox, QFrame#CanvasOpsBox,
QFrame#CanvasAudioBox, QFrame#CanvasFilterBox, QFrame#CanvasABox,
QFrame#CanvasSourceButtons {{
    background: {T.panel};
    border: 1px solid {T.border};
    border-radius: 18px;
}}
QFrame#MainHero {{
    background: {T.panel_raised};
    border-color: {T.border};
    border-radius: 22px;
}}
QLabel#HeroTitle {{
    color: {T.text};
    font-size: 20px;
    font-weight: 900;
    letter-spacing: -0.4px;
}}
QLabel#HeroSubtitle {{
    color: {T.text_muted};
    font-size: 12px;
    font-weight: 600;
}}
QLabel#HeroChip {{
    background: {T.bg_soft};
    border: 1px solid {T.border};
    border-radius: 999px;
    color: {T.text_muted};
    padding: 6px 12px;
    font-family: {T.mono};
    font-weight: 800;
}}
QLabel#SectionKicker {{
    color: {T.text_muted};
    font-size: 11px;
    font-weight: 800;
    letter-spacing: 0.8px;
}}
QFrame#CanvasDockPanel {{
    background: {T.panel_raised};
    border-color: {T.border};
}}
QFrame#CanvasStatusBar {{
    background: {T.bg_soft};
    border-radius: 14px;
}}
QGroupBox {{
    margin-top: 16px;
    padding: 18px 14px 14px 14px;
}}
QGroupBox::title {{
    subcontrol-origin: margin;
    left: 14px;
    padding: 0 8px;
    color: {T.text_muted};
    background: {T.bg};
    font-size: 12px;
    font-weight: 800;
    letter-spacing: 0.6px;
}}
QGroupBox#CollapsedPanel {{
    background: {T.panel_raised};
    border: 1px dashed {T.accent_deep};
    border-radius: 18px;
    margin-top: 8px;
    padding: 10px;
}}
QGroupBox#CollapsedPanel::title {{
    color: {T.accent_hover};
}}
QGroupBox#AIFocusBox {{
    background: {T.panel_warm};
    border: 1px solid {T.blue};
    border-radius: 20px;
}}
QGroupBox#AIFocusBox::title {{
    color: {T.text};
    background: {T.blue};
    border: 1px solid {T.blue_hover};
    border-radius: 999px;
    padding: 4px 12px;
    font-weight: 800;
}}
QLabel {{
    color: {T.text};
    background: transparent;
}}
QLabel#StatusLabel, QLabel#CurrentSceneLabel, QLabel#StreamStatusBadge,
QLabel#RecordStatusBadge, QLabel#CanvasInfo, QLabel#CanvasSelectionInfo {{
    background: {T.bg_soft};
    border: 1px solid {T.border};
    border-radius: 999px;
    padding: 6px 11px;
    color: {T.text_muted};
    font-weight: 700;
    font-family: {T.mono};
}}
QLabel#FaceStatusText, QLabel#LayerMetrics {{
    color: {T.blue_hover};
    font-weight: 700;
}}
QLabel#CanvasTitle {{
    color: {T.text};
    font-size: 18px;
    font-weight: 800;
    letter-spacing: -0.2px;
}}
QPushButton {{
    background: {T.panel_raised};
    border: 1px solid {T.border};
    border-radius: 16px;
    color: {T.text};
    padding: 8px 16px;
    font-weight: 700;
    min-height: 32px;
    letter-spacing: 0.1px;
}}
QPushButton:hover {{
    background: {T.panel_warm};
    border-color: {T.accent_hover};
}}
QPushButton:pressed {{
    background: {T.accent_deep};
    border-color: {T.accent_hover};
}}
QPushButton:disabled {{
    background: {T.bg_soft};
    color: {T.text_weak};
    border-color: {T.border_soft};
}}
QPushButton[role="primary"] {{
    background: {T.accent};
    border-color: {T.accent_hover};
    color: #0B0B0C;
}}
QPushButton[role="primary"]:hover {{
    background: {T.accent_hover};
}}
QPushButton[role="danger"] {{
    background: {T.danger_dark};
    border-color: {T.danger};
    color: #FFE7E3;
}}
QPushButton[role="danger"]:hover {{
    background: #563030;
    border-color: #C06D67;
}}
QPushButton[role="toggle"]:checked {{
    background: #18261F;
    border-color: {T.accent};
    color: #E8F8ED;
}}
QPushButton[role="toolbar"] {{
    background: {T.bg_soft};
    border-color: {T.border};
    color: {T.text_muted};
    padding: 6px 12px;
    min-height: 28px;
    border-radius: 12px;
}}
QPushButton[role="toolbar"]:hover, QPushButton[role="toolbar"]:checked {{
    background: {T.panel_warm};
    border-color: {T.accent};
    color: {T.text};
}}
QPushButton[role="compact"] {{
    background: {T.bg_soft};
    border-color: {T.border};
    color: {T.text_muted};
    padding: 5px 11px;
    min-height: 26px;
    font-size: 12px;
    border-radius: 12px;
}}
QPushButton[role="compact"]:hover, QPushButton[role="compact"]:checked {{
    background: {T.panel_warm};
    border-color: {T.accent};
    color: {T.text};
}}
QPushButton[accent="ai"] {{
    background: #17283A;
    border: 1px solid {T.blue};
    color: #E9F2FF;
    font-weight: 800;
    padding: 8px 15px;
    border-radius: 16px;
}}
QPushButton[accent="ai"]:hover {{
    background: #203B56;
    border-color: {T.blue_hover};
}}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QTextEdit {{
    background: {T.field};
    border: 1px solid {T.border};
    border-radius: 14px;
    color: {T.text};
    padding: 7px 10px;
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QTextEdit:focus {{
    border-color: {T.accent};
    background: {T.panel_raised};
}}
QComboBox::drop-down {{
    border: none;
    width: 24px;
}}
QComboBox QAbstractItemView {{
    background: {T.panel};
    border: 1px solid {T.border};
    selection-background-color: {T.accent_deep};
    color: {T.text};
}}
QListWidget {{
    background: {T.bg_soft};
    border: 1px solid {T.border};
    border-radius: 18px;
    padding: 8px;
    outline: 0;
}}
QListWidget::item {{
    border: none;
    background: transparent;
    padding: 4px;
}}
QListWidget::item:selected {{
    background: {T.accent_deep};
    color: {T.text};
    border-radius: 10px;
}}
QListWidget::item:hover {{
    background: {T.panel_warm};
    border-radius: 10px;
}}
QMenu {{
    background: {T.panel_raised};
    border: 1px solid {T.border};
    border-radius: 14px;
    color: {T.text};
    padding: 6px;
}}
QMenu::item {{
    padding: 7px 22px 7px 12px;
    border-radius: 8px;
}}
QMenu::item:selected {{
    background: {T.accent_deep};
    color: {T.text};
}}
QScrollArea {{
    background: transparent;
    border: none;
}}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {T.border};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{
    background: {T.accent_deep};
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
    height: 0;
}}
QScrollBar:horizontal {{
    background: transparent;
    height: 10px;
    margin: 2px;
}}
QScrollBar::handle:horizontal {{
    background: {T.border};
    border-radius: 5px;
    min-width: 24px;
}}
QScrollBar::handle:horizontal:hover {{
    background: {T.accent_deep};
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{
    width: 0;
}}
QSlider::groove:horizontal {{
    height: 4px;
    border-radius: 2px;
    background: {T.border_soft};
}}
QSlider::sub-page:horizontal {{
    background: {T.accent};
    border-radius: 2px;
}}
QSlider::handle:horizontal {{
    width: 16px;
    margin: -6px 0;
    border-radius: 8px;
    border: 1px solid {T.accent_hover};
    background: {T.text};
}}
QSlider::groove:vertical {{
    width: 4px;
    border-radius: 2px;
    background: {T.border_soft};
}}
QSlider::sub-page:vertical {{
    background: {T.accent};
    border-radius: 2px;
}}
QSlider::handle:vertical {{
    height: 16px;
    margin: 0 -6px;
    border-radius: 8px;
    border: 1px solid {T.accent_hover};
    background: {T.text};
}}
QCheckBox {{
    spacing: 7px;
    color: {T.text_muted};
}}
QCheckBox::indicator {{
    width: 15px;
    height: 15px;
    border-radius: 5px;
    border: 1px solid {T.border};
    background: {T.field};
}}
QCheckBox::indicator:checked {{
    background: {T.accent};
    border-color: {T.accent_hover};
}}
QProgressBar {{
    background: {T.field};
    border: 1px solid {T.border};
    border-radius: 7px;
    color: {T.text_muted};
    text-align: center;
}}
QProgressBar::chunk {{
    background: {T.accent};
    border-radius: 6px;
}}
QStatusBar {{
    background: {T.bg_soft};
    color: {T.text_muted};
    border-top: 1px solid {T.border};
}}
QToolTip {{
    background: {T.panel_raised};
    color: {T.text};
    border: 1px solid {T.border};
    border-radius: 10px;
    padding: 6px 8px;
}}
QTabWidget::pane {{
    background: {T.panel};
    border: 1px solid {T.border};
    border-radius: 18px;
    top: -1px;
}}
QTabBar::tab {{
    background: {T.bg_soft};
    color: {T.text_muted};
    border: 1px solid {T.border};
    border-bottom: none;
    border-radius: 999px;
    padding: 7px 14px;
    margin-right: 6px;
}}
QTabBar::tab:selected {{
    background: {T.accent};
    color: #0B0B0C;
    border-color: {T.accent_hover};
}}
"""


PREVIEW_FRAME_STYLE = (
    f"background:{T.bg_soft}; border:1px solid {T.border}; "
    f"border-radius:16px; color:{T.text_weak};"
)

SCENE_CARD_SELECTED_QSS = f"""
QWidget#SceneCard {{ background:{T.panel_raised}; border:2px solid {T.accent}; border-radius:18px; }}
QLabel#SceneThumb {{ background:{T.bg_soft}; border:1px solid {T.border_soft}; border-radius:14px; color:{T.text_weak}; }}
QLabel#SceneName {{ color:{T.text}; font-weight:800; }}
QLabel#SceneBadge {{ background:{T.accent}; border:1px solid {T.accent_hover}; border-radius:12px; color:#0B0B0C; font-weight:800; padding:2px 10px; }}
QLabel#SceneAiBadge {{ background:#1B3148; border:1px solid {T.blue_hover}; border-radius:12px; color:#E8F1FF; font-weight:800; padding:2px 10px; }}
"""

SCENE_CARD_NORMAL_QSS = f"""
QWidget#SceneCard {{ background:{T.panel}; border:1px solid {T.border}; border-radius:18px; }}
QLabel#SceneThumb {{ background:{T.bg_soft}; border:1px solid {T.border_soft}; border-radius:14px; color:{T.text_weak}; }}
QLabel#SceneName {{ color:{T.text_muted}; font-weight:700; }}
QLabel#SceneBadge {{ background:{T.bg_soft}; border:1px solid {T.border}; border-radius:12px; color:{T.text_weak}; padding:2px 10px; }}
QLabel#SceneAiBadge {{ background:#162636; border:1px solid {T.blue}; border-radius:12px; color:#C8D9F3; font-weight:800; padding:2px 10px; }}
"""

SCENE_CARD_RECOMMENDED_QSS = f"""
QWidget#SceneCard {{ background:{T.panel_raised}; border:2px solid {T.blue_hover}; border-radius:18px; }}
QLabel#SceneThumb {{ background:{T.bg_soft}; border:1px solid {T.blue}; border-radius:14px; color:{T.text_weak}; }}
QLabel#SceneName {{ color:{T.text}; font-weight:800; }}
QLabel#SceneBadge {{ background:{T.accent}; border:1px solid {T.accent_hover}; border-radius:12px; color:#0B0B0C; font-weight:800; padding:2px 10px; }}
QLabel#SceneAiBadge {{ background:#1B3148; border:1px solid {T.blue_hover}; border-radius:12px; color:#E8F1FF; font-weight:800; padding:2px 10px; }}
"""

LAYER_CARD_SELECTED_QSS = f"""
QWidget#LayerCard {{ background:{T.panel_raised}; border:2px solid {T.accent}; border-radius:16px; }}
QLabel#LayerTitle {{ color:{T.text}; font-weight:800; }}
QLabel#LayerField {{ color:{T.text_muted}; }}
QLabel#LayerMetrics {{ color:{T.blue_hover}; font-weight:700; }}
"""

LAYER_CARD_NORMAL_QSS = f"""
QWidget#LayerCard {{ background:{T.panel}; border:1px solid {T.border}; border-radius:16px; }}
QLabel#LayerTitle {{ color:{T.text_muted}; font-weight:700; }}
QLabel#LayerField {{ color:{T.text_weak}; }}
QLabel#LayerMetrics {{ color:{T.blue}; font-weight:700; }}
"""


def status_badge_qss(kind: str = "idle") -> str:
    if kind == "error":
        return f"background:{T.danger_dark};border:1px solid {T.danger};color:#FFE7E3;border-radius:999px;padding:4px 10px;"
    if kind == "running":
        return f"background:#18261F;border:1px solid {T.accent};color:#E8F8ED;border-radius:999px;padding:4px 10px;"
    return f"background:{T.bg_soft};border:1px solid {T.border};color:{T.text_muted};border-radius:999px;padding:4px 10px;"


AI_DIALOG_QSS = QUICKISH_APP_QSS + f"""
QDialog#AIFeatureDialog {{
    background: {T.panel};
    border: 1px solid {T.blue};
    border-radius: 20px;
}}
QFrame#AIFeatureHeader, QFrame#AIFeatureCard {{
    background: {T.panel_raised};
    border: 1px solid {T.border};
    border-radius: 18px;
}}
QLabel#AIFeatureStatus {{
    background: {T.bg_soft};
    border: 1px solid {T.border};
    border-radius: 14px;
    color: {T.text_muted};
    padding: 10px 12px;
}}
QPushButton#PrimaryAction {{
    background: {T.blue};
    border: 1px solid {T.blue_hover};
    color: {T.text};
}}
QPushButton#PrimaryAction:hover {{
    background: {T.blue_hover};
}}
"""


HAULIX_APP_QSS = QUICKISH_APP_QSS
