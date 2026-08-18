from __future__ import annotations

import copy
import queue
import threading
import time

from nsy_broadcasting_platform.audio.audio_monitor import AudioMonitor
from nsy_broadcasting_platform.audio.input_capture import InputCapture
from nsy_broadcasting_platform.audio.loopback_capture import LoopbackCapture
from nsy_broadcasting_platform.compat import NP
from nsy_broadcasting_platform.models import AudioDiagnostics, AudioTrack, AudioTrackKind

np = NP.module


class AudioController:
    """WASAPI loopback 音频控制器，负责增益、分发与诊断。"""

    def __init__(self, sample_rate: int, channels: int, chunk_size: int) -> None:
        self.sample_rate = sample_rate
        self.channels = channels
        self.chunk_size = chunk_size
        self._lock = threading.RLock()
        self._listeners: dict[str, queue.Queue[tuple[float, bytes]]] = {}
        self._diag = AudioDiagnostics()
        self._target_pid: int | None = None
        self._target_process: str | None = None
        self._strict_isolation = False
        self._gain: float = 1.0
        self._muted = False
        self._amplitude = 1.0
        self._low_gain = 1.0
        self._mid_gain = 1.0
        self._high_gain = 1.0
        self._active_kind = AudioTrackKind.SYSTEM
        self._ai_processors: list[object] = []
        self._monitor: AudioMonitor | None = None
        self._monitor_gain: float = 0.6
        self._capture = LoopbackCapture(
            sample_rate=sample_rate,
            channels=channels,
            chunk_size=chunk_size,
            on_chunk=self._on_chunk,
        )
        self._input_capture = InputCapture(
            sample_rate=sample_rate,
            channels=channels,
            chunk_size=chunk_size,
            on_chunk=self._on_input_chunk,
        )

    def start(self) -> None:
        with self._lock:
            kind = self._active_kind
        if kind == AudioTrackKind.MICROPHONE:
            self._input_capture.start()
        else:
            self._capture.start()

    def stop(self) -> None:
        self.stop_monitoring()
        self._capture.stop()
        self._input_capture.stop()

    def restart_with_device(self, device_index: int | None) -> None:
        with self._lock:
            active_kind = self._active_kind
        self._capture.stop()
        self._capture.set_device(device_index)
        self._capture.set_target(self._target_pid, self._target_process, self._strict_isolation)
        if active_kind != AudioTrackKind.MICROPHONE:
            self._capture.start()

    def restart_with_input_device(self, device_index: int | None) -> None:
        with self._lock:
            active_kind = self._active_kind
        self._input_capture.stop()
        self._input_capture.set_device(device_index)
        if active_kind == AudioTrackKind.MICROPHONE:
            self._input_capture.start()

    def set_target(self, pid: int | None, process_name: str | None, strict_isolation: bool = False) -> None:
        with self._lock:
            self._target_pid = pid
            self._target_process = process_name
            self._strict_isolation = bool(strict_isolation and (pid is not None or process_name))
            self._diag.target_pid = pid
            self._diag.target_process = process_name
        self._capture.set_target(pid, process_name, self._strict_isolation)

    def set_gain(self, gain: float) -> None:
        with self._lock:
            self._gain = max(0.0, min(4.0, float(gain)))

    def set_track_profile(self, track: AudioTrack, strict_isolation: bool = False) -> None:
        with self._lock:
            old_kind = self._active_kind
            self._active_kind = track.kind
            self._gain = max(0.0, min(4.0, float(track.volume)))
            self._muted = bool(track.muted or not track.enabled)
            self._amplitude = max(0.0, min(4.0, float(track.amplitude)))
            self._low_gain = max(0.0, min(4.0, float(track.low_gain)))
            self._mid_gain = max(0.0, min(4.0, float(track.mid_gain)))
            self._high_gain = max(0.0, min(4.0, float(track.high_gain)))
            self._target_pid = track.pid
            self._target_process = track.process_name
            self._strict_isolation = bool(strict_isolation and track.kind == AudioTrackKind.WINDOW)
            self._diag.target_pid = track.pid
            self._diag.target_process = track.process_name

        if track.kind == AudioTrackKind.MICROPHONE:
            self._capture.set_target(None, None, False)
            if old_kind != AudioTrackKind.MICROPHONE:
                self._capture.stop()
                self._input_capture.start()
        else:
            self._capture.set_target(track.pid, track.process_name, self._strict_isolation)
            if old_kind == AudioTrackKind.MICROPHONE:
                self._input_capture.stop()
                self._capture.start()

    def register_ai_processor(self, processor: object) -> None:
        """预留智能降噪、违禁词检测等处理器接口。processor 可实现 process_pcm(chunk, meta)。"""
        with self._lock:
            if processor not in self._ai_processors:
                self._ai_processors.append(processor)

    def unregister_ai_processor(self, processor: object) -> None:
        with self._lock:
            self._ai_processors = [item for item in self._ai_processors if item is not processor]

    @staticmethod
    def _apply_gain(chunk: bytes, gain: float) -> bytes:
        if np is None or not chunk or abs(gain - 1.0) <= 0.001:
            return chunk
        arr = np.frombuffer(chunk, dtype=np.int16).astype(np.float32)
        arr *= gain
        return np.clip(arr, -32768, 32767).astype(np.int16).tobytes()

    def _apply_track_processing(self, chunk: bytes) -> bytes:
        with self._lock:
            muted = self._muted
            gain = self._gain * self._amplitude
            low_gain = self._low_gain
            mid_gain = self._mid_gain
            high_gain = self._high_gain
            processors = list(self._ai_processors)
        if not chunk:
            return chunk
        if muted:
            return b"\x00" * len(chunk)
        processed = self._apply_eq(chunk, low_gain, mid_gain, high_gain)
        processed = self._apply_gain(processed, gain)
        for processor in processors:
            try:
                fn = getattr(processor, "process_pcm", None)
                if callable(fn):
                    processed = fn(processed, {"sample_rate": self.sample_rate, "channels": self.channels}) or processed
            except Exception:
                continue
        return processed

    def _apply_eq(self, chunk: bytes, low_gain: float, mid_gain: float, high_gain: float) -> bytes:
        if np is None or not chunk:
            return chunk
        if abs(low_gain - 1.0) < 0.01 and abs(mid_gain - 1.0) < 0.01 and abs(high_gain - 1.0) < 0.01:
            return chunk
        frame_unit = self.channels * 2
        valid = (len(chunk) // frame_unit) * frame_unit if frame_unit > 0 else 0
        if valid <= 0:
            return chunk
        arr = np.frombuffer(chunk[:valid], dtype=np.int16).astype(np.float32)
        arr = arr.reshape((-1, self.channels))
        freqs = np.fft.rfftfreq(arr.shape[0], d=1.0 / float(self.sample_rate))
        spec = np.fft.rfft(arr, axis=0)
        spec[freqs < 250.0] *= low_gain
        spec[(freqs >= 250.0) & (freqs < 4000.0)] *= mid_gain
        spec[freqs >= 4000.0] *= high_gain
        out = np.fft.irfft(spec, n=arr.shape[0], axis=0)
        out = np.clip(out, -32768, 32767).astype(np.int16).reshape(-1).tobytes()
        if valid < len(chunk):
            out += chunk[valid:]
        return out

    @staticmethod
    def _push_listener(q: queue.Queue[tuple[float, bytes]], item: tuple[float, bytes]) -> None:
        try:
            q.put_nowait(item)
        except queue.Full:
            try:
                q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(item)
            except queue.Full:
                pass

    def _on_chunk(
        self,
        chunk: bytes,
        level: float,
        chunk_empty: bool,
        session_hit: bool,
        device_name: str,
        device_index: int,
        backend_name: str,
        note: str,
    ) -> None:
        with self._lock:
            self._diag.device_name = device_name
            self._diag.device_index = device_index
            self._diag.level = level
            self._diag.chunk_empty = chunk_empty
            self._diag.session_hit = session_hit
            self._diag.backend = backend_name
            self._diag.note = note
            listeners = list(self._listeners.values())

        chunk = self._apply_track_processing(chunk)
        item = (time.perf_counter(), chunk)
        for q in listeners:
            self._push_listener(q, item)

    def _on_input_chunk(
        self,
        chunk: bytes,
        level: float,
        chunk_empty: bool,
        device_name: str,
        device_index: int,
        backend_name: str,
        note: str,
    ) -> None:
        self._on_chunk(
            chunk,
            level,
            chunk_empty,
            False,
            device_name,
            device_index,
            backend_name,
            note or "麦克风输入",
        )

    def register_listener(self, name: str, maxsize: int = 90) -> queue.Queue[tuple[float, bytes]]:
        q: queue.Queue[tuple[float, bytes]] = queue.Queue(maxsize=maxsize)
        with self._lock:
            self._listeners[name] = q
        return q

    def unregister_listener(self, name: str) -> None:
        with self._lock:
            self._listeners.pop(name, None)

    def start_monitoring(self) -> tuple[bool, str]:
        with self._lock:
            if self._monitor is not None and self._monitor.running:
                return False, "监听已开启"
        q = self.register_listener("monitor", maxsize=16)
        monitor = AudioMonitor(q, self.sample_rate, self.channels, self.chunk_size)
        monitor.set_gain(self._monitor_gain)
        with self._lock:
            self._monitor = monitor
        monitor.start()
        return True, "节目声音监听已开启"

    def stop_monitoring(self) -> tuple[bool, str]:
        with self._lock:
            monitor = self._monitor
            self._monitor = None
        self.unregister_listener("monitor")
        if monitor is None:
            return False, "监听未开启"
        monitor.stop()
        return True, "节目声音监听已关闭"

    def set_monitor_gain(self, gain: float) -> None:
        with self._lock:
            self._monitor_gain = max(0.0, min(2.0, float(gain)))
            monitor = self._monitor
        if monitor is not None:
            monitor.set_gain(self._monitor_gain)

    def monitor_status(self) -> dict[str, object]:
        with self._lock:
            monitor = self._monitor
            gain = self._monitor_gain
        if monitor is None:
            return {"running": False, "gain": gain, "backend": "none", "error": ""}
        return {
            "running": monitor.running,
            "gain": gain,
            "backend": monitor.backend_name,
            "error": monitor.error,
        }

    def get_diagnostics(self) -> AudioDiagnostics:
        with self._lock:
            return copy.copy(self._diag)

    @staticmethod
    def list_devices() -> list[dict]:
        return LoopbackCapture.list_devices()

    @staticmethod
    def list_input_devices() -> list[dict]:
        return InputCapture.list_devices()
