from __future__ import annotations

from pathlib import Path

from nsy_broadcasting_platform.capture.base import BaseVideoSource
from nsy_broadcasting_platform.compat import CV2
from nsy_broadcasting_platform.utils import placeholder_frame

cv2 = CV2.module


class ImageSource(BaseVideoSource):
    def __init__(self, source_id: str, image_path: str) -> None:
        super().__init__(source_id, fps=1)
        self.image_path = image_path
        self._cached = None

    def on_start(self) -> None:
        self._cached = self._load_image()
        self._set_frame(self._cached)

    def _load_image(self):
        if cv2 is None:
            return placeholder_frame(640, 360, "静态图片图层不可用: opencv 缺失")
        path = Path(self.image_path)
        if not path.exists():
            return placeholder_frame(640, 360, f"图片不存在: {path.name}")
        img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        if img is None:
            return placeholder_frame(640, 360, "图片读取失败")
        if getattr(img, "ndim", 0) == 2:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif getattr(img, "ndim", 0) == 3 and img.shape[2] == 1:
            img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
        elif getattr(img, "ndim", 0) != 3 or img.shape[2] not in (3, 4):
            return placeholder_frame(640, 360, "图片通道格式不支持")
        return img

    def capture_once(self):
        return self._cached

