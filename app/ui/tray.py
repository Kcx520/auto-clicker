"""系统托盘图标（Shell_NotifyIcon，纯 ctypes 实现，不依赖第三方库）。"""

from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes
from typing import Callable, Optional

LRESULT = ctypes.c_ssize_t
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)

WM_APP = 0x8000
WM_TRAY = WM_APP + 20
WM_DESTROY = 0x0002
WM_COMMAND = 0x0111
WM_RBUTTONUP = 0x0205
WM_LBUTTONDBLCLK = 0x0203
WM_LBUTTONUP = 0x0202
WM_QUIT = 0x0012

NIM_ADD = 0
NIM_MODIFY = 1
NIM_DELETE = 2
NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04

IDI_APPLICATION = 32512
MF_STRING = 0x0000
TPM_RIGHTBUTTON = 0x0002
TPM_RETURNCMD = 0x0100

ID_SHOW = 1001
ID_TOGGLE = 1002
ID_STOP = 1003
ID_QUIT = 1004


class GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


WNDPROC = ctypes.WINFUNCTYPE(LRESULT, wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

# 一定要声明签名，否则 64 位下指针参数会被截断成 32 位而报 OverflowError
user32.RegisterClassW.argtypes = [ctypes.c_void_p]
user32.RegisterClassW.restype = wintypes.WORD
user32.CreateWindowExW.restype = wintypes.HWND
user32.CreateWindowExW.argtypes = [wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR,
                                   wintypes.DWORD, ctypes.c_int, ctypes.c_int,
                                   ctypes.c_int, ctypes.c_int, wintypes.HWND,
                                   wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p]
user32.DefWindowProcW.restype = LRESULT
user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.DestroyWindow.argtypes = [wintypes.HWND]
user32.LoadIconW.restype = wintypes.HICON
user32.LoadIconW.argtypes = [wintypes.HINSTANCE, ctypes.c_void_p]
user32.CreatePopupMenu.restype = wintypes.HMENU
user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_size_t, wintypes.LPCWSTR]
user32.TrackPopupMenu.restype = wintypes.BOOL
user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  ctypes.c_int, wintypes.HWND, ctypes.c_void_p]
user32.DestroyMenu.argtypes = [wintypes.HMENU]
user32.SetForegroundWindow.argtypes = [wintypes.HWND]
user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.c_void_p]
kernel32.GetModuleHandleW.restype = wintypes.HINSTANCE
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]


class TrayIcon:
    """托盘图标：左键双击打开主界面，右键弹出菜单。"""

    def __init__(self, app, on_show: Callable, on_toggle: Callable,
                 on_stop: Callable, on_quit: Callable) -> None:
        self.app = app
        self.on_show = on_show
        self.on_toggle = on_toggle
        self.on_stop = on_stop
        self.on_quit = on_quit
        self._thread: Optional[threading.Thread] = None
        self._hwnd = 0
        self._tid = 0
        self._running = False
        self._nid: Optional[NOTIFYICONDATAW] = None
        self._wndproc = WNDPROC(self._window_proc)

    # ------------------------------------------------------------------
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._running = True
        self._thread = threading.Thread(target=self._run, name="tray", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._running = False
        if self._hwnd:
            try:
                shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(self._nid))
            except Exception:
                pass
        if self._tid:
            try:
                user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)
            except Exception:
                pass

    # ------------------------------------------------------------------
    def _run(self) -> None:
        self._tid = int(kernel32.GetCurrentThreadId())
        hinstance = kernel32.GetModuleHandleW(None)
        class_name = f"AutoClickerProTray{self._tid}"
        wc = WNDCLASSW()
        wc.lpfnWndProc = ctypes.cast(self._wndproc, ctypes.c_void_p)
        wc.hInstance = hinstance
        wc.lpszClassName = class_name
        try:
            user32.RegisterClassW(ctypes.byref(wc))
        except Exception:
            pass

        self._hwnd = int(user32.CreateWindowExW(0, class_name, "AutoClickerPro", 0, 0, 0, 0, 0,
                                                None, None, hinstance, None) or 0)
        if not self._hwnd:
            self.app.ui_call(lambda: self.app.log("托盘窗口创建失败", "warn"))
            return

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = self._hwnd
        nid.uID = 1
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
        nid.uCallbackMessage = WM_TRAY
        nid.hIcon = user32.LoadIconW(None, ctypes.c_wchar_p(IDI_APPLICATION))
        nid.szTip = "连点器 AutoClicker Pro"
        self._nid = nid
        if not shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid)):
            self.app.ui_call(lambda: self.app.log("托盘图标添加失败", "warn"))
            return

        self.app.ui_call(lambda: self.app.log("已最小化到托盘，双击图标可重新打开", "ok"))

        msg = wintypes.MSG()
        while self._running:
            ret = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if ret in (0, -1):
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

        try:
            shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))
        except Exception:
            pass
        if self._hwnd:
            try:
                user32.DestroyWindow(self._hwnd)
            except Exception:
                pass
        self._hwnd = 0

    # ------------------------------------------------------------------
    def _window_proc(self, hwnd, msg, wparam, lparam) -> int:
        try:
            if msg == WM_TRAY:
                event = int(lparam) & 0xFFFF
                if event in (WM_LBUTTONDBLCLK, WM_LBUTTONUP):
                    self._invoke(self.on_show)
                elif event == WM_RBUTTONUP:
                    self._menu()
                return 0
            if msg == WM_COMMAND:
                self._handle_command(int(wparam) & 0xFFFF)
                return 0
            if msg == WM_DESTROY:
                user32.PostQuitMessage(0)
                return 0
        except Exception:
            pass
        try:
            return int(user32.DefWindowProcW(hwnd, msg, wparam, lparam))
        except Exception:
            return 0

    def _menu(self) -> None:
        menu = user32.CreatePopupMenu()
        running = False
        try:
            running = bool(self.app.clicker.is_running())
        except Exception:
            pass
        user32.AppendMenuW(menu, MF_STRING, ID_SHOW, "显示主界面")
        user32.AppendMenuW(menu, MF_STRING, ID_TOGGLE, "停止连点" if running else "开始连点")
        user32.AppendMenuW(menu, MF_STRING, ID_STOP, "紧急停止全部")
        user32.AppendMenuW(menu, 0x00000800, 0, None)  # 分隔线
        user32.AppendMenuW(menu, MF_STRING, ID_QUIT, "退出程序")
        point = wintypes.POINT()
        user32.GetCursorPos(ctypes.byref(point))
        user32.SetForegroundWindow(self._hwnd)
        command = user32.TrackPopupMenu(menu, TPM_RIGHTBUTTON | TPM_RETURNCMD,
                                        point.x, point.y, 0, self._hwnd, None)
        user32.DestroyMenu(menu)
        if command:
            self._handle_command(int(command))

    def _handle_command(self, command: int) -> None:
        if command == ID_SHOW:
            self._invoke(self.on_show)
        elif command == ID_TOGGLE:
            self._invoke(self.on_toggle)
        elif command == ID_STOP:
            self._invoke(self.on_stop)
        elif command == ID_QUIT:
            self._invoke(self.on_quit)

    def _invoke(self, func: Callable) -> None:
        try:
            self.app.ui_call(func)
        except Exception:
            try:
                func()
            except Exception:
                pass
