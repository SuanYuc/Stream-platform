from __future__ import annotations

import importlib
import platform
from dataclasses import dataclass


def _safe_import(name: str):
    try:
        return importlib.import_module(name), None
    except Exception as exc:  # pragma: no cover
        return None, str(exc)


@dataclass(slots=True)
class ImportStatus:
    module: object | None
    error: str | None

    @property
    def ok(self) -> bool:
        return self.module is not None


def _import_status(name: str) -> ImportStatus:
    return ImportStatus(*_safe_import(name))


CV2 = _import_status("cv2")
NP = _import_status("numpy")
AV = _import_status("av")
MSS = _import_status("mss")
PSUTIL = _import_status("psutil")
MEDIAPIPE = _import_status("mediapipe")

PYAUDIOWPATCH = _import_status("pyaudiowpatch")
PYAUDIO = _import_status("pyaudio")

PYCAW = _import_status("pycaw.pycaw")
PYWIN32_GUI = _import_status("win32gui")
PYWIN32_UI = _import_status("win32ui")
PYWIN32_CON = _import_status("win32con")
PYWIN32_PROC = _import_status("win32process")

IS_WINDOWS = platform.system().lower() == "windows"
