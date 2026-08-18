from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class AppConfig:
    canvas_width: int = 1280
    canvas_height: int = 720
    render_fps: int = 30
    program_delay_ms: int = 0
    audio_sample_rate: int = 48000
    audio_channels: int = 2
    audio_chunk: int = 1024
    output_dir: Path = Path("outputs")
    default_rtmp_url: str = "rtmp://<YOUR_SERVER_IP>:1935/live/main"
    cloud_hls_url: str = "http://<YOUR_SERVER_IP>:8888/live/main/index.m3u8"
    cloud_api_url: str = "http://<YOUR_SERVER_IP>:8088"
    default_record_bitrate: str = "8000k"
    default_stream_bitrate: str = "5000k"
    default_record_encoder: str = "auto"
    default_stream_encoder: str = "gpu"
    default_output_quality: str = "balanced_720p60"
    default_capture_quality: str = "balanced"
    adaptive_bitrate_enabled: bool = True
    adaptive_bitrate_min: str = "2500k"


CONFIG = AppConfig()
