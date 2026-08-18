from __future__ import annotations

import queue
import threading
import time
from fractions import Fraction
from pathlib import Path

from nsy_broadcasting_platform.compat import AV, NP

av = AV.module
np = NP.module


class EncoderWorker:
    """编码工作线程，负责本地录制或 RTMP 推流。"""

    def __init__(
        self,
        name: str,
        mode: str,
        target: str,
        width: int,
        height: int,
        fps: int,
        sample_rate: int,
        channels: int,
        audio_queue: queue.Queue[tuple[float, bytes]] | None,
        bitrate: str,
        encoder_mode: str = "auto",
    ) -> None:
        self.name = name
        self.mode = mode
        self.target = target
        self.width = width
        self.height = height
        self.fps = fps
        self.sample_rate = sample_rate
        self.channels = channels
        self.audio_queue = audio_queue
        self.bitrate = bitrate
        self.encoder_mode = str(encoder_mode or "auto").strip().lower()
        self._video_queue: queue.Queue[tuple[float, object]] = queue.Queue(maxsize=180)
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self.running = False
        self.error: str | None = None
        self._v_pts = 0
        self._a_pts = 0
        self._last_v_pts = -1
        self._start_time = 0.0
        self.video_codec: str | None = None
        self._stats_lock = threading.Lock()
        self._frames_submitted = 0
        self._frames_enqueued = 0
        self._frames_dropped = 0
        self._frames_encoded = 0
        self._last_drop_time = 0.0

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, name=f"enc-{self.name}", daemon=True)
        self._thread.start()

    def _enqueue_video(self, item: tuple[float, object]) -> None:
        with self._stats_lock:
            self._frames_submitted += 1
        if self.mode == "stream":
            self._trim_stream_queue_under_pressure()
        try:
            self._video_queue.put_nowait(item)
            with self._stats_lock:
                self._frames_enqueued += 1
        except queue.Full:
            try:
                self._video_queue.get_nowait()
                with self._stats_lock:
                    self._frames_dropped += 1
                    self._last_drop_time = time.perf_counter()
            except queue.Empty:
                pass
            try:
                self._video_queue.put_nowait(item)
                with self._stats_lock:
                    self._frames_enqueued += 1
            except queue.Full:
                with self._stats_lock:
                    self._frames_dropped += 1
                    self._last_drop_time = time.perf_counter()

    def _trim_stream_queue_under_pressure(self) -> None:
        """Drop stale live frames before latency grows enough to desync audio/video."""
        capacity = max(1, self._video_queue.maxsize)
        if self._video_queue.qsize() / capacity < 0.86:
            return
        target_size = int(capacity * 0.58)
        dropped = 0
        while self._video_queue.qsize() > target_size:
            try:
                self._video_queue.get_nowait()
                dropped += 1
            except queue.Empty:
                break
        if dropped:
            with self._stats_lock:
                self._frames_dropped += dropped
                self._last_drop_time = time.perf_counter()

    def push_video(self, frame) -> None:
        if self._stop_event.is_set():
            return
        self._enqueue_video((time.perf_counter(), frame.copy()))

    def stats(self) -> dict[str, object]:
        capacity = max(1, self._video_queue.maxsize)
        queue_size = self._video_queue.qsize()
        with self._stats_lock:
            return {
                "name": self.name,
                "mode": self.mode,
                "running": self.running,
                "bitrate": self.bitrate,
                "encoder_mode": self.encoder_mode,
                "video_codec": self.video_codec or "",
                "queue_size": queue_size,
                "queue_capacity": capacity,
                "queue_pressure": queue_size / capacity,
                "frames_submitted": self._frames_submitted,
                "frames_enqueued": self._frames_enqueued,
                "frames_dropped": self._frames_dropped,
                "frames_encoded": self._frames_encoded,
                "last_drop_time": self._last_drop_time,
            }

    def stop(self, timeout: float = 4.0) -> None:
        # 先请求停止写入，再等待线程 flush 并安全收尾。
        self._stop_event.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=timeout)
        self._thread = None
        self.running = False

    def _build_video_stream(self, container):
        codec_try = self._codec_candidates()
        base_opts = {"b:v": self.bitrate}
        last_exc = None
        for codec in codec_try:
            try:
                opts = self._codec_options(codec, base_opts)
                self._preflight_video_codec(codec, opts)
                stream = container.add_stream(codec, rate=self.fps)
                stream.width = self.width
                stream.height = self.height
                stream.pix_fmt = "yuv420p"
                stream.options = opts
                if self.mode == "stream" and getattr(stream, "codec_context", None) is not None:
                    try:
                        stream.codec_context.max_b_frames = 0
                    except Exception:
                        pass
                codec_context = getattr(stream, "codec_context", None)
                if codec_context is not None and hasattr(codec_context, "open"):
                    # PyAV may defer avcodec_open2 until the first frame. Open here so
                    # unsupported GPU codecs/options can fall back before writing RTMP.
                    codec_context.open()
                self.video_codec = codec
                return stream
            except Exception as exc:
                last_exc = exc
        raise RuntimeError(f"无法创建可用的视频编码器：{last_exc}")

    def _preflight_video_codec(self, codec: str, opts: dict[str, str]) -> None:
        """在写入真实容器前验证编码器，避免失败的 GPU stream 污染输出。"""
        if av is None:
            return
        codec_context = av.CodecContext.create(codec, "w")
        codec_context.width = self.width
        codec_context.height = self.height
        codec_context.pix_fmt = "yuv420p"
        codec_context.time_base = Fraction(1, self.fps)
        codec_context.framerate = Fraction(self.fps, 1)
        codec_context.options = dict(opts)
        if self.mode == "stream":
            try:
                codec_context.max_b_frames = 0
            except Exception:
                pass
        codec_context.open()

    def _codec_candidates(self) -> list[str]:
        """按用户选择决定 CPU/GPU 编码路径，GPU 不可用时仍自动回退 CPU。"""
        gpu_codecs = ["h264_nvenc"]
        cpu_codecs = ["libx264"]
        if self.encoder_mode in {"gpu", "nvenc"}:
            return gpu_codecs + cpu_codecs
        if self.encoder_mode in {"cpu", "x264"}:
            return cpu_codecs
        if self.encoder_mode in {"hybrid", "cpu+gpu", "auto"}:
            return gpu_codecs + cpu_codecs
        return gpu_codecs + cpu_codecs

    def _codec_options(self, codec: str, base_opts: dict[str, str]) -> dict[str, str]:
        gop = str(max(1, int(self.fps) * 2))
        if codec == "h264_nvenc":
            opts = {"b:v": self.bitrate}
            opts.setdefault("preset", "p4" if self.mode == "stream" else "p5")
            opts.setdefault("tune", "ll" if self.mode == "stream" else "hq")
            opts.setdefault("rc", "cbr" if self.mode == "stream" else "vbr")
            if self.mode != "stream":
                opts.setdefault("cq", "19")
            opts.setdefault("bf", "0" if self.mode == "stream" else "2")
            opts.setdefault("maxrate", self.bitrate)
            opts.setdefault("bufsize", self._double_bitrate(self.bitrate))
            opts.setdefault("g", gop)
        elif codec == "libx264":
            opts = dict(base_opts)
            opts.setdefault("preset", "veryfast" if self.mode == "stream" else "fast")
            opts.setdefault("tune", "zerolatency" if self.mode == "stream" else "film")
            opts.setdefault("bf", "0" if self.mode == "stream" else "2")
            opts.setdefault("g", gop)
            opts.setdefault("keyint_min", str(max(1, int(self.fps))))
            opts.setdefault("sc_threshold", "0" if self.mode == "stream" else "40")
        else:
            opts = dict(base_opts)
        return opts

    @staticmethod
    def _double_bitrate(value: str) -> str:
        raw = str(value or "").strip().lower()
        try:
            if raw.endswith("k"):
                return f"{max(1, int(float(raw[:-1]) * 2))}k"
            if raw.endswith("m"):
                return f"{max(1, int(float(raw[:-1]) * 2))}m"
            return str(max(1, int(float(raw) * 2)))
        except Exception:
            return raw or "8000k"

    def _build_audio_stream(self, container):
        stream = container.add_stream("aac", rate=self.sample_rate)
        stream.layout = "stereo" if self.channels == 2 else "mono"
        stream.time_base = Fraction(1, self.sample_rate)
        return stream

    def _encode_video(self, stream, container, frame_arr, capture_ts: float) -> None:
        if av is None:
            return
        if self._start_time <= 0:
            self._start_time = capture_ts
        current_time = max(0.0, capture_ts - self._start_time)
        pts = int(current_time * self.fps)
        if self._a_pts > 0:
            pts = max(pts, int((self._a_pts / self.sample_rate) * self.fps))
        pts = max(pts, self._last_v_pts + 1)
        video_frame = av.VideoFrame.from_ndarray(frame_arr, format="bgr24")
        video_frame = video_frame.reformat(width=self.width, height=self.height, format="yuv420p")
        video_frame.pts = pts
        video_frame.time_base = Fraction(1, self.fps)
        self._v_pts = pts
        self._last_v_pts = pts
        for packet in stream.encode(video_frame):
            container.mux(packet)
        with self._stats_lock:
            self._frames_encoded += 1

    def _encode_audio(self, stream, container, chunk: bytes, chunk_ts: float) -> None:
        if av is None or not chunk:
            return
        if self._start_time <= 0:
            self._start_time = chunk_ts
        sample_width = 2
        frame_unit = self.channels * sample_width
        valid = (len(chunk) // frame_unit) * frame_unit if frame_unit > 0 else 0
        if valid <= 0:
            return
        chunk = chunk[:valid]
        samples = valid // frame_unit
        pts = max(self._a_pts, int(max(0.0, chunk_ts - self._start_time) * self.sample_rate))
        aframe = av.AudioFrame(format="s16", layout="stereo" if self.channels == 2 else "mono", samples=samples)
        aframe.sample_rate = self.sample_rate
        aframe.pts = pts
        aframe.time_base = Fraction(1, self.sample_rate)
        aframe.planes[0].update(chunk)
        self._a_pts = pts + samples
        for packet in stream.encode(aframe):
            container.mux(packet)

    def _open_container(self):
        if av is None:
            raise RuntimeError("PyAV 不可用，无法创建输出容器")
        if self.mode == "record":
            out_path = Path(self.target)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            return av.open(str(out_path), mode="w", format="mp4")
        return av.open(self.target, mode="w", format="flv")

    def _drain_audio_queue(self, a_stream, container) -> int:
        if self.audio_queue is None or a_stream is None:
            return 0
        drained = 0
        for _ in range(6):
            try:
                audio_item = self.audio_queue.get_nowait()
            except queue.Empty:
                break
            if isinstance(audio_item, tuple) and len(audio_item) == 2:
                chunk_ts, chunk = audio_item
            else:
                chunk_ts, chunk = time.perf_counter(), audio_item
            self._encode_audio(a_stream, container, chunk, chunk_ts)
            drained += 1
        return drained

    def _ensure_audio_continuity(self, a_stream, container, now: float) -> None:
        if a_stream is None or self.sample_rate <= 0 or self.channels <= 0:
            return
        if self._start_time <= 0:
            self._start_time = now
        expected_pts = int(max(0.0, now - self._start_time) * self.sample_rate)
        max_lag = max(int(self.sample_rate * 0.12), int(self.sample_rate / max(1, self.fps)))
        deficit = expected_pts - self._a_pts
        if deficit <= max_lag:
            return
        samples = max(256, min(deficit, int(self.sample_rate / max(1, self.fps))))
        silence = b"\x00" * samples * self.channels * 2
        silence_ts = self._start_time + (self._a_pts / self.sample_rate)
        self._encode_audio(a_stream, container, silence, silence_ts)

    @staticmethod
    def _flush_stream(stream, container) -> None:
        if stream is None or container is None:
            return
        for packet in stream.encode():
            container.mux(packet)

    def _run(self) -> None:
        self.running = True
        container = None
        v_stream = None
        a_stream = None
        self._start_time = time.perf_counter()
        self._v_pts = 0
        self._a_pts = 0
        self._last_v_pts = -1
        try:
            container = self._open_container()
            v_stream = self._build_video_stream(container)
            a_stream = self._build_audio_stream(container) if self.audio_queue is not None else None
            while not self._stop_event.is_set() or not self._video_queue.empty():
                now = time.perf_counter()
                try:
                    frame_item = self._video_queue.get(timeout=0.05)
                except queue.Empty:
                    frame_item = None
                if frame_item is not None and np is not None:
                    capture_ts, frame_arr = frame_item
                    self._encode_video(v_stream, container, frame_arr, capture_ts)
                drained = self._drain_audio_queue(a_stream, container)
                if drained == 0:
                    self._ensure_audio_continuity(a_stream, container, now)
        except Exception as exc:
            self.error = str(exc)
        finally:
            try:
                self._flush_stream(v_stream, container)
                self._flush_stream(a_stream, container)
            except Exception:
                pass
            if container is not None:
                try:
                    container.close()
                except Exception:
                    pass
            self.running = False
