from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any


@dataclass
class AdaptiveBitrateDecision:
    state: str
    text: str
    target_bitrate: str = ""
    pressure: float = 0.0
    dropped_delta: int = 0
    submitted_delta: int = 0


class AdaptiveBitrateController:
    """
    Lightweight network/encoder pressure observer.

    It intentionally does not restart a live encoder by default. PyAV/FFmpeg
    bitrate changes are not generally safe once a stream is opened, so this
    controller recommends a lower bitrate and lets the UI apply it before the
    next stream start while live frame dropping keeps A/V sync protected.
    """

    LADDER_KBPS = (2500, 3500, 4500, 6000, 8000, 10000, 12000)

    def __init__(self, min_bitrate: str = "2500k", cooldown_s: float = 12.0) -> None:
        self.min_kbps = self.parse_bitrate_kbps(min_bitrate) or 2500
        self.cooldown_s = max(3.0, float(cooldown_s))
        self._last_dropped: int | None = None
        self._last_submitted: int | None = None
        self._last_action_ts = 0.0
        self._last_state = "idle"

    @classmethod
    def parse_bitrate_kbps(cls, value: str | int | float | None) -> int:
        raw = str(value or "").strip().lower()
        if not raw:
            return 0
        try:
            if raw.endswith("k"):
                return max(1, int(float(raw[:-1])))
            if raw.endswith("m"):
                return max(1, int(float(raw[:-1]) * 1000))
            return max(1, int(float(raw)))
        except Exception:
            return 0

    @staticmethod
    def format_bitrate(kbps: int) -> str:
        return f"{max(1, int(kbps))}k"

    def reset(self) -> None:
        self._last_dropped = None
        self._last_submitted = None
        self._last_action_ts = 0.0
        self._last_state = "idle"

    def observe(
        self,
        stats: dict[str, Any] | None,
        current_bitrate: str,
        *,
        stream_running: bool,
    ) -> AdaptiveBitrateDecision:
        if not stream_running:
            self.reset()
            return AdaptiveBitrateDecision("idle", "ABR: 待机")
        if not stats:
            return AdaptiveBitrateDecision("observe", "ABR: 等待编码统计")

        dropped = int(stats.get("frames_dropped") or 0)
        submitted = int(stats.get("frames_submitted") or 0)
        pressure = float(stats.get("queue_pressure") or 0.0)
        queue_size = int(stats.get("queue_size") or 0)
        queue_capacity = int(stats.get("queue_capacity") or 0)

        if self._last_dropped is None:
            self._last_dropped = dropped
            self._last_submitted = submitted
            return AdaptiveBitrateDecision(
                "stable",
                f"ABR: 稳定 队列 {queue_size}/{queue_capacity}",
                pressure=pressure,
            )

        dropped_delta = max(0, dropped - self._last_dropped)
        submitted_delta = max(0, submitted - int(self._last_submitted or 0))
        self._last_dropped = dropped
        self._last_submitted = submitted
        drop_ratio = dropped_delta / max(1, submitted_delta)

        severe = pressure >= 0.82 or dropped_delta >= 8 or drop_ratio >= 0.08
        moderate = pressure >= 0.62 or dropped_delta >= 2 or drop_ratio >= 0.03
        now = time.monotonic()

        if severe and now - self._last_action_ts >= self.cooldown_s:
            target = self.lower_bitrate(current_bitrate)
            self._last_action_ts = now
            self._last_state = "reduce"
            if target:
                return AdaptiveBitrateDecision(
                    "reduce",
                    f"ABR: 拥塞，建议降至 {target}",
                    target_bitrate=target,
                    pressure=pressure,
                    dropped_delta=dropped_delta,
                    submitted_delta=submitted_delta,
                )
        if moderate:
            self._last_state = "observe"
            return AdaptiveBitrateDecision(
                "observe",
                f"ABR: 观察中 丢帧 {dropped_delta}",
                pressure=pressure,
                dropped_delta=dropped_delta,
                submitted_delta=submitted_delta,
            )

        self._last_state = "stable"
        return AdaptiveBitrateDecision(
            "stable",
            f"ABR: 稳定 队列 {queue_size}/{queue_capacity}",
            pressure=pressure,
            dropped_delta=dropped_delta,
            submitted_delta=submitted_delta,
        )

    def lower_bitrate(self, current_bitrate: str) -> str:
        current = self.parse_bitrate_kbps(current_bitrate) or 5000
        floor = max(1000, self.min_kbps)
        candidates = [value for value in self.LADDER_KBPS if floor <= value < current]
        if candidates:
            return self.format_bitrate(candidates[-1])
        lowered = int(current * 0.75)
        if lowered < current and lowered >= floor:
            return self.format_bitrate(lowered)
        return ""
