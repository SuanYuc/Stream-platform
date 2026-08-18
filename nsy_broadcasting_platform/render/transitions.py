from __future__ import annotations

import math
from pathlib import Path

from nsy_broadcasting_platform.compat import CV2, NP
from nsy_broadcasting_platform.models import TransitionConfig

cv2 = CV2.module
np = NP.module


class ProgramTransition:
    """节目输出转场器，只处理两帧之间的过渡画面。"""

    IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}
    VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".flv", ".wmv"}

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self._active = False
        self._config = TransitionConfig()
        self._from_frame = None
        self._start_at = 0.0
        self._media_path = ""
        self._media_image = None
        self._media_cap = None
        self._grid_cache: dict[tuple[int, int], tuple[object, object]] = {}

    @property
    def active(self) -> bool:
        return self._active

    def close(self) -> None:
        self._release_media()
        self._grid_cache.clear()

    def set_canvas_size(self, width: int, height: int) -> None:
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self.cancel()
        self._grid_cache.clear()

    def cancel(self) -> None:
        self._active = False
        self._from_frame = None
        self._release_media()

    def start(self, config: TransitionConfig, from_frame, now: float) -> None:
        if from_frame is None or np is None:
            self.cancel()
            return
        self._config = config.clone()
        self._from_frame = self._fit_frame(from_frame).copy()
        self._start_at = now
        self._active = self._config.mode != "cut" and self._config.duration_ms > 0
        self._prepare_media()

    def render(self, to_frame, now: float):
        if not self._active or to_frame is None:
            self.cancel()
            return to_frame, True
        to_frame = self._fit_frame(to_frame)
        duration_s = max(0.001, self._config.duration_ms / 1000.0)
        progress = max(0.0, min(1.0, (now - self._start_at) / duration_s))
        if progress >= 1.0:
            self.cancel()
            return to_frame, True

        from_frame = self._fit_frame(self._from_frame)
        mode = str(self._config.mode or "cut")
        if mode == "dissolve":
            return self._blend(from_frame, to_frame, progress), False
        if mode == "wipe":
            return self._wipe(from_frame, to_frame, progress), False
        if mode == "dve":
            return self._dve(from_frame, to_frame, progress), False
        if mode == "media":
            return self._media_transition(from_frame, to_frame, progress), False
        self.cancel()
        return to_frame, True

    def _fit_frame(self, frame):
        if frame is None or np is None:
            return frame
        if frame.shape[0] == self.height and frame.shape[1] == self.width:
            return frame[:, :, :3] if frame.shape[2] > 3 else frame
        if cv2 is not None:
            return cv2.resize(frame[:, :, :3], (self.width, self.height), interpolation=cv2.INTER_LINEAR)
        y_idx = np.linspace(0, frame.shape[0] - 1, self.height).astype(np.int32)
        x_idx = np.linspace(0, frame.shape[1] - 1, self.width).astype(np.int32)
        return frame[y_idx][:, x_idx][:, :, :3]

    @staticmethod
    def _blend(old_frame, new_frame, progress: float):
        if cv2 is not None:
            return cv2.addWeighted(old_frame, 1.0 - progress, new_frame, progress, 0)
        return (old_frame.astype("float32") * (1.0 - progress) + new_frame.astype("float32") * progress).astype("uint8")

    def _grids(self, h: int, w: int):
        key = (h, w)
        cached = self._grid_cache.get(key)
        if cached is not None:
            return cached
        yy, xx = np.ogrid[:h, :w]
        self._grid_cache[key] = (yy, xx)
        return yy, xx

    def _wipe(self, old_frame, new_frame, progress: float):
        h, w = old_frame.shape[:2]
        shape = str(self._config.wipe_shape or "horizontal")
        yy, xx = self._grids(h, w)
        if shape == "vertical":
            mask = yy < int(h * progress)
        elif shape == "circle":
            cx, cy = w / 2.0, h / 2.0
            radius = math.hypot(w, h) * progress / 2.0
            mask = ((xx - cx) ** 2 + (yy - cy) ** 2) <= radius * radius
        elif shape == "diagonal":
            mask = (xx / max(1, w) + yy / max(1, h)) <= progress * 2.0
        else:
            mask = xx < int(w * progress)
        return np.where(mask[:, :, None], new_frame, old_frame).astype("uint8")

    def _dve(self, old_frame, new_frame, progress: float):
        mode = str(self._config.dve_mode or "push")
        h, w = old_frame.shape[:2]
        if mode == "rotate":
            return self._dve_rotate(old_frame, new_frame, progress, w, h)
        if mode == "page":
            return self._dve_page(old_frame, new_frame, progress, w)
        if mode == "squeeze":
            return self._dve_squeeze(old_frame, new_frame, progress, w, h)
        return self._dve_push(old_frame, new_frame, progress, w)

    @staticmethod
    def _dve_push(old_frame, new_frame, progress: float, w: int):
        shift = max(0, min(w, int(w * progress)))
        if shift <= 0:
            return old_frame.copy()
        if shift >= w:
            return new_frame.copy()
        out = old_frame.copy()
        out[:, : w - shift] = old_frame[:, shift:]
        out[:, w - shift :] = new_frame[:, :shift]
        return out

    @staticmethod
    def _dve_page(old_frame, new_frame, progress: float, w: int):
        split = max(0, min(w, int(w * progress)))
        out = old_frame.copy()
        if split > 0:
            out[:, :split] = new_frame[:, :split]
            shadow_x = min(w - 1, split)
            out[:, max(0, shadow_x - 3) : shadow_x + 1] = (out[:, max(0, shadow_x - 3) : shadow_x + 1] * 0.65).astype(
                "uint8"
            )
        return out

    @staticmethod
    def _dve_squeeze(old_frame, new_frame, progress: float, w: int, h: int):
        if cv2 is None:
            return ProgramTransition._blend(old_frame, new_frame, progress)
        squeeze_w = max(1, int(w * (1.0 - progress)))
        out = new_frame.copy()
        resized = cv2.resize(old_frame, (squeeze_w, h), interpolation=cv2.INTER_LINEAR)
        x = (w - squeeze_w) // 2
        out[:, x : x + squeeze_w] = resized
        return out

    @staticmethod
    def _dve_rotate(old_frame, new_frame, progress: float, w: int, h: int):
        if cv2 is None:
            return ProgramTransition._blend(old_frame, new_frame, progress)
        scale = max(0.12, 1.0 - progress * 0.75)
        matrix = cv2.getRotationMatrix2D((w / 2.0, h / 2.0), 360.0 * progress, scale)
        warped = cv2.warpAffine(old_frame, matrix, (w, h), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0))
        mask_src = np.ones((h, w), dtype="float32")
        mask = cv2.warpAffine(mask_src, matrix, (w, h), flags=cv2.INTER_LINEAR, borderValue=0)[:, :, None]
        mask = mask * (1.0 - progress)
        return (warped.astype("float32") * mask + new_frame.astype("float32") * (1.0 - mask)).astype("uint8")

    def _prepare_media(self) -> None:
        self._release_media()
        if self._config.mode != "media" or cv2 is None:
            return
        path = str(self._config.media_path or "").strip()
        if not path:
            return
        suffix = Path(path).suffix.lower()
        self._media_path = path
        if suffix in self.IMAGE_SUFFIXES:
            image = cv2.imread(path, cv2.IMREAD_UNCHANGED)
            if image is not None:
                if getattr(image, "ndim", 0) == 2:
                    image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
                if image.shape[2] == 4:
                    image = image[:, :, :3]
                self._media_image = self._fit_frame(image)
        elif suffix in self.VIDEO_SUFFIXES:
            cap = cv2.VideoCapture(path)
            if cap.isOpened():
                self._media_cap = cap

    def _release_media(self) -> None:
        if self._media_cap is not None:
            try:
                self._media_cap.release()
            except Exception:
                pass
        self._media_cap = None
        self._media_image = None
        self._media_path = ""

    def _next_media_frame(self):
        if cv2 is None:
            return None
        if self._media_image is not None:
            return self._media_image
        if self._media_cap is None:
            return None
        ok, frame = self._media_cap.read()
        if not ok or frame is None:
            self._media_cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, frame = self._media_cap.read()
        if not ok or frame is None:
            return None
        return self._fit_frame(frame)

    def _media_transition(self, old_frame, new_frame, progress: float):
        media = self._next_media_frame()
        if media is None:
            return self._blend(old_frame, new_frame, progress)
        if progress < 0.5:
            return self._blend(old_frame, media, progress * 2.0)
        return self._blend(media, new_frame, (progress - 0.5) * 2.0)
