from __future__ import annotations

import queue
import threading
from dataclasses import dataclass

from PyQt6.QtCore import QObject, pyqtSignal

from nsy_broadcasting_platform.semantic_director.engine import (
    Fgclip2SemanticEngine,
    SemanticRecommendationResult,
    SemanticSceneFrame,
)


@dataclass(slots=True)
class _RecommendationTask:
    query: str
    threshold: float
    frames: list[SemanticSceneFrame]


class SemanticRecommendationWorker(QObject):
    """后台语义推荐线程，避免 FG-CLIP2 ONNX 推理阻塞 Qt 主线程。"""

    result_ready = pyqtSignal(object)
    status_changed = pyqtSignal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._engine = Fgclip2SemanticEngine()
        self._queue: queue.Queue[_RecommendationTask | None] = queue.Queue(maxsize=1)
        self._stop_event = threading.Event()
        self._busy_lock = threading.Lock()
        self._busy = False
        self._thread = threading.Thread(target=self._run, name="SemanticRecommendationWorker", daemon=True)
        self._thread.start()

    def submit(self, query: str, frames: list[SemanticSceneFrame], threshold: float = 0.0) -> bool:
        task = _RecommendationTask(query=(query or "").strip(), threshold=float(threshold), frames=list(frames))
        if not task.query:
            self.status_changed.emit("请输入语义搜索词。")
            return False
        if not task.frames:
            self.status_changed.emit("等待场景缩略图生成后再进行推荐。")
            return False

        # 队列只保留最新任务，避免用户快速输入时堆积大量过期推理请求。
        try:
            while True:
                self._queue.get_nowait()
        except queue.Empty:
            pass
        try:
            self._queue.put_nowait(task)
            return True
        except queue.Full:
            return False

    def stop(self) -> None:
        self._stop_event.set()
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass

    def is_busy(self) -> bool:
        with self._busy_lock:
            return self._busy

    def provider_label(self) -> str:
        return self._engine.provider_label

    def _set_busy(self, value: bool) -> None:
        with self._busy_lock:
            self._busy = value

    def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                task = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if task is None:
                continue
            self._set_busy(True)
            try:
                self.status_changed.emit("正在加载 FG-CLIP2 ONNX 模型并计算推荐结果...")
                result = self._engine.recommend(task.query, task.frames, threshold=task.threshold)
                self.result_ready.emit(result)
                if result.error:
                    self.status_changed.emit(result.error)
                elif result.best_scene_id:
                    self.status_changed.emit(
                        f"推荐完成: {len(result.scores)} 个场景，推理设备 {result.provider}，耗时 {result.elapsed_ms:.0f} ms。"
                    )
                else:
                    self.status_changed.emit("推荐完成，但没有场景超过当前阈值。")
            except Exception as exc:
                self.result_ready.emit(
                    SemanticRecommendationResult(
                        task.query,
                        [],
                        None,
                        self._engine.provider_label,
                        0.0,
                        f"语义推荐线程异常: {exc}",
                    )
                )
                self.status_changed.emit(f"语义推荐线程异常: {exc}")
            finally:
                self._set_busy(False)
