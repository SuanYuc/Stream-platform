from __future__ import annotations

import math
import os
import importlib
import threading
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, replace
from pathlib import Path

from nsy_broadcasting_platform.compat import CV2, MEDIAPIPE, NP
from nsy_broadcasting_platform.gpu_runtime import create_session, preload_onnxruntime

cv2 = CV2.module
mp = MEDIAPIPE.module
np = NP.module

_FACE_MESH = None
_FACE_MESH_LOCK = threading.Lock()
_STICKER_CACHE: dict[str, object] = {}
_STICKER_CACHE_LOCK = threading.Lock()
_BGR_IMAGE_CACHE: dict[str, object] = {}
_BGR_IMAGE_CACHE_LOCK = threading.Lock()
_FACE_STATUS_LOCK = threading.Lock()
_STYLE_FILTER_CACHE: dict[str, "OnnxStyleTransferFilter"] = {}
_STYLE_FILTER_CACHE_LOCK = threading.Lock()
_STYLE_FILTER_LOADING: set[str] = set()
_STYLE_FILTER_LOADING_LOCK = threading.Lock()
_MEDIAPIPE_PREWARM_STARTED: set[str] = set()
_MEDIAPIPE_PREWARM_LOCK = threading.Lock()
_ONNXRUNTIME_MODULE = None
_ONNXRUNTIME_ERROR: str | None = None
_ONNXRUNTIME_IMPORT_ATTEMPTED = False
_ONNXRUNTIME_LOCK = threading.Lock()


AR_EFFECT_DOG_NOSE = "dog_nose"
AR_EFFECT_CAT_EARS = "cat_ears"
AR_EFFECT_CARTOON_EYES = "cartoon_eyes"

AR_EFFECTS: dict[str, dict[str, str]] = {
    AR_EFFECT_DOG_NOSE: {
        "label": "狗鼻子",
        "filename": "dog_nose.png",
        "anchor": "nose",
    },
    AR_EFFECT_CAT_EARS: {
        "label": "猫耳",
        "filename": "cat_ears.png",
        "anchor": "hat",
    },
    AR_EFFECT_CARTOON_EYES: {
        "label": "卡通眼睛",
        "filename": "cartoon_eyes.png",
        "anchor": "eyes",
    },
}
_AR_EFFECT_ALIASES = {
    "dog": AR_EFFECT_DOG_NOSE,
    "dog_nose": AR_EFFECT_DOG_NOSE,
    "nose": AR_EFFECT_DOG_NOSE,
    "cat": AR_EFFECT_CAT_EARS,
    "cat_ears": AR_EFFECT_CAT_EARS,
    "ear": AR_EFFECT_CAT_EARS,
    "ears": AR_EFFECT_CAT_EARS,
    "hat": AR_EFFECT_CAT_EARS,
    "cartoon_eyes": AR_EFFECT_CARTOON_EYES,
    "eye": AR_EFFECT_CARTOON_EYES,
    "eyes": AR_EFFECT_CARTOON_EYES,
}
_AR_EFFECT_SCALE_MULTIPLIERS = {
    "nose": 1.45,
    "hat": 2.85,
    "eyes": 2.25,
}

ONNX_STYLE_NONE = "none"
ONNX_STYLE_CARTOON = "cartoon"
ONNX_STYLE_MONET = "monet"
ONNX_STYLE_VANGOGH = "vangogh"

ONNX_STYLE_FILTERS: dict[str, dict[str, str]] = {
    ONNX_STYLE_CARTOON: {"label": "卡通化", "filename": "cartoon.onnx"},
    ONNX_STYLE_MONET: {"label": "莫奈风格", "filename": "monet.onnx"},
    ONNX_STYLE_VANGOGH: {"label": "梵高风格", "filename": "vangogh.onnx"},
}


def canonical_onnx_style(style_key: str | None) -> str:
    raw = str(style_key or "").strip().lower()
    if raw in {"", "none", "off", "关闭", "无"}:
        return ONNX_STYLE_NONE
    aliases = {
        "cartoonize": ONNX_STYLE_CARTOON,
        "cartoon": ONNX_STYLE_CARTOON,
        "卡通": ONNX_STYLE_CARTOON,
        "卡通化": ONNX_STYLE_CARTOON,
        "monet": ONNX_STYLE_MONET,
        "莫奈": ONNX_STYLE_MONET,
        "vangogh": ONNX_STYLE_VANGOGH,
        "van_gogh": ONNX_STYLE_VANGOGH,
        "梵高": ONNX_STYLE_VANGOGH,
    }
    return aliases.get(raw, raw if raw in ONNX_STYLE_FILTERS else ONNX_STYLE_NONE)


def onnx_style_label(style_key: str | None) -> str:
    style = canonical_onnx_style(style_key)
    if style == ONNX_STYLE_NONE:
        return "关闭"
    return ONNX_STYLE_FILTERS.get(style, {}).get("label", style)


def default_onnx_style_model_path(style_key: str | None) -> str:
    style = canonical_onnx_style(style_key)
    meta = ONNX_STYLE_FILTERS.get(style)
    if not meta:
        return ""
    return str(Path(__file__).resolve().parent.parent / "onnx_models" / meta["filename"])


def _load_onnxruntime():
    """按需加载 onnxruntime，避免程序启动时触发 ONNX 动态库初始化。"""
    global _ONNXRUNTIME_MODULE, _ONNXRUNTIME_ERROR, _ONNXRUNTIME_IMPORT_ATTEMPTED
    with _ONNXRUNTIME_LOCK:
        if _ONNXRUNTIME_IMPORT_ATTEMPTED:
            return _ONNXRUNTIME_MODULE, _ONNXRUNTIME_ERROR
        _ONNXRUNTIME_IMPORT_ATTEMPTED = True
        _ONNXRUNTIME_MODULE, error = preload_onnxruntime()
        _ONNXRUNTIME_ERROR = error or None
        return _ONNXRUNTIME_MODULE, _ONNXRUNTIME_ERROR


def canonical_ar_effect_type(effect_type: str | None) -> str:
    raw = str(effect_type or "").strip().lower()
    if not raw:
        return ""
    return _AR_EFFECT_ALIASES.get(raw, raw if raw in AR_EFFECTS else "")


def ar_effect_label(effect_type: str | None) -> str:
    effect_key = canonical_ar_effect_type(effect_type)
    return AR_EFFECTS.get(effect_key, {}).get("label", effect_key)


def default_ar_sticker_path(effect_type: str | None) -> str:
    effect_key = canonical_ar_effect_type(effect_type)
    meta = AR_EFFECTS.get(effect_key)
    if not meta:
        return ""
    return str(Path(__file__).resolve().parent / "assets" / "ar" / meta["filename"])


