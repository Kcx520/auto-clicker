"""Windows 原生 API 封装（纯 ctypes，无第三方依赖）。

包含四块能力：
1. 鼠标 / 键盘注入（SendInput，硬件级，兼容绝大多数程序）
2. 后台点击（PostMessage，不抢占鼠标）
3. 窗口信息查询（前台窗口、标题、矩形、进程名、窗口跟随）
4. 热键字符串解析（"Ctrl+Alt+Q" / "MouseX2" -> 虚拟键码）
"""

from __future__ import annotations

import ctypes
import os
import time
from ctypes import wintypes
from typing import Any, Dict, Optional, Tuple

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
try:
    shcore = ctypes.WinDLL("shcore", use_last_error=True)
except OSError:  # pragma: no cover
    shcore = None

IS_64 = ctypes.sizeof(ctypes.c_void_p) == 8
ULONG_PTR = ctypes.c_ulonglong if IS_64 else ctypes.c_ulong
LRESULT = ctypes.c_ssize_t

# --------------------------------------------------------------------------
# 结构体
# --------------------------------------------------------------------------


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ULONG_PTR),
    ]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class POINT(ctypes.Structure):
    _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", wintypes.LONG),
        ("biHeight", wintypes.LONG),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", wintypes.LONG),
        ("biYPelsPerMeter", wintypes.LONG),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", wintypes.DWORD * 3)]


# --------------------------------------------------------------------------
# 常量
# --------------------------------------------------------------------------
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1

MOUSEEVENTF_MOVE = 0x0001
MOUSEEVENTF_LEFTDOWN = 0x0002
MOUSEEVENTF_LEFTUP = 0x0004
MOUSEEVENTF_RIGHTDOWN = 0x0008
MOUSEEVENTF_RIGHTUP = 0x0010
MOUSEEVENTF_MIDDLEDOWN = 0x0020
MOUSEEVENTF_MIDDLEUP = 0x0040
MOUSEEVENTF_XDOWN = 0x0080
MOUSEEVENTF_XUP = 0x0100
MOUSEEVENTF_WHEEL = 0x0800
MOUSEEVENTF_HWHEEL = 0x1000
MOUSEEVENTF_ABSOLUTE = 0x8000
MOUSEEVENTF_VIRTUALDESK = 0x4000

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_SCANCODE = 0x0008

XBUTTON1 = 0x0001
XBUTTON2 = 0x0002

WHEEL_DELTA = 120

SM_XVIRTUALSCREEN = 76
SM_YVIRTUALSCREEN = 77
SM_CXVIRTUALSCREEN = 78
SM_CYVIRTUALSCREEN = 79

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
DIB_RGB_COLORS = 0

WM_MOUSEMOVE = 0x0200
WM_LBUTTONDOWN, WM_LBUTTONUP = 0x0201, 0x0202
WM_RBUTTONDOWN, WM_RBUTTONUP = 0x0204, 0x0205
WM_MBUTTONDOWN, WM_MBUTTONUP = 0x0207, 0x0208
WM_XBUTTONDOWN, WM_XBUTTONUP = 0x020B, 0x020C
WM_MOUSEWHEEL = 0x020A

MK_LBUTTON = 0x0001
MK_RBUTTON = 0x0002
MK_MBUTTON = 0x0010
MK_XBUTTON1 = 0x0020
MK_XBUTTON2 = 0x0040

VK_SHIFT, VK_CONTROL, VK_MENU = 0x10, 0x11, 0x12
VK_LWIN, VK_RWIN = 0x5B, 0x5C

