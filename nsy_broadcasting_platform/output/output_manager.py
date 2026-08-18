from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from nsy_broadcasting_platform.audio.audio_controller import AudioController
from nsy_broadcasting_platform.compat import AV
from nsy_broadcasting_platform.output.encoder_worker import EncoderWorker


class OutputManager:
    def __init__(
        self,
        audio_controller: AudioController,
        width: int,
        height: int,
        fps: int,
        sample_rate: int,
        channels: int,
        record_bitrate: str,
        stream_bitrate: str,
        record_encoder: str = "auto",
        stream_encoder: str = "auto",
    ) -> None:
        self.audio_controller = audio_controller
        self.width = width
        self.height = height
        self.fps = fps
        self.sample_rate = sample_rate
        self.channels = channels
        self.record_bitrate = record_bitrate
        self.stream_bitrate = stream_bitrate
        self.record_encoder = record_encoder
        self.stream_encoder = stream_encoder

        self._lock = threading.RLock()
        self._record_worker: EncoderWorker | None = None
        self._stream_worker: EncoderWorker | None = None

    def set_video_size(self, width: int, height: int) -> None:
        with self._lock:
            self.width = max(1, int(width))
            self.height = max(1, int(height))

    def set_fps(self, fps: int) -> None:
        with self._lock:
            self.fps = max(1, int(fps))

    def build_record_path(self, output_dir: Path) -> str:
        output_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        return str(output_dir / f"record_{ts}.mp4")

    def _ensure_av(self, action: str) -> tuple[bool, str] | None:
        if AV.module is None:
            return False, f"当前 PyAV 不可用，无法{action}。"
        return None

    def set_encoding_profile(
        self,
        *,
        record_bitrate: str | None = None,
        stream_bitrate: str | None = None,
        record_encoder: str | None = None,
        stream_encoder: str | None = None,
    ) -> None:
        with self._lock:
            if record_bitrate:
                self.record_bitrate = str(record_bitrate).strip()
            if stream_bitrate:
                self.stream_bitrate = str(stream_bitrate).strip()
            if record_encoder:
                self.record_encoder = str(record_encoder).strip().lower()
            if stream_encoder:
                self.stream_encoder = str(stream_encoder).strip().lower()

    def _create_worker(self, *, name: str, mode: str, target: str, bitrate: str, encoder_mode: str) -> EncoderWorker:
        audio_q = self.audio_controller.register_listener(name)
        return EncoderWorker(
            name=name,
            mode=mode,
            target=target,
            width=self.width,
            height=self.height,
            fps=self.fps,
            sample_rate=self.sample_rate,
            channels=self.channels,
            audio_queue=audio_q,
            bitrate=bitrate,
            encoder_mode=encoder_mode,
        )

    def _start_worker(
        self,
        *,
        kind: str,
        target: str,
        bitrate: str,
        encoder_mode: str,
        current: EncoderWorker | None,
        running_message: str,
    ) -> tuple[bool, str]:
        av_state = self._ensure_av("启动输出")
        if av_state is not None:
            return av_state
        if current and current.running:
            return False, running_message
        worker = self._create_worker(name=kind, mode=kind, target=target, bitrate=bitrate, encoder_mode=encoder_mode)
        worker.start()
        if kind == "record":
            self._record_worker = worker
            return True, f"开始录制：{target}"
        self._stream_worker = worker
        return True, f"开始推流：{target}"

    def _stop_worker(self, *, kind: str, worker: EncoderWorker | None, not_running_message: str) -> tuple[bool, str]:
        if kind == "record":
            self._record_worker = None
        else:
            self._stream_worker = None
        self.audio_controller.unregister_listener(kind)
        if worker is None:
            return False, not_running_message
        worker.stop()
        if worker.error:
            action = "录制" if kind == "record" else "推流"
            return False, f"{action}停止后出现错误：{worker.error}"
        return True, "录制已停止" if kind == "record" else "推流已停止"

    def start_record(self, file_path: str) -> tuple[bool, str]:
        with self._lock:
            return self._start_worker(
                kind="record",
                target=file_path,
                bitrate=self.record_bitrate,
                encoder_mode=self.record_encoder,
                current=self._record_worker,
                running_message="录制已在进行中",
            )

    def stop_record(self) -> tuple[bool, str]:
        with self._lock:
            return self._stop_worker(kind="record", worker=self._record_worker, not_running_message="当前未在录制")

    def start_stream(self, rtmp_url: str) -> tuple[bool, str]:
        with self._lock:
            return self._start_worker(
                kind="stream",
                target=rtmp_url,
                bitrate=self.stream_bitrate,
                encoder_mode=self.stream_encoder,
                current=self._stream_worker,
                running_message="推流已在进行中",
            )

    def stop_stream(self) -> tuple[bool, str]:
        with self._lock:
            return self._stop_worker(kind="stream", worker=self._stream_worker, not_running_message="当前未在推流")

    def push_video_frame(self, frame) -> None:
        with self._lock:
            workers = [worker for worker in (self._record_worker, self._stream_worker) if worker and worker.running]
        for worker in workers:
            worker.push_video(frame)

    def stop_all(self) -> list[str]:
        messages: list[str] = []
        ok, msg = self.stop_record()
        if ok or "当前未在录制" not in msg:
            messages.append(msg)
        ok, msg = self.stop_stream()
        if ok or "当前未在推流" not in msg:
            messages.append(msg)
        return messages

    def status(self) -> dict[str, object]:
        with self._lock:
            rec = self._record_worker
            stm = self._stream_worker
        return {
            "record": "录制中" if rec and rec.running else "未录制",
            "stream": "推流中" if stm and stm.running else "未推流",
            "record_error": rec.error if rec and rec.error else "",
            "stream_error": stm.error if stm and stm.error else "",
            "record_encoder": self.record_encoder,
            "stream_encoder": self.stream_encoder,
            "record_bitrate": self.record_bitrate,
            "stream_bitrate": stm.bitrate if stm and stm.running else self.stream_bitrate,
            "configured_stream_bitrate": self.stream_bitrate,
            "video_size": f"{self.width}x{self.height}",
            "fps": str(self.fps),
            "record_stats": rec.stats() if rec else {},
            "stream_stats": stm.stats() if stm else {},
        }
