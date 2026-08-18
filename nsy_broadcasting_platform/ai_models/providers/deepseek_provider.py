from __future__ import annotations

from nsy_broadcasting_platform.ai_models.base import AIProviderError, BaseAIProvider, http_json
from nsy_broadcasting_platform.ai_models.tasks import AIProviderResponse, AITask


class DeepSeekProvider(BaseAIProvider):
    provider_name = "DeepSeek"
    supports_image_generation = False
    supports_image_editing = False
    supports_image_analysis = False

    def _endpoint(self) -> str:
        return f"{(self.settings.base_url or 'https://api.deepseek.com').rstrip('/')}/chat/completions"

    def _chat(self, prompt: str, *, model: str | None = None) -> AIProviderResponse:
        self.validate_key()
        payload = {
            "model": model or self.model or "deepseek-chat",
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
        }
        raw = http_json(
            self._endpoint(),
            payload,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout_s=self.settings.timeout_s,
        )
        try:
            text = raw["choices"][0]["message"]["content"]
        except Exception as exc:
            raise AIProviderError("DeepSeek 返回结构无法解析") from exc
        return AIProviderResponse(text=str(text or "").strip(), raw=raw)

    def generate_image(self, prompt: str, task: AITask) -> AIProviderResponse:
        text = (
            "DeepSeek 官方聊天接口当前不直接返回图片。"
            "请将下面需求改写成适合图像生成模型的高质量中文提示词。"
            "输出需包含：主体、构图、镜头视角、背景、光线、清晰度、可作为直播图层使用的要求，以及负面提示词。"
            "不要写与需求无关的内容：\n"
            f"{prompt.strip()}"
        )
        response = self._chat(text, model=task.model or self.model)
        response.warning = "DeepSeek 当前作为提示词助手使用；图片生成请切换 Gemini。"
        return response

    def edit_image(self, image_path: str, prompt: str, task: AITask) -> AIProviderResponse:
        text = (
            "DeepSeek 官方聊天接口当前不直接编辑图片。"
            "请把下面的图片编辑需求整理成清晰的图像编辑提示词。"
            "必须分别说明需要保留的内容、需要改变的区域、边缘融合要求、适合叠加到直播画面的输出要求和负面约束：\n"
            f"{prompt.strip()}"
        )
        response = self._chat(text, model=task.model or self.model)
        response.warning = "DeepSeek 当前只生成编辑提示词；实际图片编辑请切换 Gemini。"
        return response

    def analyze_image(self, image_path: str, prompt: str, task: AITask) -> AIProviderResponse:
        text = (
            "当前 DeepSeek 接口未接入图片二进制输入。请基于用户描述生成导播分析模板。"
            "如果后续接入视觉模型，可按该模板输出主体、画面价值、风险、切换建议、图层处理建议和广告位建议。\n"
            f"用户要求：{prompt.strip() or '分析当前直播画面'}"
        )
        response = self._chat(text, model=task.model or self.model)
        response.warning = "DeepSeek 当前未读取图片，只返回分析模板。"
        return response

    def prompt_assist(self, prompt: str, task: AITask) -> AIProviderResponse:
        text = (
            "请把下面需求改写成适合图像生成、图像编辑和直播导播场景使用的提示词。"
            "输出三段：图片生成提示词、图片编辑提示词、导播分析提示词。"
            "每段都要能直接复制到模型或本项目的 AI 子界面中使用。\n"
            f"{prompt.strip()}"
        )
        return self._chat(text, model=task.model or self.model)
