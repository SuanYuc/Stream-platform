from __future__ import annotations

from nsy_broadcasting_platform.capture.base import BaseVideoSource
from nsy_broadcasting_platform.compat import CV2, MSS, NP
from nsy_broadcasting_platform.utils import placeholder_frame

cv2 = CV2.module
mss = MSS.module
np = NP.module


class ScreenSource(BaseVideoSource):
    def __init__(
        self,
        source_id: str,
        monitor_index: int = 1,
        fps: int = 20,
        target_width: int = 0,
        target_height: int = 0,
    ) -> None:
        super().__init__(source_id, fps=fps, target_width=target_width, target_height=target_height)
        self.monitor_index = monitor_index
        self._sct = None

    def on_start(self) -> None:
        if mss is None:
            self.last_error = MSS.error or "mss 未安装"
            return
        self._sct = mss.mss()

    def capture_once(self):
        if mss is None or np is None:
            return placeholder_frame(640, 360, "屏幕采集不可用")
        if self._sct is None:
            return placeholder_frame(640, 360, "屏幕采集初始化失败")

        monitors = self._sct.monitors
        idx = self.monitor_index
        if idx < 0 or idx >= len(monitors):
            idx = 1 if len(monitors) > 1 else 0
        shot = self._sct.grab(monitors[idx])
        arr = np.asarray(shot)
        return cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR) if cv2 is not None else arr[:, :, :3]

    def on_stop(self) -> None:
        if self._sct is not None:
            try:
                self._sct.close()
            except Exception:
                pass
            self._sct = None

