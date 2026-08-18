from __future__ import annotations

from nsy_broadcasting_platform.capture.base import BaseVideoSource
from nsy_broadcasting_platform.capture.window_capture_win import capture_window
from nsy_broadcasting_platform.utils import placeholder_frame


class WindowSource(BaseVideoSource):
    def __init__(
        self,
        source_id: str,
        hwnd: int,
        title: str = "",
        fps: int = 20,
        target_width: int = 0,
        target_height: int = 0,
    ) -> None:
        super().__init__(source_id, fps=fps, target_width=target_width, target_height=target_height)
        self.hwnd = hwnd
        self.title = title
        self.last_strategy = "N/A"

    def capture_once(self):
        if not self.hwnd:
            return placeholder_frame(640, 360, "窗口句柄无效")
        frame, strategy = capture_window(self.hwnd)
        self.last_strategy = strategy
        if frame is None:
            return placeholder_frame(640, 360, f"窗口采集失败: {strategy}")
        return frame

