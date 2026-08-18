from __future__ import annotations

import threading
import time
from abc import ABC, abstractmethod

from nsy_broadcasting_platform.compat import CV2

cv2 = CV2.module


class BaseVideoSource(ABC):
    def __init__(
        self,
        source_id: str,
        fps: int = 30,
        target_width: int = 0,
        target_height: int = 0,
    ) -> None:
        self.source_id = source_id
        self.fps = max(1, fps)
        self.target_width = max(0, int(target_width))
        self.target_height = max(0, int(target_height))
        self._frame = None
        self._frame_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._ready = False
        self.last_error: str | None = None

    def _thread_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        if self._thread_running():
            return
        self._stop_event.clear()
        self.last_error = None
        self._thread = threading.Thread(target=self._run, name=f"src-{self.source_id}", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.5) -> None:
        self._stop_event.set()
        if self._thread_running():
            self._thread.join(timeout=timeout)
        self._thread = None
        self.on_stop()

    def get_latest_frame(self):
        with self._frame_lock:
            return None if self._frame is None else self._frame.copy()

    def _set_frame(self, frame) -> None:
        frame = self._fit_quality_frame(frame)
        with self._frame_lock:
            self._frame = frame
            self._ready = frame is not None

    def _fit_quality_frame(self, frame):
        if frame is None or cv2 is None or self.target_width <= 0 or self.target_height <= 0:
            return frame
        try:
            h, w = frame.shape[:2]
            if w == self.target_width and h == self.target_height:
                return frame
            interpolation = cv2.INTER_AREA if w > self.target_width or h > self.target_height else cv2.INTER_LINEAR
            return cv2.resize(frame, (self.target_width, self.target_height), interpolation=interpolation)
        except Exception:
            return frame

    def is_ready(self) -> bool:
        return self._ready

    def _run(self) -> None:
        interval = 1.0 / float(self.fps)
        self.on_start()
        while not self._stop_event.is_set():
            start = time.perf_counter()
            try:
                frame = self.capture_once()
                if frame is not None:
                    self._set_frame(frame)
            except Exception as exc:  # pragma: no cover
                self.last_error = str(exc)
            cost = time.perf_counter() - start
            wait = max(0.001, interval - cost)
            self._stop_event.wait(wait)

    @abstractmethod
    def capture_once(self):
        raise NotImplementedError

    def on_start(self) -> None:
        pass

    def on_stop(self) -> None:
        pass
