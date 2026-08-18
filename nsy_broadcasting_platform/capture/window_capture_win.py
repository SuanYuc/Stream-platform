from __future__ import annotations

import ctypes
from ctypes import wintypes

from nsy_broadcasting_platform.compat import CV2, MSS, NP, PSUTIL, PYWIN32_CON, PYWIN32_GUI, PYWIN32_PROC, PYWIN32_UI

cv2 = CV2.module
mss = MSS.module
np = NP.module
psutil = PSUTIL.module
win32con = PYWIN32_CON.module
win32gui = PYWIN32_GUI.module
win32process = PYWIN32_PROC.module
win32ui = PYWIN32_UI.module

DWMWA_EXTENDED_FRAME_BOUNDS = 9


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


def _rect_tuple(rect: RECT) -> tuple[int, int, int, int]:
    return int(rect.left), int(rect.top), int(rect.right), int(rect.bottom)


def get_extended_frame_bounds(hwnd: int) -> tuple[int, int, int, int] | None:
    if win32gui is None:
        return None
    try:
        rect = RECT()
        dwmapi = ctypes.windll.dwmapi
        hr = dwmapi.DwmGetWindowAttribute(
            wintypes.HWND(hwnd),
            ctypes.c_uint(DWMWA_EXTENDED_FRAME_BOUNDS),
            ctypes.byref(rect),
            ctypes.sizeof(rect),
        )
        if hr == 0:
            l, t, r, b = _rect_tuple(rect)
            if r > l and b > t:
                return l, t, r, b
    except Exception:
        pass
    try:
        l, t, r, b = win32gui.GetWindowRect(hwnd)
        if r > l and b > t:
            return int(l), int(t), int(r), int(b)
    except Exception:
        return None
    return None


def _dc_to_bgr(hwnd: int, width: int, height: int, draw_func) -> tuple[bool, object | None]:
    if win32gui is None or win32ui is None or np is None:
        return False, None
    hwnd_dc = None
    mfc_dc = None
    save_dc = None
    bitmap = None
    try:
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
        save_dc.SelectObject(bitmap)
        ok = bool(draw_func(mfc_dc, save_dc))
        bmp_bytes = bitmap.GetBitmapBits(True)
        img = np.frombuffer(bmp_bytes, dtype=np.uint8).reshape((height, width, 4))
        if cv2 is not None:
            frame = cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
        else:
            frame = img[:, :, :3]
        return ok, frame
    except Exception:
        return False, None
    finally:
        try:
            if bitmap is not None:
                win32gui.DeleteObject(bitmap.GetHandle())
        except Exception:
            pass
        try:
            if save_dc is not None:
                save_dc.DeleteDC()
        except Exception:
            pass
        try:
            if mfc_dc is not None:
                mfc_dc.DeleteDC()
        except Exception:
            pass
        try:
            if hwnd_dc is not None:
                win32gui.ReleaseDC(hwnd, hwnd_dc)
        except Exception:
            pass


def _is_mostly_black(frame) -> bool:
    if frame is None or np is None:
        return True
    return float(frame.mean()) < 2.0


def capture_window(hwnd: int):
    rect = get_extended_frame_bounds(hwnd)
    if rect is None:
        return None, "无法获取窗口区域"
    left, top, right, bottom = rect
    width = max(1, right - left)
    height = max(1, bottom - top)

    # 优先 PrintWindow + PW_RENDERFULLCONTENT，兼顾 Win11 渲染路径。
    if win32gui is not None:
        ok, frame = _dc_to_bgr(
            hwnd,
            width,
            height,
            lambda _src, dst: ctypes.windll.user32.PrintWindow(hwnd, dst.GetSafeHdc(), 2),
        )
        if ok and not _is_mostly_black(frame):
            return frame, "PrintWindow(2)"
        ok, frame = _dc_to_bgr(
            hwnd,
            width,
            height,
            lambda _src, dst: ctypes.windll.user32.PrintWindow(hwnd, dst.GetSafeHdc(), 0),
        )
        if ok and not _is_mostly_black(frame):
            return frame, "PrintWindow(0)"

    # 降级到 BitBlt。
    if win32con is not None:
        ok, frame = _dc_to_bgr(
            hwnd,
            width,
            height,
            lambda src, dst: dst.BitBlt((0, 0), (width, height), src, (0, 0), win32con.SRCCOPY),
        )
        if ok and frame is not None:
            return frame, "BitBlt"

    # 最后使用 mss 按矩形抓屏。
    if mss is not None and np is not None:
        try:
            with mss.mss() as sct:
                shot = sct.grab({"left": left, "top": top, "width": width, "height": height})
            arr = np.array(shot)
            if cv2 is not None:
                frame = cv2.cvtColor(arr, cv2.COLOR_BGRA2BGR)
            else:
                frame = arr[:, :, :3]
            return frame, "MSSRect"
        except Exception:
            return None, "MSSRect 失败"
    return None, "所有策略失败"


def enum_windows() -> list[dict]:
    windows: list[dict] = []
    if win32gui is None:
        return windows

    def _callback(hwnd, _arg):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            title = win32gui.GetWindowText(hwnd).strip()
            if not title:
                return True
            l, t, r, b = get_extended_frame_bounds(hwnd) or (0, 0, 0, 0)
            if (r - l) < 50 or (b - t) < 50:
                return True
            pid = None
            process_name = None
            if win32process is not None:
                _tid, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid and psutil is not None:
                    try:
                        process_name = psutil.Process(pid).name()
                    except Exception:
                        process_name = None
            windows.append(
                {
                    "hwnd": int(hwnd),
                    "title": title,
                    "pid": int(pid) if pid else None,
                    "process_name": process_name,
                }
            )
        except Exception:
            return True
        return True

    try:
        win32gui.EnumWindows(_callback, None)
    except Exception:
        return []
    windows.sort(key=lambda item: item["title"].lower())
    return windows
