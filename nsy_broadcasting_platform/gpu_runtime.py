from __future__ import annotations

import ctypes
import os
import site
import sys
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


_ORT_MODULE = None
_ORT_ERROR = ""
_ORT_LOCK = threading.Lock()
_BAD_PROVIDERS: set[str] = set()
_BAD_PROVIDER_LOCK = threading.Lock()
_DLL_DIRECTORY_HANDLES: list[Any] = []
_DLL_DIRECTORY_PATHS: set[str] = set()
_DLL_DIRECTORY_LOCK = threading.Lock()


@dataclass(slots=True)
class OnnxSessionInfo:
    session: Any | None
    provider: str = ""
    providers: list[str] = field(default_factory=list)
    error: str = ""


def _candidate_site_packages() -> list[Path]:
    paths: list[Path] = []
    for raw in site.getsitepackages() + [site.getusersitepackages(), str(Path(sys.prefix) / "Lib" / "site-packages")]:
        try:
            path = Path(raw)
        except Exception:
            continue
        if path.exists() and path not in paths:
            paths.append(path)
    return paths


def add_nvidia_dll_directories() -> list[str]:
    """把 pip 安装的 NVIDIA CUDA/cuDNN runtime DLL 目录加入当前进程搜索路径。"""
    if os.name != "nt" or not hasattr(os, "add_dll_directory"):
        return []
    added: list[str] = []
    with _DLL_DIRECTORY_LOCK:
        for site_dir in _candidate_site_packages():
            nvidia_root = site_dir / "nvidia"
            if not nvidia_root.exists():
                continue
            for bin_dir in sorted(nvidia_root.glob("*/bin")):
                if not bin_dir.is_dir():
                    continue
                raw = str(bin_dir)
                if raw in _DLL_DIRECTORY_PATHS:
                    continue
                try:
                    handle = os.add_dll_directory(raw)
                    _DLL_DIRECTORY_HANDLES.append(handle)
                    _DLL_DIRECTORY_PATHS.add(raw)
                    added.append(raw)
                except Exception:
                    pass
    return added


def preload_onnxruntime(*, quiet: bool = True):
    """提前导入 onnxruntime，避免 Windows 上 PyQt 先加载后触发 ORT DLL 初始化失败。"""
    global _ORT_MODULE, _ORT_ERROR
    with _ORT_LOCK:
        if _ORT_MODULE is not None or _ORT_ERROR:
            return _ORT_MODULE, _ORT_ERROR
        try:
            add_nvidia_dll_directories()
            import onnxruntime as ort

            if quiet:
                try:
                    ort.set_default_logger_severity(4)
                except Exception:
                    pass
            try:
                preload_dlls = getattr(ort, "preload_dlls", None)
                if callable(preload_dlls):
                    preload_dlls()
            except Exception:
                pass
            _ORT_MODULE = ort
            _ORT_ERROR = ""
        except Exception as exc:
            _ORT_MODULE = None
            _ORT_ERROR = str(exc)
        return _ORT_MODULE, _ORT_ERROR


def _env_truthy(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "cuda", "gpu"}


def _forced_provider() -> str:
    # 统一支持全局 Provider 指定；语义导播保留历史环境变量作为兼容入口。
    return (
        os.environ.get("NSY_ONNX_PROVIDER", "").strip()
        or os.environ.get("NSY_SEMANTIC_ONNX_PROVIDER", "").strip()
    )


def provider_label(provider: str) -> str:
    if provider == "CUDAExecutionProvider":
        return "CUDA GPU"
    if provider == "DmlExecutionProvider":
        return "DirectML GPU"
    if provider == "TensorrtExecutionProvider":
        return "TensorRT GPU"
    if provider == "CPUExecutionProvider":
        return "CPU"
    return provider or "未知"


def available_onnx_providers() -> list[str]:
    ort, _error = preload_onnxruntime()
    if ort is None:
        return []
    try:
        return list(ort.get_available_providers())
    except Exception:
        return []


