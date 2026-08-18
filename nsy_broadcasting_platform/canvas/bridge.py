from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from nsy_broadcasting_platform.canvas.models import CanvasDocument, CanvasGroupModel, CanvasItemModel, CanvasOutputFrame, CanvasViewport
from nsy_broadcasting_platform.models import Layer, LayerType, Scene, new_id


class SceneCanvasAdapter:
    """负责把导播台场景 / 图层转换成画布文档，也负责反向回写。"""

    _DEFAULT_PLACEHOLDER = Path(__file__).resolve().parents[1] / "assets" / "default_emergency_placeholder.png"

    @staticmethod
    def _layer_source_snapshot(layer: Layer) -> dict[str, Any]:
        snapshot = copy.deepcopy(layer.source or {})
        canvas_meta = snapshot.setdefault("_canvas", {})
        audio_meta = dict(canvas_meta.get("audio") or {})
        audio_meta.setdefault("volume", layer.volume)
        audio_meta.setdefault("muted", bool(snapshot.get("muted", False)))
        audio_meta.setdefault("amplitude", 1.0)
        audio_meta.setdefault("low_gain", 1.0)
        audio_meta.setdefault("mid_gain", 1.0)
        audio_meta.setdefault("high_gain", 1.0)
        canvas_meta.update(
            {
                "rotation": 0.0,
                "opacity": 1.0,
                "crop": {"left": 0, "top": 0, "right": 0, "bottom": 0},
                "filters": {
                    "saturation": layer.saturation,
                    "contrast": layer.contrast,
                    "color_temp": layer.color_temp,
                    "mosaic": layer.mosaic,
                },
                "audio": audio_meta,
            }
        )
        return snapshot

    @staticmethod
    def _item_type_for_layer(layer_type: LayerType) -> str:
        return layer_type.value

    @staticmethod
    def _layer_type_from_item_type(item_type: str) -> LayerType:
        try:
            return LayerType(str(item_type))
        except ValueError:
            return LayerType.PNG

    @staticmethod
    def _layer_from_dict(data: dict[str, Any]) -> Layer:
        source = copy.deepcopy(data.get("source") or {})
        return Layer(
            id=str(data.get("id") or new_id("layer")),
            name=str(data.get("name") or "图层"),
            layer_type=SceneCanvasAdapter._layer_type_from_item_type(str(data.get("layer_type") or "png")),
            enabled=bool(data.get("enabled", True)),
            locked=bool(data.get("locked", False)),
            x=int(data.get("x", 0)),
            y=int(data.get("y", 0)),
            width=max(1, int(data.get("width", 640))),
            height=max(1, int(data.get("height", 360))),
            saturation=float(data.get("saturation", 1.0)),
            contrast=float(data.get("contrast", 1.0)),
            color_temp=int(data.get("color_temp", 0)),
            mosaic=int(data.get("mosaic", 0)),
            volume=float(data.get("volume", 1.0)),
            priority=int(data.get("priority", 1)),
            source=source,
        )

    @staticmethod
    def _scene_from_dict(data: dict[str, Any]) -> Scene:
        layers = [SceneCanvasAdapter._layer_from_dict(layer) for layer in (data.get("layers") or [])]
        return Scene(
            id=str(data.get("id") or new_id("scene")),
            name=str(data.get("name") or "场景"),
            layers=layers,
            is_placeholder=bool(data.get("is_placeholder", False)),
        )

    @staticmethod
    def _layer_to_dict(layer: Layer) -> dict[str, Any]:
        return {
            "id": layer.id,
            "name": layer.name,
            "layer_type": layer.layer_type.value,
            "enabled": layer.enabled,
            "locked": layer.locked,
            "x": layer.x,
            "y": layer.y,
            "width": layer.width,
            "height": layer.height,
            "saturation": layer.saturation,
            "contrast": layer.contrast,
            "color_temp": layer.color_temp,
            "mosaic": layer.mosaic,
            "volume": layer.volume,
            "priority": layer.priority,
            "source": copy.deepcopy(layer.source or {}),
        }

    @classmethod
    def _scene_to_dict(cls, scene: Scene) -> dict[str, Any]:
        return {
            "id": scene.id,
            "name": scene.name,
            "is_placeholder": scene.is_placeholder,
            "layers": [cls._layer_to_dict(layer) for layer in scene.layers],
        }

    @classmethod
    def layer_to_item(cls, layer: Layer, scene_id: str | None = None) -> CanvasItemModel:
        source = layer.source or {}
        canvas_meta = source.get("_canvas", {})
        filters = dict(canvas_meta.get("filters") or {})
        filters.setdefault("saturation", layer.saturation)
        filters.setdefault("contrast", layer.contrast)
        filters.setdefault("color_temp", layer.color_temp)
        filters.setdefault("mosaic", layer.mosaic)
        filters.setdefault("onnx_style", source.get("onnx_style", "none"))

        chroma_key = dict(canvas_meta.get("chroma_key") or {})
        chroma_key.setdefault("face_enabled", bool(source.get("face_enabled", False)))
        chroma_key.setdefault("effect_type", source.get("effect_type", ""))
        chroma_key.setdefault("face_scale_percent", int(source.get("face_scale_percent", 100)))
        chroma_key.setdefault("face_smoothing", int(source.get("face_smoothing", 60)))
        chroma_key.setdefault("virtual_bg_enabled", bool(source.get("virtual_bg_enabled", False)))
        chroma_key.setdefault("virtual_bg_mode", source.get("virtual_bg_mode", "image"))
        chroma_key.setdefault("virtual_bg_blur_strength", int(source.get("virtual_bg_blur_strength", 55)))
        chroma_key.setdefault("virtual_bg_path", source.get("virtual_bg_path", ""))

        audio = dict(canvas_meta.get("audio") or {})
        audio.setdefault("volume", layer.volume)
        audio.setdefault("muted", bool(source.get("muted", False)))
        audio.setdefault("amplitude", 1.0)
        audio.setdefault("low_gain", 1.0)
        audio.setdefault("mid_gain", 1.0)
        audio.setdefault("high_gain", 1.0)

        return CanvasItemModel(
            item_id=layer.id,
            type=cls._item_type_for_layer(layer.layer_type),
            source_ref=layer.id,
            scene_ref=scene_id,
            name=layer.name,
            x=layer.x,
            y=layer.y,
            width=layer.width,
            height=layer.height,
            rotation=float(canvas_meta.get("rotation", 0.0)),
            opacity=float(canvas_meta.get("opacity", 1.0)),
            visible=layer.enabled,
            locked=layer.locked,
            z_index=layer.priority,
            crop=dict(canvas_meta.get("crop") or {"left": 0, "top": 0, "right": 0, "bottom": 0}),
            filters=filters,
            chroma_key=chroma_key,
            audio=audio,
            metadata={
                "source_snapshot": cls._layer_source_snapshot(layer),
                "layer_type": layer.layer_type.value,
            },
        )

    @classmethod
    def scene_to_item(cls, scene: Scene, canvas_width: int, canvas_height: int) -> CanvasItemModel:
        return CanvasItemModel(
            item_id=new_id("canvas_scene"),
            type="scene",
            scene_ref=scene.id,
            name=scene.name,
            x=0,
            y=0,
            width=max(1, canvas_width),
            height=max(1, canvas_height),
            visible=True,
            locked=False,
            z_index=-1000,
            metadata={
                "scene_snapshot": cls._scene_to_dict(scene),
                "scene_canvas_width": max(1, canvas_width),
                "scene_canvas_height": max(1, canvas_height),
            },
        )

    @classmethod
    def scene_to_document(cls, scene: Scene, canvas_width: int, canvas_height: int) -> CanvasDocument:
        doc = CanvasDocument(
            name=scene.name,
            viewport=CanvasViewport(zoom=1.0),
            output_frame=CanvasOutputFrame(width=canvas_width, height=canvas_height, aspect_ratio=cls._aspect_ratio(canvas_width, canvas_height)),
            items=[cls.layer_to_item(layer, scene_id=scene.id) for layer in sorted(scene.layers, key=lambda item: item.priority)],
            groups=[
                CanvasGroupModel(
                    group_id=f"scene::{scene.id}",
                    name=scene.name,
                    item_ids=[layer.id for layer in scene.layers],
                    metadata={"scene_ref": scene.id},
                )
            ],
            history_metadata={"scene_id": scene.id, "scene_name": scene.name},
        )
        return doc

    @staticmethod
    def _aspect_ratio(width: int, height: int) -> str:
        if width <= 0 or height <= 0:
            return "16:9"
        ratio = width / height
        target = {
            "4:3": 4 / 3,
            "16:9": 16 / 9,
            "16:10": 16 / 10,
            "21:9": 21 / 9,
        }
        return min(target, key=lambda key: abs(target[key] - ratio))

    @classmethod
    def _item_to_layer(
        cls,
        item: CanvasItemModel,
        index: int,
        *,
        x: int | None = None,
        y: int | None = None,
        width: int | None = None,
        height: int | None = None,
        priority: int | None = None,
    ) -> Layer:
        """把画布中的单个图层对象转回导播台 Layer，保留滤镜、音频和智能增强参数。"""
        layer_type = cls._layer_type_from_item_type(item.type)
        source_snapshot = copy.deepcopy(item.metadata.get("source_snapshot") or {})
        source_snapshot.setdefault("_canvas", {})
        source_snapshot["_canvas"].update(
            {
                "rotation": item.rotation,
                "opacity": item.opacity,
                "crop": dict(item.crop or {}),
                "filters": dict(item.filters or {}),
                "chroma_key": dict(item.chroma_key or {}),
                "audio": dict(item.audio or {}),
            }
        )
        filters = dict(item.filters or {})
        chroma_key = dict(item.chroma_key or {})
        audio = dict(item.audio or {})
        if "onnx_style" in filters:
            source_snapshot["onnx_style"] = filters.get("onnx_style") or "none"
        if "face_enabled" in chroma_key:
            source_snapshot["face_enabled"] = bool(chroma_key.get("face_enabled"))
        if "effect_type" in chroma_key:
            source_snapshot["effect_type"] = str(chroma_key.get("effect_type") or "")
        if "face_scale_percent" in chroma_key:
            source_snapshot["face_scale_percent"] = int(chroma_key.get("face_scale_percent") or 100)
        if "face_smoothing" in chroma_key:
            source_snapshot["face_smoothing"] = int(chroma_key.get("face_smoothing") or 60)
        if "virtual_bg_enabled" in chroma_key:
            source_snapshot["virtual_bg_enabled"] = bool(chroma_key.get("virtual_bg_enabled"))
        if "virtual_bg_mode" in chroma_key:
            source_snapshot["virtual_bg_mode"] = str(chroma_key.get("virtual_bg_mode") or "image")
        if "virtual_bg_blur_strength" in chroma_key:
            source_snapshot["virtual_bg_blur_strength"] = int(chroma_key.get("virtual_bg_blur_strength") or 55)
        if "virtual_bg_path" in chroma_key:
            source_snapshot["virtual_bg_path"] = str(chroma_key.get("virtual_bg_path") or "")
        if "muted" in audio:
            source_snapshot["muted"] = bool(audio.get("muted"))
        source_snapshot.setdefault("_canvas", {})
        source_snapshot["_canvas"].setdefault("audio", {})
        source_snapshot["_canvas"]["audio"].update(
            {
                "volume": float(audio.get("volume", 1.0)),
                "muted": bool(audio.get("muted", False)),
                "amplitude": float(audio.get("amplitude", 1.0)),
                "low_gain": float(audio.get("low_gain", 1.0)),
                "mid_gain": float(audio.get("mid_gain", 1.0)),
                "high_gain": float(audio.get("high_gain", 1.0)),
            }
        )

        if layer_type == LayerType.PNG and not source_snapshot.get("image_path"):
            source_snapshot["image_path"] = str(cls._DEFAULT_PLACEHOLDER)

        if item.source_ref and not source_snapshot:
            source_snapshot["source_ref"] = item.source_ref

        return Layer(
            id=item.source_ref or item.item_id or new_id("layer"),
            name=item.name or f"{layer_type.value.upper()} {index + 1}",
            layer_type=layer_type,
            enabled=item.visible,
            locked=item.locked,
            x=int(item.x if x is None else x),
            y=int(item.y if y is None else y),
            width=max(1, int(item.width if width is None else width)),
            height=max(1, int(item.height if height is None else height)),
            saturation=float(item.filters.get("saturation", source_snapshot.get("_canvas", {}).get("filters", {}).get("saturation", 1.0))),
            contrast=float(item.filters.get("contrast", source_snapshot.get("_canvas", {}).get("filters", {}).get("contrast", 1.0))),
            color_temp=int(item.filters.get("color_temp", source_snapshot.get("_canvas", {}).get("filters", {}).get("color_temp", 0))),
            mosaic=int(item.filters.get("mosaic", source_snapshot.get("_canvas", {}).get("filters", {}).get("mosaic", 0))),
            volume=float(item.audio.get("volume", source_snapshot.get("_canvas", {}).get("audio", {}).get("volume", 1.0))),
            priority=max(1, int(priority if priority is not None else (item.z_index if item.z_index else index + 1))),
            source=source_snapshot,
        )

    @classmethod
    def _scene_item_to_layers(
        cls,
        scene_item: CanvasItemModel,
        children: list[CanvasItemModel],
        document: CanvasDocument,
        index_base: int,
        removed_layer_ids: set[str] | None = None,
    ) -> list[Layer]:
        """展开画布中的场景框，并把被拖入该场景框的子图层追加到该集合后面。"""
        layers: list[Layer] = []
        removed_layer_ids = removed_layer_ids or set()
        embedded_scene = cls._scene_from_dict(scene_item.metadata.get("scene_snapshot") or {})
        source_w = max(1, int(scene_item.metadata.get("scene_canvas_width") or document.output_frame.width or 1280))
        source_h = max(1, int(scene_item.metadata.get("scene_canvas_height") or document.output_frame.height or 720))
        scale_x = max(0.01, scene_item.width / source_w)
        scale_y = max(0.01, scene_item.height / source_h)
        child_layer_ids = {str(child.source_ref or child.item_id) for child in children}
        for embedded_index, embedded_layer in enumerate(embedded_scene.layers):
            if embedded_layer.id in child_layer_ids or embedded_layer.id in removed_layer_ids:
                continue
            exported = embedded_layer.clone()
            exported.x = int(scene_item.x + round(exported.x * scale_x))
            exported.y = int(scene_item.y + round(exported.y * scale_y))
            exported.width = max(1, int(round(exported.width * scale_x)))
            exported.height = max(1, int(round(exported.height * scale_y)))
            exported.enabled = scene_item.visible and exported.enabled
            exported.locked = scene_item.locked or exported.locked
            exported.priority = index_base + embedded_index + 1
            layers.append(exported)

        for child_index, child in enumerate(sorted(children, key=lambda item: (item.z_index, item.item_id)), start=len(layers) + 1):
            # Child layers keep their own filter/audio state; only local geometry is mapped back to scene coordinates.
            local = dict(child.metadata.get("local_geometry") or {})
            if local:
                child_x = int(scene_item.x + round(local.get("x", child.x) * scene_item.width / max(1, source_w)))
                child_y = int(scene_item.y + round(local.get("y", child.y) * scene_item.height / max(1, source_h)))
                child_w = max(1, int(round(local.get("w", child.width) * scene_item.width / max(1, source_w))))
                child_h = max(1, int(round(local.get("h", child.height) * scene_item.height / max(1, source_h))))
            else:
                child_x = int(child.x)
                child_y = int(child.y)
                child_w = int(child.width)
                child_h = int(child.height)
            layers.append(
                cls._item_to_layer(
                    child,
                    index_base + child_index,
                    x=child_x,
                    y=child_y,
                    width=child_w,
                    height=child_h,
                    priority=index_base + child_index,
                )
            )
        return layers

    @classmethod
    def document_to_layers(cls, document: CanvasDocument) -> list[Layer]:
        layers: list[Layer] = []
        visible_items = [item for item in sorted(document.items, key=lambda item: (item.z_index, item.item_id)) if item.visible]
        scene_items = {item.item_id: item for item in visible_items if item.type == "scene" and item.metadata.get("scene_snapshot")}
        children_by_parent: dict[str, list[CanvasItemModel]] = {item_id: [] for item_id in scene_items}
        removed_by_scene_ref: dict[str, set[str]] = {}
        root_items: list[CanvasItemModel] = []

        for item in visible_items:
            if item.type != "scene" and item.parent_item_id in scene_items:
                children_by_parent.setdefault(str(item.parent_item_id), []).append(item)
                continue
            if item.type != "scene":
                removed_scene_ref = str(item.metadata.get("removed_from_scene_ref") or "")
                removed_layer_id = str(item.source_ref or item.item_id or "")
                if removed_scene_ref and removed_layer_id:
                    removed_by_scene_ref.setdefault(removed_scene_ref, set()).add(removed_layer_id)
            root_items.append(item)

        for index, item in enumerate(root_items):
            if item.type == "scene" and item.item_id in scene_items:
                layers.extend(
                    cls._scene_item_to_layers(
                        item,
                        children_by_parent.get(item.item_id, []),
                        document,
                        index * 1000,
                        removed_by_scene_ref.get(str(item.scene_ref or ""), set()),
                    )
                )
                continue
            if item.type == "scene":
                continue
            layers.append(cls._item_to_layer(item, index))
        seen_ids: set[str] = set()
        for priority, layer in enumerate(layers, start=1):
            if layer.id in seen_ids:
                layer.id = new_id("layer")
            seen_ids.add(layer.id)
            layer.priority = priority
        return layers

    @classmethod
    def scene_frame_to_layers(
        cls,
        scene_item: CanvasItemModel,
        children: list[CanvasItemModel],
        document: CanvasDocument,
        removed_layer_ids: set[str] | None = None,
    ) -> list[Layer]:
        """把画布中的单个场景框转换回该场景自己的本地 Layer 列表。"""
        embedded_scene = cls._scene_from_dict(scene_item.metadata.get("scene_snapshot") or {})
        source_w = max(1, int(scene_item.metadata.get("scene_canvas_width") or document.output_frame.width or 1280))
        source_h = max(1, int(scene_item.metadata.get("scene_canvas_height") or document.output_frame.height or 720))
        removed_layer_ids = removed_layer_ids or set()
        child_layer_ids = {str(child.source_ref or child.item_id) for child in children}
        layers = [
            item.clone()
            for item in sorted(embedded_scene.layers, key=lambda item: item.priority)
            if item.id not in child_layer_ids and item.id not in removed_layer_ids
        ]

        for child_index, child in enumerate(sorted(children, key=lambda item: (item.z_index, item.item_id)), start=len(layers) + 1):
            local = dict(child.metadata.get("local_geometry") or {})
            if local:
                child_x = int(local.get("x", 0))
                child_y = int(local.get("y", 0))
                child_w = max(1, int(local.get("w", child.width)))
                child_h = max(1, int(local.get("h", child.height)))
            else:
                child_x = int(round((child.x - scene_item.x) * source_w / max(1, scene_item.width)))
                child_y = int(round((child.y - scene_item.y) * source_h / max(1, scene_item.height)))
                child_w = max(1, int(round(child.width * source_w / max(1, scene_item.width))))
                child_h = max(1, int(round(child.height * source_h / max(1, scene_item.height))))
            layers.append(
                cls._item_to_layer(
                    child,
                    child_index,
                    x=child_x,
                    y=child_y,
                    width=child_w,
                    height=child_h,
                    priority=child_index,
                )
            )

        seen_ids: set[str] = set()
        for priority, layer in enumerate(layers, start=1):
            if layer.id in seen_ids:
                layer.id = new_id("layer")
            seen_ids.add(layer.id)
            layer.priority = priority
        return layers

    @classmethod
    def document_to_scene(
        cls,
        document: CanvasDocument,
        scene: Scene | None = None,
        *,
        preserve_placeholder: bool = False,
    ) -> Scene:
        base = scene.clone() if scene is not None else Scene(id=new_id("scene"), name=document.name)
        if base.is_placeholder and not preserve_placeholder:
            return base
        base.name = document.name or base.name
        base.layers = cls.document_to_layers(document)
        return base

    @classmethod
    def scene_to_group_item(cls, scene: Scene) -> CanvasGroupModel:
        return CanvasGroupModel(
            group_id=f"scene::{scene.id}",
            name=scene.name,
            item_ids=[layer.id for layer in scene.layers],
            metadata={"scene_ref": scene.id},
        )


