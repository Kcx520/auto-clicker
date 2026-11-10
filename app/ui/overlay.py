"""屏幕交互层：框选区域、拾取坐标点、屏幕标记、倒计时提示。"""

from __future__ import annotations

import tkinter as tk
from typing import Any, Callable, Dict, List, Optional, Tuple

from .. import screen as S
from .. import winapi as W
from . import theme
from .theme import COLORS, FONTS, dp

GWLP_EXSTYLE = -20
WS_EX_LAYERED = 0x00080000
WS_EX_TRANSPARENT = 0x00000020
WS_EX_TOOLWINDOW = 0x00000080
HWND_TOPMOST = -1
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040


def _hwnd(win: tk.Misc) -> int:
    """拿到真正的顶层窗口句柄。

    Tk 的 ``winfo_id()`` 有时给的是内部子窗口，直接 SetWindowPos 会"移动了但看不见"，
    所以统一往上找到根窗口。
    """
    try:
        handle = int(win.winfo_id())
    except Exception:
        return 0
    if not handle:
        return 0
    try:
        root = W.ancestor_window(handle)
        return int(root) if root else handle
    except Exception:
        return handle


def place(win: tk.Misc, x: int, y: int, w: int, h: int) -> None:
    """用 Win32 精确定位窗口，负坐标 / 多显示器都能正确处理。"""
    win.update_idletasks()
    handle = _hwnd(win)
    if not handle:
        return
    try:
        W.user32.SetWindowPos(handle, -1, int(x), int(y), int(w), int(h),
                              SWP_NOACTIVATE | SWP_SHOWWINDOW)
    except Exception:
        try:
            win.geometry(f"{int(w)}x{int(h)}+{max(0, int(x))}+{max(0, int(y))}")
        except Exception:
            pass


def make_click_through(win: tk.Misc) -> None:
    handle = _hwnd(win)
    if not handle:
        return
    try:
        import ctypes
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
        set_long = getattr(user32, "SetWindowLongPtrW", user32.SetWindowLongW)
        get_long.restype = ctypes.c_ssize_t
        get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
        set_long.restype = ctypes.c_ssize_t
        set_long.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        style = get_long(ctypes.c_void_p(handle), GWLP_EXSTYLE)
        set_long(ctypes.c_void_p(handle), GWLP_EXSTYLE,
                 style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW)
    except Exception:
        pass


class _BaseOverlay:
    """在所有显示器上铺一层透明窗口。"""

    def __init__(self, root: tk.Misc, alpha: float = 0.25, bg: str = "#000000",
                 cursor: str = "crosshair"):
        self.root = root
        self.on_done: Optional[Callable] = None
        self.windows: List[tk.Toplevel] = []
        self.canvases: List[tk.Canvas] = []
        self._monitors = S.monitors()
        for monitor in self._monitors:
            win = tk.Toplevel(root)
            win.overrideredirect(True)
            win.attributes("-topmost", True)
            try:
                win.attributes("-alpha", alpha)
            except Exception:
                pass
            win.configure(bg=bg, cursor=cursor)
            canvas = tk.Canvas(win, bg=bg, highlightthickness=0, bd=0, cursor=cursor)
            canvas.pack(fill="both", expand=True)
            place(win, monitor["x"], monitor["y"], monitor["w"], monitor["h"])
            self.windows.append(win)
            self.canvases.append(canvas)
            self._bind(canvas, monitor)
        self._closed = False

    def _bind(self, canvas: tk.Canvas, monitor: Dict[str, Any]) -> None:
        pass

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        for win in self.windows:
            try:
                win.destroy()
            except Exception:
                pass
        self.windows = []
        self.canvases = []


