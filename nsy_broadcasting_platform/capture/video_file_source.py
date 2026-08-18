from __future__ import annotations

from pathlib import Path

from nsy_broadcasting_platform.capture.base import BaseVideoSource
from nsy_broadcasting_platform.compat import CV2
from nsy_broadcasting_platform.utils import placeholder_frame

cv2 = CV2.module


class VideoFileSource(BaseVideoSource):
    """本地视频文件采集源，到达末尾后自动回到第一帧循环播放。"""

    def __init__(
        self,
        source_id: str,
        video_path: str,
        fps: int = 30,
        target_width: int = 0,
        target_height: int = 0,
    ) -> None:
        super().__init__(source_id, fps=fps, target_width=target_width, target_height=target_height)
        self.video_path = video_path
        self._cap = None

    def on_start(self) -> None:
        self._open_capture()

    def _open_capture(self) -> None:
        if cv2 is None:
            self.last_error = CV2.error or "opencv 未安装"
            return
        path = Path(self.video_path)
        if not path.exists():
            self.last_error = f"占位视频不存在: {path.name}"
            return
        self._cap = cv2.VideoCapture(str(path))
        if not self._cap.isOpened():
            self.last_error = f"占位视频打开失败: {path.name}"

    def _rewind(self) -> None:
        if self._cap is not None and cv2 is not None:
            self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)

    def capture_once(self):
        if cv2 is None:
            return placeholder_frame(640, 360, "占位视频不可用: opencv 缺失")
        if not self.video_path:
            return placeholder_frame(640, 360, "未设置占位视频")
        if self._cap is None or not self._cap.isOpened():
            self._open_capture()
            return placeholder_frame(640, 360, "占位视频加载中...")

        ok, frame = self._cap.read()
        if ok and frame is not None:
            self.last_error = None
            return frame

        self._rewind()
        ok, frame = self._cap.read()
        if ok and frame is not None:
            self.last_error = None
            return frame

        self.last_error = "占位视频读取失败"
        return placeholder_frame(640, 360, "占位视频读取失败")

    def on_stop(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
            self._cap = None
