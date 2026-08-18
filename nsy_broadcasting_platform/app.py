from __future__ import annotations

from pathlib import Path

from nsy_broadcasting_platform.config import CONFIG
from nsy_broadcasting_platform.gpu_runtime import preload_onnxruntime


def _preload_onnxruntime_before_qt() -> None:
    """Windows 上 PyQt 先加载后可能导致 onnxruntime DLL 初始化失败。"""
    _ort, error = preload_onnxruntime()
    if error:
        print(f"[Nsy_Broadcasting_platform] ONNX Runtime 预加载失败，相关 AI 功能会自动降级：{error}")


def run() -> None:
    _preload_onnxruntime_before_qt()
    try:
        from PyQt6.QtWidgets import QApplication
        from nsy_broadcasting_platform.ui.main_window import MainWindow
        from nsy_broadcasting_platform.ui.theme import HAULIX_APP_QSS
    except Exception as exc:
        print("[Nsy_Broadcasting_platform] 启动失败：缺少 GUI 依赖（PyQt6）。")
        print(f"[Nsy_Broadcasting_platform] 详细信息：{exc}")
        print("[Nsy_Broadcasting_platform] 请先执行：pip install -r requirements.txt")
        return

    CONFIG.output_dir = Path.cwd() / "outputs"
    app = QApplication.instance() or QApplication([])
    app.setStyleSheet(HAULIX_APP_QSS)
    window = MainWindow(CONFIG)
    window.show()
    app.exec()

