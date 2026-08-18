from __future__ import annotations

from nsy_broadcasting_platform.capture.base import BaseVideoSource
from nsy_broadcasting_platform.compat import CV2
from nsy_broadcasting_platform.utils import placeholder_frame

cv2 = CV2.module


class CameraSource(BaseVideoSource):
    def __init__(
        self,
        source_id: str,
        camera_index: int = 0,
        fps: int = 30,
        target_width: int = 0,
        target_height: int = 0,
    ) -> None:
        super().__init__(source_id, fps=fps, target_width=target_width, target_height=target_height)
        self.camera_index = camera_index
        self._cap = None

    def on_start(self) -> None:
        if cv2 is None:
            self.last_error = CV2.error or "opencv 未安装"
            return
        self._cap = cv2.VideoCapture(self.camera_index, cv2.CAP_DSHOW)
        if self.target_width > 0:
            self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.target_width)
        if self.target_height > 0:
            self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.target_height)
        self._cap.set(cv2.CAP_PROP_FPS, self.fps)
        if not self._cap.isOpened():
            self.last_error = f"无法打开摄像头 {self.camera_index}"

    def capture_once(self):
        if cv2 is None:
            return placeholder_frame(640, 360, "摄像头不可用: 未安装 opencv")
        if self._cap is None or not self._cap.isOpened():
            return placeholder_frame(640, 360, f"摄像头 {self.camera_index} 打开失败")
        ok, frame = self._cap.read()
        if not ok or frame is None:
            return placeholder_frame(640, 360, "摄像头帧读取失败")
        return frame

    def on_stop(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None

