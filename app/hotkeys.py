"""全局热键 / 急停 / 取点 / 录制事件源。

实现方式：``WH_KEYBOARD_LL`` + ``WH_MOUSE_LL`` 两个低级钩子（纯 ctypes）。
钩子回调必须极快返回，所以：
- 钩子线程只做「解析 + 入队 + 决定是否拦截」
- 另开一个派发线程执行真正的动作回调（回调里可以随便耗时）
"""

from __future__ import annotations

import ctypes
import queue
import threading
import time
from ctypes import wintypes
from typing import Any, Callable, Dict, List, Optional, Set

from . import winapi as W
from .config import Config

user32 = W.user32
kernel32 = W.kernel32

WH_KEYBOARD_LL = 13
WH_MOUSE_LL = 14

WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
WM_SYSKEYDOWN, WM_SYSKEYUP = 0x0104, 0x0105
WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_MOUSEWHEEL, WM_MOUSEHWHEEL = 0x020A, 0x020E
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C

LLKHF_INJECTED = 0x10
LLMHF_INJECTED = 0x01

WM_QUIT = 0x0012

LRESULT = ctypes.c_ssize_t


class KBDLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("vkCode", wintypes.DWORD),
        ("scanCode", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", W.ULONG_PTR),
    ]


class MSLLHOOKSTRUCT(ctypes.Structure):
    _fields_ = [
        ("pt", W.POINT),
        ("mouseData", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", W.ULONG_PTR),
    ]


HOOKPROC = ctypes.WINFUNCTYPE(LRESULT, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM)

user32.SetWindowsHookExW.restype = ctypes.c_void_p
user32.SetWindowsHookExW.argtypes = [ctypes.c_int, HOOKPROC, ctypes.c_void_p, wintypes.DWORD]
user32.CallNextHookEx.restype = LRESULT
user32.CallNextHookEx.argtypes = [ctypes.c_void_p, ctypes.c_int, wintypes.WPARAM, wintypes.LPARAM]
user32.UnhookWindowsHookEx.argtypes = [ctypes.c_void_p]
user32.GetMessageW.argtypes = [ctypes.POINTER(wintypes.MSG), ctypes.c_void_p, wintypes.UINT, wintypes.UINT]
user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
kernel32.GetCurrentThreadId.restype = wintypes.DWORD


class HotkeyManager:
    """热键管理。

    ``on_action(name, info)`` 的 name 取值：
      toggle / once / delayed_start / save_position / toggle_window /
      emergency_stop / quit / hold_start / hold_stop / position:<id> /
      captured（捕获热键时，info 里带回 captured）
    """

    def __init__(self, config: Config,
                 on_action: Callable[[str, Dict[str, Any]], None],
                 on_record: Optional[Callable[[Dict[str, Any]], None]] = None) -> None:
        self.config = config
        self.on_action = on_action
        self.on_record = on_record

        self._thread: Optional[threading.Thread] = None
        self._dispatch_thread: Optional[threading.Thread] = None
        self._tid = 0
        self._hook_kb: Optional[int] = None
        self._hook_ms: Optional[int] = None
        self._queue: "queue.Queue[tuple]" = queue.Queue()
        self._running = threading.Event()
        self._lock = threading.RLock()

        self.enabled = True
        self.capture_target: Optional[str] = None      # 非空 = 正在录制热键
        self.is_clicking: Callable[[], bool] = lambda: False
        self.self_move_age: Callable[[], float] = lambda: 999.0
        self.pressed: Set[int] = set()
        self._mods: Set[str] = set()

        self._matchers: List[Dict[str, Any]] = []
        self._hold_active = False
        self._last_corner_hit = 0.0
        self._proc_kb = HOOKPROC(self._kb_callback)   # 必须保持引用，否则被回收
        self._proc_ms = HOOKPROC(self._ms_callback)

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.reload()
        self._running.set()
        self._thread = threading.Thread(target=self._hook_loop, name="hotkey-hooks", daemon=True)
        self._thread.start()
        self._dispatch_thread = threading.Thread(target=self._dispatch_loop,
                                                 name="hotkey-dispatch", daemon=True)
        self._dispatch_thread.start()

    def stop(self) -> None:
        self._running.clear()
        if self._tid:
            try:
                user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            except Exception:
                pass
        self._hook_kb = None
        self._hook_ms = None

    def alive(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    # ------------------------------------------------------------------
    # 配置解析
    # ------------------------------------------------------------------
    def reload(self) -> None:
        matchers: List[Dict[str, Any]] = []
        hotkeys = self.config.get("hotkeys") or {}
        for name, text in hotkeys.items():
            parsed = W.parse_hotkey(text)
            if parsed:
                parsed = dict(parsed)
                parsed["name"] = name
                matchers.append(parsed)
        for item in self.config.positions():
            text = item.get("hotkey") or ""
            parsed = W.parse_hotkey(text)
            if parsed:
                parsed = dict(parsed)
                parsed["name"] = f"position:{item.get('id')}"
                matchers.append(parsed)
        with self._lock:
            self._matchers = matchers

    def set_enabled(self, value: bool) -> None:
        self.enabled = bool(value)

    def current_mods(self) -> Set[str]:
        return set(self._mods)

    # ------------------------------------------------------------------
    # 钩子线程
    # ------------------------------------------------------------------
    def _hook_loop(self) -> None:
        self._tid = int(kernel32.GetCurrentThreadId())
        try:
            self._hook_kb = user32.SetWindowsHookExW(WH_KEYBOARD_LL, self._proc_kb, None, 0)
            self._hook_ms = user32.SetWindowsHookExW(WH_MOUSE_LL, self._proc_ms, None, 0)
        except Exception:
            self._hook_kb = self._hook_ms = None

        msg = wintypes.MSG()
        while self._running.is_set():
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        for hook in (self._hook_kb, self._hook_ms):
            if hook:
                try:
                    user32.UnhookWindowsHookEx(hook)
                except Exception:
                    pass
        self._hook_kb = self._hook_ms = None

    # ------------------------------------------------------------------
    # 钩子回调（必须飞快）
    # ------------------------------------------------------------------
    def _kb_callback(self, code: int, wparam: int, lparam: int) -> int:
        try:
            if code >= 0 and self.enabled:
                data = ctypes.cast(lparam, ctypes.POINTER(KBDLLHOOKSTRUCT)).contents
                if not (data.flags & LLKHF_INJECTED):
                    swallow = self._handle_key(int(wparam), int(data.vkCode))
                    if swallow:
                        return 1
        except Exception:
            pass
        return user32.CallNextHookEx(None, code, wparam, lparam)

    def _ms_callback(self, code: int, wparam: int, lparam: int) -> int:
        try:
            if code >= 0 and self.enabled:
                data = ctypes.cast(lparam, ctypes.POINTER(MSLLHOOKSTRUCT)).contents
                if not (data.flags & LLMHF_INJECTED):
                    swallow = self._handle_mouse(int(wparam), data)
                    if swallow:
                        return 1
        except Exception:
            pass
        return user32.CallNextHookEx(None, code, wparam, lparam)

    # ---- 键盘事件 ----
    def _handle_key(self, msg: int, vk: int) -> bool:
        is_down = msg in (WM_KEYDOWN, WM_SYSKEYDOWN)
        is_up = msg in (WM_KEYUP, WM_SYSKEYUP)
        if not (is_down or is_up):
            return False

        repeat = vk in self.pressed
        mod = self._MOD_MAP.get(vk)
        if mod:
            if is_down:
                self._mods.add(mod)
            else:
                self._mods.discard(mod)
        if is_down:
            self.pressed.add(vk)
        else:
            self.pressed.discard(vk)

        if self.on_record is not None:
            try:
                self.on_record({"type": "key_down" if is_down else "key_up", "vk": vk,
                                "t": time.perf_counter()})
            except Exception:
                pass

        # 捕获模式：把这次按键当作用户想设置的热键
        if self.capture_target is not None:
            if is_down and not repeat:
                if vk in (W.VK_CONTROL, W.VK_SHIFT, W.VK_MENU, W.VK_LWIN, W.VK_RWIN):
                    return True  # 先按住修饰键，等真正的主键
                self._queue.put(("__capture__", {"mods": set(self._mods), "vk": vk, "mouse": None}))
                return True

        if not is_down or repeat:
            return False

        matched = self._match(vk=vk)
        if matched:
            x, y = W.get_cursor_pos()
            self._queue.put((matched, {"x": x, "y": y, "vk": vk, "mods": set(self._mods)}))
            return True
        return False

    # ---- 鼠标事件 ----
    def _handle_mouse(self, msg: int, data: MSLLHOOKSTRUCT) -> bool:
        x, y = int(data.pt.x), int(data.pt.y)
        now = time.perf_counter()

        if self.on_record is not None and msg in (WM_MOUSEMOVE, WM_LBUTTONDOWN, WM_LBUTTONUP,
                                                  WM_RBUTTONDOWN, WM_RBUTTONUP, WM_MBUTTONDOWN,
                                                  WM_MBUTTONUP, WM_XBUTTONDOWN, WM_XBUTTONUP,
                                                  WM_MOUSEWHEEL):
            try:
                kind = {WM_MOUSEMOVE: "mouse_move", WM_LBUTTONDOWN: "mouse_down",
                        WM_LBUTTONUP: "mouse_up", WM_RBUTTONDOWN: "mouse_down",
                        WM_RBUTTONUP: "mouse_up", WM_MBUTTONDOWN: "mouse_down",
                        WM_MBUTTONUP: "mouse_up", WM_XBUTTONDOWN: "mouse_down",
                        WM_XBUTTONUP: "mouse_up", WM_MOUSEWHEEL: "wheel"}[msg]
                button = {WM_LBUTTONDOWN: "left", WM_LBUTTONUP: "left",
                          WM_RBUTTONDOWN: "right", WM_RBUTTONUP: "right",
                          WM_MBUTTONDOWN: "middle", WM_MBUTTONUP: "middle"}.get(msg)
                if msg in (WM_XBUTTONDOWN, WM_XBUTTONUP):
                    button = "x1" if (data.mouseData >> 16) == 1 else "x2"
                delta = 0
                if msg == WM_MOUSEWHEEL:
                    delta = ctypes.c_short((data.mouseData >> 16) & 0xFFFF).value
                self.on_record({"type": kind, "x": x, "y": y, "button": button,
                                "delta": delta, "t": now})
            except Exception:
                pass

        # 捕获模式：鼠标按键当热键
        if self.capture_target is not None and msg in (WM_XBUTTONDOWN, WM_MBUTTONDOWN,
                                                       WM_LBUTTONDOWN, WM_RBUTTONDOWN):
            mouse = {WM_LBUTTONDOWN: "left", WM_RBUTTONDOWN: "right",
                     WM_MBUTTONDOWN: "middle"}.get(msg)
            if msg == WM_XBUTTONDOWN:
                mouse = "x1" if (data.mouseData >> 16) == 1 else "x2"
            self._queue.put(("__capture__", {"mods": set(self._mods), "vk": None, "mouse": mouse}))
            return True

        # 鼠标甩角急停
        if msg == WM_MOUSEMOVE and self._corner_enabled():
            if self._check_corner(x, y, now):
                self._queue.put(("emergency_stop", {"x": x, "y": y, "reason": "鼠标甩到屏幕角落"}))
                return False

        # 鼠标侧键热键
        if msg == WM_XBUTTONDOWN:
            mouse = "x1" if (data.mouseData >> 16) == 1 else "x2"
            matched = self._match(mouse=mouse)
            if matched:
                self._queue.put((matched, {"x": x, "y": y, "mouse": mouse}))
                return True
        if msg == WM_XBUTTONUP:
            mouse = "x1" if (data.mouseData >> 16) == 1 else "x2"
            active = self._match(mouse=mouse)
            if active == "hold_to_click" and self._hold_active:
                self._hold_active = False
                self._queue.put(("hold_stop", {"x": x, "y": y}))
                return True
        return False

    # ---- 匹配 ----
    def _match(self, vk: Optional[int] = None, mouse: Optional[str] = None) -> Optional[str]:
        mods = set(self._mods)
        with self._lock:
            matchers = list(self._matchers)
        for item in matchers:
            if item.get("mods") != mods:
                continue
            if vk is not None and item.get("vk") == vk and not item.get("mouse"):
                name = item["name"]
                if name == "hold_to_click":
                    self._hold_active = True
                    return "hold_start"
                return name
            if mouse is not None and item.get("mouse") == mouse:
                name = item["name"]
                if name == "hold_to_click":
                    if not self._hold_active:
                        self._hold_active = True
                        return "hold_start"
                    return name
                return name
        return None

    def _corner_enabled(self) -> bool:
        if not self.config.get("safety.corner_stop_enabled", True):
            return False
        if not self.config.get("safety.emergency_enabled", True):
            return False
        try:
            if not self.is_clicking():
                return False
        except Exception:
            return False
        # 自己程序移动鼠标造成的位移不算
        try:
            if self.self_move_age() < 0.4:
                return False
        except Exception:
            pass
        return True

    def _check_corner(self, x: int, y: int, now: float) -> bool:
        if now - self._last_corner_hit < 1.0:
            return False
        margin = int(self.config.get("safety.corner_margin", 5) or 5)
        corner = self.config.get("safety.corner", "topleft")
        vx, vy, vw, vh = W.virtual_screen_rect()
        points = {
            "topleft": (vx, vy),
            "topright": (vx + vw - 1, vy),
            "bottomleft": (vx, vy + vh - 1),
            "bottomright": (vx + vw - 1, vy + vh - 1),
        }
        cx, cy = points.get(corner, points["topleft"])
        if abs(x - cx) <= margin and abs(y - cy) <= margin:
            self._last_corner_hit = now
            return True
        return False

    # ------------------------------------------------------------------
    # 派发线程
    # ------------------------------------------------------------------
    def _dispatch_loop(self) -> None:
        while self._running.is_set():
            try:
                name, info = self._queue.get(timeout=0.2)
            except queue.Empty:
                continue
            if name == "__capture__":
                self.capture_target = None
                try:
                    self.on_action("captured", info)
                except Exception:
                    pass
                continue
            try:
                self.on_action(name, info)
            except Exception:
                pass

    # ------------------------------------------------------------------
    # 修饰键跟踪（从钩子流里维护）
    # ------------------------------------------------------------------
    _MOD_MAP = {
        W.VK_CONTROL: "ctrl", 0xA2: "ctrl", 0xA3: "ctrl",
        W.VK_MENU: "alt", 0xA4: "alt", 0xA5: "alt",
        W.VK_SHIFT: "shift", 0xA0: "shift", 0xA1: "shift",
        W.VK_LWIN: "win", W.VK_RWIN: "win",
    }
