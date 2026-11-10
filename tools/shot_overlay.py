"""截图验证：屏幕十字标记 + 识别范围虚线框 + 迷你悬浮面板。"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def main() -> int:
    from app import screen as S
    from app import winapi as W

    W.set_dpi_awareness()
    from app.ui.app import App

    app = App()
    app.cfg.set("general.first_run", False)
    app.update()

    positions = app.cfg.positions()
    positions[0]["name"] = "确认按钮"
    positions[0]["x"], positions[0]["y"] = 700, 420
    positions[1]["name"] = "开始游戏"
    positions[1]["x"], positions[1]["y"] = 1180, 880
    app.cfg.set("recognize.region", [420, 300, 520, 260])
    app.cfg.set("click.position_id", positions[0]["id"])
    app.cfg.save()
    app.tab_positions.refresh()      # 让卡片界面和配置同步（否则卡片自动保存会把坐标改回去）
    app.tab_click.refresh_positions()

    hwnd = app.winfo_id()
    W.user32.SetWindowPos(hwnd, -1, 40, 40, 1000, 700, 0x0040)
    app.update()

    app.marker.refresh()
    app.toggle_mini()
    for _ in range(12):
        app.update()
        time.sleep(0.15)
    # 把主窗口藏起来，这样能看清屏幕上的标记和迷你面板
    app.withdraw()
    for _ in range(15):
        app.update()
        time.sleep(0.15)
    time.sleep(1.0)

    out = os.path.join(ROOT, "docs", "screenshots", "08_overlay.png")
    S.save_png(out, S.grab((0, 0, 1700, 1200)))
    print("已保存", out)

    # 单独把迷你面板抠出来
    mini = app._mini  # noqa: SLF001
    if mini is not None:
        try:
            mx, my = mini.winfo_x(), mini.winfo_y()
            mw, mh = mini.winfo_width(), mini.winfo_height()
            pad = 12
            S.save_png(os.path.join(ROOT, "docs", "screenshots", "09_mini_panel.png"),
                       S.grab((max(0, mx - pad), max(0, my - pad), mw + pad * 2, mh + pad * 2)))
            print(f"已保存迷你面板 {mx},{my} {mw}x{mh}")
        except Exception as exc:
            print("迷你面板截图失败：", exc)

    app.quit_app()
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        sys.stdout.flush()
        os._exit(code)
