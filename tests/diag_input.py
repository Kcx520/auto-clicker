"""诊断：SendInput 鼠标注入到底有没有生效。"""

from __future__ import annotations

import ctypes
import os
import sys
import time
import tkinter as tk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import winapi as W  # noqa: E402


def main() -> int:
    W.set_dpi_awareness()
    print("会话信息")
    print("  SM_REMOTESESSION =", W.user32.GetSystemMetrics(0x1000))
    pid = ctypes.windll.kernel32.GetCurrentProcessId()
    sid = ctypes.c_ulong(0)
    ctypes.windll.kernel32.ProcessIdToSessionId(pid, ctypes.byref(sid))
    print("  当前进程 PID =", pid, " SessionId =", sid.value)
    print("  虚拟屏幕 =", W.virtual_screen_rect())
    print("  管理员 =", W.is_admin())

    root = tk.Tk()
    root.withdraw()
    probe = tk.Toplevel(root)
    probe.overrideredirect(True)
    probe.attributes("-topmost", True)
    probe.geometry("400x300+100+100")
    probe.configure(bg="#224466")
    hits: list = []
    probe.bind("<Button-1>", lambda e: hits.append(("down", e.x_root, e.y_root)))
    probe.bind("<ButtonRelease-1>", lambda e: hits.append(("up", e.x_root, e.y_root)))
    label = tk.Label(probe, text="测试窗口", bg="#224466", fg="white")
    label.pack(fill="both", expand=True)
    label.bind("<Button-1>", lambda e: hits.append(("label", e.x_root, e.y_root)))
    root.update()
    probe.update()
    time.sleep(0.4)

    hwnd_probe = int(probe.winfo_id())
    print("探针窗口 hwnd =", hwnd_probe, " 标题 =", repr(W.window_title(hwnd_probe)))
    W.user32.SetForegroundWindow(hwnd_probe)
    time.sleep(0.2)

    W.set_cursor_pos(250, 250)
    time.sleep(0.2)
    print("光标 =", W.get_cursor_pos())
    at = W.window_from_point(250, 250)
    print("该点的窗口 hwnd =", at, " 是探针吗 =", at == hwnd_probe,
          " 根窗口 =", W.ancestor_window(at) == hwnd_probe)
    print("前台窗口 =", W.foreground_window(), " 是探针吗 =", W.foreground_window() == hwnd_probe)

    # 1) 直接用 SendInput
    ret = W._send_inputs([W._mouse_input(W.MOUSEEVENTF_LEFTDOWN),
                          W._mouse_input(W.MOUSEEVENTF_LEFTUP)])  # noqa: SLF001
    print("SendInput 返回值 =", ret, "（2 表示两个事件都成功）")

    # 2) 用 W.click
    W.click("left", 30, 250, 250)
    print("W.click 已调用")

    # 3) 老 API mouse_event 对比
    try:
        W.user32.mouse_event(W.MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)
        W.user32.mouse_event(W.MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)
        print("mouse_event 已调用")
    except Exception as exc:
        print("mouse_event 失败:", exc)

    for _ in range(40):
        root.update()
        time.sleep(0.02)
    print("探针收到的事件 =", hits)

    # 4) 键盘注入测试（用记事本不方便，直接看 SendInput 返回值）
    kret = W._send_inputs([W._key_input(0x41)])  # noqa: SLF001
    print("键盘 SendInput 返回值 =", kret)

    probe.destroy()
    root.destroy()
    return 0


if __name__ == "__main__":
    sys.exit(main())