class DirectorCanvasBridge:
    """画布模式与导播台之间的薄桥接层。"""

    def __init__(self, state) -> None:
        self.state = state
        self.adapter = SceneCanvasAdapter()

    def build_document_from_scene(self, scene_id: str | None, canvas_width: int, canvas_height: int) -> CanvasDocument:
        if not scene_id:
            return CanvasDocument(
                output_frame=CanvasOutputFrame(width=canvas_width, height=canvas_height),
                name="场景画布",
            )
        scene = self.state.get_scene_by_id(scene_id)
        if scene is None:
            return CanvasDocument(
                output_frame=CanvasOutputFrame(width=canvas_width, height=canvas_height),
                name="场景画布",
            )
        return self.adapter.scene_to_document(scene, canvas_width, canvas_height)

    def import_active_scene(self, canvas_width: int, canvas_height: int) -> CanvasDocument:
        return self.build_document_from_scene(self.state.get_active_scene_id(), canvas_width, canvas_height)

    def export_document_to_scene(
        self,
        document: CanvasDocument,
        target_scene_id: str | None = None,
        *,
        create_new_scene: bool = False,
        activate: bool = False,
    ) -> tuple[bool, str, str | None]:
        scene_id = target_scene_id
        if create_new_scene or not scene_id:
            new_scene = self.state.add_scene(document.name or "画布场景")
            scene_id = new_scene.id
        if scene_id is None:
            return False, "未找到可写入的场景。", None
        scene = self.state.get_scene_by_id(scene_id)
        if scene is None:
            return False, "目标场景不存在。", None
        if scene.is_placeholder:
            return False, "紧急占位场景不支持通过画布写回。", scene_id

        self.state.rename_scene(scene_id, document.name or scene.name)
        self.state.clear_scene_layers(scene_id)
        for layer in self.adapter.document_to_layers(document):
            self.state.add_layer(layer, scene_id=scene_id)
        if activate:
            self.state.set_active_scene(scene_id)
        return True, f"已写回场景：{document.name or scene.name}", scene_id
