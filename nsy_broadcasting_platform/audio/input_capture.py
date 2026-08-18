from __future__ import annotations

import threading
import time
from typing import Callable

from nsy_broadcasting_platform.compat import NP, PYAUDIO, PYAUDIOWPATCH

np = NP.module
pyaudiowpatch = PYAUDIOWPATCH.module
pyaudio = PYAUDIO.module

ChunkCallback = Callable[[bytes, float, bool, str, int, str, str], None]


def _select_backend():
    """麦克风采集优先使用常规 pyaudio，缺失时退回 pyaudiowpatch。"""
    if pyaudio is not None:
        return pyaudio, "pyaudio"
    if pyaudiowpatch is not None:
        return pyaudiowpatch, "pyaudiowpatch"
    return None, "none"


class InputCapture:
    """麦克风输入采集线程，接口尽量贴近 LoopbackCapture。"""

    def __init__(
        self,
        sample_rate: int,
        channels: int,
        chunk_size: int,
        on_chunk: ChunkCallback,
    ) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self._on_chunk = on_chunk
        self.device_index: int | None = None
        self.device_name = "默认麦克风"
        self.backend_name = "none"
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._note = ""

    @staticmethod
    def list_devices() -> list[dict]:
        backend, backend_name = _select_backend()
        if backend is None:
            return []
        pa = None
        try:
            pa = backend.PyAudio()
            devices = []
            for idx in range(pa.get_device_count()):
                info = pa.get_device_info_by_index(idx)
                max_in = int(info.get("maxInputChannels", 0))
                if max_in <= 0 or bool(info.get("isLoopbackDevice", False)):
                    continue
                name = str(info.get("name", f"输入设备 {idx}"))
                devices.append({"index": idx, "name": name, "backend": backend_name})
            return devices
        except Exception:
            return []
        finally:
            if pa is not None:
                try:
                    pa.terminate()
                except Exception:
                    pass

    def set_device(self, device_index: int | None) -> None:
        self.device_index = device_index

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="audio-input", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None

    def _choose_device(self, pa) -> int | None:
        if self.device_index is not None:
            try:
                info = pa.get_device_info_by_index(self.device_index)
                self.device_name = str(info.get("name", str(self.device_index)))
                return int(self.device_index)
            except Exception:
                self._note = "指定麦克风不可用，已回退到默认输入"
        try:
            info = pa.get_default_input_device_info()
            idx = int(info["index"])
            self.device_name = str(info.get("name", f"输入设备 {idx}"))
            return idx
        except Exception:
            self._note = "未找到可用麦克风输入"
            return None

    def _open_stream(self, pa, backend):
        idx = self._choose_device(pa)
        if idx is None:
            return None
        self.device_index = idx
        try:
            return pa.open(
                format=backend.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                input=True,
                input_device_index=idx,
                frames_per_buffer=self.chunk_size,
            )
        except Exception:
            self._note = "无法打开麦克风输入流"
            return None

    @staticmethod
    def _chunk_level(chunk: bytes) -> tuple[float, bool]:
        if np is None:
            return 0.0, len(chunk) == 0
        arr = np.frombuffer(chunk, dtype=np.int16)
        if arr.size == 0:
            return 0.0, True
        arr_f32 = arr.astype(np.float32)
        level = float(np.sqrt(np.mean(arr_f32 * arr_f32)) / 32768.0)
        return level, bool(np.max(np.abs(arr)) < 8)

    def _run(self) -> None:
        backend, backend_name = _select_backend()
        self.backend_name = backend_name
        if backend is None:
            self._note = "未找到可用的麦克风采集后端"
            return
        pa = None
        stream = None
        try:
            pa = backend.PyAudio()
            stream = self._open_stream(pa, backend)
            if stream is None:
                return
            while not self._stop_event.is_set():
                try:
                    chunk = stream.read(self.chunk_size, exception_on_overflow=False)
                except TypeError:
                    chunk = stream.read(self.chunk_size)
                except Exception:
                    time.sleep(0.01)
                    continue
                level, chunk_empty = self._chunk_level(chunk)
                self._on_chunk(
                    chunk,
                    level,
                    chunk_empty,
                    self.device_name,
                    int(self.device_index if self.device_index is not None else -1),
                    backend_name,
                    self._note,
                )
        finally:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except Exception:
                    pass
            if pa is not None:
                try:
                    pa.terminate()
                except Exception:
                    pass
