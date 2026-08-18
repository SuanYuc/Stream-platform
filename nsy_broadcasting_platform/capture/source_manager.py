from __future__ import annotations

import threading

from nsy_broadcasting_platform.capture.base import BaseVideoSource
from nsy_broadcasting_platform.capture.camera_source import CameraSource
from nsy_broadcasting_platform.capture.image_source import ImageSource
from nsy_broadcasting_platform.capture.network_source import NetworkSource
from nsy_broadcasting_platform.capture.screen_source import ScreenSource
from nsy_broadcasting_platform.capture.video_file_source import VideoFileSource
from nsy_broadcasting_platform.capture.window_source import WindowSource
from nsy_broadcasting_platform.models import Layer, LayerType, Scene


class SourceManager:
    """统一管理场景中各图层对应的视频采集源。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._sources: dict[str, BaseVideoSource] = {}
        self._signatures: dict[str, tuple] = {}

    def _signature(self, layer: Layer) -> tuple:
        source = layer.source
        return (
            layer.layer_type.value,
            source.get("camera_index"),
            source.get("monitor_index"),
            source.get("hwnd"),
            source.get("image_path"),
            source.get("video_path"),
            source.get("url"),
            source.get("capture_quality"),
            source.get("capture_width"),
            source.get("capture_height"),
            source.get("capture_fps"),
            layer.enabled,
        )

    def _build_source(self, layer: Layer) -> BaseVideoSource | None:
        source = layer.source
        target_width = int(source.get("capture_width", 0) or 0)
        target_height = int(source.get("capture_height", 0) or 0)
        capture_fps = int(source.get("capture_fps", 0) or 0)
        fps = capture_fps if capture_fps > 0 else 30
        if layer.layer_type == LayerType.CAMERA:
            return CameraSource(
                layer.id,
                camera_index=int(source.get("camera_index", 0)),
                fps=fps,
                target_width=target_width,
                target_height=target_height,
            )
        if layer.layer_type == LayerType.SCREEN:
            return ScreenSource(
                layer.id,
                monitor_index=int(source.get("monitor_index", 1)),
                fps=fps,
                target_width=target_width,
                target_height=target_height,
            )
        if layer.layer_type == LayerType.WINDOW:
            return WindowSource(
                layer.id,
                hwnd=int(source.get("hwnd", 0)),
                title=str(source.get("title", "")),
                fps=fps,
                target_width=target_width,
                target_height=target_height,
            )
        if layer.layer_type == LayerType.NETWORK:
            return NetworkSource(
                layer.id,
                url=str(source.get("url", "")),
                fps=fps,
                target_width=target_width,
                target_height=target_height,
            )
        if layer.layer_type == LayerType.VIDEO:
            return VideoFileSource(
                layer.id,
                video_path=str(source.get("video_path", "")),
                fps=fps,
                target_width=target_width,
                target_height=target_height,
            )
        if layer.layer_type == LayerType.PNG:
            return ImageSource(layer.id, image_path=str(source.get("image_path", "")))
        return None

    @staticmethod
    def _enabled_layers(scenes: list[Scene]) -> list[Layer]:
        return [layer for scene in scenes for layer in scene.layers if layer.enabled]

    def _drop_source(self, layer_id: str) -> None:
        src = self._sources.pop(layer_id, None)
        self._signatures.pop(layer_id, None)
        if src is not None:
            src.stop()

    def sync_scene(self, scene: Scene | None) -> None:
        if scene is None:
            self.stop_all()
            return
        self.sync_scenes([scene])

    def sync_scenes(self, scenes: list[Scene] | None) -> None:
        if not scenes:
            self.stop_all()
            return
        with self._lock:
            merged_layers = self._enabled_layers(scenes)
            keep_ids = {layer.id for layer in merged_layers}
            for layer_id in tuple(self._sources.keys() - keep_ids):
                self._drop_source(layer_id)
            for layer in merged_layers:
                signature = self._signature(layer)
                if self._signatures.get(layer.id) == signature and layer.id in self._sources:
                    continue
                self._drop_source(layer.id)
                src = self._build_source(layer)
                if src is None:
                    continue
                src.start()
                self._sources[layer.id] = src
                self._signatures[layer.id] = signature

    def get_frame(self, layer_id: str):
        with self._lock:
            src = self._sources.get(layer_id)
        return None if src is None else src.get_latest_frame()

    def get_source_note(self, layer_id: str) -> str:
        with self._lock:
            src = self._sources.get(layer_id)
        if src is None:
            return "未启动"
        if src.last_error:
            return src.last_error
        if hasattr(src, "last_strategy"):
            return getattr(src, "last_strategy")
        return "正常"

    def stop_all(self) -> None:
        with self._lock:
            items = list(self._sources.values())
            self._sources.clear()
            self._signatures.clear()
        for src in items:
            src.stop()
