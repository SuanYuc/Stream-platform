from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


def _config_dir() -> Path:
    root = os.environ.get("APPDATA")
    if root:
        return Path(root) / "Nsy_Broadcasting_platform"
    return Path.home() / ".nsy_broadcasting_platform"


@dataclass(slots=True)
class AIProviderSettings:
    provider: str
    api_key: str = ""
    model: str = ""
    base_url: str = ""
    timeout_s: int = 90


class AISettingsStore:
    """保存大模型配置。API Key 优先读取环境变量，界面保存值作为兜底。"""

    DEFAULTS = {
        "gemini": AIProviderSettings(
            provider="gemini",
            model="gemini-2.5-flash-image-preview",
            base_url="https://generativelanguage.googleapis.com/v1beta",
        ),
        "deepseek": AIProviderSettings(
            provider="deepseek",
            model="deepseek-chat",
            base_url="https://api.deepseek.com",
        ),
    }

    ENV_KEYS = {
        "gemini": ("NSY_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
        "deepseek": ("NSY_DEEPSEEK_API_KEY", "DEEPSEEK_API_KEY"),
    }

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else _config_dir() / "ai_settings.json"
        self._settings = {key: value for key, value in self.DEFAULTS.items()}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            return
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(payload, dict):
            return
        for provider, default in self.DEFAULTS.items():
            raw = dict(payload.get(provider) or {})
            self._settings[provider] = AIProviderSettings(
                provider=provider,
                api_key=str(raw.get("api_key") or default.api_key),
                model=str(raw.get("model") or default.model),
                base_url=str(raw.get("base_url") or default.base_url),
                timeout_s=max(10, int(raw.get("timeout_s") or default.timeout_s)),
            )

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            key: {
                "api_key": value.api_key,
                "model": value.model,
                "base_url": value.base_url,
                "timeout_s": value.timeout_s,
            }
            for key, value in self._settings.items()
        }
        self.path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def get(self, provider: str) -> AIProviderSettings:
        key = provider.strip().lower()
        setting = self._settings.get(key, self.DEFAULTS.get(key, AIProviderSettings(provider=key)))
        env_key = next((os.environ[name] for name in self.ENV_KEYS.get(key, ()) if os.environ.get(name)), "")
        if env_key:
            return AIProviderSettings(
                provider=setting.provider,
                api_key=env_key,
                model=setting.model,
                base_url=setting.base_url,
                timeout_s=setting.timeout_s,
            )
        return setting

    def update(self, provider: str, *, api_key: str, model: str, base_url: str, timeout_s: int = 90) -> None:
        key = provider.strip().lower()
        self._settings[key] = AIProviderSettings(
            provider=key,
            api_key=api_key.strip(),
            model=model.strip(),
            base_url=base_url.strip(),
            timeout_s=max(10, int(timeout_s)),
        )
        self.save()

    def providers(self) -> list[str]:
        return list(self.DEFAULTS.keys())
