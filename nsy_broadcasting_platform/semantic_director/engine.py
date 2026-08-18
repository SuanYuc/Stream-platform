from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from nsy_broadcasting_platform.gpu_runtime import create_session


@dataclass(slots=True)
class SemanticSceneFrame:
    scene_id: str
    scene_name: str
    image: Any


@dataclass(slots=True)
class SemanticSceneScore:
    scene_id: str
    scene_name: str
    score: float
    reason: str
    inference_ms: float = 0.0


@dataclass(slots=True)
class SemanticRecommendationResult:
    query: str
    scores: list[SemanticSceneScore]
    best_scene_id: str | None
    provider: str
    elapsed_ms: float
    error: str = ""


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_fgclip2_dir() -> Path:
    """返回项目内 FG-CLIP2 ONNX 模型目录，兼容历史 onnx_models 布局。"""
    root = _project_root()
    direct = root / "fgclip2_semantic"
    if direct.exists():
        return direct
    return root / "onnx_models" / "fgclip2_semantic"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return json.loads(path.read_text(encoding="utf-8-sig"))


def _normalize_embedding(value: np.ndarray) -> np.ndarray:
    arr = np.asarray(value, dtype=np.float32)
    arr = np.squeeze(arr)
    if arr.ndim != 1:
        arr = arr.reshape(-1)
    norm = float(np.linalg.norm(arr))
    if norm <= 1e-8:
        return arr
    return arr / norm


def _dtype_for_onnx(type_name: str, default: np.dtype = np.int64) -> np.dtype:
    lowered = (type_name or "").lower()
    if "int32" in lowered:
        return np.int32
    if "int64" in lowered:
        return np.int64
    if "float16" in lowered:
        return np.float16
    if "float" in lowered:
        return np.float32
    return default


def qimage_to_rgb_array(image: Any) -> np.ndarray | None:
    """把 QImage 转为 RGB ndarray；复制一次是为了让后台线程持有稳定数据。"""
    if image is None or not hasattr(image, "isNull") or image.isNull():
        return None
    try:
        from PyQt6.QtGui import QImage

        qimg = image.convertToFormat(QImage.Format.Format_RGB888)
        width, height = qimg.width(), qimg.height()
        ptr = qimg.bits()
        ptr.setsize(qimg.bytesPerLine() * height)
        raw = np.frombuffer(ptr, dtype=np.uint8).reshape((height, qimg.bytesPerLine()))
        return raw[:, : width * 3].reshape((height, width, 3)).copy()
    except Exception:
        return None


