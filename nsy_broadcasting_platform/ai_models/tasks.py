from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from nsy_broadcasting_platform.models import new_id


AI_TASK_GENERATE_IMAGE = "generate_image"
AI_TASK_EDIT_IMAGE = "edit_image"
AI_TASK_ANALYZE_IMAGE = "analyze_image"
AI_TASK_PROMPT_ASSIST = "prompt_assist"


@dataclass(slots=True)
class AITask:
    provider: str
    task_type: str
    prompt: str
    input_image_path: str = ""
    model: str = ""
    task_id: str = field(default_factory=lambda: new_id("ai_task"))
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AIProviderResponse:
    text: str = ""
    image_payloads: list[tuple[bytes, str]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    warning: str = ""


@dataclass(slots=True)
class AIResult:
    task_id: str
    provider: str
    task_type: str
    ok: bool
    message: str
    text: str = ""
    image_paths: list[str] = field(default_factory=list)
    input_image_path: str = ""
    model: str = ""
    elapsed_ms: float = 0.0
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def first_image_path(self) -> str:
        return self.image_paths[0] if self.image_paths else ""


def safe_image_suffix(mime_type: str | None) -> str:
    mime = (mime_type or "").lower()
    if "jpeg" in mime or "jpg" in mime:
        return ".jpg"
    if "webp" in mime:
        return ".webp"
    return ".png"


def ensure_output_dir(base_dir: str | Path) -> Path:
    path = Path(base_dir)
    path.mkdir(parents=True, exist_ok=True)
    return path
