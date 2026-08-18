from __future__ import annotations

import copy
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class LayerType(str, Enum):
    CAMERA = "camera"
    SCREEN = "screen"
    WINDOW = "window"
    NETWORK = "network"
    VIDEO = "video"
    PNG = "png"


class AudioTrackKind(str, Enum):
    AUTO = "auto"
    SYSTEM = "system"
    WINDOW = "window"
    MICROPHONE = "microphone"
    MASTER = "master"


@dataclass(slots=True)
class TransitionConfig:
    mode: str = "cut"
    duration_ms: int = 600
    wipe_shape: str = "horizontal"
    dve_mode: str = "push"
    media_path: str = ""

    def clone(self) -> "TransitionConfig":
        return TransitionConfig(
            mode=self.mode,
            duration_ms=self.duration_ms,
            wipe_shape=self.wipe_shape,
            dve_mode=self.dve_mode,
            media_path=self.media_path,
        )


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@dataclass(slots=True)
class Layer:
    id: str
    name: str
    layer_type: LayerType
    enabled: bool = True
    locked: bool = False
    x: int = 100
    y: int = 100
    width: int = 640
    height: int = 360
    saturation: float = 1.0
    contrast: float = 1.0
    color_temp: int = 0
    mosaic: int = 0
    volume: float = 1.0
    priority: int = 0
    source: dict[str, Any] = field(default_factory=dict)

    def clone(self) -> "Layer":
        return Layer(
            id=self.id,
            name=self.name,
            layer_type=self.layer_type,
            enabled=self.enabled,
            locked=self.locked,
            x=self.x,
            y=self.y,
            width=self.width,
            height=self.height,
            saturation=self.saturation,
            contrast=self.contrast,
            color_temp=self.color_temp,
            mosaic=self.mosaic,
            volume=self.volume,
            priority=self.priority,
            source=copy.deepcopy(self.source),
        )


@dataclass(slots=True)
class AudioTrack:
    id: str
    name: str
    kind: AudioTrackKind
    enabled: bool = True
    muted: bool = False
    volume: float = 1.0
    amplitude: float = 1.0
    low_gain: float = 1.0
    mid_gain: float = 1.0
    high_gain: float = 1.0
    layer_id: str = ""
    scene_id: str = ""
    pid: int | None = None
    process_name: str | None = None
    device_index: int | None = None
    note: str = ""

    def clone(self) -> "AudioTrack":
        return AudioTrack(
            id=self.id,
            name=self.name,
            kind=self.kind,
            enabled=self.enabled,
            muted=self.muted,
            volume=self.volume,
            amplitude=self.amplitude,
            low_gain=self.low_gain,
            mid_gain=self.mid_gain,
            high_gain=self.high_gain,
            layer_id=self.layer_id,
            scene_id=self.scene_id,
            pid=self.pid,
            process_name=self.process_name,
            device_index=self.device_index,
            note=self.note,
        )


@dataclass(slots=True)
class Scene:
    id: str
    name: str
    layers: list[Layer] = field(default_factory=list)
    is_placeholder: bool = False

    def clone(self) -> "Scene":
        return Scene(
            id=self.id,
            name=self.name,
            layers=[layer.clone() for layer in self.layers],
            is_placeholder=self.is_placeholder,
        )


@dataclass(slots=True)
class AudioDiagnostics:
    device_name: str = "N/A"
    device_index: int = -1
    target_pid: int | None = None
    target_process: str | None = None
    session_hit: bool = False
    level: float = 0.0
    chunk_empty: bool = True
    backend: str = "none"
    note: str = ""