# 按键 -> SendInput/PostMessage 参数
BUTTON_INFO: Dict[str, Dict[str, int]] = {
    "left": dict(down=MOUSEEVENTF_LEFTDOWN, up=MOUSEEVENTF_LEFTUP,
                 msg_down=WM_LBUTTONDOWN, msg_up=WM_LBUTTONUP,
                 wparam_down=MK_LBUTTON, wparam_up=0, xdata=0),
    "right": dict(down=MOUSEEVENTF_RIGHTDOWN, up=MOUSEEVENTF_RIGHTUP,
                  msg_down=WM_RBUTTONDOWN, msg_up=WM_RBUTTONUP,
                  wparam_down=MK_RBUTTON, wparam_up=0, xdata=0),
    "middle": dict(down=MOUSEEVENTF_MIDDLEDOWN, up=MOUSEEVENTF_MIDDLEUP,
                   msg_down=WM_MBUTTONDOWN, msg_up=WM_MBUTTONUP,
                   wparam_down=MK_MBUTTON, wparam_up=0, xdata=0),
    "x1": dict(down=MOUSEEVENTF_XDOWN, up=MOUSEEVENTF_XUP,
               msg_down=WM_XBUTTONDOWN, msg_up=WM_XBUTTONUP,
               wparam_down=MK_XBUTTON1, wparam_up=0, xdata=XBUTTON1),
    "x2": dict(down=MOUSEEVENTF_XDOWN, up=MOUSEEVENTF_XUP,
               msg_down=WM_XBUTTONDOWN, msg_up=WM_XBUTTONUP,
               wparam_down=MK_XBUTTON2, wparam_up=0, xdata=XBUTTON2),
}

BUTTON_LABELS = {
    "left": "鼠标左键",
    "right": "鼠标右键",
    "middle": "鼠标中键",
    "x1": "侧键 X1",
    "x2": "侧键 X2",
    "wheel_up": "滚轮上滚",
    "wheel_down": "滚轮下滚",
}

# 名字 -> 虚拟键码
KEY_NAME_TO_VK: Dict[str, int] = {}
for _i in range(26):
    KEY_NAME_TO_VK[chr(ord("A") + _i)] = 0x41 + _i
for _i in range(10):
    KEY_NAME_TO_VK[str(_i)] = 0x30 + _i
for _i in range(1, 25):
    KEY_NAME_TO_VK[f"F{_i}"] = 0x6F + _i
KEY_NAME_TO_VK.update({
    "ESC": 0x1B, "ESCAPE": 0x1B, "TAB": 0x09, "SPACE": 0x20, "空格": 0x20,
    "ENTER": 0x0D, "RETURN": 0x0D, "BACKSPACE": 0x08, "DELETE": 0x2E, "DEL": 0x2E,
    "INSERT": 0x2D, "INS": 0x2D, "HOME": 0x24, "END": 0x23,
    "PAGEUP": 0x21, "PAGEDOWN": 0x22, "PGUP": 0x21, "PGDN": 0x22,
    "UP": 0x26, "DOWN": 0x28, "LEFT": 0x25, "RIGHT": 0x27,
    "CAPSLOCK": 0x14, "NUMLOCK": 0x90, "SCROLLLOCK": 0x91,
    "SHIFT": VK_SHIFT, "CTRL": VK_CONTROL, "CONTROL": VK_CONTROL, "ALT": VK_MENU,
    "WIN": VK_LWIN, "LWIN": VK_LWIN, "RWIN": VK_RWIN, "APPS": 0x5D,
    "PRINTSCREEN": 0x2C, "PAUSE": 0x13,
    "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, "\\": 0xDC, ";": 0xBA,
    "'": 0xDE, ",": 0xBC, ".": 0xBE, "/": 0xBF, "`": 0xC0,
    "NUM0": 0x60, "NUM1": 0x61, "NUM2": 0x62, "NUM3": 0x63, "NUM4": 0x64,
    "NUM5": 0x65, "NUM6": 0x66, "NUM7": 0x67, "NUM8": 0x68, "NUM9": 0x69,
    "NUM*": 0x6A, "NUM+": 0x6B, "NUM-": 0x6D, "NUM.": 0x6E, "NUM/": 0x6F,
})

MODIFIER_ALIASES = {
    "CTRL": "ctrl", "CONTROL": "ctrl", "控制": "ctrl",
    "ALT": "alt", "MENU": "alt",
    "SHIFT": "shift",
    "WIN": "win", "SUPER": "win", "META": "win",
}