class Fgclip2ImagePreprocessor:
    """复刻 FG-CLIP2 的 patch 化图像输入，避免依赖 transformers 重型运行库。"""

    def __init__(self, model_dir: Path, metadata: dict[str, Any]) -> None:
        config = _load_json(model_dir / "image_processor" / "preprocessor_config.json")
        image_inputs = metadata.get("image_encoder", {}).get("inputs", {})
        pixel_shape = image_inputs.get("pixel_values") or [1, 128, 768]

        self.patch_size = int(config.get("patch_size", 16) or 16)
        # ONNX 导出固定为 [1, 128, 768]，这里以模型实际输入为准。
        self.max_num_patches = int(pixel_shape[1] if len(pixel_shape) > 1 else config.get("max_num_patches", 128))
        self.rescale_factor = float(config.get("rescale_factor", 1 / 255))
        self.image_mean = np.asarray(config.get("image_mean", [0.5, 0.5, 0.5]), dtype=np.float32)
        self.image_std = np.asarray(config.get("image_std", [0.5, 0.5, 0.5]), dtype=np.float32)

    @staticmethod
    def _scaled_size(scale: float, size: int, patch_size: int) -> int:
        scaled_size = size * scale
        scaled_size = math.ceil(scaled_size / patch_size) * patch_size
        return int(max(patch_size, scaled_size))

    def _target_size(self, height: int, width: int) -> tuple[int, int]:
        scale_min, scale_max = 1e-6, 100.0
        while scale_max - scale_min >= 1e-5:
            scale = (scale_min + scale_max) / 2
            target_h = self._scaled_size(scale, height, self.patch_size)
            target_w = self._scaled_size(scale, width, self.patch_size)
            num_patches = (target_h // self.patch_size) * (target_w // self.patch_size)
            if num_patches <= self.max_num_patches:
                scale_min = scale
            else:
                scale_max = scale
        target_h = self._scaled_size(scale_min, height, self.patch_size)
        target_w = self._scaled_size(scale_min, width, self.patch_size)
        return target_h, target_w

    def _patchify(self, image: np.ndarray) -> np.ndarray:
        h, w, channels = image.shape
        ph = h // self.patch_size
        pw = w // self.patch_size
        patches = image.reshape(ph, self.patch_size, pw, self.patch_size, channels)
        patches = patches.transpose(0, 2, 1, 3, 4)
        return patches.reshape(ph * pw, self.patch_size * self.patch_size * channels)

    def preprocess(
        self,
        image_rgb: np.ndarray,
        *,
        mask_dtype: np.dtype = np.int64,
        shape_dtype: np.dtype = np.int64,
        pixel_dtype: np.dtype = np.float32,
    ) -> dict[str, np.ndarray]:
        height, width = image_rgb.shape[:2]
        target_h, target_w = self._target_size(height, width)
        resized = cv2.resize(image_rgb, (target_w, target_h), interpolation=cv2.INTER_LINEAR)
        normalized = resized.astype(np.float32) * self.rescale_factor
        normalized = (normalized - self.image_mean) / np.maximum(self.image_std, 1e-6)

        patches = self._patchify(np.ascontiguousarray(normalized)).astype(pixel_dtype, copy=False)
        patch_count = patches.shape[0]
        if patch_count > self.max_num_patches:
            patches = patches[: self.max_num_patches]
            patch_count = self.max_num_patches

        pixel_values = np.zeros((1, self.max_num_patches, patches.shape[1]), dtype=pixel_dtype)
        pixel_values[0, :patch_count] = patches

        pixel_mask = np.zeros((1, self.max_num_patches), dtype=mask_dtype)
        pixel_mask[0, :patch_count] = 1

        spatial_shapes = np.asarray(
            [[target_h // self.patch_size, target_w // self.patch_size]],
            dtype=shape_dtype,
        )
        return {
            "pixel_values": pixel_values,
            "pixel_attention_mask": pixel_mask,
            "spatial_shapes": spatial_shapes,
        }


class Fgclip2SemanticEngine:
    """FG-CLIP2 双塔语义匹配引擎，用场景缩略图和中文文本计算相似度。"""

    def __init__(self, model_dir: Path | None = None) -> None:
        self.model_dir = model_dir or default_fgclip2_dir()
        self.metadata: dict[str, Any] = {}
        self.image_session = None
        self.text_session = None
        self.tokenizer = None
        self.preprocessor: Fgclip2ImagePreprocessor | None = None
        self.provider_label = "未加载"
        self._text_cache: dict[str, np.ndarray] = {}
        self._load_error = ""

    @property
    def is_loaded(self) -> bool:
        return self.image_session is not None and self.text_session is not None and self.tokenizer is not None

    def load(self) -> None:
        if self.is_loaded:
            return
        if not self.model_dir.exists():
            raise RuntimeError(f"找不到 FG-CLIP2 模型目录: {self.model_dir}")

        try:
            from tokenizers import Tokenizer
        except Exception as exc:
            raise RuntimeError("缺少 tokenizers 依赖，请执行 pip install -r requirements.txt") from exc

        self.metadata = _load_json(self.model_dir / "metadata.json")
        self.preprocessor = Fgclip2ImagePreprocessor(self.model_dir, self.metadata)
        self.tokenizer = Tokenizer.from_file(str(self.model_dir / "tokenizer" / "tokenizer.json"))

        image_model = self.model_dir / self.metadata.get("image_encoder", {}).get("file", "fgclip2_image_encoder.onnx")
        text_model = self.model_dir / self.metadata.get("text_encoder", {}).get("file", "fgclip2_text_encoder.onnx")
        if not image_model.exists() or not text_model.exists():
            raise RuntimeError("FG-CLIP2 图像或文本 ONNX 文件缺失。")

        intra_threads = max(1, min(4, (os.cpu_count() or 4) // 2))
        image_info = create_session(image_model, intra_threads=intra_threads)
        if image_info.session is None:
            self._load_error = image_info.error
            raise RuntimeError(f"FG-CLIP2 图像 ONNX 会话创建失败: {self._load_error}")

        text_info = create_session(text_model, intra_threads=intra_threads, provider_order=image_info.providers)
        if text_info.session is None:
            self.image_session = None
            self._load_error = text_info.error
            raise RuntimeError(f"FG-CLIP2 文本 ONNX 会话创建失败: {self._load_error}")

        self.image_session = image_info.session
        self.text_session = text_info.session
        self.provider_label = image_info.provider or self._provider_label(image_info.providers[0] if image_info.providers else "")
        self._load_error = ""

    @staticmethod
    def _provider_candidates(available: list[str]) -> list[list[str]]:
        env_provider = os.environ.get("NSY_SEMANTIC_ONNX_PROVIDER", "").strip()
        priority = [
            env_provider,
            "CUDAExecutionProvider",
            "DmlExecutionProvider",
            "CPUExecutionProvider",
        ]
        candidates: list[list[str]] = []
        for provider in priority:
            if provider and provider in available and [provider] not in candidates:
                candidates.append([provider])
        if "CPUExecutionProvider" in available and ["CPUExecutionProvider"] not in candidates:
            candidates.append(["CPUExecutionProvider"])
        return candidates or [["CPUExecutionProvider"]]

    @staticmethod
    def _provider_label(provider: str) -> str:
        if provider == "CUDAExecutionProvider":
            return "CUDA GPU"
        if provider == "DmlExecutionProvider":
            return "DirectML GPU"
        if provider == "TensorrtExecutionProvider":
            return "TensorRT GPU"
        return "CPU"

    def encode_text(self, query: str) -> np.ndarray:
        self.load()
        assert self.text_session is not None and self.tokenizer is not None
        query = (query or "").strip()
        if query in self._text_cache:
            return self._text_cache[query]

        max_len = int(self.metadata.get("text_encoder", {}).get("inputs", {}).get("input_ids", [1, 64])[1])
        max_len = max(8, min(256, max_len))
        encoded = self.tokenizer.encode(query)
        ids = list(encoded.ids[:max_len])
        attention = list(getattr(encoded, "attention_mask", [])[:max_len])
        if len(attention) < len(ids):
            attention.extend([1] * (len(ids) - len(attention)))
        if len(ids) < max_len:
            pad_id = self.tokenizer.token_to_id("<pad>")
            pad_len = max_len - len(ids)
            ids.extend([0 if pad_id is None else int(pad_id)] * pad_len)
            attention.extend([0] * pad_len)

        feed: dict[str, np.ndarray] = {}
        for input_meta in self.text_session.get_inputs():
            dtype = _dtype_for_onnx(input_meta.type, np.int64)
            name = input_meta.name
            if name == "input_ids":
                feed[name] = np.asarray([ids], dtype=dtype)
            elif name == "attention_mask":
                feed[name] = np.asarray([attention], dtype=dtype)
            elif "token_type" in name:
                feed[name] = np.zeros((1, max_len), dtype=dtype)

        output_name = self._output_name(self.text_session, self.metadata.get("text_encoder", {}).get("output"))
        text_embeds = self.text_session.run([output_name], feed)[0]
        embedding = _normalize_embedding(text_embeds)
        self._text_cache[query] = embedding
        return embedding

    def encode_image(self, image: Any) -> np.ndarray | None:
        self.load()
        assert self.image_session is not None and self.preprocessor is not None
        image_rgb = qimage_to_rgb_array(image)
        if image_rgb is None or image_rgb.size == 0:
            return None

        input_meta = {item.name: item for item in self.image_session.get_inputs()}
        pixel_dtype = _dtype_for_onnx(input_meta.get("pixel_values").type if "pixel_values" in input_meta else "float32", np.float32)
        mask_dtype = _dtype_for_onnx(
            input_meta.get("pixel_attention_mask").type if "pixel_attention_mask" in input_meta else "int64",
            np.int64,
        )
        shape_dtype = _dtype_for_onnx(
            input_meta.get("spatial_shapes").type if "spatial_shapes" in input_meta else "int64",
            np.int64,
        )
        prepared = self.preprocessor.preprocess(
            image_rgb,
            mask_dtype=mask_dtype,
            shape_dtype=shape_dtype,
            pixel_dtype=pixel_dtype,
        )
        feed = {name: prepared[name] for name in input_meta if name in prepared}
        output_name = self._output_name(self.image_session, self.metadata.get("image_encoder", {}).get("output"))
        image_embeds = self.image_session.run([output_name], feed)[0]
        return _normalize_embedding(image_embeds)

    @staticmethod
    def _output_name(session: Any, preferred: str | None) -> str:
        outputs = session.get_outputs()
        if preferred and any(item.name == preferred for item in outputs):
            return preferred
        return outputs[0].name

    def recommend(
        self,
        query: str,
        frames: list[SemanticSceneFrame],
        *,
        threshold: float = 0.0,
    ) -> SemanticRecommendationResult:
        started = time.perf_counter()
        query = (query or "").strip()
        if not query:
            return SemanticRecommendationResult(query, [], None, self.provider_label, 0.0, "请输入语义搜索词。")
        if not frames:
            return SemanticRecommendationResult(query, [], None, self.provider_label, 0.0, "暂无可用于匹配的场景缩略图。")

        try:
            text_embedding = self.encode_text(query)
        except Exception as exc:
            elapsed = (time.perf_counter() - started) * 1000
            return SemanticRecommendationResult(query, [], None, self.provider_label, elapsed, str(exc))

        scores: list[SemanticSceneScore] = []
        for frame in frames:
            single_started = time.perf_counter()
            try:
                image_embedding = self.encode_image(frame.image)
            except Exception as exc:
                scores.append(
                    SemanticSceneScore(
                        scene_id=frame.scene_id,
                        scene_name=frame.scene_name,
                        score=-1.0,
                        reason=f"推理失败: {exc}",
                        inference_ms=(time.perf_counter() - single_started) * 1000,
                    )
                )
                continue
            if image_embedding is None:
                continue
            score = float(np.dot(image_embedding, text_embedding))
            if score < threshold:
                reason = f"低于阈值 {threshold:.2f}"
            else:
                reason = "FG-CLIP2 图文相似度命中"
            scores.append(
                SemanticSceneScore(
                    scene_id=frame.scene_id,
                    scene_name=frame.scene_name,
                    score=score,
                    reason=reason,
                    inference_ms=(time.perf_counter() - single_started) * 1000,
                )
            )

        scores.sort(key=lambda item: item.score, reverse=True)
        best = scores[0].scene_id if scores and scores[0].score >= threshold else None
        elapsed = (time.perf_counter() - started) * 1000
        return SemanticRecommendationResult(query, scores, best, self.provider_label, elapsed)
