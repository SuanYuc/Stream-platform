from __future__ import annotations

import json
import mimetypes
import urllib.error
import urllib.request
from base64 import b64encode
from pathlib import Path
from typing import Any

from nsy_broadcasting_platform.ai_models.settings import AIProviderSettings
from nsy_broadcasting_platform.ai_models.tasks import AIProviderResponse, AITask


class AIProviderError(RuntimeError):
    pass


class BaseAIProvider:
    provider_name = "base"
    supports_image_generation = False
    supports_image_editing = False
    supports_image_analysis = False

    def __init__(self, settings: AIProviderSettings) -> None:
        self.settings = settings

    @property
    def model(self) -> str:
        return self.settings.model

    @property
    def api_key(self) -> str:
        return self.settings.api_key

    def validate_key(self) -> None:
        if not self.api_key:
            raise AIProviderError(f"{self.provider_name} API Key 未配置")

    def run(self, task: AITask) -> AIProviderResponse:
        if task.task_type == "generate_image":
            return self.generate_image(task.prompt, task)
        if task.task_type == "edit_image":
            return self.edit_image(task.input_image_path, task.prompt, task)
        if task.task_type == "analyze_image":
            return self.analyze_image(task.input_image_path, task.prompt, task)
        if task.task_type == "prompt_assist":
            return self.prompt_assist(task.prompt, task)
        raise AIProviderError(f"未知 AI 任务类型: {task.task_type}")

    def generate_image(self, prompt: str, task: AITask) -> AIProviderResponse:
        raise AIProviderError(f"{self.provider_name} 暂不支持图片生成")

    def edit_image(self, image_path: str, prompt: str, task: AITask) -> AIProviderResponse:
        raise AIProviderError(f"{self.provider_name} 暂不支持图片编辑")

    def analyze_image(self, image_path: str, prompt: str, task: AITask) -> AIProviderResponse:
        raise AIProviderError(f"{self.provider_name} 暂不支持图片分析")

    def prompt_assist(self, prompt: str, task: AITask) -> AIProviderResponse:
        raise AIProviderError(f"{self.provider_name} 暂不支持提示词处理")


def http_json(url: str, payload: dict[str, Any], *, headers: dict[str, str] | None = None, timeout_s: int = 90) -> dict[str, Any]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise AIProviderError(f"HTTP {exc.code}: {detail[:600]}") from exc
    except urllib.error.URLError as exc:
        raise AIProviderError(f"网络请求失败: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AIProviderError("接口返回不是合法 JSON") from exc


def file_to_inline_data(path: str | Path) -> dict[str, str]:
    image_path = Path(path)
    if not image_path.exists():
        raise AIProviderError(f"输入图片不存在: {image_path}")
    mime_type = mimetypes.guess_type(str(image_path))[0] or "image/png"
    return {
        "mime_type": mime_type,
        "data": b64encode(image_path.read_bytes()).decode("ascii"),
    }
