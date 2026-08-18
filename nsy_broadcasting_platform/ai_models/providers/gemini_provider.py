from __future__ import annotations

from base64 import b64decode
from typing import Any

from nsy_broadcasting_platform.ai_models.base import AIProviderError, BaseAIProvider, file_to_inline_data, http_json
from nsy_broadcasting_platform.ai_models.tasks import AIProviderResponse, AITask


class GeminiProvider(BaseAIProvider):
    provider_name = "Gemini"
    supports_image_generation = True
    supports_image_editing = True
    supports_image_analysis = True

    def _endpoint(self, model: str) -> str:
        base = (self.settings.base_url or "https://generativelanguage.googleapis.com/v1beta").rstrip("/")
        return f"{base}/models/{model}:generateContent"

    def _request(
        self,
        parts: list[dict[str, Any]],
        *,
        model: str | None = None,
        image_mode: bool = False,
        options: dict[str, Any] | None = None,
    ) -> AIProviderResponse:
        self.validate_key()
        selected_model = model or self.model or "gemini-2.5-flash-image-preview"
        payload: dict[str, Any] = {"contents": [{"role": "user", "parts": parts}]}
        opts = options or {}
        generation_config: dict[str, Any] = {
            "temperature": float(opts.get("temperature", 0.72 if image_mode else 0.35)),
            "topP": float(opts.get("top_p", 0.9)),
        }
        if image_mode:
            # Gemini 图像模型通常返回 inlineData；responseModalities 让文本和图片都可返回。
            generation_config["responseModalities"] = ["TEXT", "IMAGE"]
        payload["generationConfig"] = generation_config
        raw = http_json(
            self._endpoint(selected_model),
            payload,
            headers={"x-goog-api-key": self.api_key},
            timeout_s=self.settings.timeout_s,
        )
        text_parts: list[str] = []
        image_payloads: list[tuple[bytes, str]] = []
        self._walk_parts(raw, text_parts, image_payloads)
        if image_mode and not image_payloads:
            warning = "Gemini 未返回图片数据，请检查模型是否支持图片输出。"
        else:
            warning = ""
        return AIProviderResponse(text="\n".join(part for part in text_parts if part).strip(), image_payloads=image_payloads, raw=raw, warning=warning)

    @staticmethod
    def _broadcast_image_prompt(prompt: str) -> str:
        return (
            "请生成适合直播导播系统使用的高质量视觉素材。要求主体清晰、边缘干净、构图稳定，"
            "可以作为独立图层拖入画布；避免文字乱码、水印、过度噪点和明显伪影。"
            "画面应具备真实可用的光影、细节和空间层次。\n"
            f"用户需求：{prompt.strip()}"
        )

    @staticmethod
    def _broadcast_edit_prompt(prompt: str) -> str:
        return (
            "请在保留原图主体结构、透视关系和重要内容的前提下进行图像编辑。输出应适合直播画面叠加或替换；"
            "边缘要自然，避免明显拼接痕迹、过曝、涂抹和多余文字。\n"
            f"编辑需求：{prompt.strip()}"
        )

    def _walk_parts(self, node: Any, text_parts: list[str], image_payloads: list[tuple[bytes, str]]) -> None:
        if isinstance(node, dict):
            text = node.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())
            inline = node.get("inlineData") or node.get("inline_data")
            if isinstance(inline, dict):
                data = inline.get("data")
                mime = inline.get("mimeType") or inline.get("mime_type") or "image/png"
                if isinstance(data, str) and data:
                    try:
                        image_payloads.append((b64decode(data), str(mime)))
                    except Exception:
                        pass
            for value in node.values():
                self._walk_parts(value, text_parts, image_payloads)
        elif isinstance(node, list):
            for value in node:
                self._walk_parts(value, text_parts, image_payloads)

    def generate_image(self, prompt: str, task: AITask) -> AIProviderResponse:
        if not prompt.strip():
            raise AIProviderError("图片生成提示词不能为空")
        parts = [{"text": self._broadcast_image_prompt(prompt)}]
        return self._request(parts, model=task.model or self.model, image_mode=True, options=task.options)

    def edit_image(self, image_path: str, prompt: str, task: AITask) -> AIProviderResponse:
        if not image_path:
            raise AIProviderError("图片编辑需要输入画面")
        if not prompt.strip():
            raise AIProviderError("图片编辑提示词不能为空")
        inline = file_to_inline_data(image_path)
        parts = [{"text": self._broadcast_edit_prompt(prompt)}, {"inline_data": inline}]
        return self._request(parts, model=task.model or self.model, image_mode=True, options=task.options)

    def analyze_image(self, image_path: str, prompt: str, task: AITask) -> AIProviderResponse:
        if not image_path:
            raise AIProviderError("图片分析需要输入画面")
        inline = file_to_inline_data(image_path)
        text = prompt.strip() or (
            "请分析这张直播画面，按主体、场景、风险点、可导播价值、可作为图层处理的区域五项输出。"
            "回答要简洁，重点说明是否适合切到节目输出。"
        )
        parts = [{"text": text}, {"inline_data": inline}]
        return self._request(parts, model=task.model or self.model, image_mode=False, options=task.options)

    def prompt_assist(self, prompt: str, task: AITask) -> AIProviderResponse:
        text = (
            "请把下面的中文需求改写成适合图像生成或图像编辑模型的提示词，"
            "要求具体、可执行、保留原意，并补充构图、主体、背景、光线、清晰度和负面约束；只输出提示词：\n"
            f"{prompt.strip()}"
        )
        return self._request([{"text": text}], model=task.model or self.model, image_mode=False, options=task.options)
