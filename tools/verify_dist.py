"""验证打包好的 exe 是否真的能跑起来：找窗口、置顶、截图。"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from app import screen as S  # noqa: E402
from app import winapi as W  # noqa: E402


def main() -> int:
    W.set_dpi_awareness()
    keyword = "连点器 AutoClicker Pro"
    hwnd = 0
    for _ in range(20):
        hwnd = W.find_window_by_title(keyword)
        if hwnd:
            break
        time.sleep(0.5)
    if not hwnd:
        print("没找到连点器窗口 —— exe 可能没启动成功")
        return 1

    title = W.window_title(hwnd)
    print("找到窗口：", repr(title), "hwnd =", hwnd)
    pid = W.window_pid(hwnd)
    print("进程：", W.process_name(pid), "pid =", pid)

    x, y, w, h = W.window_rect(hwnd)
    print("窗口矩形：", (x, y, w, h))

    W.user32.SetWindowPos(hwnd, -1, 60, 60, w, h, 0x0040)
    W.user32.SetForegroundWindow(hwnd)
    time.sleep(2.0)

    out = os.path.join(ROOT, "docs", "screenshots", "07_dist_exe.png")
    S.save_png(out, S.grab((60, 60, w, h)))
    print("已保存截图：", out)
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        sys.stdout.flush()
        os._exit(code)
