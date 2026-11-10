"""给界面各页签截图，便于视觉检查。"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

OUT = os.path.join(ROOT, "docs", "screenshots")


def main() -> int:
    os.makedirs(OUT, exist_ok=True)
    from app import screen as S
    from app import winapi as W

    W.set_dpi_awareness()
    from app.ui.app import App

    app = App()
    app.cfg.set("general.first_run", False)
    app.update()
    width, height = 1400, 940
    app.geometry(f"{width}x{height}+10+10")
    app.update()
    # 置顶，避免被其它窗口挡住
    hwnd = app.winfo_id()
    W.user32.SetWindowPos(hwnd, -1, 10, 10, width, height, 0x0040)
    W.user32.SetForegroundWindow(hwnd)
    for _ in range(8):
        app.update()
        time.sleep(0.15)

    tabs = [("01_click", app.tab_click), ("02_recognize", app.tab_recognize),
            ("03_positions", app.tab_positions), ("04_record", app.tab_record),
            ("05_settings", app.tab_settings)]
    x, y, w, h = W.window_rect(app.winfo_id())
    print("窗口矩形 =", (x, y, w, h))

    for name, tab in tabs:
        app.notebook.select(tab)
        for _ in range(6):
            app.update()
            time.sleep(0.12)
        time.sleep(0.5)
        app.update()
        image = S.grab((x, y, w, h))
        path = os.path.join(OUT, f"{name}.png")
        S.save_png(path, image)
        print("已保存", path, image.shape)

    # 迷你面板
    app.toggle_mini()
    for _ in range(6):
        app.update()
        time.sleep(0.1)
    time.sleep(0.3)
    full = S.grab((0, 0, 2560, 1600))
    S.save_png(os.path.join(OUT, "06_mini_desktop.png"), full)
    print("已保存迷你面板截图")

    app.quit_app()
    return 0


if __name__ == "__main__":
    try:
        code = main()
    finally:
        # 保证进程一定退出，避免残留全局钩子干扰用户
        sys.stdout.flush()
        os._exit(0)
