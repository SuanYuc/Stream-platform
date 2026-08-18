from __future__ import annotations

import threading
import time
from collections import deque

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtGui import QImage

from nsy_broadcasting_platform.audio.audio_controller import AudioController
from nsy_broadcasting_platform.capture.source_manager import SourceManager
from nsy_broadcasting_platform.compat import CV2
from nsy_broadcasting_platform.core.state import AppState
from nsy_broadcasting_platform.output.output_manager import OutputManager
from nsy_broadcasting_platform.render.compositor import Compositor
from nsy_broadcasting_platform.render.transitions import ProgramTransition
cv2 = CV2.module


class RenderThread(QThread):
    edit_frame_ready = pyqtSignal(object)
    program_frame_ready = pyqtSignal(object)
    scene_preview_ready = pyqtSignal(str, object)
    layer_metrics_ready = pyqtSignal(object)

    def __init__(
        self,
        state: AppState,
        source_manager: SourceManager,
        audio_controller: AudioController,
        output_manager: OutputManager,
        width: int,
        height: int,
        fps: int,
        delay_ms: int = 0,
    ) -> None:
        super().__init__()
        self.state = state
        self.source_manager = source_manager
        self.audio_controller = audio_controller
        self.output_manager = output_manager
        self.compositor = Compositor(source_manager, width=width, height=height)
        self.thumbnail_compositor = Compositor(source_manager, width=width, height=height)
        self.transition = ProgramTransition(width=width, height=height)
        self.fps = max(1, fps)
        self._delay_ms = max(0, delay_ms)
        self._delay_lock = threading.Lock()
        self._stop_event = threading.Event()
        self._thumb_interval_s = 1.0
        self._last_thumb_at = 0.0
        self._thumb_thread: threading.Thread | None = None
        self._thumb_lock = threading.Lock()
        self._canvas_revision = 0

    def set_delay_ms(self, delay_ms: int) -> None:
        with self._delay_lock:
            self._delay_ms = max(0, int(delay_ms))

    def set_fps(self, fps: int) -> None:
        self.fps = max(1, int(fps))

    def set_thumbnail_interval(self, interval_s: float) -> None:
        self._thumb_interval_s = max(0.3, float(interval_s))

    def set_canvas_size(self, width: int, height: int) -> None:
        self.compositor.set_canvas_size(width, height)
        self.thumbnail_compositor.set_canvas_size(width, height)
        self.transition.set_canvas_size(width, height)
        with self._delay_lock:
            self._canvas_revision += 1

    def stop(self) -> None:
        self._stop_event.set()
        self.wait(1500)
        self.transition.close()
        self.compositor.close()
        self.thumbnail_compositor.close()

    def _frame_to_qimage(self, frame) -> QImage | None:
        if frame is None:
            return None
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB) if cv2 is not None else frame[:, :, ::-1].copy()
        return QImage(rgb.data, w, h, rgb.strides[0], QImage.Format.Format_RGB888).copy()

    @staticmethod
    def _find_active_scene(scenes, active_scene_id: str | None):
        return next((scene for scene in scenes if scene.id == active_scene_id), None)

    def _schedule_thumbnails(self, scenes, active_scene_id: str | None) -> None:
        now = time.perf_counter()
        if (now - self._last_thumb_at) < self._thumb_interval_s:
            return
        with self._thumb_lock:
            if self._thumb_thread is not None and self._thumb_thread.is_alive():
                return
            self._last_thumb_at = now
            self._thumb_thread = threading.Thread(
                target=self._generate_scene_thumbnails,
                args=(scenes, active_scene_id),
                name="scene-thumbs",
                daemon=True,
            )
            self._thumb_thread.start()

    def _generate_scene_thumbnails(self, scenes, active_scene_id: str | None) -> None:
        for scene in scenes:
            if self._stop_event.is_set():
                break
            self.thumbnail_compositor.reset_temporal_state()
            result = self.thumbnail_compositor.render_scene(scene)
            image = self._frame_to_qimage(result.frame)
            if image is not None:
                self.scene_preview_ready.emit(scene.id, image)

    @staticmethod
    def _update_audio_target(
        audio_controller: AudioController,
        state: AppState,
        active_scene,
        last_audio_target: tuple,
    ) -> tuple:
        if active_scene is None:
            return last_audio_target
        track = state.resolve_audio_track_profile(active_scene.id)
        strict_isolation = state.audio_isolation_requested(active_scene.id)
        target = (
            track.id,
            track.kind.value,
            track.pid,
            track.process_name,
            strict_isolation,
            track.enabled,
            track.muted,
            round(track.volume, 4),
            round(track.amplitude, 4),
            round(track.low_gain, 4),
            round(track.mid_gain, 4),
            round(track.high_gain, 4),
        )
        if target != last_audio_target:
            audio_controller.set_track_profile(track, strict_isolation=strict_isolation)
            return target
        return last_audio_target

    @staticmethod
    def _program_frame(frame, delayed_frames: deque, delay_ms: int, now_s: float, previous_frame):
        if delay_ms <= 0:
            delayed_frames.clear()
            return frame.copy()

        delay_s = max(0.0, delay_ms / 1000.0)
        target_s = now_s - delay_s
        delayed_frames.append((now_s, frame.copy()))

        max_keep_s = max(delay_s + 1.5, 3.0)
        while len(delayed_frames) > 2 and (now_s - delayed_frames[0][0]) > max_keep_s:
            delayed_frames.popleft()

        # 缓冲未填满时保持上一帧，避免延时刚开启或调整时在当前帧和旧帧之间闪烁。
        if not delayed_frames or delayed_frames[0][0] > target_s:
            return previous_frame.copy() if previous_frame is not None else delayed_frames[0][1].copy()

        # 保留最接近目标时间且不晚于目标时间的一帧。只丢弃更老的帧，不跨越目标点。
        while len(delayed_frames) >= 2 and delayed_frames[1][0] <= target_s:
            delayed_frames.popleft()
        return delayed_frames[0][1].copy()

    def run(self) -> None:
        delayed_frames = deque()
        last_program_frame = None
        last_program_source_frame = None
        last_audio_target: tuple = ()
        last_scene_id: str | None = None
        last_program_scene_key: tuple[str | None, bool] = (None, False)
        last_emergency_active = False
        last_canvas_revision = 0

        while not self._stop_event.is_set():
            start = time.perf_counter()
            interval = 1.0 / max(1, int(self.fps))
            with self._delay_lock:
                canvas_revision = self._canvas_revision
            if canvas_revision != last_canvas_revision:
                delayed_frames.clear()
                last_program_frame = None
                last_program_source_frame = None
                self.transition.cancel()
                self.compositor.reset_temporal_state()
                self.thumbnail_compositor.reset_temporal_state()
                last_canvas_revision = canvas_revision

            scenes = self.state.snapshot_scenes()
            active_scene_id = self.state.get_active_scene_id()
            emergency_active = self.state.get_emergency_placeholder_active()
            placeholder_scene_id = self.state.get_placeholder_scene_id()
            active_scene = self._find_active_scene(scenes, active_scene_id)
            program_scene_id = placeholder_scene_id if emergency_active else active_scene_id
            program_scene = self._find_active_scene(scenes, program_scene_id)
            scene_changed = active_scene_id != last_scene_id
            emergency_changed = emergency_active != last_emergency_active
            start_scene_transition = False

            if scene_changed:
                if not emergency_active and not last_emergency_active and last_scene_id is not None:
                    start_scene_transition = True
                    delayed_frames.clear()
                    last_program_frame = None
                elif not emergency_active:
                    delayed_frames.clear()
                    last_program_frame = None
                    last_program_source_frame = None
                    self.transition.cancel()
                self.compositor.reset_temporal_state()
                self.thumbnail_compositor.reset_temporal_state()
                last_scene_id = active_scene_id

            program_scene_key = (program_scene_id, emergency_active)
            if program_scene_key != last_program_scene_key:
                delayed_frames.clear()
                last_program_frame = None
                if emergency_active or last_program_scene_key[1] or emergency_changed:
                    last_program_source_frame = None
                    self.transition.cancel()
                self.compositor.reset_temporal_state()
                last_program_scene_key = program_scene_key
            last_emergency_active = emergency_active

            self.source_manager.sync_scenes(scenes)
            last_audio_target = self._update_audio_target(
                self.audio_controller,
                self.state,
                active_scene,
                last_audio_target,
            )

            render_result = self.compositor.render_scene(active_scene)
            frame = render_result.frame
            if frame is None:
                self.msleep(20)
                continue

            program_result = render_result
            if program_scene_id != active_scene_id:
                program_result = self.compositor.render_scene(program_scene)
            program_source_frame = program_result.frame
            if program_source_frame is None:
                program_source_frame = frame

            if emergency_active:
                self.transition.cancel()
            else:
                transition_config = self.state.get_transition_config()
                if (
                    start_scene_transition
                    and transition_config.mode != "cut"
                    and transition_config.duration_ms > 0
                    and last_program_source_frame is not None
                ):
                    self.transition.start(transition_config, last_program_source_frame, time.perf_counter())
                transition_frame_active = self.transition.active
                if self.transition.active:
                    program_source_frame, _transition_done = self.transition.render(program_source_frame, time.perf_counter())
            if emergency_active:
                transition_frame_active = False

            now_s = time.perf_counter()
            with self._delay_lock:
                delay_ms = self._delay_ms
            effective_delay_ms = 0 if transition_frame_active else delay_ms
            if transition_frame_active:
                delayed_frames.clear()
                program_frame = program_source_frame.copy()
            else:
                program_frame = self._program_frame(
                    program_source_frame,
                    delayed_frames,
                    effective_delay_ms,
                    now_s,
                    last_program_frame,
                )
            last_program_frame = program_frame.copy()
            if not emergency_active:
                last_program_source_frame = program_source_frame.copy()

            self.edit_frame_ready.emit(frame)
            self.program_frame_ready.emit(program_frame)
            layer_metrics = dict(render_result.layer_metrics)
            if program_result is not render_result:
                layer_metrics.update(program_result.layer_metrics)
            if layer_metrics:
                self.layer_metrics_ready.emit(layer_metrics)
            # 推流和录制只接收节目输出帧，确保远端画面与节目监看一致。
            self.output_manager.push_video_frame(program_frame)
            self._schedule_thumbnails(scenes, active_scene_id)

            self._stop_event.wait(max(0.001, interval - (time.perf_counter() - start)))

