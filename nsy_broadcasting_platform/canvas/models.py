from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from nsy_broadcasting_platform.models import new_id


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


@dataclass(slots=True)
class CanvasViewport:
    x: int = 0
    y: int = 0
    zoom: float = 1.0

    def to_dict(self) -> dict[str, Any]:
        return {"x": self.x, "y": self.y, "zoom": self.zoom}

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CanvasViewport":
        data = data or {}
        return cls(x=_int(data.get("x")), y=_int(data.get("y")), zoom=max(0.1, _float(data.get("zoom"), 1.0)))


@dataclass(slots=True)
class CanvasOutputFrame:
    x: int = 0
    y: int = 0
    width: int = 1280
    height: int = 720
    aspect_ratio: str = "16:9"

    def to_dict(self) -> dict[str, Any]:
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "aspect_ratio": self.aspect_ratio,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CanvasOutputFrame":
        data = data or {}
        return cls(
            x=_int(data.get("x")),
            y=_int(data.get("y")),
            width=max(1, _int(data.get("width"), 1280)),
            height=max(1, _int(data.get("height"), 720)),
            aspect_ratio=str(data.get("aspect_ratio") or "16:9"),
        )


@dataclass(slots=True)
class CanvasItemModel:
    item_id: str
    type: str
    source_ref: str | None = None
    scene_ref: str | None = None
    parent_item_id: str | None = None
    name: str = ""
    x: int = 0
    y: int = 0
    width: int = 640
    height: int = 360
    rotation: float = 0.0
    opacity: float = 1.0
    visible: bool = True
    locked: bool = False
    z_index: int = 0
    crop: dict[str, int] = field(default_factory=lambda: {"left": 0, "top": 0, "right": 0, "bottom": 0})
    filters: dict[str, Any] = field(default_factory=dict)
    chroma_key: dict[str, Any] = field(default_factory=dict)
    audio: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def clone(self) -> "CanvasItemModel":
        return CanvasItemModel.from_dict(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "type": self.type,
            "source_ref": self.source_ref,
            "scene_ref": self.scene_ref,
            "parent_item_id": self.parent_item_id,
            "name": self.name,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "rotation": self.rotation,
            "opacity": self.opacity,
            "visible": self.visible,
            "locked": self.locked,
            "z_index": self.z_index,
            "crop": dict(self.crop or {}),
            "filters": dict(self.filters or {}),
            "chroma_key": dict(self.chroma_key or {}),
            "audio": dict(self.audio or {}),
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CanvasItemModel":
        data = data or {}
        crop = data.get("crop") or {"left": 0, "top": 0, "right": 0, "bottom": 0}
        return cls(
            item_id=str(data.get("item_id") or new_id("canvas_item")),
            type=str(data.get("type") or "source"),
            source_ref=data.get("source_ref"),
            scene_ref=data.get("scene_ref"),
            parent_item_id=data.get("parent_item_id"),
            name=str(data.get("name") or ""),
            x=_int(data.get("x")),
            y=_int(data.get("y")),
            width=max(1, _int(data.get("width"), 640)),
            height=max(1, _int(data.get("height"), 360)),
            rotation=_float(data.get("rotation"), 0.0),
            opacity=max(0.0, min(1.0, _float(data.get("opacity"), 1.0))),
            visible=bool(data.get("visible", True)),
            locked=bool(data.get("locked", False)),
            z_index=_int(data.get("z_index")),
            crop={
                "left": _int(crop.get("left")),
                "top": _int(crop.get("top")),
                "right": _int(crop.get("right")),
                "bottom": _int(crop.get("bottom")),
            },
            filters=dict(data.get("filters") or {}),
            chroma_key=dict(data.get("chroma_key") or {}),
            audio=dict(data.get("audio") or {}),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(slots=True)
class CanvasGroupModel:
    group_id: str
    name: str
    item_ids: list[str] = field(default_factory=list)
    locked: bool = False
    visible: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)

    def clone(self) -> "CanvasGroupModel":
        return CanvasGroupModel.from_dict(self.to_dict())

    def to_dict(self) -> dict[str, Any]:
        return {
            "group_id": self.group_id,
            "name": self.name,
            "item_ids": list(self.item_ids),
            "locked": self.locked,
            "visible": self.visible,
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CanvasGroupModel":
        data = data or {}
        return cls(
            group_id=str(data.get("group_id") or new_id("canvas_group")),
            name=str(data.get("name") or "组合"),
            item_ids=[str(item_id) for item_id in (data.get("item_ids") or [])],
            locked=bool(data.get("locked", False)),
            visible=bool(data.get("visible", True)),
            metadata=dict(data.get("metadata") or {}),
        )


@dataclass(slots=True)
class CanvasDocument:
    document_id: str = field(default_factory=lambda: new_id("canvas_doc"))
    name: str = "场景画布"
    version: str = "1.0"
    viewport: CanvasViewport = field(default_factory=CanvasViewport)
    output_frame: CanvasOutputFrame = field(default_factory=CanvasOutputFrame)
    items: list[CanvasItemModel] = field(default_factory=list)
    groups: list[CanvasGroupModel] = field(default_factory=list)
    history_metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_utc_now)
    updated_at: str = field(default_factory=_utc_now)

    def clone(self) -> "CanvasDocument":
        return CanvasDocument.from_dict(self.to_dict())

    def touch(self) -> None:
        self.updated_at = _utc_now()

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_id": self.document_id,
            "name": self.name,
            "version": self.version,
            "viewport": self.viewport.to_dict(),
            "output_frame": self.output_frame.to_dict(),
            "items": [item.to_dict() for item in self.items],
            "groups": [group.to_dict() for group in self.groups],
            "history_metadata": dict(self.history_metadata or {}),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CanvasDocument":
        data = data or {}
        return cls(
            document_id=str(data.get("document_id") or new_id("canvas_doc")),
            name=str(data.get("name") or "场景画布"),
            version=str(data.get("version") or "1.0"),
            viewport=CanvasViewport.from_dict(data.get("viewport")),
            output_frame=CanvasOutputFrame.from_dict(data.get("output_frame")),
            items=[CanvasItemModel.from_dict(item) for item in (data.get("items") or [])],
            groups=[CanvasGroupModel.from_dict(group) for group in (data.get("groups") or [])],
            history_metadata=dict(data.get("history_metadata") or {}),
            created_at=str(data.get("created_at") or _utc_now()),
            updated_at=str(data.get("updated_at") or _utc_now()),
        )