def is_default_ar_sticker_path(path: str | None) -> bool:
    if not path:
        return False
    try:
        target = _path_cache_key(str(path))
        defaults = {
            _path_cache_key(default_ar_sticker_path(effect_key))
            for effect_key in AR_EFFECTS
        }
        return target in defaults
    except Exception:
        return False


def _load_mp_solution(module_name: str, class_name: str):
    if mp is None:
        return None

    solutions = getattr(mp, "solutions", None)
    if solutions is not None:
        module = getattr(solutions, module_name, None)
        if module is not None:
            factory = getattr(module, class_name, None)
            if factory is not None:
                return factory

    for import_name in (
        f"mediapipe.solutions.{module_name}",
        f"mediapipe.python.solutions.{module_name}",
    ):
        try:
            module = importlib.import_module(import_name)
            factory = getattr(module, class_name, None)
            if factory is not None:
                return factory
        except Exception:
            continue
    return None


@dataclass(slots=True)
class FaceEffectStatus:
    enabled: bool = False
    running: bool = False
    detected: bool = False
    effect_type: str = ""
    note: str = "未启用"
    layer_id: str = ""
    updated_ms: int = 0


_FACE_STATUS = FaceEffectStatus()


def now_ms() -> int:
    return int(time.time() * 1000)


