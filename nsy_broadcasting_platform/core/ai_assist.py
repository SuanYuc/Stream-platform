from __future__ import annotations

import re
from dataclasses import dataclass

from nsy_broadcasting_platform.models import Layer, Scene


@dataclass(slots=True)
class SemanticHit:
    scene_id: str
    scene_name: str
    layer_id: str
    layer_name: str
    score: int
    reason: str


_TOKEN_RE = re.compile(r"[\w\u4e00-\u9fff]+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return [token.lower() for token in _TOKEN_RE.findall(text or "")]


def _source_search_fields(layer: Layer) -> list[str]:
    source = layer.source
    return [
        field
        for field in (
            str(source.get("title", "")),
            str(source.get("process_name", "")),
            str(source.get("url", "")),
            str(source.get("image_path", "")),
            str(source.get("camera_index", "")),
            str(source.get("monitor_index", "")),
            str(source.get("effect_type", "")),
            str(source.get("ar_mode", "")),
        )
        if field
    ]


def _iter_search_fields(scene: Scene, layer: Layer):
    yield "场景", scene.name
    yield "图层", layer.name
    yield "类型", layer.layer_type.value
    for field in _source_search_fields(layer):
        yield "来源", field


def find_best_semantic_hit(scenes: list[Scene], query: str) -> SemanticHit | None:
    normalized_query = (query or "").strip().lower()
    if not normalized_query:
        return None
    tokens = _tokenize(normalized_query) or [normalized_query]
    best_hit: SemanticHit | None = None

    for scene in scenes:
        for layer in scene.layers:
            score = 0
            reasons: list[str] = []
            for label, text in _iter_search_fields(scene, layer):
                normalized = (text or "").strip().lower()
                if not normalized:
                    continue
                if normalized_query in normalized:
                    score += 50
                    reasons.append(f"{label}完全匹配")
                token_hits = sum(1 for token in tokens if token in normalized)
                if token_hits:
                    score += token_hits * 12
                    reasons.append(f"{label}命中{token_hits}项")
            if layer.enabled:
                score += 6
            if layer.source.get("face_enabled"):
                score += 2
            if score <= 0:
                continue
            hit = SemanticHit(
                scene_id=scene.id,
                scene_name=scene.name,
                layer_id=layer.id,
                layer_name=layer.name,
                score=score,
                reason="；".join(reasons[:2]) if reasons else "基础相关",
            )
            if best_hit is None or hit.score > best_hit.score:
                best_hit = hit
    return best_hit