class RegionSelector(_BaseOverlay):
    """拖动鼠标框选一块屏幕区域，回调 (x, y, w, h) 或 None。"""

    def __init__(self, root: tk.Misc, on_done: Callable[[Optional[Tuple[int, int, int, int]]], None],
                 show_hint: bool = True):
        super().__init__(root, alpha=0.28, cursor="crosshair")
        self.on_done = on_done
        self._start: Optional[Tuple[int, int]] = None
        self._rect: Optional[int] = None
        self._label: Optional[int] = None
        for win in self.windows:
            win.bind("<Escape>", lambda _e: self._finish(None))
        if show_hint and self.canvases:
            self._draw_hint()

    def _draw_hint(self) -> None:
        canvas = self.canvases[0]
        try:
            canvas.create_text(dp(24), dp(24), anchor="nw", fill="#ffffff",
                               font=FONTS["heading"],
                               text="拖动鼠标框选要识别的范围　（按 ESC 取消）")
        except Exception:
            pass

    def _bind(self, canvas: tk.Canvas, monitor: Dict[str, Any]) -> None:
        canvas.bind("<ButtonPress-1>", lambda e, m=monitor: self._press(e, m))
        canvas.bind("<B1-Motion>", lambda e, m=monitor: self._drag(e, m))
        canvas.bind("<ButtonRelease-1>", lambda e, m=monitor: self._release(e, m))

    def _press(self, event, monitor) -> None:
        self._start = (event.x, event.y)
        if self._rect:
            self.canvases[self._index_of(monitor)].delete(self._rect)
        self._rect = self.canvases[self._index_of(monitor)].create_rectangle(
            event.x, event.y, event.x, event.y, outline=COLORS["danger"], width=dp(2))

    def _index_of(self, monitor) -> int:
        try:
            return self._monitors.index(monitor)
        except Exception:
            return 0

    def _drag(self, event, monitor) -> None:
        if not self._start or self._rect is None:
            return
        canvas = self.canvases[self._index_of(monitor)]
        canvas.coords(self._rect, self._start[0], self._start[1], event.x, event.y)
        x0, y0 = self._start
        w = abs(event.x - x0)
        h = abs(event.y - y0)
        if self._label:
            canvas.delete(self._label)
        self._label = canvas.create_text(min(x0, event.x), min(y0, event.y) - dp(14),
                                         anchor="sw", fill="#ffffff", font=FONTS["small"],
                                         text=f"{w} × {h}")

    def _release(self, event, monitor) -> None:
        if not self._start:
            return
        x0, y0 = self._start
        left, top = min(x0, event.x), min(y0, event.y)
        width, height = abs(event.x - x0), abs(event.y - y0)
        if width < 8 or height < 8:
            self._finish(None)
            return
        region = (monitor["x"] + left, monitor["y"] + top, width, height)
        self._finish(region)

    def _finish(self, region) -> None:
        callback = self.on_done
        self.close()
        if callback:
            callback(region)


class PointPicker(_BaseOverlay):
    """鼠标移动到目标处点一下，回调 (x, y) 或 None。"""

    def __init__(self, root: tk.Misc, on_done: Callable[[Optional[Tuple[int, int]]], None]):
        super().__init__(root, alpha=0.06, bg="#101418", cursor="crosshair")
        self.on_done = on_done
        for win in self.windows:
            win.bind("<Escape>", lambda _e: self._finish(None))
        for index, canvas in enumerate(self.canvases):
            canvas.bind("<Motion>", lambda e, c=canvas: self._motion(e, c))
            canvas.bind("<ButtonRelease-1>", lambda e, m=self._monitors[index]: self._click(e, m))
        self._hint()

    def _hint(self) -> None:
        if not self.canvases:
            return
        try:
            canvas = self.canvases[0]
            canvas.create_text(dp(24), dp(24), anchor="nw", fill="#ffffff", font=FONTS["heading"],
                               text="把鼠标移到目标位置后点一下　（按 ESC 取消）")
            self._readout = canvas.create_text(dp(24), dp(52), anchor="nw", fill=COLORS["warn"],
                                               font=FONTS["base"], text="")
        except Exception:
            pass

    def _motion(self, event, canvas) -> None:
        try:
            canvas.itemconfigure(getattr(self, "_readout", None),
                                 text=f"当前坐标  {event.x_root}, {event.y_root}")
        except Exception:
            pass

    def _click(self, event, monitor) -> None:
        self._finish((int(event.x_root), int(event.y_root)))

    def _finish(self, point) -> None:
        callback = self.on_done
        self.close()
        if callback:
            callback(point)


