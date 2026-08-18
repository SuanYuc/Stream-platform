from __future__ import annotations

import json
import queue
import threading
import time
from pathlib import Path

from PyQt6.QtCore import QObject, pyqtSignal

from nsy_broadcasting_platform.ai_models.base import AIProviderError
from nsy_broadcasting_platform.ai_models.providers import DeepSeekProvider, GeminiProvider
from nsy_broadcasting_platform.ai_models.settings import AISettingsStore
from nsy_broadcasting_platform.ai_models.tasks import AIResult, AITask, ensure_output_dir, safe_image_suffix


class AIWorker(QObject):
    """后台大模型任务队列。所有网络请求都在 Python 线程中执行，避免阻塞 GUI 和渲染线程。"""

    task_started = pyqtSignal(object)
    task_finished = pyqtSignal(object)

    def __init__(self, settings: AISettingsStore, output_root: str | Path) -> None:
        super().__init__()
        self.settings = settings
        self.output_root = Path(output_root)
        self._queue: queue.Queue[AITask | None] = queue.Queue()
        self._thread = threading.Thread(target=self._run, name="ai-model-worker", daemon=True)
        self._thread.start()

    def submit(self, task: AITask) -> None:
        self._queue.put(task)

    def shutdown(self) -> None:
        self._queue.put(None)

    def _provider_for(self, provider: str):
        key = provider.strip().lower()
        settings = self.settings.get(key)
        if key == "gemini":
            return GeminiProvider(settings)
        if key == "deepseek":
            return DeepSeekProvider(settings)
        raise AIProviderError(f"未知模型供应商: {provider}")

    def _task_dir(self) -> Path:
        return ensure_output_dir(self.output_root / time.strftime("%Y%m%d"))

    def _save_images(self, task: AITask, payloads: list[tuple[bytes, str]]) -> list[str]:
        out_dir = self._task_dir()
        paths: list[str] = []
        for idx, (blob, mime) in enumerate(payloads, start=1):
            suffix = safe_image_suffix(mime)
            path = out_dir / f"{task.task_id}_{idx}{suffix}"
            path.write_bytes(blob)
            paths.append(str(path))
        return paths

    def _save_metadata(self, task: AITask, result: AIResult) -> None:
        try:
            meta_path = self._task_dir() / f"{task.task_id}.json"
            meta_path.write_text(
                json.dumps(
                    {
                        "task_id": result.task_id,
                        "provider": result.provider,
                        "task_type": result.task_type,
                        "model": result.model,
                        "ok": result.ok,
                        "message": result.message,
                        "text": result.text,
                        "input_image_path": result.input_image_path,
                        "image_paths": result.image_paths,
                        "elapsed_ms": result.elapsed_ms,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception:
            return

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            if task is None:
                return
            self.task_started.emit(task)
            start = time.perf_counter()
            try:
                provider = self._provider_for(task.provider)
                response = provider.run(task)
                image_paths = self._save_images(task, response.image_payloads)
                message = response.warning or ("任务完成" if image_paths or response.text else "任务完成，但没有返回有效内容")
                result = AIResult(
                    task_id=task.task_id,
                    provider=task.provider,
                    task_type=task.task_type,
                    ok=True,
                    message=message,
                    text=response.text,
                    image_paths=image_paths,
                    input_image_path=task.input_image_path,
                    model=task.model or provider.model,
                    elapsed_ms=(time.perf_counter() - start) * 1000,
                    raw=response.raw,
                )
            except Exception as exc:
                result = AIResult(
                    task_id=task.task_id,
                    provider=task.provider,
                    task_type=task.task_type,
                    ok=False,
                    message=str(exc),
                    input_image_path=task.input_image_path,
                    model=task.model,
                    elapsed_ms=(time.perf_counter() - start) * 1000,
                )
            self._save_metadata(task, result)
            self.task_finished.emit(result)
