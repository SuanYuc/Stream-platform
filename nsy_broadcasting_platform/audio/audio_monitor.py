from __future__ import annotations

import queue
import threading

from nsy_broadcasting_platform.compat import NP, PYAUDIO, PYAUDIOWPATCH

np = NP.module
pyaudiowpatch = PYAUDIOWPATCH.module
pyaudio = PYAUDIO.module


def _select_backend():
    if pyaudiowpatch is not None:
        return pyaudiowpatch, "pyaudiowpatch"
    if pyaudio is not None:
        return pyaudio, "pyaudio"
    return None, "none"


class AudioMonitor:
    """低延迟节目声音监听，把现有 PCM chunk 播放到系统输出。"""

    def __init__(
        self,
        audio_queue: queue.Queue[tuple[float, bytes]],
        sample_rate: int,
        channels: int,
        chunk_size: int,
    ) -> None:
        self.audio_queue = audio_queue
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self._gain = 0.6
        self._thread: threading.Thread | None = None
        self._stop_event = threading.Event()
        self.running = False
        self.error: str = ""
        self.backend_name = "none"

    def set_gain(self, gain: float) -> None:
        self._gain = max(0.0, min(2.0, float(gain)))

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name="audio-monitor", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.5) -> None:
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None
        self.running = False

    def _apply_gain(self, chunk: bytes) -> bytes:
        if np is None or not chunk or abs(self._gain - 1.0) <= 0.001:
            return chunk
        arr = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
        arr *= self._gain
        return np.clip(arr, -32768, 32767).astype(np.int16).tobytes()

    def _next_chunk(self) -> bytes | None:
        try:
            _ts, chunk = self.audio_queue.get(timeout=0.12)
        except queue.Empty:
            return None

        # 监听重视实时性，积压时丢旧包，避免越听越滞后。
        while self.audio_queue.qsize() > 3:
            try:
                _ts, chunk = self.audio_queue.get_nowait()
            except queue.Empty:
                break
        return chunk

    def _run(self) -> None:
        backend, backend_name = _select_backend()
        self.backend_name = backend_name
        if backend is None:
            self.error = "未找到可用音频播放后端"
            return

        pa = None
        stream = None
        self.running = True
        self.error = ""
        try:
            pa = backend.PyAudio()
            stream = pa.open(
                format=backend.paInt16,
                channels=self.channels,
                rate=self.sample_rate,
                output=True,
                frames_per_buffer=self.chunk_size,
            )
            while not self._stop_event.is_set():
                chunk = self._next_chunk()
                if not chunk:
                    continue
                try:
                    stream.write(self._apply_gain(chunk), exception_on_underflow=False)
                except TypeError:
                    stream.write(self._apply_gain(chunk))
        except Exception as exc:
            self.error = str(exc)
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
            self.running = False
