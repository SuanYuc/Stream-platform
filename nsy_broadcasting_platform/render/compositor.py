from __future__ import annotations

from dataclasses import dataclass, field

from nsy_broadcasting_platform.capture.source_manager import SourceManager
from nsy_broadcasting_platform.compat import CV2, NP
from nsy_broadcasting_platform.models import Layer, LayerType, Scene
from nsy_broadcasting_platform.utils import (
    apply_onnx_style_filter,
    apply_video_filters,
    canonical_onnx_style,
    canonical_ar_effect_type,
    default_ar_sticker_path,
    FaceEffectFilter,
    load_background_image,
    placeholder_frame,
    set_face_effect_status,
    VirtualBackgroundFilter,
)

cv2 = CV2.module
np = NP.module


@dataclass(slots=True)
class RenderResult:
    frame: object
    layer_rects: dict[str, tuple[int, int, int, int]]
    layer_metrics: dict[str, dict[str, object]] = field(default_factory=dict)


class Compositor:
    def __init__(self, source_manager: SourceManager, width: int, height: int) -> None:
        self.source_manager = source_manager
        self.width = width
        self.height = height
        self._virtual_bg_filters: dict[str, VirtualBackgroundFilter] = {}
        self._face_filters: dict[str, tuple[tuple[str, str, int, int], FaceEffectFilter]] = {}

    def set_canvas_size(self, width: int, height: int) -> None:
        self.width = max(1, int(width))
        self.height = max(1, int(height))

    def close(self) -> None:
        for _layer_id, (_signature, face_filter) in list(self._face_filters.items()):
            face_filter.close()
        self._face_filters.clear()
        for _layer_id, bg_filter in list(self._virtual_bg_filters.items()):
            bg_filter.close()
        self._virtual_bg_filters.clear()

    def reset_temporal_state(self) -> None:
        for _layer_id, bg_filter in list(self._virtual_bg_filters.items()):
            bg_filter.reset_state()

    def _get_virtual_bg_filter(self, layer: Layer) -> VirtualBackgroundFilter:
        cached = self._virtual_bg_filters.get(layer.id)
        if cached is not None:
            return cached
        bg_filter = VirtualBackgroundFilter()
        self._virtual_bg_filters[layer.id] = bg_filter
        return bg_filter

    def _get_face_filter(
        self,
        layer: Layer,
        sticker_path: str,
        effect_type: str,
        scale_percent: int,
        smoothing: int,
    ) -> FaceEffectFilter:
        signature = (sticker_path, effect_type, int(scale_percent), int(smoothing))
        cached = self._face_filters.get(layer.id)
        if cached is not None and cached[0] == signature:
            return cached[1]
        if cached is not None:
            cached[1].close()
        face_filter = FaceEffectFilter(
            sticker_path=sticker_path,
            effect_type=effect_type,
            scale_percent=scale_percent,
            tracking_smoothing=smoothing,
        )
        self._face_filters[layer.id] = (signature, face_filter)
        return face_filter

    def _clip(self, x: int, y: int, w: int, h: int) -> tuple[int, int, int, int]:
        x1 = max(0, x)
        y1 = max(0, y)
        x2 = min(self.width, x + w)
        y2 = min(self.height, y + h)
        if x2 <= x1 or y2 <= y1:
            return 0, 0, 0, 0
        return x1, y1, x2 - x1, y2 - y1

    def _resize(self, frame, w: int, h: int):
        if cv2 is not None:
            return cv2.resize(frame, (w, h), interpolation=cv2.INTER_LINEAR)
        if np is None:
            return frame
        src_h, src_w = frame.shape[:2]
        if src_h == h and src_w == w:
            return frame
        y_idx = np.linspace(0, src_h - 1, h).astype(np.int32)
        x_idx = np.linspace(0, src_w - 1, w).astype(np.int32)
        return frame[y_idx][:, x_idx]

    def _blit_rgb(self, canvas, src_rgb, x: int, y: int, w: int, h: int) -> None:
        cx, cy, cw, ch = self._clip(x, y, w, h)
        if cw <= 0 or ch <= 0:
            return
        ox = cx - x
        oy = cy - y
        canvas[cy : cy + ch, cx : cx + cw] = src_rgb[oy : oy + ch, ox : ox + cw]

    def _blit_rgba(self, canvas, src_rgba, x: int, y: int, w: int, h: int) -> None:
        cx, cy, cw, ch = self._clip(x, y, w, h)
        if cw <= 0 or ch <= 0:
            return
        ox = cx - x
        oy = cy - y
        patch = src_rgba[oy : oy + ch, ox : ox + cw]
        rgb = patch[:, :, :3].astype("float32")
        alpha = (patch[:, :, 3:4].astype("float32")) / 255.0
        dst = canvas[cy : cy + ch, cx : cx + cw].astype("float32")
        blended = rgb * alpha + dst * (1.0 - alpha)
        canvas[cy : cy + ch, cx : cx + cw] = blended.astype("uint8")

    def _prepare_layer_frame(self, layer: Layer):
        frame = self.source_manager.get_frame(layer.id)
        if frame is None:
            return None, None
        layer_metric = None
        # 静态图片图层保持原图通道，其他视频图层做滤镜。
        if layer.layer_type != LayerType.PNG:
            frame = apply_video_filters(
                frame,
                layer.saturation,
                layer.contrast,
                layer.color_temp,
                mosaic=layer.mosaic,
            )

        onnx_style = canonical_onnx_style(layer.source.get("onnx_style", "none"))
        if onnx_style != "none":
            # ONNX 风格属于图层级视觉滤镜，静态图片也应生效；四通道图片需要保留原 Alpha。
            if getattr(frame, "ndim", 0) == 3 and frame.shape[2] == 4:
                alpha = frame[:, :, 3:4].copy()
                styled = apply_onnx_style_filter(frame[:, :, :3], onnx_style)
                if styled is not None and getattr(styled, "ndim", 0) == 3 and styled.shape[2] >= 3:
                    frame = np.concatenate((styled[:, :, :3], alpha), axis=2)
            else:
                frame = apply_onnx_style_filter(frame, onnx_style)

        if layer.layer_type != LayerType.PNG:
            face_enabled = bool(layer.source.get("face_enabled", False))
            if face_enabled:
                effect_type = canonical_ar_effect_type(layer.source.get("effect_type", ""))
                sticker_path = str(layer.source.get("sticker_path", "")).strip()
                if effect_type and not sticker_path:
                    sticker_path = default_ar_sticker_path(effect_type)
                if not effect_type:
                    set_face_effect_status(
                        enabled=True,
                        running=False,
                        detected=False,
                        note="已启用，未选择特效类型",
                        layer_id=layer.id,
                    )
                elif not sticker_path:
                    set_face_effect_status(
                        enabled=True,
                        running=False,
                        detected=False,
                        effect_type=effect_type,
                        note="默认AR素材缺失，请导入AR素材",
                        layer_id=layer.id,
                    )
                else:
                    scale_percent = int(max(50, min(200, layer.source.get("face_scale_percent", 100))))
                    smoothing = int(max(0, min(100, layer.source.get("face_smoothing", 60))))
                    face_filter = self._get_face_filter(layer, sticker_path, effect_type, scale_percent, smoothing)
                    frame = face_filter.apply_sticker(frame, layer_id=layer.id)

            if bool(layer.source.get("virtual_bg_enabled", False)):
                virtual_bg_mode = str(layer.source.get("virtual_bg_mode", "image") or "image").strip().lower()
                bg_path = str(layer.source.get("virtual_bg_path", "")).strip()
                blur_strength = int(max(0, min(100, layer.source.get("virtual_bg_blur_strength", 55))))
                bg_image = load_background_image(bg_path) if bg_path and virtual_bg_mode != "blur" else None
                bg_filter = self._get_virtual_bg_filter(layer)
                bg_filter.switch_engine("mediapipe")
                frame, _inference_time_ms = bg_filter.process_frame(
                    frame,
                    bg_image,
                    mode=virtual_bg_mode,
                    blur_strength=blur_strength,
                )
                layer_metric = dict(bg_filter.last_metrics)
        return frame, layer_metric

    def render_scene(self, scene: Scene | None) -> RenderResult:
        if np is None:
            return RenderResult(frame=None, layer_rects={}, layer_metrics={})
        if scene is None:
            frame = placeholder_frame(self.width, self.height, "无场景")
            return RenderResult(frame=frame, layer_rects={}, layer_metrics={})
        canvas = np.zeros((self.height, self.width, 3), dtype=np.uint8)
        layer_rects: dict[str, tuple[int, int, int, int]] = {}
        layer_metrics: dict[str, dict[str, object]] = {}

        # 优先级编号越大越靠上，因此先绘制低编号图层，再绘制高编号图层。
        ordered_layers = [layer for _idx, layer in sorted(enumerate(scene.layers), key=lambda item: (item[1].priority, item[0]))]
        for layer in ordered_layers:
            if not layer.enabled:
                continue
            src, metric = self._prepare_layer_frame(layer)
            if src is None:
                continue
            w = max(1, int(layer.width))
            h = max(1, int(layer.height))
            x = int(layer.x)
            y = int(layer.y)

            resized = self._resize(src, w, h)
            if resized is None:
                continue
            if getattr(resized, "ndim", 0) != 3:
                continue
            if resized.shape[2] == 4:
                self._blit_rgba(canvas, resized, x, y, w, h)
            else:
                self._blit_rgb(canvas, resized[:, :, :3], x, y, w, h)
            layer_rects[layer.id] = (x, y, w, h)
            if metric:
                layer_metrics[layer.id] = metric
        if scene.is_placeholder and not layer_rects:
            canvas = placeholder_frame(self.width, self.height, "紧急占位画面")
        return RenderResult(frame=canvas, layer_rects=layer_rects, layer_metrics=layer_metrics)


