from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable

from nsy_broadcasting_platform.models import AudioTrack, AudioTrackKind, Layer, LayerType, Scene, TransitionConfig, new_id


class AppState:
    """应用状态中心，统一管理场景、图层与当前选择。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._scenes: list[Scene] = []
        self._active_scene_id: str | None = None
        self._placeholder_scene_id: str | None = None
        self._placeholder_video_layer_id: str | None = None
        self._emergency_placeholder_active = False
        self._transition_config = TransitionConfig()
        self._audio_source_key: str = "auto"
        self._audio_track_settings: dict[str, dict[str, float | bool]] = {}
        self._init_default_scene()

    def _init_default_scene(self) -> None:
        normal_scenes = [Scene(id=new_id("scene"), name=f"场景 {idx}") for idx in range(1, 10)]
        placeholder_scene = Scene(id=new_id("scene"), name="紧急占位场景", is_placeholder=True)
        placeholder_scene.layers.append(self._build_default_placeholder_layer())
        self._scenes.extend(normal_scenes)
        self._scenes.append(placeholder_scene)
        self._active_scene_id = normal_scenes[0].id
        self._placeholder_scene_id = placeholder_scene.id

    def _ensure_placeholder_scene_last_locked(self) -> None:
        """紧急占位场景固定排在场景列表最后，避免新增场景把它挤到中间。"""
        if not self._scenes:
            return
        normal_scenes = [scene for scene in self._scenes if not scene.is_placeholder]
        placeholder_scenes = [scene for scene in self._scenes if scene.is_placeholder]
        if not placeholder_scenes:
            return
        self._scenes = normal_scenes + placeholder_scenes
        if self._placeholder_scene_id is None:
            self._placeholder_scene_id = placeholder_scenes[-1].id
        if normal_scenes and self._active_scene_id in {scene.id for scene in placeholder_scenes}:
            self._active_scene_id = normal_scenes[0].id

    @staticmethod
    def _default_placeholder_image_path() -> str:
        return str(Path(__file__).resolve().parents[1] / "assets" / "default_emergency_placeholder.png")

    def _build_default_placeholder_layer(self) -> Layer:
        return Layer(
            id=new_id("layer"),
            name="默认占位测试卡",
            layer_type=LayerType.PNG,
            x=0,
            y=0,
            width=1280,
            height=720,
            source={"image_path": self._default_placeholder_image_path()},
        )

    def _scene_ref(self, scene_id: str | None) -> Scene | None:
        if scene_id is None:
            return None
        return next((scene for scene in self._scenes if scene.id == scene_id), None)

    def _resolve_scene_ref(self, scene_id: str | None) -> Scene | None:
        return self._scene_ref(scene_id or self._active_scene_id)

    @staticmethod
    def _layer_ref(scene: Scene | None, layer_id: str) -> Layer | None:
        if scene is None:
            return None
        return next((layer for layer in scene.layers if layer.id == layer_id), None)

    @staticmethod
    def _layers_top_to_bottom(scene: Scene) -> list[Layer]:
        return sorted(scene.layers, key=lambda layer: layer.priority, reverse=True)

    @staticmethod
    def _next_layer_priority(scene: Scene) -> int:
        return len(scene.layers) + 1

    @staticmethod
    def _layers_bottom_to_top(scene: Scene) -> list[Layer]:
        return [
            layer
            for _idx, layer in sorted(
                enumerate(scene.layers),
                key=lambda item: (item[1].priority if item[1].priority > 0 else item[0] + 1, item[0]),
            )
        ]

    @classmethod
    def _normalize_layer_priorities(cls, scene: Scene) -> None:
        """把图层优先级整理为 1..N，数值越大越靠上。"""
        ordered_layers = cls._layers_bottom_to_top(scene)
        scene.layers = ordered_layers
        cls._assign_layer_priorities(scene)

    @staticmethod
    def _assign_layer_priorities(scene: Scene) -> None:
        for priority, layer in enumerate(scene.layers, start=1):
            layer.priority = priority

    def _normalize_all_layer_priorities_locked(self) -> None:
        for scene in self._scenes:
            self._normalize_layer_priorities(scene)

    def snapshot_scenes(self) -> list[Scene]:
        with self._lock:
            self._ensure_placeholder_scene_last_locked()
            self._normalize_all_layer_priorities_locked()
            return [scene.clone() for scene in self._scenes]

    def get_active_scene(self) -> Scene | None:
        with self._lock:
            self._ensure_placeholder_scene_last_locked()
            self._normalize_all_layer_priorities_locked()
            scene = self._scene_ref(self._active_scene_id)
            return None if scene is None else scene.clone()

    def get_scene_by_id(self, scene_id: str) -> Scene | None:
        with self._lock:
            self._ensure_placeholder_scene_last_locked()
            self._normalize_all_layer_priorities_locked()
            scene = self._scene_ref(scene_id)
            return None if scene is None else scene.clone()

    def get_active_scene_id(self) -> str | None:
        with self._lock:
            return self._active_scene_id

    def set_active_scene(self, scene_id: str) -> bool:
        with self._lock:
            scene = self._scene_ref(scene_id)
            if scene is None or scene.is_placeholder:
                return False
            self._active_scene_id = scene_id
            return True

    def resize_canvas(self, old_width: int, old_height: int, new_width: int, new_height: int) -> None:
        """输出画布比例变化时，按比例同步缩放所有场景内的图层坐标。"""
        with self._lock:
            old_width = max(1, int(old_width))
            old_height = max(1, int(old_height))
            new_width = max(1, int(new_width))
            new_height = max(1, int(new_height))
            if old_width == new_width and old_height == new_height:
                return
            sx = new_width / old_width
            sy = new_height / old_height
            for scene in self._scenes:
                for layer in scene.layers:
                    layer.x = int(round(layer.x * sx))
                    layer.y = int(round(layer.y * sy))
                    layer.width = max(1, int(round(layer.width * sx)))
                    layer.height = max(1, int(round(layer.height * sy)))

    def add_scene(self, name: str | None = None) -> Scene:
        with self._lock:
            self._ensure_placeholder_scene_last_locked()
            normal_count = sum(1 for scene in self._scenes if not scene.is_placeholder)
            scene = Scene(id=new_id("scene"), name=name or f"场景 {normal_count + 1}")
            insert_at = next((idx for idx, item in enumerate(self._scenes) if item.is_placeholder), len(self._scenes))
            self._scenes.insert(insert_at, scene)
            self._active_scene_id = scene.id
            return scene.clone()

    def set_normal_scene_count(self, count: int) -> int:
        """按场景网格数量裁剪或补齐普通场景，紧急占位场景始终保留。"""
        with self._lock:
            target_count = max(1, int(count))
            self._ensure_placeholder_scene_last_locked()
            normal_scenes = [scene for scene in self._scenes if not scene.is_placeholder]
            placeholder_scenes = [scene for scene in self._scenes if scene.is_placeholder]

            removed_ids: set[str] = set()
            if len(normal_scenes) > target_count:
                removed_ids = {scene.id for scene in normal_scenes[target_count:]}
                normal_scenes = normal_scenes[:target_count]
            while len(normal_scenes) < target_count:
                next_index = len(normal_scenes) + 1
                normal_scenes.append(Scene(id=new_id("scene"), name=f"场景 {next_index}"))

            self._scenes = normal_scenes + placeholder_scenes
            normal_ids = {scene.id for scene in normal_scenes}
            if self._active_scene_id in removed_ids or self._active_scene_id not in normal_ids:
                self._active_scene_id = normal_scenes[0].id
            return len(normal_scenes)

    def delete_scene(self, scene_id: str) -> bool:
        with self._lock:
            scene_ids = [scene.id for scene in self._scenes]
            try:
                idx = scene_ids.index(scene_id)
            except ValueError:
                return False
            if self._scenes[idx].is_placeholder:
                return False
            if sum(1 for scene in self._scenes if not scene.is_placeholder) <= 1:
                return False
            self._scenes.pop(idx)
            self._ensure_placeholder_scene_last_locked()
            if self._active_scene_id == scene_id:
                normal_scenes = [scene for scene in self._scenes if not scene.is_placeholder]
                self._active_scene_id = normal_scenes[max(0, min(idx - 1, len(normal_scenes) - 1))].id
            return True

    def clear_scene_layers(self, scene_id: str | None = None) -> bool:
        """清空指定普通场景中的全部图层，不删除场景本身。"""
        with self._lock:
            scene = self._resolve_scene_ref(scene_id)
            if scene is None or scene.is_placeholder:
                return False
            scene.layers.clear()
            return True

    def rename_scene(self, scene_id: str, name: str) -> bool:
        with self._lock:
            scene = self._scene_ref(scene_id)
            if scene is None:
                return False
            scene.name = name
            return True

    def get_placeholder_scene_id(self) -> str | None:
        with self._lock:
            return self._placeholder_scene_id

    def get_emergency_placeholder_active(self) -> bool:
        with self._lock:
            return self._emergency_placeholder_active

    def set_emergency_placeholder_active(self, enabled: bool) -> None:
        with self._lock:
            self._emergency_placeholder_active = bool(enabled)

    def set_placeholder_video(self, video_path: str, width: int, height: int) -> bool:
        with self._lock:
            scene = self._scene_ref(self._placeholder_scene_id)
            if scene is None:
                return False
            path = str(Path(video_path))
            layer = self._layer_ref(scene, self._placeholder_video_layer_id or "")
            if layer is None:
                layer = Layer(
                    id=new_id("layer"),
                    name=f"占位视频 {Path(path).name}",
                    layer_type=LayerType.VIDEO,
                    x=0,
                    y=0,
                    width=width,
                    height=height,
                    source={"video_path": path},
                )
                self._placeholder_video_layer_id = layer.id
                scene.layers.append(layer)
            else:
                layer.name = f"占位视频 {Path(path).name}"
                layer.layer_type = LayerType.VIDEO
                layer.source["video_path"] = path
                layer.x = 0
                layer.y = 0
                layer.width = width
                layer.height = height
            self._normalize_layer_priorities(scene)
            return True

    def get_transition_config(self) -> TransitionConfig:
        with self._lock:
            return self._transition_config.clone()

    def set_transition_config(self, config: TransitionConfig) -> None:
        with self._lock:
            self._transition_config = TransitionConfig(
                mode=str(config.mode or "cut"),
                duration_ms=max(0, min(10000, int(config.duration_ms))),
                wipe_shape=str(config.wipe_shape or "horizontal"),
                dve_mode=str(config.dve_mode or "push"),
                media_path=str(config.media_path or ""),
            )

    def add_layer(self, layer: Layer, scene_id: str | None = None) -> bool:
        with self._lock:
            scene = self._resolve_scene_ref(scene_id)
            if scene is None:
                return False
            self._normalize_layer_priorities(scene)
            layer.priority = self._next_layer_priority(scene)
            scene.layers.append(layer)
            self._normalize_layer_priorities(scene)
            return True

    def remove_layer(self, layer_id: str, scene_id: str | None = None) -> bool:
        with self._lock:
            scene = self._resolve_scene_ref(scene_id)
            if scene is None:
                return False
            layer = self._layer_ref(scene, layer_id)
            if layer is None:
                return False
            scene.layers.remove(layer)
            self._normalize_layer_priorities(scene)
            return True

    def update_layer(self, layer_id: str, updater: Callable[[Layer], None], scene_id: str | None = None) -> bool:
        with self._lock:
            layer = self._layer_ref(self._resolve_scene_ref(scene_id), layer_id)
            if layer is None:
                return False
            updater(layer)
            return True

    def find_layer(self, layer_id: str, scene_id: str | None = None) -> Layer | None:
        with self._lock:
            layer = self._layer_ref(self._resolve_scene_ref(scene_id), layer_id)
            return None if layer is None else layer.clone()

    def reorder_layers(self, ordered_layer_ids: list[str], scene_id: str | None = None) -> bool:
        with self._lock:
            scene = self._resolve_scene_ref(scene_id)
            if scene is None:
                return False
            ordered_ids = set(ordered_layer_ids)
            layer_map = {layer.id: layer for layer in scene.layers}
            scene.layers = [layer_map[lid] for lid in ordered_layer_ids if lid in layer_map] + [
                layer for layer in scene.layers if layer.id not in ordered_ids
            ]
            self._assign_layer_priorities(scene)
            return True

    def set_layer_priority(self, layer_id: str, priority: int, scene_id: str | None = None) -> bool:
        with self._lock:
            scene = self._resolve_scene_ref(scene_id)
            if scene is None:
                return False
            self._normalize_layer_priorities(scene)
            layer = self._layer_ref(scene, layer_id)
            if layer is None:
                return False
            target_priority = max(1, min(len(scene.layers), int(priority)))
            ordered_layers = [item for item in self._layers_bottom_to_top(scene) if item.id != layer_id]
            ordered_layers.insert(target_priority - 1, layer)
            scene.layers = ordered_layers
            self._assign_layer_priorities(scene)
            return True

    def first_window_audio_target(self, scene_id: str | None = None) -> tuple[int | None, str | None]:
        pid, process, _volume = self.first_window_audio_profile(scene_id)
        return pid, process

    def first_window_audio_profile(self, scene_id: str | None = None) -> tuple[int | None, str | None, float]:
        with self._lock:
            scene = self._resolve_scene_ref(scene_id)
            if scene is None:
                return None, None, 1.0
            self._normalize_layer_priorities(scene)
            for layer in self._layers_top_to_bottom(scene):
                if layer.enabled and layer.layer_type == LayerType.WINDOW:
                    return layer.source.get("pid"), layer.source.get("process_name"), max(0.0, layer.volume)
            return None, None, 1.0

    def set_audio_capture_source(self, source_key: str | None) -> None:
        """保存用户选择的音轨来源。

        source_key 取值：
        - auto: 自动跟随当前场景中最上层可用窗口
        - system: 直接采集系统回放声音
        - microphone: 采集默认麦克风输入
        - master: 采集系统总音轨
        - layer_id: 指定某个窗口图层作为音频会话识别目标
        """
        with self._lock:
            self._audio_source_key = source_key or "auto"

    def get_audio_capture_source(self) -> str:
        with self._lock:
            return self._audio_source_key

    def set_audio_track_params(
        self,
        track_id: str,
        *,
        volume: float | None = None,
        muted: bool | None = None,
        amplitude: float | None = None,
        low_gain: float | None = None,
        mid_gain: float | None = None,
        high_gain: float | None = None,
    ) -> None:
        with self._lock:
            settings = self._audio_track_settings.setdefault(track_id, {})
            if volume is not None:
                settings["volume"] = max(0.0, min(4.0, float(volume)))
            if muted is not None:
                settings["muted"] = bool(muted)
            if amplitude is not None:
                settings["amplitude"] = max(0.0, min(4.0, float(amplitude)))
            if low_gain is not None:
                settings["low_gain"] = max(0.0, min(4.0, float(low_gain)))
            if mid_gain is not None:
                settings["mid_gain"] = max(0.0, min(4.0, float(mid_gain)))
            if high_gain is not None:
                settings["high_gain"] = max(0.0, min(4.0, float(high_gain)))

    def _apply_audio_track_settings(self, track: AudioTrack) -> AudioTrack:
        settings = self._audio_track_settings.get(track.id, {})
        if "volume" in settings:
            track.volume = float(settings["volume"])
        if "muted" in settings:
            track.muted = bool(settings["muted"])
        if "amplitude" in settings:
            track.amplitude = float(settings["amplitude"])
        if "low_gain" in settings:
            track.low_gain = float(settings["low_gain"])
        if "mid_gain" in settings:
            track.mid_gain = float(settings["mid_gain"])
        if "high_gain" in settings:
            track.high_gain = float(settings["high_gain"])
        return track

    def _window_layer_audio_track(self, layer: Layer, scene_id: str | None = None) -> AudioTrack:
        pid = layer.source.get("pid")
        process_name = layer.source.get("process_name")
        title = layer.source.get("title") or layer.name
        label = f"窗口音轨: {title}"
        note = "" if pid or process_name else "窗口会话信息不足，已保底采集系统声音"
        audio_meta = dict(layer.source.get("_canvas", {}).get("audio") or {})
        track = AudioTrack(
            id=layer.id,
            name=label,
            kind=AudioTrackKind.WINDOW,
            enabled=bool(layer.enabled),
            muted=bool(layer.source.get("muted", audio_meta.get("muted", False))),
            volume=max(0.0, float(audio_meta.get("volume", layer.volume))),
            amplitude=max(0.0, float(audio_meta.get("amplitude", 1.0))),
            low_gain=max(0.0, float(audio_meta.get("low_gain", 1.0))),
            mid_gain=max(0.0, float(audio_meta.get("mid_gain", 1.0))),
            high_gain=max(0.0, float(audio_meta.get("high_gain", 1.0))),
            layer_id=layer.id,
            scene_id=scene_id or "",
            pid=pid,
            process_name=process_name,
            note=note,
        )
        return self._apply_audio_track_settings(track)

    def list_audio_tracks(self, scene_id: str | None = None) -> list[AudioTrack]:
        with self._lock:
            scene = self._resolve_scene_ref(scene_id)
            tracks = [
                self._apply_audio_track_settings(
                    AudioTrack(id="auto", name="自动跟随当前场景窗口", kind=AudioTrackKind.AUTO, note="优先使用当前场景最上层窗口音轨")
                ),
                self._apply_audio_track_settings(
                    AudioTrack(id="system", name="系统声音", kind=AudioTrackKind.SYSTEM, note="采集系统输出混音")
                ),
                self._apply_audio_track_settings(
                    AudioTrack(id="master", name="总音轨", kind=AudioTrackKind.MASTER, note="输出总线，当前等同系统声音")
                ),
                self._apply_audio_track_settings(
                    AudioTrack(id="microphone", name="麦克风", kind=AudioTrackKind.MICROPHONE, note="采集默认麦克风输入")
                ),
            ]
            if scene is None:
                return [track.clone() for track in tracks]
            self._normalize_layer_priorities(scene)
            for layer in self._layers_top_to_bottom(scene):
                if layer.layer_type == LayerType.WINDOW:
                    tracks.append(self._window_layer_audio_track(layer, scene.id))
            return [track.clone() for track in tracks]

    def resolve_audio_track_profile(self, scene_id: str | None = None) -> AudioTrack:
        with self._lock:
            scene = self._resolve_scene_ref(scene_id)
            source_key = self._audio_source_key
            if source_key in ("system", "master"):
                kind = AudioTrackKind.MASTER if source_key == "master" else AudioTrackKind.SYSTEM
                return self._apply_audio_track_settings(
                    AudioTrack(id=source_key, name="总音轨" if source_key == "master" else "系统声音", kind=kind, note="直接采集系统输出")
                ).clone()
            if source_key == "microphone":
                return self._apply_audio_track_settings(
                    AudioTrack(id="microphone", name="麦克风", kind=AudioTrackKind.MICROPHONE, note="采集默认麦克风输入")
                ).clone()
            if scene is None:
                return self._apply_audio_track_settings(
                    AudioTrack(id="system", name="系统声音", kind=AudioTrackKind.SYSTEM, note="未选择场景，已保底采集系统声音")
                ).clone()
            self._normalize_layer_priorities(scene)

            if source_key not in ("", "auto"):
                layer = self._layer_ref(scene, source_key)
                if layer is not None and layer.layer_type == LayerType.WINDOW and layer.enabled:
                    return self._window_layer_audio_track(layer, scene.id).clone()
                return self._apply_audio_track_settings(
                    AudioTrack(id="system", name="系统声音", kind=AudioTrackKind.SYSTEM, note="选定窗口不在当前场景或已停用，已保底采集系统声音")
                ).clone()

            for layer in self._layers_top_to_bottom(scene):
                if layer.enabled and layer.layer_type == LayerType.WINDOW:
                    return self._window_layer_audio_track(layer, scene.id).clone()
            return self._apply_audio_track_settings(
                AudioTrack(id="system", name="系统声音", kind=AudioTrackKind.SYSTEM, note="当前场景无窗口音轨，已保底采集系统声音")
            ).clone()

    def resolve_audio_capture_profile(
        self,
        scene_id: str | None = None,
    ) -> tuple[int | None, str | None, float, str, str]:
        """根据用户选择解析当前实际音频目标，始终保留系统声音兜底。"""
        track = self.resolve_audio_track_profile(scene_id)
        return track.pid, track.process_name, (0.0 if track.muted else track.volume * track.amplitude), track.name, track.note

    def audio_isolation_requested(self, scene_id: str | None = None) -> bool:
        """用户手动选择具体窗口音轨时，启用严格会话隔离。"""
        with self._lock:
            source_key = self._audio_source_key
            if source_key in ("", "auto", "system"):
                return False
            if source_key in ("microphone", "master"):
                return False
            scene = self._resolve_scene_ref(scene_id)
            layer = self._layer_ref(scene, source_key)
            return bool(
                layer is not None
                and layer.enabled
                and layer.layer_type == LayerType.WINDOW
                and (layer.source.get("pid") is not None or layer.source.get("process_name"))
            )