class MarkerOverlay:
    """常驻屏幕标记：目标位置十字准星 + 识别范围虚线框（鼠标可穿透）。"""

    def __init__(self, root: tk.Misc):
        self.root = root
        self.window: Optional[tk.Toplevel] = None
        self.canvas: Optional[tk.Canvas] = None
        self.visible = False
        self._job: Optional[str] = None
        self._source: Callable[[], Dict[str, Any]] = lambda: {}

    def set_source(self, source: Callable[[], Dict[str, Any]]) -> None:
        self._source = source

    def show(self) -> None:
        if self.visible:
            return
        self.visible = True
        x, y, w, h = W.virtual_screen_rect()
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        key = "#010203"
        try:
            win.attributes("-transparentcolor", key)
        except Exception:
            win.attributes("-alpha", 0.35)
        win.configure(bg=key)
        canvas = tk.Canvas(win, bg=key, highlightthickness=0, bd=0)
        canvas.pack(fill="both", expand=True)
        place(win, x, y, w, h)
        make_click_through(win)
        self.window, self.canvas = win, canvas
        self._redraw()

    def hide(self) -> None:
        self.visible = False
        if self._job:
            try:
                self.root.after_cancel(self._job)
            except Exception:
                pass
            self._job = None
        if self.window:
            try:
                self.window.destroy()
            except Exception:
                pass
        self.window = self.canvas = None

    def refresh(self) -> None:
        if self.visible:
            self.hide()
            self.show()

    def _redraw(self) -> None:
        if not self.visible or self.canvas is None:
            return
        canvas = self.canvas
        try:
            canvas.delete("all")
            data = self._source() or {}
            region = data.get("region")
            if region and len(region) == 4:
                x, y, w, h = (int(v) for v in region)
                canvas.create_rectangle(x - _vx(), y - _vy(), x - _vx() + w, y - _vy() + h,
                                        outline=COLORS["accent"], dash=(dp(6), dp(4)),
                                        width=dp(2))
                canvas.create_text(x - _vx() + dp(6), y - _vy() - dp(12), anchor="w",
                                   fill=COLORS["accent"], font=FONTS["small"],
                                   text="识别范围")
            for mark in data.get("marks", []):
                mx, my = int(mark.get("x", 0)), int(mark.get("y", 0))
                cx, cy = mx - _vx(), my - _vy()
                color = COLORS["ok"] if mark.get("active", True) else COLORS["muted"]
                size = dp(12)
                canvas.create_line(cx - size, cy, cx + size, cy, fill=color, width=dp(2))
                canvas.create_line(cx, cy - size, cx, cy + size, fill=color, width=dp(2))
                canvas.create_oval(cx - dp(4), cy - dp(4), cx + dp(4), cy + dp(4),
                                   outline=color, width=dp(1))
                canvas.create_text(cx + dp(14), cy - dp(12), anchor="w", fill=color,
                                   font=FONTS["small"],
                                   text=f"{mark.get('name', '')} ({mx}, {my})")
        except Exception:
            pass
        try:
            self._job = self.root.after(600, self._redraw)
        except Exception:
            self._job = None


def _vx() -> int:
    return W.virtual_screen_rect()[0]


def _vy() -> int:
    return W.virtual_screen_rect()[1]


def flash_box(root: tk.Misc, x: int, y: int, w: int, h: int, ms: int = 2500,
              text: str = "") -> None:
    """在屏幕上闪一个红框（用于展示"测试识别"的结果）。"""
    try:
        win = tk.Toplevel(root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        pad = dp(6)
        width = max(dp(40), int(w) + pad * 2)
        height = max(dp(30), int(h) + pad * 2)
        win.configure(bg=COLORS["danger"])
        canvas = tk.Canvas(win, bg=COLORS["danger"], highlightthickness=0, bd=0,
                           width=width, height=height)
        canvas.pack()
        if text:
            canvas.create_text(dp(4), dp(4), anchor="nw", fill="#ffffff", font=FONTS["small"],
                               text=text)
        place(win, int(x) - pad, int(y) - pad, width, height)
        make_click_through(win)
        win.after(max(300, int(ms)), win.destroy)
    except Exception:
        pass


def countdown_window(root: tk.Misc, seconds: int, text: str = "秒后开始",
                     on_done: Optional[Callable[[], None]] = None) -> tk.Toplevel:
    """屏幕中央的倒计时提示窗（开始前给你反应时间）。

    返回的窗口带 ``cancel()`` 方法：调用后不会再触发 ``on_done``（急停时用）。
    """
    win = tk.Toplevel(root)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    try:
        win.attributes("-alpha", 0.88)
    except Exception:
        pass
    win.configure(bg=COLORS["card"])
    label = tk.Label(win, text="", bg=COLORS["card"], fg=COLORS["fg"], font=FONTS["title"],
                     padx=dp(28), pady=dp(18))
    label.pack()
    width, height = dp(240), dp(90)
    vx, vy, vw, vh = W.virtual_screen_rect()
    place(win, vx + (vw - width) // 2, vy + int(vh * 0.18), width, height)
    remaining = [max(1, int(seconds))]
    state = {"cancelled": False}

    def cancel() -> None:
        state["cancelled"] = True
        try:
            win.destroy()
        except Exception:
            pass

    win.cancel = cancel  # type: ignore[attr-defined]

    def tick() -> None:
        if state["cancelled"]:
            return
        if remaining[0] <= 0:
            try:
                win.destroy()
            except Exception:
                pass
            if on_done:
                on_done()
            return
        label.configure(text=f"{remaining[0]} {text}")
        remaining[0] -= 1
        try:
            win.after(1000, tick)
        except Exception:
            pass

    tick()
    return win
