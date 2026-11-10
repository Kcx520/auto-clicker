"""诊断：屏幕标记覆盖层到底有没有画出来。"""

from __future__ import annotations

import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)


def count_marker_pixels(image) -> tuple[int, int]:
    """统计接近标记颜色（绿 #37c98b / 蓝 #4f8cff）的像素数。"""
    green = np.array([0x37, 0xC9, 0x8B], dtype=np.int16)
    blue = np.array([0x4F, 0x8C, 0xFF], dtype=np.int16)
    arr = image[:, :, :3].astype(np.int16)
    near_green = (np.abs(arr - green).sum(axis=2) < 90).sum()
    near_blue = (np.abs(arr - blue).sum(axis=2) < 90).sum()
    return int(near_green), int(near_blue)


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
    app.cfg.set("recognize.region", [420, 300, 520, 260])
    app.cfg.set("click.position_id", positions[0]["id"])
    app.cfg.save()
    app.tab_positions.refresh()
    app.update()

    before_g, before_b = count_marker_pixels(S.grab((500, 250, 900, 500)))
    print(f"开覆盖层之前：绿色像素 {before_g}，蓝色像素 {before_b}")

    app.marker.show()
    for _ in range(12):
        app.update()
        time.sleep(0.12)
    time.sleep(1.5)

    win = app.marker.window
    raw = int(win.winfo_id())
    root_hwnd = W.ancestor_window(raw)
    print("overlay raw hwnd =", raw, " root =", root_hwnd)
    print("  数据源 =", app._marker_source())  # noqa: SLF001
    print("  canvas 尺寸 =", win.winfo_width(), win.winfo_height(),
          " canvas.winfo_width =", app.marker.canvas.winfo_width(),
          app.marker.canvas.winfo_height())
    print("  canvas 上的元素 =", app.marker.canvas.find_all())
    print("  bbox(all) =", app.marker.canvas.bbox("all"))
    for label, hwnd in (("raw", raw), ("root", root_hwnd)):
        print(f"  {label}: rect={W.window_rect(hwnd)} 可见={bool(W.user32.IsWindowVisible(hwnd))}")
    print("  虚拟屏幕 =", W.virtual_screen_rect())

    import ctypes
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    get_long = getattr(user32, "GetWindowLongPtrW", user32.GetWindowLongW)
    get_long.restype = ctypes.c_ssize_t
    get_long.argtypes = [ctypes.c_void_p, ctypes.c_int]
    print("  exstyle = 0x%X" % (get_long(ctypes.c_void_p(root_hwnd), -20) & 0xFFFFFFFF))

    after_g, after_b = count_marker_pixels(S.grab((500, 250, 900, 500)))
    print(f"开覆盖层之后（主界面还在）：绿色像素 {after_g}，蓝色像素 {after_b}")

    app.withdraw()
    for _ in range(10):
        app.update()
        time.sleep(0.12)
    time.sleep(0.8)
    hid_g, hid_b = count_marker_pixels(S.grab((500, 250, 900, 500)))
    print(f"隐藏主界面之后：绿色像素 {hid_g}，蓝色像素 {hid_b}")

    out = os.path.join(ROOT, "docs", "screenshots", "diag_overlay.png")
    S.save_png(out, S.grab((400, 250, 900, 500)))
    print("已保存局部截图", out)

    # 紧跟标记点周围的精确裁剪
    tight = (680, 400, 240, 120)
    t_g, t_b = count_marker_pixels(S.grab(tight))
    c_g, c_b = count_marker_pixels(S.grab((1900, 1400, 240, 120)))
    print(f"标记点周围 ({tight})：绿色 {t_g}，蓝色 {t_b}")
    print(f"对照区域（屏幕右下）：绿色 {c_g}，蓝色 {c_b}")
    S.save_png(os.path.join(ROOT, "docs", "screenshots", "diag_marker_tight.png"),
               S.grab((600, 360, 400, 200)))

    app.quit_app()
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        sys.stdout.flush()
        os._exit(code)
