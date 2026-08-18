from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PREFERENCES_DIR = Path.home() / ".nsy_broadcasting_platform"
PREFERENCES_FILE = PREFERENCES_DIR / "preferences.json"


@dataclass
class UserPreferences:
    version: int = 1
    output_quality: str = "balanced_720p60"
    capture_quality: str = "balanced"
    stream_bitrate: str = "5000k"
    record_bitrate: str = "8000k"
    stream_encoder: str = "gpu"
    record_encoder: str = "auto"
    adaptive_bitrate_enabled: bool = True
    adaptive_bitrate_min: str = "2500k"

    def apply_to_config(self, config) -> None:
        config.default_output_quality = self.output_quality
        config.default_capture_quality = self.capture_quality
        config.default_stream_bitrate = self.stream_bitrate
        config.default_record_bitrate = self.record_bitrate
        config.default_stream_encoder = self.stream_encoder
        config.default_record_encoder = self.record_encoder
        config.adaptive_bitrate_enabled = self.adaptive_bitrate_enabled
        config.adaptive_bitrate_min = self.adaptive_bitrate_min


class PreferenceStore:
    """Small JSON preference store for UI/output options that should survive restarts."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or PREFERENCES_FILE

    def load(self) -> UserPreferences:
        if not self.path.exists():
            return UserPreferences()
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return UserPreferences()
        if not isinstance(payload, dict):
            return UserPreferences()

        defaults = asdict(UserPreferences())
        values: dict[str, Any] = {key: payload.get(key, value) for key, value in defaults.items()}
        values["adaptive_bitrate_enabled"] = bool(values.get("adaptive_bitrate_enabled", True))
        for key in (
            "output_quality",
            "capture_quality",
            "stream_bitrate",
            "record_bitrate",
            "stream_encoder",
            "record_encoder",
            "adaptive_bitrate_min",
        ):
            values[key] = str(values.get(key) or defaults[key]).strip() or defaults[key]
        try:
            values["version"] = int(values.get("version") or 1)
        except Exception:
            values["version"] = 1
        return UserPreferences(**values)

    def save(self, preferences: UserPreferences) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(asdict(preferences), ensure_ascii=False, indent=2)
        tmp_path = self.path.with_suffix(".json.tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(self.path)
