"""屏幕截图（GDI BitBlt，比 Pillow.ImageGrab 快很多，且支持多显示器负坐标）。"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Optional, Tuple

import numpy as np

from . import winapi as W
from .winapi import BITMAPINFO, BITMAPINFOHEADER, RECT, gdi32, user32

_lock = threading.RLock()

_SRCCOPY = W.SRCCOPY
_CAPTUREBLT = W.CAPTUREBLT
_DIB_RGB_COLORS = W.DIB_RGB_COLORS

# 让 64 位下句柄不被截断
gdi32.CreateCompatibleDC.restype = ctypes.c_void_p
gdi32.CreateCompatibleDC.argtypes = [ctypes.c_void_p]
gdi32.CreateCompatibleBitmap.restype = ctypes.c_void_p
gdi32.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = ctypes.c_void_p
gdi32.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
gdi32.DeleteObject.argtypes = [ctypes.c_void_p]
gdi32.DeleteDC.argtypes = [ctypes.c_void_p]
gdi32.BitBlt.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int, wintypes.DWORD]
gdi32.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT,
                            ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wintypes.UINT]
user32.GetDC.restype = ctypes.c_void_p
user32.GetDC.argtypes = [ctypes.c_void_p]
user32.ReleaseDC.argtypes = [ctypes.c_void_p, ctypes.c_void_p]


def virtual_rect() -> Tuple[int, int, int, int]:
    return W.virtual_screen_rect()


def monitors() -> list[dict]:
    """枚举显示器：返回 [{x,y,w,h,primary}]。"""
    result: list[dict] = []

    MonitorEnumProc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p,
                                         ctypes.POINTER(RECT), ctypes.c_double)

    def _cb(hmon, hdc, lprect, data):
        rect = lprect.contents
        result.append({
            "x": int(rect.left), "y": int(rect.top),
            "w": int(rect.right - rect.left), "h": int(rect.bottom - rect.top),
            "primary": int(rect.left) == 0 and int(rect.top) == 0,
        })
        return 1

    try:
        user32.EnumDisplayMonitors(None, None, MonitorEnumProc(_cb), 0)
    except Exception:
        pass
    if not result:
        x, y, w, h = virtual_rect()
        result.append({"x": x, "y": y, "w": w, "h": h, "primary": True})
    return result


def grab(bbox: Optional[Tuple[int, int, int, int]] = None) -> np.ndarray:
    """截屏，返回 RGB uint8 ndarray。

    bbox 为屏幕坐标 (x, y, w, h)；None 表示整个虚拟桌面（所有显示器）。
    """
    if bbox is None:
        x, y, w, h = virtual_rect()
    else:
        x, y, w, h = (int(v) for v in bbox)
    w = max(1, w)
    h = max(1, h)

    with _lock:
        hdc_screen = user32.GetDC(None)
        if not hdc_screen:
            return np.zeros((h, w, 3), dtype=np.uint8)
        hdc_mem = None
        hbmp = None
        old = None
        try:
            hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
            hbmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
            if not hdc_mem or not hbmp:
                return np.zeros((h, w, 3), dtype=np.uint8)
            old = gdi32.SelectObject(hdc_mem, hbmp)
            if not gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_screen, x, y,
                                _SRCCOPY | _CAPTUREBLT):
                return np.zeros((h, w, 3), dtype=np.uint8)

            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = w
            bmi.bmiHeader.biHeight = -h          # 负数 = 自上而下
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0      # BI_RGB
            buf = ctypes.create_string_buffer(w * h * 4)
            if not gdi32.GetDIBits(hdc_mem, hbmp, 0, h, buf, ctypes.byref(bmi),
                                   _DIB_RGB_COLORS):
                return np.zeros((h, w, 3), dtype=np.uint8)
            arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, w, 4)
            return np.ascontiguousarray(arr[:, :, 2::-1])   # BGRA -> RGB
        except Exception:
            return np.zeros((h, w, 3), dtype=np.uint8)
        finally:
            try:
                if hdc_mem and old:
                    gdi32.SelectObject(hdc_mem, old)
                if hbmp:
                    gdi32.DeleteObject(hbmp)
                if hdc_mem:
                    gdi32.DeleteDC(hdc_mem)
            finally:
                if hdc_screen:
                    user32.ReleaseDC(None, hdc_screen)


def grab_pil(bbox: Optional[Tuple[int, int, int, int]] = None):
    """截屏并返回 PIL.Image（RGB）。"""
    from PIL import Image
    return Image.fromarray(grab(bbox), "RGB")


def save_png(path: str, image: np.ndarray) -> bool:
    try:
        from PIL import Image
        Image.fromarray(image, "RGB").save(path, "PNG")
        return True
    except Exception:
        return False


def load_png(path: str) -> Optional[np.ndarray]:
    try:
        from PIL import Image
        with Image.open(path) as img:
            return np.asarray(img.convert("RGB"), dtype=np.uint8)
    except Exception:
        return None


def size() -> Tuple[int, int]:
    _, _, w, h = virtual_rect()
    return w, h