def _provider_candidates(
    *,
    prefer_cuda: bool = True,
    prefer_dml: bool = True,
    provider_order: list[str] | None = None,
) -> list[str]:
    available = available_onnx_providers()
    forced = _forced_provider()
    ordered: list[str] = []
    if provider_order:
        ordered.extend([provider for provider in provider_order if provider in available])
    if forced and forced in available:
        ordered.append(forced)
    # TensorRT 建图成本和依赖要求更高，不主动用于实时滤镜；用户可通过 NSY_ONNX_PROVIDER 强制指定。
    if prefer_cuda and _env_truthy("NSY_ONNX_ENABLE_CUDA", True) and "CUDAExecutionProvider" in available:
        ordered.append("CUDAExecutionProvider")
    if prefer_dml and "DmlExecutionProvider" in available:
        ordered.append("DmlExecutionProvider")
    if "CPUExecutionProvider" in available:
        ordered.append("CPUExecutionProvider")

    with _BAD_PROVIDER_LOCK:
        bad = set(_BAD_PROVIDERS)
    result: list[str] = []
    for provider in ordered:
        if provider in bad and provider != forced:
            continue
        if provider not in result:
            result.append(provider)
    return result or ["CPUExecutionProvider"]


def create_session(
    model_path: str | Path,
    *,
    prefer_cuda: bool = True,
    prefer_dml: bool = True,
    intra_threads: int | None = None,
    provider_order: list[str] | None = None,
) -> OnnxSessionInfo:
    """按 GPU 优先级创建 ONNX session；单个 provider 失败后自动降级并缓存失败 provider。"""
    ort, error = preload_onnxruntime()
    if ort is None:
        return OnnxSessionInfo(None, error=f"onnxruntime 无法初始化: {error}")

    model = Path(model_path)
    if not model.exists():
        return OnnxSessionInfo(None, error=f"ONNX 模型不存在: {model}")

    session_options = ort.SessionOptions()
    session_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    if intra_threads is not None:
        session_options.intra_op_num_threads = max(1, int(intra_threads))

    last_error = ""
    tried: list[str] = []
    for provider in _provider_candidates(prefer_cuda=prefer_cuda, prefer_dml=prefer_dml, provider_order=provider_order):
        tried.append(provider)
        try:
            session = ort.InferenceSession(
                str(model),
                sess_options=session_options,
                providers=[provider],
            )
            providers = list(session.get_providers())
            if provider != "CPUExecutionProvider" and provider not in providers:
                last_error = f"{provider}: session 已静默回落到 {providers}"
                with _BAD_PROVIDER_LOCK:
                    _BAD_PROVIDERS.add(provider)
                continue
            return OnnxSessionInfo(
                session=session,
                provider=provider_label(providers[0] if providers else provider),
                providers=providers,
            )
        except Exception as exc:
            last_error = f"{provider}: {exc}"
            if provider != "CPUExecutionProvider":
                with _BAD_PROVIDER_LOCK:
                    _BAD_PROVIDERS.add(provider)
            continue

    return OnnxSessionInfo(None, error=f"ONNX 会话创建失败，已尝试 {', '.join(tried)}；{last_error}")


def cuda_dependency_report() -> dict[str, object]:
    """给界面或日志使用的轻量 GPU 诊断信息。"""
    add_nvidia_dll_directories()
    required = [
        "cublasLt64_12.dll",
        "cublas64_12.dll",
        "cudart64_12.dll",
        "cudnn64_9.dll",
        "cudnn_ops64_9.dll",
        "cudnn_cnn64_9.dll",
        "cudnn_adv64_9.dll",
        "zlibwapi.dll",
    ]
    missing: list[str] = []
    for dll in required:
        try:
            ctypes.WinDLL(dll)
        except Exception:
            missing.append(dll)
    opencv_cuda_devices = 0
    try:
        import cv2

        opencv_cuda_devices = int(cv2.cuda.getCudaEnabledDeviceCount()) if hasattr(cv2, "cuda") else 0
    except Exception:
        opencv_cuda_devices = 0

    providers = available_onnx_providers()
    return {
        "onnxruntime_error": _ORT_ERROR,
        "available_providers": providers,
        "bad_providers": sorted(_BAD_PROVIDERS),
        "missing_cuda_dlls": missing,
        "cuda_ready": "CUDAExecutionProvider" in providers and not missing,
        "directml_ready": "DmlExecutionProvider" in providers,
        "opencv_cuda_devices": opencv_cuda_devices,
    }