MOUSE_KEY_NAMES = {
    "MOUSELEFT": "left", "MOUSERIGHT": "right", "MOUSEMIDDLE": "middle",
    "MOUSEX1": "x1", "MOUSEX2": "x2", "X1": "x1", "X2": "x2",
    "MOUSEWHEELUP": "wheel_up", "MOUSEWHEELDOWN": "wheel_down",
}

_MODIFIER_VK = {"ctrl": VK_CONTROL, "alt": VK_MENU, "shift": VK_SHIFT, "win": VK_LWIN}


# --------------------------------------------------------------------------
# 基础设置
# --------------------------------------------------------------------------
def set_dpi_awareness() -> str:
    """让坐标使用物理像素，多显示器 / 4K 屏才不会点偏。"""
    try:
        ctx = ctypes.c_void_p(-4)  # PER_MONITOR_AWARE_V2
        if user32.SetProcessDpiAwarenessContext(ctx):
            return "per-monitor-v2"
    except Exception:
        pass
    try:
        if shcore is not None and shcore.SetProcessDpiAwareness(2) == 0:
            return "per-monitor"
    except Exception:
        pass
    try:
        user32.SetProcessDPIAware()
        return "system"
    except Exception:
        return "none"


def set_dpi_awareness_for_hwnd(hwnd: int = 0) -> None:
    try:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        pass


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def relaunch_as_admin() -> bool:
    """以管理员身份重启自身（用于点击管理员权限的程序）。"""
    try:
        import sys
        if getattr(sys, "frozen", False):
            exe, params = sys.executable, ""
        else:
            exe = sys.executable
            params = " ".join(f'"{a}"' for a in sys.argv)
        return bool(ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1) > 32)
    except Exception:
        return False


# --------------------------------------------------------------------------
# 光标
# --------------------------------------------------------------------------
def get_cursor_pos() -> Tuple[int, int]:
    pt = POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def set_cursor_pos(x: int, y: int) -> None:
    user32.SetCursorPos(int(x), int(y))


def virtual_screen_rect() -> Tuple[int, int, int, int]:
    """返回 (x, y, w, h)——所有显示器组成的虚拟桌面。"""
    try:
        x = user32.GetSystemMetrics(SM_XVIRTUALSCREEN)
        y = user32.GetSystemMetrics(SM_YVIRTUALSCREEN)
        w = user32.GetSystemMetrics(SM_CXVIRTUALSCREEN)
        h = user32.GetSystemMetrics(SM_CYVIRTUALSCREEN)
        if w > 0 and h > 0:
            return int(x), int(y), int(w), int(h)
    except Exception:
        pass
    return 0, 0, user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)


# --------------------------------------------------------------------------
# 鼠标注入
# --------------------------------------------------------------------------
def _send_inputs(inputs) -> int:
    count = len(inputs)
    array = (INPUT * count)(*inputs)
    return int(user32.SendInput(count, ctypes.byref(array), ctypes.sizeof(INPUT)))


