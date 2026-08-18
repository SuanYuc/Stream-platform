from __future__ import annotations

import time

from nsy_broadcasting_platform.capture.base import BaseVideoSource
from nsy_broadcasting_platform.compat import CV2
from nsy_broadcasting_platform.utils import placeholder_frame

cv2 = CV2.module


class NetworkSource(BaseVideoSource):
    """网络流采集源（RTMP/RTSP/HTTP），带自动重连。"""

    def __init__(
        self,
        source_id: str,
        url: str,
        fps: int = 25,
        reconnect_interval: float = 1.0,
        target_width: int = 0,
        target_height: int = 0,
    ) -> None:
        super().__init__(source_id, fps=fps, target_width=target_width, target_height=target_height)
        self.url = url
        self.reconnect_interval = max(0.3, reconnect_interval)
        self._cap = None
        self._last_retry = 0.0
        self._fail_count = 0

    def on_start(self) -> None:
        self._open_capture(force=True)

    def _open_capture(self, force: bool = False) -> None:
        if cv2 is None:
            self.last_error = CV2.error or "opencv 未安装"
            return
        now = time.perf_counter()
        if not force and (now - self._last_retry) < self.reconnect_interval:
            return
        self._last_retry = now
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
        self._cap = cv2.VideoCapture(self.url)
        try:
            self._cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            self._cap.set(cv2.CAP_PROP_FPS, self.fps)
        except Exception:
            pass
        if not self._cap.isOpened():
            self.last_error = f"网络流连接失败: {self.url}"

    def capture_once(self):
        if cv2 is None:
            return placeholder_frame(640, 360, "网络流不可用: opencv 缺失")
        if not self.url:
            return placeholder_frame(640, 360, "网络流 URL 为空")
        if self._cap is None or not self._cap.isOpened():
            self._open_capture()
            return placeholder_frame(640, 360, "网络流连接中...")

        ok, frame = self._cap.read()
        if ok and frame is not None:
            self._fail_count = 0
            return frame

        self._fail_count += 1
        if self._fail_count >= 3:
            self._open_capture(force=True)
            self._fail_count = 0
        return placeholder_frame(640, 360, "网络流读取失败，正在重连...")

    def on_stop(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