def placeholder_frame(width: int, height: int, text: str, color: tuple[int, int, int] = (35, 35, 35)):
    if np is None:
        return None
    frame = np.full((height, width, 3), color, dtype=np.uint8)
    if cv2 is not None:
        cv2.putText(
            frame,
            text,
            (20, max(40, height // 2)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (220, 220, 220),
            2,
            cv2.LINE_AA,
        )
    return frame


def _path_cache_key(path: str) -> str:
    try:
        return str(Path(path).resolve())
    except Exception:
        return path


def _load_cached_image(
    path: str,
    *,
    cache: dict[str, object],
    cache_lock: threading.Lock,
    flags: int,
    required_channels: int,
    exact_channels: bool,
):
    if cv2 is None or np is None or not path:
        return None
    key = _path_cache_key(path)
    with cache_lock:
        cached = cache.get(key)
    if cached is not None:
        return cached

    img = _read_image(key, flags)
    channels = getattr(img, "shape", (0, 0, 0))[2] if getattr(img, "ndim", 0) == 3 else 0
    if img is None or channels < required_channels:
        return None
    if exact_channels and channels != required_channels:
        return None

    out = np.ascontiguousarray(img[:, :, :required_channels])
    with cache_lock:
        cache[key] = out
    return out


def load_sticker_image(path: str):
    if cv2 is None:
        return None
    return _load_cached_image(
        path,
        cache=_STICKER_CACHE,
        cache_lock=_STICKER_CACHE_LOCK,
        flags=cv2.IMREAD_UNCHANGED,
        required_channels=4,
        exact_channels=True,
    )


def _read_image(path: str, flags: int):
    if cv2 is None or np is None or not path:
        return None
    try:
        data = np.fromfile(path, dtype=np.uint8)
        if data.size > 0:
            img = cv2.imdecode(data, flags)
            if img is not None:
                return img
    except Exception:
        pass
    try:
        return cv2.imread(path, flags)
    except Exception:
        return None


def load_background_image(path: str):
    if cv2 is None:
        return None
    return _load_cached_image(
        path,
        cache=_BGR_IMAGE_CACHE,
        cache_lock=_BGR_IMAGE_CACHE_LOCK,
        flags=cv2.IMREAD_COLOR,
        required_channels=3,
        exact_channels=False,
    )


def set_face_effect_status(
    *,
    enabled: bool | None = None,
    running: bool | None = None,
    detected: bool | None = None,
    effect_type: str | None = None,
    note: str | None = None,
    layer_id: str | None = None,
) -> None:
    global _FACE_STATUS
    with _FACE_STATUS_LOCK:
        st = _FACE_STATUS
        if enabled is not None:
            st.enabled = enabled
        if running is not None:
            st.running = running
        if detected is not None:
            st.detected = detected
        if effect_type is not None:
            st.effect_type = effect_type
        if note is not None:
            st.note = note
        if layer_id is not None:
            st.layer_id = layer_id
        st.updated_ms = now_ms()


def get_face_effect_status() -> FaceEffectStatus:
    with _FACE_STATUS_LOCK:
        return replace(_FACE_STATUS)


def apply_video_filters(frame, saturation: float, contrast: float, color_temp: int, mosaic: int = 0):
    if frame is None or np is None or cv2 is None:
        return frame

    strength = int(max(0, min(100, mosaic)))
    if contrast == 1.0 and saturation == 1.0 and color_temp == 0 and strength == 0:
        return frame

    out = frame
    if contrast != 1.0:
        out = frame.astype(np.float32, copy=True)
        out = (out - 127.5) * contrast + 127.5

    if saturation != 1.0:
        hsv_source = out.astype(np.uint8, copy=False)
        hsv = cv2.cvtColor(hsv_source, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[:, :, 1] *= saturation
        np.clip(hsv[:, :, 1], 0, 255, out=hsv[:, :, 1])
        out = cv2.cvtColor(hsv.astype(np.uint8), cv2.COLOR_HSV2BGR)

    if color_temp != 0:
        if out.dtype != np.float32:
            out = out.astype(np.float32)
        shift = max(-100, min(100, color_temp)) / 100.0 * 35.0
        out[:, :, 2] += shift
        out[:, :, 0] -= shift

    if out.dtype != np.uint8:
        out = np.clip(out, 0, 255).astype(np.uint8)

    if strength > 0:
        h, w = out.shape[:2]
        scale = max(0.02, 1.0 - (strength / 100.0))
        dw = max(1, int(w * scale))
        dh = max(1, int(h * scale))
        small = cv2.resize(out, (dw, dh), interpolation=cv2.INTER_LINEAR)
        out = cv2.resize(small, (w, h), interpolation=cv2.INTER_NEAREST)

    return out


class OnnxStyleTransferFilter:
    """ONNX 风格迁移滤镜，输入输出均保持 BGR uint8，便于直接接入 OpenCV 渲染链。"""

    __slots__ = (
        "style_key",
        "model_path",
        "session",
        "input_name",
        "output_name",
        "provider_name",
        "last_error",
        "last_inference_ms",
        "_lock",
        "_max_inference_size",
        "_blend_strength",
    )

    def __init__(self, style_key: str, max_inference_size: int = 448, blend_strength: float | None = None) -> None:
        self.style_key = canonical_onnx_style(style_key)
        self.model_path = default_onnx_style_model_path(self.style_key)
        self.session = None
        self.input_name = ""
        self.output_name = ""
        self.provider_name = ""
        self.last_error = ""
        self.last_inference_ms = 0.0
        self._lock = threading.Lock()
        self._max_inference_size = max(64, int(max_inference_size))
        if blend_strength is None:
            blend_strength = 0.95 if self.style_key == ONNX_STYLE_VANGOGH else 0.88
        self._blend_strength = float(max(0.0, min(1.0, blend_strength)))
        self._load_session()

    @property
    def available(self) -> bool:
        return self.session is not None and bool(self.input_name and self.output_name)

    def _load_session(self) -> None:
        if self.style_key == ONNX_STYLE_NONE:
            return
        if not self.model_path or not Path(self.model_path).exists():
            self.last_error = f"ONNX 模型不存在: {self.model_path}"
            return
        info = create_session(self.model_path, intra_threads=max(1, min(4, os.cpu_count() or 4)))
        if info.session is None:
            self.session = None
            self.last_error = f"ONNX 模型加载失败: {info.error}"
            return
        self.session = info.session
        self.input_name = self.session.get_inputs()[0].name
        self.output_name = self.session.get_outputs()[0].name
        self.provider_name = info.provider or ",".join(info.providers)
        if info.providers and info.providers[0] == "CPUExecutionProvider":
            self._max_inference_size = min(self._max_inference_size, 384)
        else:
            self._max_inference_size = max(self._max_inference_size, 640)

    def _fit_inference_size(self, width: int, height: int) -> tuple[int, int]:
        longer = max(width, height)
        if longer <= self._max_inference_size:
            return width, height
        scale = self._max_inference_size / float(longer)
        # 取 8 的倍数可以减少部分模型在卷积链路上的边界问题。
        new_w = max(8, int(width * scale) // 8 * 8)
        new_h = max(8, int(height * scale) // 8 * 8)
        return new_w, new_h

    @staticmethod
    def _postprocess_output(output_tensor):
        out = output_tensor
        if isinstance(out, (list, tuple)):
            out = out[0]
        out = np.asarray(out)
        if out.ndim == 4:
            out = out[0]
        if out.ndim == 3 and out.shape[0] in (1, 3, 4):
            out = np.transpose(out[:3], (1, 2, 0))
        if out.ndim != 3:
            return None

        out = out.astype(np.float32, copy=False)
        min_v = float(np.nanmin(out))
        max_v = float(np.nanmax(out))
        if min_v >= -1.2 and max_v <= 1.2:
            if min_v < 0.0:
                out = (out + 1.0) * 127.5
            else:
                out = out * 255.0
        return np.clip(out, 0, 255).astype(np.uint8)

    @staticmethod
    def _is_degenerate_output(frame_bgr) -> bool:
        """识别明显不可用的模型输出，避免黑屏、过曝或单色块直接进入节目画面。"""
        if frame_bgr is None or getattr(frame_bgr, "ndim", 0) != 3:
            return True
        gray = cv2.cvtColor(frame_bgr[:, :, :3], cv2.COLOR_BGR2GRAY)
        mean_v = float(gray.mean())
        std_v = float(gray.std())
        dark_ratio = float(np.mean(gray < 8))
        bright_ratio = float(np.mean(gray > 247))
        if mean_v < 8.0 or mean_v > 247.0:
            return True
        if std_v < 2.0:
            return True
        if dark_ratio + bright_ratio > 0.68:
            return True
        return dark_ratio > 0.92 or bright_ratio > 0.92

    def _fallback_style(self, frame_bgr):
        """当 ONNX 模型输出异常时，使用轻量 OpenCV 近似滤镜保证画面仍可用。"""
        frame = np.ascontiguousarray(frame_bgr[:, :, :3])
        if self.style_key == ONNX_STYLE_CARTOON:
            return self._fallback_cartoon(frame)
        if self.style_key == ONNX_STYLE_MONET:
            return self._fallback_monet(frame)
        if self.style_key == ONNX_STYLE_VANGOGH:
            return self._fallback_vangogh(frame)
        return frame_bgr

    @staticmethod
    def _fallback_cartoon(frame_bgr):
        h, w = frame_bgr.shape[:2]
        gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
        gray = cv2.medianBlur(gray, 7)
        edge = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 9, 2)
        color = cv2.bilateralFilter(frame_bgr, 9, 75, 75)
        small_w = max(1, w // 4)
        small_h = max(1, h // 4)
        color = cv2.resize(color, (small_w, small_h), interpolation=cv2.INTER_LINEAR)
        color = cv2.resize(color, (w, h), interpolation=cv2.INTER_NEAREST)
        return cv2.bitwise_and(color, color, mask=edge)

    @staticmethod
    def _fallback_monet(frame_bgr):
        h, w = frame_bgr.shape[:2]
        work = frame_bgr
        max_side = max(h, w)
        if max_side > 480:
            scale = 480.0 / float(max_side)
            work = cv2.resize(frame_bgr, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)
        if hasattr(cv2, "edgePreservingFilter"):
            base = cv2.edgePreservingFilter(work, flags=1, sigma_s=48, sigma_r=0.32)
        else:
            base = cv2.bilateralFilter(work, 9, 75, 75)
        hsv = cv2.cvtColor(base, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[:, :, 1] *= 0.72
        hsv[:, :, 2] *= 1.08
        hsv = np.clip(hsv, 0, 255).astype(np.uint8)
        pastel = cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)
        pastel = cv2.GaussianBlur(pastel, (0, 0), 0.8)
        if pastel.shape[:2] != (h, w):
            pastel = cv2.resize(pastel, (w, h), interpolation=cv2.INTER_LINEAR)
        return pastel

    @staticmethod
    def _fallback_vangogh(frame_bgr):
        h, w = frame_bgr.shape[:2]
        work = frame_bgr
        max_side = max(h, w)
        if max_side > 480:
            scale = 480.0 / float(max_side)
            work = cv2.resize(frame_bgr, (max(1, int(w * scale)), max(1, int(h * scale))), interpolation=cv2.INTER_AREA)

        # 梵高风格的保底效果只做快速色彩和笔触增强，避免 cv2.stylization 在渲染线程内造成卡顿。
        hsv = cv2.cvtColor(work, cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[:, :, 0] = (hsv[:, :, 0] + 6.0) % 180.0
        hsv[:, :, 1] *= 1.82
        hsv[:, :, 2] = (hsv[:, :, 2] - 118.0) * 1.12 + 128.0
        vivid = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)

        smooth = cv2.bilateralFilter(vivid, 7, 55, 55)
        gray = cv2.cvtColor(smooth, cv2.COLOR_BGR2GRAY)
        grad_x = cv2.Sobel(gray, cv2.CV_16S, 1, 0, ksize=3)
        grad_y = cv2.Sobel(gray, cv2.CV_16S, 0, 1, ksize=3)
        strokes = cv2.addWeighted(cv2.convertScaleAbs(grad_x), 0.58, cv2.convertScaleAbs(grad_y), 0.42, 0)
        strokes = cv2.GaussianBlur(255 - strokes, (0, 0), 0.7)
        strokes_bgr = cv2.applyColorMap(strokes, cv2.COLORMAP_OCEAN)
        styled = cv2.addWeighted(smooth, 0.82, strokes_bgr, 0.18, 0)

        if styled.shape[:2] != (h, w):
            styled = cv2.resize(styled, (w, h), interpolation=cv2.INTER_LINEAR)
        return styled

    def _enhance_style_output(self, output_bgr):
        if self.style_key != ONNX_STYLE_VANGOGH or output_bgr is None:
            return output_bgr
        hsv = cv2.cvtColor(output_bgr[:, :, :3], cv2.COLOR_BGR2HSV).astype(np.float32)
        hsv[:, :, 0] = (hsv[:, :, 0] + 3.0) % 180.0
        hsv[:, :, 1] *= 1.48
        hsv[:, :, 2] = (hsv[:, :, 2] - 127.5) * 1.12 + 127.5
        vivid = cv2.cvtColor(np.clip(hsv, 0, 255).astype(np.uint8), cv2.COLOR_HSV2BGR)
        detail = cv2.GaussianBlur(vivid, (0, 0), 1.0)
        vivid = cv2.addWeighted(vivid, 1.18, detail, -0.18, 0)
        return cv2.addWeighted(vivid, 0.76, self._fallback_vangogh(output_bgr), 0.24, 0)

    def _blend_with_source(self, source_bgr, styled_bgr):
        if styled_bgr is None or styled_bgr.shape[:2] != source_bgr.shape[:2]:
            return source_bgr
        if self._blend_strength >= 0.999:
            return styled_bgr
        src = np.ascontiguousarray(source_bgr[:, :, :3])
        styled = np.ascontiguousarray(styled_bgr[:, :, :3])
        return cv2.addWeighted(src, 1.0 - self._blend_strength, styled, self._blend_strength, 0.0)

    def process(self, frame_bgr):
        if frame_bgr is None or np is None or cv2 is None:
            return frame_bgr
        if getattr(frame_bgr, "ndim", 0) != 3 or frame_bgr.shape[2] < 3:
            return frame_bgr
        if not self.available:
            if self.style_key != ONNX_STYLE_NONE:
                if not self.last_error:
                    self.last_error = f"{onnx_style_label(self.style_key)} ONNX 不可用，已使用本地近似滤镜。"
                return self._blend_with_source(frame_bgr, self._fallback_style(frame_bgr))
            return frame_bgr

        try:
            h, w = frame_bgr.shape[:2]
            infer_w, infer_h = self._fit_inference_size(w, h)
            work = frame_bgr[:, :, :3]
            if infer_w != w or infer_h != h:
                work = cv2.resize(work, (infer_w, infer_h), interpolation=cv2.INTER_AREA)
            rgb = cv2.cvtColor(work, cv2.COLOR_BGR2RGB)
            input_tensor = rgb.astype(np.float32) / 255.0
            input_tensor = np.transpose(input_tensor, (2, 0, 1))[None, :, :, :]
            start = time.perf_counter()
            with self._lock:
                output = self.session.run([self.output_name], {self.input_name: input_tensor})[0]
            self.last_inference_ms = (time.perf_counter() - start) * 1000.0
            output_rgb = self._postprocess_output(output)
            if output_rgb is None:
                self.last_error = f"{onnx_style_label(self.style_key)} 模型输出无法解析，已使用本地近似滤镜。"
                return self._blend_with_source(frame_bgr, self._fallback_style(frame_bgr))
            output_bgr = cv2.cvtColor(output_rgb, cv2.COLOR_RGB2BGR)
            if output_bgr.shape[:2] != (h, w):
                output_bgr = cv2.resize(output_bgr, (w, h), interpolation=cv2.INTER_CUBIC)
            output_bgr = self._enhance_style_output(output_bgr)
            if self._is_degenerate_output(output_bgr):
                self.last_error = f"{onnx_style_label(self.style_key)} 模型输出异常，已自动降级为本地近似滤镜。"
                output_bgr = self._fallback_style(frame_bgr)
            return self._blend_with_source(frame_bgr, output_bgr)
        except Exception as exc:
            self.last_error = f"ONNX 风格迁移推理失败: {exc}"
            return frame_bgr


def fallback_onnx_style_filter(frame, style_key: str | None):
    style = canonical_onnx_style(style_key)
    if style == ONNX_STYLE_NONE or frame is None or np is None or cv2 is None:
        return frame
    if style == ONNX_STYLE_CARTOON:
        styled = OnnxStyleTransferFilter._fallback_cartoon(np.ascontiguousarray(frame[:, :, :3]))
        return cv2.addWeighted(frame[:, :, :3], 0.12, styled, 0.88, 0)
    if style == ONNX_STYLE_MONET:
        styled = OnnxStyleTransferFilter._fallback_monet(np.ascontiguousarray(frame[:, :, :3]))
        return cv2.addWeighted(frame[:, :, :3], 0.16, styled, 0.84, 0)
    if style == ONNX_STYLE_VANGOGH:
        styled = OnnxStyleTransferFilter._fallback_vangogh(np.ascontiguousarray(frame[:, :, :3]))
        return cv2.addWeighted(frame[:, :, :3], 0.05, styled, 0.95, 0)
    return frame


def preload_onnx_style_filter(style_key: str | None) -> None:
    style = canonical_onnx_style(style_key)
    if style == ONNX_STYLE_NONE:
        return
    with _STYLE_FILTER_CACHE_LOCK:
        if style in _STYLE_FILTER_CACHE:
            return
    with _STYLE_FILTER_LOADING_LOCK:
        if style in _STYLE_FILTER_LOADING:
            return
        _STYLE_FILTER_LOADING.add(style)

    def loader() -> None:
        try:
            style_filter = OnnxStyleTransferFilter(style)
            with _STYLE_FILTER_CACHE_LOCK:
                _STYLE_FILTER_CACHE[style] = style_filter
        finally:
            with _STYLE_FILTER_LOADING_LOCK:
                _STYLE_FILTER_LOADING.discard(style)

    threading.Thread(target=loader, name=f"onnx-preload-{style}", daemon=True).start()


def get_onnx_style_filter(style_key: str | None, *, blocking: bool = False) -> OnnxStyleTransferFilter | None:
    style = canonical_onnx_style(style_key)
    if style == ONNX_STYLE_NONE:
        return None
    with _STYLE_FILTER_CACHE_LOCK:
        cached = _STYLE_FILTER_CACHE.get(style)
        if cached is not None:
            return cached
    if not blocking:
        preload_onnx_style_filter(style)
        return None
    style_filter = OnnxStyleTransferFilter(style)
    with _STYLE_FILTER_CACHE_LOCK:
        _STYLE_FILTER_CACHE[style] = style_filter
    return style_filter


def apply_onnx_style_filter(frame, style_key: str | None):
    style = canonical_onnx_style(style_key)
    if style == ONNX_STYLE_NONE:
        return frame
    # 渲染线程不负责启动 ONNX 模型加载，避免首次应用时抢占 CPU 造成节目画面卡顿。
    # 模型预加载由 UI 的“应用”动作触发；加载未完成前使用轻量本地近似滤镜兜底。
    with _STYLE_FILTER_CACHE_LOCK:
        style_filter = _STYLE_FILTER_CACHE.get(style)
    if style_filter is None:
        return fallback_onnx_style_filter(frame, style)
    return style_filter.process(frame)


def prewarm_mediapipe_components(*, segmentation: bool = False, face_mesh: bool = False) -> None:
    """后台预热 MediaPipe 模型，减少首次启用虚拟背景或 AR 时的卡顿。"""
    if mp is None:
        return
    requested = set()
    if segmentation:
        requested.add("segmentation")
    if face_mesh:
        requested.add("face_mesh")
    if not requested:
        return
    with _MEDIAPIPE_PREWARM_LOCK:
        pending = requested - _MEDIAPIPE_PREWARM_STARTED
        if not pending:
            return
        _MEDIAPIPE_PREWARM_STARTED.update(pending)

    def runner() -> None:
        try:
            if "segmentation" in pending:
                engine = MediaPipeEngine(ema_alpha=0.62, mask_threshold=0.42, erode_size=2, feather_kernel=11)
                engine.close()
            if "face_mesh" in pending:
                face_mesh_cls = _load_mp_solution("face_mesh", "FaceMesh")
                if face_mesh_cls is not None:
                    mesh = face_mesh_cls(
                        static_image_mode=False,
                        max_num_faces=1,
                        refine_landmarks=True,
                        min_detection_confidence=0.66,
                        min_tracking_confidence=0.66,
                    )
                    try:
                        mesh.close()
                    except Exception:
                        pass
        except Exception:
            pass

    threading.Thread(target=runner, name="mediapipe-prewarm", daemon=True).start()


class BaseMattingEngine(ABC):
    engine_key = "base"
    display_name = "Base"

    def __init__(self) -> None:
        self.last_error = ""
        self.provider_name = ""

    @abstractmethod
    def process(self, frame_bgr):
        raise NotImplementedError

    @abstractmethod
    def reset_state(self) -> None:
        raise NotImplementedError

    def close(self) -> None:
        self.reset_state()


class MediaPipeEngine(BaseMattingEngine):
    engine_key = "mediapipe"
    display_name = "MediaPipe"

    __slots__ = (
        "_segmenter",
        "_ema_alpha",
        "_mask_threshold",
        "_feather_kernel",
        "_erode_kernel",
        "_prev_mask",
        "last_error",
        "provider_name",
    )

    def __init__(
        self,
        ema_alpha: float = 0.62,
        mask_threshold: float = 0.42,
        erode_size: int = 2,
        feather_kernel: int = 11,
    ) -> None:
        super().__init__()
        self._segmenter = None
        self._ema_alpha = float(max(0.0, min(1.0, ema_alpha)))
        self._mask_threshold = float(max(0.0, min(1.0, mask_threshold)))
        kernel = max(3, int(feather_kernel))
        if kernel % 2 == 0:
            kernel += 1
        erode = max(1, int(erode_size))
        self._feather_kernel = kernel
        self._erode_kernel = None if cv2 is None else cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (erode, erode))
        self._prev_mask = None
        self.provider_name = "MediaPipe"

        if mp is None:
            self.last_error = MEDIAPIPE.error or "mediapipe unavailable"
            return

        try:
            selfie_segmentation_cls = _load_mp_solution("selfie_segmentation", "SelfieSegmentation")
            if selfie_segmentation_cls is None:
                raise RuntimeError("mediapipe selfie segmentation solution is unavailable")
            self._segmenter = selfie_segmentation_cls(model_selection=1)
        except Exception as exc:
            self.last_error = str(exc)
            self._segmenter = None

    def reset_state(self) -> None:
        self._prev_mask = None

    def close(self) -> None:
        segmenter = self._segmenter
        self._segmenter = None
        if segmenter is not None:
            try:
                segmenter.close()
            except Exception:
                pass
        self.reset_state()

    def _postprocess_mask(self, mask):
        if np is None:
            return None
        mask = np.array(mask, dtype=np.float32, copy=True)
        np.clip(mask, 0.0, 1.0, out=mask)
        if self._prev_mask is not None and getattr(self._prev_mask, "shape", None) == mask.shape:
            alpha = self._ema_alpha
            mask = alpha * mask + (1.0 - alpha) * self._prev_mask
        self._prev_mask = mask.copy()

        if cv2 is None:
            return mask[:, :, None]

        soft_mask = cv2.bilateralFilter(mask, 5, 32, 32)
        _retval, binary_mask = cv2.threshold(soft_mask, self._mask_threshold, 1.0, cv2.THRESH_BINARY)
        if self._erode_kernel is not None:
            binary_mask = cv2.erode(binary_mask, self._erode_kernel, iterations=1)
            binary_mask = cv2.morphologyEx(binary_mask, cv2.MORPH_OPEN, self._erode_kernel, iterations=1)
            binary_mask = cv2.morphologyEx(binary_mask, cv2.MORPH_CLOSE, self._erode_kernel, iterations=1)
        feathered_mask = cv2.GaussianBlur(binary_mask, (self._feather_kernel, self._feather_kernel), 0)
        confidence = cv2.GaussianBlur(soft_mask, (5, 5), 0)
        feathered_mask = np.where(confidence > 0.82, 1.0, feathered_mask)
        feathered_mask = np.where(
            (confidence > self._mask_threshold) & (confidence <= 0.82),
            np.maximum(feathered_mask, confidence * 0.92),
            feathered_mask,
        )
        np.clip(feathered_mask, 0.0, 1.0, out=feathered_mask)
        return feathered_mask[:, :, None]

    def process(self, frame_bgr):
        if frame_bgr is None or np is None or cv2 is None or self._segmenter is None:
            return None, None
        try:
            rgb = cv2.cvtColor(frame_bgr[:, :, :3], cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            result = self._segmenter.process(rgb)
            raw_mask = getattr(result, "segmentation_mask", None)
            if raw_mask is None:
                self.last_error = "empty segmentation mask"
                return None, None
            alpha = self._postprocess_mask(raw_mask)
            if alpha is None:
                self.last_error = "mask post-process failed"
                return None, None
            fgr = frame_bgr[:, :, :3].astype(np.float32) / 255.0
            self.last_error = ""
            return fgr, alpha.astype(np.float32, copy=False)
        except Exception as exc:
            self.last_error = str(exc)
            return None, None


class VirtualBackgroundFilter:
    """Virtual background filter backed by MediaPipe matting."""

    __slots__ = (
        "current_engine",
        "current_engine_type",
        "_background_blur_kernel",
        "_bg_cache_key",
        "_bg_cache_f32",
        "last_error",
        "last_metrics",
    )

    def __init__(self, blur_kernel: int = 41) -> None:
        blur = max(3, int(blur_kernel))
        if blur % 2 == 0:
            blur += 1
        self.current_engine = MediaPipeEngine()
        self.current_engine_type = "mediapipe"
        self._background_blur_kernel = blur
        self._bg_cache_key = None
        self._bg_cache_f32 = None
        self.last_error = getattr(self.current_engine, "last_error", "")
        self.last_metrics: dict[str, object] = {}

    def _update_metrics(self, inference_time_ms: float, note: str = "", provider: str = "") -> None:
        fps = 0.0 if inference_time_ms <= 0.0 else 1000.0 / inference_time_ms
        self.last_metrics = {
            "engine_type": "mediapipe",
            "engine_label": "MediaPipe",
            "provider": provider,
            "inference_time_ms": inference_time_ms,
            "estimated_fps": fps,
            "note": note,
        }

    def switch_engine(self, engine_type: str = "mediapipe", *_args, **_kwargs) -> None:
        if self.current_engine is None:
            self.current_engine = MediaPipeEngine()
        self.current_engine_type = "mediapipe"
        self.current_engine.reset_state()
        self.last_error = getattr(self.current_engine, "last_error", "")

    def reset_state(self) -> None:
        if self.current_engine is not None:
            self.current_engine.reset_state()

    def close(self) -> None:
        if self.current_engine is not None:
            try:
                self.current_engine.close()
            except Exception:
                pass
        self.current_engine = None
        self._bg_cache_key = None
        self._bg_cache_f32 = None

    def _prepare_background(self, bg_image_bgr, width: int, height: int):
        if bg_image_bgr is None or np is None:
            return None
        if getattr(bg_image_bgr, "ndim", 0) != 3 or bg_image_bgr.shape[2] < 3:
            raise ValueError("bg_image_bgr must be a BGR image with 3 channels")

        src = bg_image_bgr[:, :, :3]
        cache_key = (id(bg_image_bgr), src.shape[1], src.shape[0], width, height, src.dtype.str)
        if cache_key == self._bg_cache_key and self._bg_cache_f32 is not None:
            return self._bg_cache_f32

        if src.shape[0] != height or src.shape[1] != width:
            if cv2 is None:
                raise RuntimeError("cv2 is required to resize background frames")
            prepared = cv2.resize(src, (width, height), interpolation=cv2.INTER_LINEAR)
        else:
            prepared = src

        self._bg_cache_key = cache_key
        self._bg_cache_f32 = prepared[:, :, :3].astype(np.float32) / 255.0
        return self._bg_cache_f32

    def _blur_kernel_for_strength(self, blur_strength: int) -> int:
        strength = int(max(0, min(100, blur_strength)))
        if strength <= 0:
            return 1
        kernel = 5 + int(round((strength / 100.0) * max(0, self._background_blur_kernel - 5)))
        if kernel % 2 == 0:
            kernel += 1
        return max(3, kernel)

    def _prepare_blurred_background(self, frame_bgr, blur_strength: int):
        kernel = self._blur_kernel_for_strength(blur_strength)
        if cv2 is None or kernel <= 1:
            return frame_bgr[:, :, :3].astype(np.float32) / 255.0
        blurred = cv2.GaussianBlur(frame_bgr[:, :, :3], (kernel, kernel), 0)
        return blurred.astype(np.float32) / 255.0

    @staticmethod
    def _blend_with_background(fgr, alpha, background_f32):
        if np is None:
            return None
        mask = alpha.astype(np.float32, copy=False)
        if mask.ndim == 2:
            mask = mask[:, :, None]
        blended = fgr.astype(np.float32, copy=False) * mask + background_f32.astype(np.float32, copy=False) * (1.0 - mask)
        return np.clip(blended * 255.0, 0.0, 255.0).astype(np.uint8)

    def process_frame(self, frame_bgr, bg_image_bgr=None, mode: str = "image", blur_strength: int = 55):
        if frame_bgr is None or np is None or cv2 is None:
            return frame_bgr, 0.0
        if self.current_engine is None:
            note = "MediaPipe 抠像引擎未就绪"
            self.last_error = note
            self._update_metrics(0.0, note=note)
            return frame_bgr, 0.0

        start = time.perf_counter()
        fgr, alpha = self.current_engine.process(frame_bgr)
        inference_time_ms = (time.perf_counter() - start) * 1000.0

        engine_label = getattr(self.current_engine, "display_name", self.current_engine_type.upper())
        provider_name = getattr(self.current_engine, "provider_name", "")
        engine_error = getattr(self.current_engine, "last_error", "")

        if fgr is None or alpha is None:
            self.last_error = engine_error or "matting failed"
            self._update_metrics(inference_time_ms, note=self.last_error, provider=provider_name)
            return frame_bgr, inference_time_ms

        normalized_mode = str(mode or "image").strip().lower()
        try:
            if normalized_mode == "blur":
                background_f32 = self._prepare_blurred_background(frame_bgr, blur_strength)
                result = self._blend_with_background(fgr, alpha, background_f32)
            elif bg_image_bgr is None:
                h, w = frame_bgr.shape[:2]
                result = np.empty((h, w, 4), dtype=np.uint8)
                result[:, :, :3] = np.clip(fgr * 255.0, 0.0, 255.0).astype(np.uint8)
                result[:, :, 3] = np.clip(alpha[:, :, 0] * 255.0, 0.0, 255.0).astype(np.uint8)
            else:
                background_f32 = self._prepare_background(bg_image_bgr, frame_bgr.shape[1], frame_bgr.shape[0])
                if background_f32 is None:
                    result = frame_bgr
                    self.last_error = "background image unavailable"
                else:
                    result = self._blend_with_background(fgr, alpha, background_f32)

            if self.last_error != "background image unavailable":
                self.last_error = engine_error
        except Exception as exc:
            self.last_error = str(exc)
            result = frame_bgr

        self._update_metrics(inference_time_ms, note=self.last_error or "", provider=provider_name)
        return result, inference_time_ms


class FaceEffectFilter:
    """Realtime AR face sticker filter backed by MediaPipe Face Mesh."""

    __slots__ = (
        "_face_mesh",
        "_sticker_bgra",
        "_effect_type",
        "_anchor_mode",
        "_base_scale_multiplier",
        "_user_scale",
        "_tracking_smoothing",
        "_lost_face_hold_seconds",
        "_prev_center",
        "_prev_angle",
        "_prev_eye_distance",
        "_last_transformed_sticker",
        "_last_top_left",
        "_last_detected_at",
        "last_error",
    )

    def __init__(
        self,
        sticker_path: str | None = None,
        sticker_bgra=None,
        effect_type: str = "nose",
        scale_percent: int = 100,
        tracking_smoothing: int = 70,
        lost_face_hold_ms: int = 240,
    ) -> None:
        self._face_mesh = None
        self._sticker_bgra = None
        self._effect_type = canonical_ar_effect_type(effect_type) or AR_EFFECT_DOG_NOSE
        effect_meta = AR_EFFECTS.get(self._effect_type, AR_EFFECTS[AR_EFFECT_DOG_NOSE])
        self._anchor_mode = effect_meta.get("anchor", "nose")
        self._base_scale_multiplier = _AR_EFFECT_SCALE_MULTIPLIERS.get(self._anchor_mode, 1.45)
        self._user_scale = max(0.3, min(3.0, float(scale_percent) / 100.0))
        default_sticker_path = default_ar_sticker_path(self._effect_type)
        sticker_path = str(sticker_path or default_sticker_path).strip()
        smooth = max(0.0, min(100.0, float(tracking_smoothing)))
        self._tracking_smoothing = min(0.92, (smooth / 100.0) * 0.92)
        self._lost_face_hold_seconds = max(0.0, min(1.2, float(lost_face_hold_ms) / 1000.0))
        self._prev_center: tuple[float, float] | None = None
        self._prev_angle: float | None = None
        self._prev_eye_distance: float | None = None
        self._last_transformed_sticker = None
        self._last_top_left: tuple[int, int] | None = None
        self._last_detected_at = 0.0
        self.last_error = ""

        if mp is None:
            self.last_error = MEDIAPIPE.error or "mediapipe unavailable"
            return

        try:
            face_mesh_cls = _load_mp_solution("face_mesh", "FaceMesh")
            if face_mesh_cls is None:
                raise RuntimeError("mediapipe face mesh solution is unavailable")
            self._face_mesh = face_mesh_cls(
                static_image_mode=False,
                max_num_faces=1,
                refine_landmarks=True,
                min_detection_confidence=0.66,
                min_tracking_confidence=0.66,
            )
        except Exception as exc:
            self.last_error = str(exc)
            self._face_mesh = None

        if sticker_bgra is not None:
            self._sticker_bgra = self._normalize_sticker(sticker_bgra)
        elif sticker_path:
            self._sticker_bgra = load_sticker_image(sticker_path)

        if self._sticker_bgra is None and default_sticker_path and sticker_path != default_sticker_path:
            self._sticker_bgra = load_sticker_image(default_sticker_path)

        if self._sticker_bgra is None and not self.last_error:
            self.last_error = "sticker image unavailable"

    def _set_status(self, *, running: bool, detected: bool, note: str, layer_id: str) -> None:
        set_face_effect_status(
            enabled=True,
            running=running,
            detected=detected,
            effect_type=self._effect_type,
            note=note,
            layer_id=layer_id,
        )

    def close(self) -> None:
        face_mesh = self._face_mesh
        self._face_mesh = None
        if face_mesh is not None:
            try:
                face_mesh.close()
            except Exception:
                pass

    @staticmethod
    def _normalize_sticker(sticker_bgra):
        if sticker_bgra is None or np is None:
            return None
        if getattr(sticker_bgra, "ndim", 0) != 3 or sticker_bgra.shape[2] != 4:
            return None
        return np.ascontiguousarray(sticker_bgra)

    def _build_transformed_sticker(self, eye_distance: float, angle: float):
        if self._sticker_bgra is None or cv2 is None:
            return None
        sh, sw = self._sticker_bgra.shape[:2]
        aspect = sw / max(1.0, float(sh))
        target_w = int(max(18, eye_distance * self._base_scale_multiplier * self._user_scale))
        target_h = int(max(18, target_w / max(0.1, aspect)))

        resize_interp = cv2.INTER_AREA if target_w < sw or target_h < sh else cv2.INTER_CUBIC
        resized = cv2.resize(self._sticker_bgra, (target_w, target_h), interpolation=resize_interp)
        center = (target_w / 2.0, target_h / 2.0)
        mat = cv2.getRotationMatrix2D(center, angle, 1.0)

        cos_v = abs(mat[0, 0])
        sin_v = abs(mat[0, 1])
        bound_w = max(1, int(target_h * sin_v + target_w * cos_v))
        bound_h = max(1, int(target_h * cos_v + target_w * sin_v))

        mat[0, 2] += (bound_w / 2.0) - center[0]
        mat[1, 2] += (bound_h / 2.0) - center[1]

        return cv2.warpAffine(
            resized,
            mat,
            (bound_w, bound_h),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_CONSTANT,
            borderValue=(0, 0, 0, 0),
        )

    @staticmethod
    def _blend_roi(frame_bgr, sticker_rgba, top_left_x: int, top_left_y: int):
        if np is None:
            return frame_bgr

        fh, fw = frame_bgr.shape[:2]
        sh, sw = sticker_rgba.shape[:2]

        x1 = max(0, top_left_x)
        y1 = max(0, top_left_y)
        x2 = min(fw, top_left_x + sw)
        y2 = min(fh, top_left_y + sh)
        if x1 >= x2 or y1 >= y2:
            return frame_bgr

        sx1 = x1 - top_left_x
        sy1 = y1 - top_left_y
        sx2 = sx1 + (x2 - x1)
        sy2 = sy1 + (y2 - y1)

        sticker_patch = sticker_rgba[sy1:sy2, sx1:sx2]
        alpha = sticker_patch[:, :, 3:4].astype(np.float32) / 255.0
        if float(alpha.max()) <= 0.0:
            return frame_bgr

        roi_old = frame_bgr[y1:y2, x1:x2].astype(np.float32)
        sticker_bgr = sticker_patch[:, :, :3].astype(np.float32)
        roi_new = sticker_bgr * alpha + roi_old * (1.0 - alpha)
        frame_bgr[y1:y2, x1:x2] = roi_new.astype(np.uint8)
        return frame_bgr

    def _smooth_tracking(self, center_x: float, center_y: float, angle: float, eye_distance: float) -> tuple[float, float, float, float]:
        factor = self._tracking_smoothing
        if factor <= 0.0 or self._prev_center is None or self._prev_angle is None or self._prev_eye_distance is None:
            self._prev_center = (center_x, center_y)
            self._prev_angle = angle
            self._prev_eye_distance = eye_distance
            return center_x, center_y, angle, eye_distance

        inv = 1.0 - factor
        smoothed_center_x = self._prev_center[0] * factor + center_x * inv
        smoothed_center_y = self._prev_center[1] * factor + center_y * inv
        smoothed_angle = self._prev_angle * factor + angle * inv
        smoothed_eye_distance = self._prev_eye_distance * factor + eye_distance * inv

        self._prev_center = (smoothed_center_x, smoothed_center_y)
        self._prev_angle = smoothed_angle
        self._prev_eye_distance = smoothed_eye_distance
        return smoothed_center_x, smoothed_center_y, smoothed_angle, smoothed_eye_distance

    def _hold_last_sticker(self, frame_bgr, layer_id: str, note: str, fallback_note: str):
        can_hold = not (
            self._lost_face_hold_seconds <= 0.0
            or self._last_transformed_sticker is None
            or self._last_top_left is None
            or (time.perf_counter() - self._last_detected_at) > self._lost_face_hold_seconds
        )
        if not can_hold:
            self._set_status(running=True, detected=False, note=fallback_note, layer_id=layer_id)
            return frame_bgr

        self._set_status(running=True, detected=False, note=note, layer_id=layer_id)
        return self._blend_roi(
            frame_bgr,
            self._last_transformed_sticker,
            self._last_top_left[0],
            self._last_top_left[1],
        )

    def apply_sticker(self, frame_bgr, layer_id: str = ""):
        if frame_bgr is None or cv2 is None or np is None:
            return frame_bgr

        if getattr(frame_bgr, "ndim", 0) != 3 or frame_bgr.shape[2] < 3:
            self.last_error = "frame_bgr must be a BGR image with 3 channels"
            return frame_bgr

        if self._sticker_bgra is None:
            self.last_error = self.last_error or "sticker image unavailable"
            self._set_status(running=False, detected=False, note="贴纸加载失败", layer_id=layer_id)
            return frame_bgr

        if self._face_mesh is None:
            self.last_error = self.last_error or "mediapipe face mesh unavailable"
            self._set_status(running=False, detected=False, note="mediapipe 不可用", layer_id=layer_id)
            return frame_bgr

        try:
            h, w = frame_bgr.shape[:2]
            rgb = cv2.cvtColor(frame_bgr[:, :, :3], cv2.COLOR_BGR2RGB)
            rgb.flags.writeable = False
            result = self._face_mesh.process(rgb)
            if not getattr(result, "multi_face_landmarks", None):
                self.last_error = ""
                return self._hold_last_sticker(frame_bgr, layer_id, "运行中，短时丢脸保持", "运行中，未检测到人脸")

            face = result.multi_face_landmarks[0]
            nose = _landmark_to_xy(face.landmark[1], w, h)
            left_eye = _landmark_to_xy(face.landmark[33], w, h)
            right_eye = _landmark_to_xy(face.landmark[263], w, h)
            forehead = _landmark_to_xy(face.landmark[10], w, h)
            chin = _landmark_to_xy(face.landmark[152], w, h)

            eye_dx = right_eye[0] - left_eye[0]
            eye_dy = right_eye[1] - left_eye[1]
            eye_distance = math.hypot(eye_dx, eye_dy)
            if eye_distance < 6.0:
                self.last_error = ""
                return self._hold_last_sticker(frame_bgr, layer_id, "人脸太小，沿用上一帧", "人脸太小，已跳过")

            eye_angle = math.degrees(math.atan2(eye_dy, eye_dx))
            face_angle = math.degrees(math.atan2(chin[1] - forehead[1], chin[0] - forehead[0])) - 90.0

            if self._anchor_mode == "hat":
                center_x = float(forehead[0])
                center_y = float(forehead[1])
                sticker_angle = face_angle
            elif self._anchor_mode == "eyes":
                center_x = float((left_eye[0] + right_eye[0]) * 0.5)
                center_y = float((left_eye[1] + right_eye[1]) * 0.5)
                sticker_angle = eye_angle
            else:
                center_x = float(nose[0])
                center_y = float(nose[1] + eye_distance * 0.12)
                sticker_angle = face_angle

            center_x, center_y, sticker_angle, eye_distance = self._smooth_tracking(
                float(center_x),
                float(center_y),
                float(sticker_angle),
                float(eye_distance),
            )

            transformed = self._build_transformed_sticker(eye_distance, sticker_angle)
            if transformed is None:
                self.last_error = "sticker transform failed"
                return frame_bgr

            if self._anchor_mode == "hat":
                center_y = center_y - transformed.shape[0] * 0.30

            top_left_x = int(round(center_x - transformed.shape[1] / 2.0))
            top_left_y = int(round(center_y - transformed.shape[0] / 2.0))
            self._last_transformed_sticker = transformed
            self._last_top_left = (top_left_x, top_left_y)
            self._last_detected_at = time.perf_counter()

            self.last_error = ""
            self._set_status(running=True, detected=True, note="运行中，已检测到人脸", layer_id=layer_id)
            return self._blend_roi(frame_bgr, transformed, top_left_x, top_left_y)
        except Exception as exc:
            self.last_error = str(exc)
            self._set_status(running=False, detected=False, note=f"贴纸处理失败: {exc}", layer_id=layer_id)
            return frame_bgr


def _get_face_mesh():
    if mp is None:
        return None
    global _FACE_MESH
    with _FACE_MESH_LOCK:
        if _FACE_MESH is None:
            face_mesh_cls = _load_mp_solution("face_mesh", "FaceMesh")
            if face_mesh_cls is None:
                return None
            _FACE_MESH = face_mesh_cls(
                static_image_mode=False,
                max_num_faces=1,
                refine_landmarks=True,
            min_detection_confidence=0.66,
            min_tracking_confidence=0.66,
            )
    return _FACE_MESH


def _landmark_to_xy(landmark, width: int, height: int) -> tuple[int, int]:
    return int(landmark.x * width), int(landmark.y * height)


def _overlay_rgba(dst, src_rgba, x: int, y: int):
    return FaceEffectFilter._blend_roi(dst, src_rgba, x, y)


def apply_face_effects(frame, sticker_img, effect_type: str, layer_id: str = ""):
    if frame is None or sticker_img is None:
        return frame
    face_filter = FaceEffectFilter(sticker_bgra=sticker_img, effect_type=effect_type)
    try:
        return face_filter.apply_sticker(frame, layer_id=layer_id)
    finally:
        face_filter.close()


def fit_rect(src_w: int, src_h: int, dst_w: int, dst_h: int) -> tuple[int, int, int, int]:
    if src_w <= 0 or src_h <= 0:
        return 0, 0, dst_w, dst_h
    scale = min(dst_w / src_w, dst_h / src_h)
    w = max(1, int(src_w * scale))
    h = max(1, int(src_h * scale))
    x = (dst_w - w) // 2
    y = (dst_h - h) // 2
    return x, y, w, h



