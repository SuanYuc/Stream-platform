from __future__ import annotations

import threading
import time
from typing import Callable

from nsy_broadcasting_platform.audio.session_matcher import SessionMatcher
from nsy_broadcasting_platform.compat import NP, PYAUDIO, PYAUDIOWPATCH

np = NP.module
pyaudiowpatch = PYAUDIOWPATCH.module
pyaudio = PYAUDIO.module

ChunkCallback = Callable[[bytes, float, bool, bool, str, int, str, str], None]


def _select_backend():
    if pyaudiowpatch is not None:
        return pyaudiowpatch, "pyaudiowpatch"
    if pyaudio is not None:
        return pyaudio, "pyaudio"
    return None, "none"


class LoopbackCapture:
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
        self.matcher = SessionMatcher()
        self.device_index: int | None = None
        self.device_name: str = "N/A"
        self.backend_name: str = "none"
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self._note: str = ""

    @staticmethod
    def _iter_devices(pa, backend_name: str) -> list[dict]:
        devices: list[dict] = []
        for idx in range(pa.get_device_count()):
            info = pa.get_device_info_by_index(idx)
            name = str(info.get("name", f"设备 {idx}"))
            max_in = int(info.get("maxInputChannels", 0))
            is_loopback = bool(info.get("isLoopbackDevice", False))
            if max_in <= 0:
                continue
            if backend_name == "pyaudiowpatch" and not (is_loopback or "loopback" in name.lower()):
                continue
            devices.append({"index": idx, "name": name, "is_loopback": is_loopback})
        return devices

    @staticmethod
    def list_devices() -> list[dict]:
        backend, backend_name = _select_backend()
        if backend is None:
            return []
        pa = None
        try:
            pa = backend.PyAudio()
            return LoopbackCapture._iter_devices(pa, backend_name)
        except Exception:
            return []
        finally:
            if pa is not None:
                try:
                    pa.terminate()
                except Exception:
                    pass

    def set_target(self, pid: int | None, process_name: str | None, strict_isolation: bool = False) -> None:
        self.matcher.set_target(pid, process_name, strict_isolation=strict_isolation)
        if strict_isolation:
            self.matcher.apply_isolation()

    def set_device(self, device_index: int | None) -> None:
        self.device_index = device_index

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="audio-loopback", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None
        self.matcher.restore_isolation()

    def _choose_device(self, pa, backend_name: str) -> int | None:
        if self.device_index is not None:
            try:
                info = pa.get_device_info_by_index(self.device_index)
                self.device_name = str(info.get("name", str(self.device_index)))
                return int(self.device_index)
            except Exception:
                self._note = "指定回采设备不可用，已回退到默认选择"

        if backend_name == "pyaudiowpatch":
            try:
                info = pa.get_default_wasapi_loopback()
                idx = int(info["index"])
                self.device_name = str(info.get("name", f"设备 {idx}"))
                return idx
            except Exception:
                pass
            devices = self._iter_devices(pa, backend_name)
            if devices:
                self.device_name = devices[0]["name"]
                return int(devices[0]["index"])

        try:
            info = pa.get_default_input_device_info()
            idx = int(info["index"])
            self.device_name = str(info.get("name", f"设备 {idx}"))
            return idx
        except Exception:
            return None

    def _open_stream(self, pa, backend, backend_name: str):
        idx = self._choose_device(pa, backend_name)
        if idx is None:
            return None
        self.device_index = idx
        kwargs = {
            "format": backend.paInt16,
            "channels": self.channels,
            "rate": self.sample_rate,
            "input": True,
            "input_device_index": idx,
            "frames_per_buffer": self.chunk_size,
        }
        if backend_name == "pyaudiowpatch":
            try:
                return pa.open(**kwargs, as_loopback=True)
            except TypeError:
                pass
            except Exception:
                pass
        try:
            return pa.open(**kwargs)
        except Exception:
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
            self._note = "未找到可用的 pyaudiowpatch 或 pyaudio 音频后端"
            return

        pa = None
        stream = None
        session_hit = False
        try:
            pa = backend.PyAudio()
            stream = self._open_stream(pa, backend, backend_name)
            if stream is None:
                self._note = "无法打开回采音频流"
                return

            tick = 0
            while not self._stop_event.is_set():
                try:
                    chunk = stream.read(self.chunk_size, exception_on_overflow=False)
                except TypeError:
                    chunk = stream.read(self.chunk_size)
                except Exception:
                    time.sleep(0.01)
                    continue

                level, chunk_empty = self._chunk_level(chunk)
                tick += 1
                target_pid = self.matcher.target_pid
                target_process = self.matcher.target_process
                has_target = target_pid is not None or bool(target_process)
                if not has_target:
                    session_hit = False
                elif tick % 12 == 0:
                    session_hit = self.matcher.apply_isolation()

                note = self.matcher.isolation_note() or self._note
                if (target_pid is not None or target_process) and not session_hit and not self.matcher.strict_isolation:
                    note = "会话未命中，按 fail-open 输出系统音频"
                elif has_target and self.matcher.strict_isolation and not session_hit:
                    # 手动选择单个窗口音轨时宁可静音，也不能把其他程序的混音送入节目。
                    chunk = b"\x00" * len(chunk)
                    level = 0.0
                    chunk_empty = True
                    note = note or "严格窗口音轨未命中，已静音保底"

                self._on_chunk(
                    chunk,
                    level,
                    chunk_empty,
                    session_hit,
                    self.device_name,
                    int(self.device_index if self.device_index is not None else -1),
                    backend_name,
                    note,
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