def _mouse_input(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> INPUT:
    item = INPUT()
    item.type = INPUT_MOUSE
    item.u.mi = MOUSEINPUT(dx, dy, data, flags, 0, 0)
    return item


def _key_input(vk: int, flags: int = 0, scan: int = 0) -> INPUT:
    item = INPUT()
    item.type = INPUT_KEYBOARD
    item.u.ki = KEYBDINPUT(vk & 0xFFFF, scan & 0xFFFF, flags, 0, 0)
    return item


def mouse_down(button: str = "left") -> None:
    info = BUTTON_INFO.get(button)
    if info is None:
        return
    _send_inputs([_mouse_input(info["down"], data=info["xdata"])])


def mouse_up(button: str = "left") -> None:
    info = BUTTON_INFO.get(button)
    if info is None:
        return
    _send_inputs([_mouse_input(info["up"], data=info["xdata"])])


def wheel(notches: int = 1, horizontal: bool = False) -> None:
    flag = MOUSEEVENTF_HWHEEL if horizontal else MOUSEEVENTF_WHEEL
    _send_inputs([_mouse_input(flag, data=int(notches) * WHEEL_DELTA)])


def click(button: str = "left", hold_ms: int = 20, x: Optional[int] = None,
          y: Optional[int] = None, move_first: bool = True) -> None:
    """在 (x, y) 位置点击一次；hold_ms 为按下与松开之间的间隔。"""
    if x is not None and y is not None and move_first:
        set_cursor_pos(x, y)
        time.sleep(0.001)
    if button == "wheel_up":
        wheel(1)
        return
    if button == "wheel_down":
        wheel(-1)
        return
    if button not in BUTTON_INFO:
        return
    mouse_down(button)
    if hold_ms > 0:
        time.sleep(hold_ms / 1000.0)
    mouse_up(button)


def double_click(button: str = "left", x: Optional[int] = None,
                 y: Optional[int] = None, gap_ms: int = 40, hold_ms: int = 20) -> None:
    click(button, hold_ms, x, y)
    if gap_ms > 0:
        time.sleep(gap_ms / 1000.0)
    click(button, hold_ms, None, None, move_first=False)


def drag(x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300,
         button: str = "left", steps: int = 20) -> None:
    if button not in BUTTON_INFO:
        button = "left"
    set_cursor_pos(x1, y1)
    time.sleep(0.02)
    mouse_down(button)
    steps = max(2, int(steps))
    for i in range(1, steps + 1):
        t = i / steps
        set_cursor_pos(int(x1 + (x2 - x1) * t), int(y1 + (y2 - y1) * t))
        time.sleep(max(0.0, duration_ms / 1000.0 / steps))
    time.sleep(0.02)
    mouse_up(button)


# --------------------------------------------------------------------------
# 键盘注入
# --------------------------------------------------------------------------
def key_down(vk: int) -> None:
    _send_inputs([_key_input(vk)])


def key_up(vk: int) -> None:
    _send_inputs([_key_input(vk, KEYEVENTF_KEYUP)])


def press_key(vk: int, hold_ms: int = 20) -> None:
    key_down(vk)
    if hold_ms > 0:
        time.sleep(hold_ms / 1000.0)
    key_up(vk)


def press_key_name(name: str, hold_ms: int = 20) -> bool:
    vk = KEY_NAME_TO_VK.get(str(name).upper())
    if vk is None:
        return False
    press_key(vk, hold_ms)
    return True


def send_combo(combo: str, hold_ms: int = 20) -> bool:
    """发送组合键，例如 "ctrl+shift+s"。"""
    parsed = parse_hotkey(combo)
    if parsed is None or parsed.get("vk") is None:
        return False
    for mod in ("ctrl", "alt", "shift", "win"):
        if mod in parsed["mods"]:
            key_down(_MODIFIER_VK[mod])
    try:
        press_key(parsed["vk"], hold_ms)
    finally:
        for mod in ("win", "shift", "alt", "ctrl"):
            if mod in parsed["mods"]:
                key_up(_MODIFIER_VK[mod])
    return True


def type_text(text: str) -> bool:
    """输入文本（走剪贴板 + Ctrl+V，中文/emoji 也能打）。"""
    if not text:
        return False
    if not set_clipboard_text(text):
        return False
    time.sleep(0.05)
    key_down(VK_CONTROL)
    press_key(0x56)  # V
    key_up(VK_CONTROL)
    return True


def set_clipboard_text(text: str) -> bool:
    CF_UNICODETEXT = 13
    GMEM_MOVEABLE = 0x0002
    kernel32.GlobalAlloc.restype = ctypes.c_void_p
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
    kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
    user32.SetClipboardData.restype = ctypes.c_void_p
    user32.SetClipboardData.argtypes = [wintypes.UINT, ctypes.c_void_p]

    for _ in range(12):
        if user32.OpenClipboard(None):
            break
        time.sleep(0.02)
    else:
        return False
    try:
        user32.EmptyClipboard()
        data = text.encode("utf-16-le") + b"\x00\x00"
        handle = kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not handle:
            return False
        ptr = kernel32.GlobalLock(handle)
        if not ptr:
            return False
        ctypes.memmove(ptr, data, len(data))
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(CF_UNICODETEXT, handle):
            return False
        return True
    except Exception:
        return False
    finally:
        user32.CloseClipboard()


def get_async_key_state(vk: int) -> bool:
    return bool(user32.GetAsyncKeyState(vk) & 0x8000)


# --------------------------------------------------------------------------
# 热键字符串解析
# --------------------------------------------------------------------------
def parse_hotkey(text: str) -> Optional[Dict[str, Any]]:
    """把 "Ctrl+Alt+Q" / "F6" / "MouseX2" 解析成字典。

    返回 {"mods": set, "vk": int|None, "mouse": str|None, "display": str}
    """
    if not text:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    parts = [p.strip() for p in raw.replace("＋", "+").split("+") if p.strip()]
    mods = set()
    key_token: Optional[str] = None
    for part in parts:
        upper = part.upper()
        if upper in MODIFIER_ALIASES:
            mods.add(MODIFIER_ALIASES[upper])
        else:
            key_token = upper
    mouse: Optional[str] = None
    vk: Optional[int] = None
    if key_token:
        compact = key_token.replace(" ", "")
        if compact in MOUSE_KEY_NAMES:
            mouse = MOUSE_KEY_NAMES[compact]
        elif compact in KEY_NAME_TO_VK:
            vk = KEY_NAME_TO_VK[compact]
        else:
            return None
    elif mods:
        return None
    display_parts = []
    for mod in ("ctrl", "alt", "shift", "win"):
        if mod in mods:
            display_parts.append(mod.capitalize())
    if key_token:
        display_parts.append(key_token if not mouse else key_token.title())
    return {
        "mods": mods,
        "vk": vk,
        "mouse": mouse,
        "display": "+".join(display_parts) or raw,
    }


def format_hotkey(mods, vk: Optional[int] = None, mouse: Optional[str] = None) -> str:
    parts = []
    for mod in ("ctrl", "alt", "shift", "win"):
        if mod in (mods or ()):
            parts.append(mod.capitalize())
    if mouse:
        parts.append({
            "left": "MouseLeft", "right": "MouseRight", "middle": "MouseMiddle",
            "x1": "MouseX1", "x2": "MouseX2",
            "wheel_up": "MouseWheelUp", "wheel_down": "MouseWheelDown",
        }.get(mouse, mouse))
    elif vk is not None:
        name = vk_to_name(vk)
        parts.append(name)
    return "+".join(parts)


def vk_to_name(vk: int) -> str:
    for name, code in KEY_NAME_TO_VK.items():
        if code == vk and len(name) <= 5:
            return name
    for name, code in KEY_NAME_TO_VK.items():
        if code == vk:
            return name
    return f"VK{vk:02X}"


# --------------------------------------------------------------------------
# 窗口
# --------------------------------------------------------------------------
def foreground_window() -> int:
    return int(user32.GetForegroundWindow() or 0)


def is_window(hwnd: int) -> bool:
    try:
        return bool(user32.IsWindow(int(hwnd)))
    except Exception:
        return False


def window_title(hwnd: int) -> str:
    if not hwnd:
        return ""
    try:
        length = user32.GetWindowTextLengthW(int(hwnd))
        buf = ctypes.create_unicode_buffer(length + 2)
        user32.GetWindowTextW(int(hwnd), buf, length + 2)
        return buf.value
    except Exception:
        return ""


def window_class(hwnd: int) -> str:
    try:
        buf = ctypes.create_unicode_buffer(512)
        user32.GetClassNameW(int(hwnd), buf, 512)
        return buf.value
    except Exception:
        return ""


def window_rect(hwnd: int) -> Tuple[int, int, int, int]:
    rect = RECT()
    if not user32.GetWindowRect(int(hwnd), ctypes.byref(rect)):
        return 0, 0, 0, 0
    return int(rect.left), int(rect.top), int(rect.right - rect.left), int(rect.bottom - rect.top)


def client_rect_on_screen(hwnd: int) -> Tuple[int, int, int, int]:
    """客户区在屏幕上的 (x, y, w, h)——用于窗口跟随。"""
    rect = RECT()
    if not user32.GetClientRect(int(hwnd), ctypes.byref(rect)):
        return 0, 0, 0, 0
    pt = POINT(0, 0)
    user32.ClientToScreen(int(hwnd), ctypes.byref(pt))
    return int(pt.x), int(pt.y), int(rect.right - rect.left), int(rect.bottom - rect.top)


def screen_to_client(hwnd: int, x: int, y: int) -> Tuple[int, int]:
    pt = POINT(int(x), int(y))
    user32.ScreenToClient(int(hwnd), ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def client_to_screen(hwnd: int, x: int, y: int) -> Tuple[int, int]:
    pt = POINT(int(x), int(y))
    user32.ClientToScreen(int(hwnd), ctypes.byref(pt))
    return int(pt.x), int(pt.y)


def window_from_point(x: int, y: int) -> int:
    return int(user32.WindowFromPoint(POINT(int(x), int(y))) or 0)


def ancestor_window(hwnd: int) -> int:
    GA_ROOT = 2
    return int(user32.GetAncestor(int(hwnd), GA_ROOT) or hwnd)


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    user32.GetWindowThreadProcessId(int(hwnd), ctypes.byref(pid))
    return int(pid.value)


def process_name(pid: int) -> str:
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ""
    try:
        size = wintypes.DWORD(1024)
        buf = ctypes.create_unicode_buffer(1024)
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
    except Exception:
        pass
    finally:
        kernel32.CloseHandle(handle)
    return ""


def window_matches(hwnd: int, keyword: str) -> bool:
    """窗口标题或进程名包含关键字（用于"目标窗口限定"）。"""
    if not keyword:
        return True
    keyword = keyword.lower()
    if not is_window(hwnd):
        return False
    if keyword in window_title(hwnd).lower():
        return True
    return keyword in process_name(window_pid(hwnd)).lower()


def find_window_by_title(keyword: str) -> int:
    """按标题关键字找窗口（只找可见的顶层窗口）。

    优先返回**标题完全相同**的窗口，找不到再退回"包含关键字"的第一个，
    避免 "连点器" 这种关键字先撞上标题里恰好含它的浏览器/资源管理器。
    """
    if not keyword:
        return 0
    keyword = keyword.lower()
    exact: list = []
    partial: list = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def _enum(hwnd, _):
        if len(exact) >= 1:
            return False
        if user32.IsWindowVisible(hwnd):
            title = window_title(hwnd)
            if title:
                low = title.lower()
                if low == keyword:
                    exact.append(int(hwnd))
                    return False
                if keyword in low and len(partial) < 8:
                    partial.append(int(hwnd))
        return True

    user32.EnumWindows(_enum, 0)
    if exact:
        return exact[0]
    return partial[0] if partial else 0


# --------------------------------------------------------------------------
# 后台点击（不抢占鼠标）
# --------------------------------------------------------------------------
def post_click(hwnd: int, x: int, y: int, button: str = "left") -> bool:
    """把点击消息直接投递给窗口，鼠标指针不动。"""
    info = BUTTON_INFO.get(button)
    if info is None or not is_window(hwnd):
        return False
    cx, cy = screen_to_client(hwnd, x, y)
    lparam = (cy & 0xFFFF) << 16 | (cx & 0xFFFF)
    try:
        user32.PostMessageW(int(hwnd), WM_MOUSEMOVE, 0, lparam)
        data = (info["xdata"] << 16) if info["xdata"] else 0
        user32.PostMessageW(int(hwnd), info["msg_down"], info["wparam_down"], lparam)
        user32.PostMessageW(int(hwnd), info["msg_up"], data, lparam)
        return True
    except Exception:
        return False


def post_key(hwnd: int, vk: int) -> bool:
    WM_KEYDOWN, WM_KEYUP = 0x0100, 0x0101
    if not is_window(hwnd):
        return False
    try:
        user32.PostMessageW(int(hwnd), WM_KEYDOWN, vk, 0)
        user32.PostMessageW(int(hwnd), WM_KEYUP, vk, 0)
        return True
    except Exception:
        return False


def minimize_console() -> None:
    """双击启动时隐藏黑框（非 frozen 情况下由启动器负责）。"""
    try:
        hwnd = kernel32.GetConsoleWindow()
        if hwnd:
            user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def _declare_signatures() -> None:
    """声明 Win32 函数签名。

    64 位下如果不声明，参数会被当成 32 位整数，句柄/指针会溢出（OverflowError）
    或被静默截断，导致"看起来调用了但什么也没发生"。
    """
    def sig(func, restype=None, argtypes=None) -> None:
        try:
            if restype is not None:
                func.restype = restype
            if argtypes is not None:
                func.argtypes = argtypes
        except Exception:
            pass

    sig(user32.SendInput, wintypes.UINT, [wintypes.UINT, ctypes.c_void_p, ctypes.c_int])
    sig(user32.SetCursorPos, wintypes.BOOL, [ctypes.c_int, ctypes.c_int])
    sig(user32.GetCursorPos, wintypes.BOOL, [ctypes.POINTER(POINT)])
    sig(user32.WindowFromPoint, wintypes.HWND, [POINT])
    sig(user32.GetForegroundWindow, wintypes.HWND, [])
    sig(user32.GetAncestor, wintypes.HWND, [wintypes.HWND, wintypes.UINT])
    sig(user32.IsWindow, wintypes.BOOL, [wintypes.HWND])
    sig(user32.GetWindowTextLengthW, ctypes.c_int, [wintypes.HWND])
    sig(user32.GetWindowTextW, ctypes.c_int, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int])
    sig(user32.GetClassNameW, ctypes.c_int, [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int])
    sig(user32.GetWindowRect, wintypes.BOOL, [wintypes.HWND, ctypes.POINTER(RECT)])
    sig(user32.GetClientRect, wintypes.BOOL, [wintypes.HWND, ctypes.POINTER(RECT)])
    sig(user32.ClientToScreen, wintypes.BOOL, [wintypes.HWND, ctypes.POINTER(POINT)])
    sig(user32.ScreenToClient, wintypes.BOOL, [wintypes.HWND, ctypes.POINTER(POINT)])
    sig(user32.GetWindowThreadProcessId, wintypes.DWORD,
        [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)])
    sig(user32.PostMessageW, wintypes.BOOL,
        [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM])
    sig(user32.SetWindowPos, wintypes.BOOL,
        [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int,
         ctypes.c_int, wintypes.UINT])
    sig(user32.SetForegroundWindow, wintypes.BOOL, [wintypes.HWND])
    sig(user32.ShowWindow, wintypes.BOOL, [wintypes.HWND, ctypes.c_int])
    sig(user32.OpenClipboard, wintypes.BOOL, [wintypes.HWND])
    sig(user32.CloseClipboard, wintypes.BOOL, [])
    sig(user32.EmptyClipboard, wintypes.BOOL, [])
    sig(user32.GetAsyncKeyState, ctypes.c_short, [ctypes.c_int])
    sig(kernel32.GetConsoleWindow, wintypes.HWND, [])
    sig(kernel32.OpenProcess, wintypes.HANDLE, [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD])
    sig(kernel32.CloseHandle, wintypes.BOOL, [wintypes.HANDLE])
    sig(kernel32.QueryFullProcessImageNameW, wintypes.BOOL,
        [wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)])
    for name in ("GetWindowLongW", "GetWindowLongPtrW"):
        func = getattr(user32, name, None)
        if func is not None:
            sig(func, ctypes.c_ssize_t, [wintypes.HWND, ctypes.c_int])
    for name in ("SetWindowLongW", "SetWindowLongPtrW"):
        func = getattr(user32, name, None)
        if func is not None:
            sig(func, ctypes.c_ssize_t, [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t])


_declare_signatures()
