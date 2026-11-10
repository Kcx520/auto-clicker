"""端到端测试：在屏幕上显示一段中文 → OCR 找到它 → 鼠标真的点过去。

为了安全，全程用测试自己创建的置顶窗口当靶子，不会点到别的程序。
"""

from __future__ import annotations

import os
import sys
import time
import tkinter as tk

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILED: list[str] = []


def check(name: str, condition: bool, extra: str = "") -> None:
    print(f"[{'OK  ' if condition else 'FAIL'}] {name}{('  -> ' + extra) if extra else ''}")
    if not condition:
        FAILED.append(name)


def main() -> int:
    from app import screen as S
    from app import winapi as W

    W.set_dpi_awareness()
    from app.ui.app import App

    app = App()
    app.cfg.set("general.first_run", False)
    app.update()

    # ---------------- 造一个置顶的靶子窗口 ----------------
    target_text = "立即抢购"
    probe = tk.Toplevel(app)
    probe.overrideredirect(True)
    probe.configure(bg="#ffffff")
    canvas = tk.Canvas(probe, width=520, height=200, bg="#ffffff", highlightthickness=0)
    canvas.pack()
    canvas.create_text(30, 100, anchor="w", text=target_text, fill="#000000",
                       font=("Microsoft YaHei UI", 40, "bold"))
    probe.geometry("520x200+240+240")
    probe.update()
    raw = int(probe.winfo_id())
    hwnd = W.ancestor_window(raw) or raw
    W.user32.SetWindowPos(hwnd, -1, 240, 240, 520, 200, 0x0040)
    W.user32.SetForegroundWindow(hwnd)
    for _ in range(14):
        app.update()
        time.sleep(0.06)

    x, y, w, h = W.window_rect(hwnd)
    region = [x + 6, y + 6, w - 12, h - 12]
    print(f"靶子窗口 raw={raw} root={hwnd} @ {x},{y} {w}x{h}；识别范围 {region}")

    top = W.window_from_point(x + w // 2, y + h // 2)
    visible = W.ancestor_window(top) == hwnd
    print(f"窗口中心处的最上层窗口 = {top}，是我们的靶子吗：{visible}")

    clicks: list = []
    canvas.bind("<Button-1>", lambda e: clicks.append((e.x_root, e.y_root)))
    probe.bind("<Button-1>", lambda e: clicks.append((e.x_root, e.y_root)))

    # ---------------- 文字识别任务 ----------------
    from app.recognizer import new_task

    task = new_task("text")
    task["keywords"] = [target_text]
    task["region"] = region
    task["threshold"] = 0.8
    task["click_where"] = "found"
    task["cooldown_s"] = 0
    app.cfg.set("recognize.tasks", [task])
    app.cfg.set("recognize.region", region)

    recognizer = app.recognizer
    engine = recognizer.engine()
    check("OCR 引擎可用", engine is not None and getattr(engine, "available", False),
          getattr(engine, "name", "无"))

    info = recognizer.test_task(task)
    check("测试识别能定位到文字", bool(info.get("ok")), str(info.get("message"))[:70])
    if info.get("ok"):
        dx = abs(info["x"] - (x + 30))
        dy = abs(info["y"] - (y + 60))
        check("识别坐标接近真实位置", dx < 60 and dy < 60,
              f"识别 ({info['x']}, {info['y']}) vs 绘制 ({x + 30}, {y + 60})")

    # ---------------- 真的跑一轮识别 + 点击 ----------------
    hits = recognizer.poll_once()
    check("poll_once 命中任务", len(hits) == 1,
          f"{[(h['task']['name'], h['screen_x'], h['screen_y']) for h in hits]}")
    for _ in range(25):
        app.update()
        time.sleep(0.03)
    check("命中后鼠标真的点到了目标窗口", len(clicks) > 0,
          f"收到 {len(clicks)} 次点击 {clicks[:2]}" if clicks else "没收到点击")
    check("命中计数已累加", int(task.get("hits", 0)) >= 1, f"hits={task.get('hits')}")

    # ---------------- 冷却时间生效 ----------------
    task["cooldown_s"] = 60
    again = recognizer.poll_once()
    check("冷却时间内不会重复触发", len(again) == 0)
    task["cooldown_s"] = 0

    # ---------------- 找图任务 ----------------
    from app.config import TEMPLATE_DIR
    TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
    shot = S.grab(tuple(region))
    template_path = str(TEMPLATE_DIR / "e2e_template.png")
    S.save_png(template_path, shot)
    image_task = new_task("image")
    image_task["template_paths"] = [template_path]
    image_task["region"] = region
    image_task["threshold"] = 0.9
    image_task["click_where"] = "found"
    image_info = recognizer.test_task(image_task)
    check("找图能匹配到自己截的图", bool(image_info.get("ok")), str(image_info.get("message"))[:70])

    image_task["cooldown_s"] = 60
    app.cfg.set("recognize.tasks", [image_task])
    image_hits = recognizer.poll_once()
    check("找图任务命中", len(image_hits) == 1,
          f"score={image_hits[0]['score']:.3f}" if image_hits else "无")

    # ---------------- 后台识别线程 ----------------
    app.cfg.set("recognize.tasks", [task])
    task["cooldown_s"] = 0
    task["max_hits"] = 2
    recognizer.start()
    time.sleep(1.6)
    running = recognizer.is_running()
    recognizer.stop()
    check("识别线程能跑起来并停下", running and not recognizer.is_running(),
          f"轮询次数={recognizer.polls} 命中={recognizer.hit_count}")

    # ---------------- 屏幕标记 ----------------
    try:
        app.marker.show()
        for _ in range(5):
            app.update()
            time.sleep(0.05)
        shown = app.marker.visible and app.marker.window is not None
        app.marker.hide()
        check("屏幕标记显示/隐藏正常", shown and not app.marker.visible)
    except Exception as exc:
        check("屏幕标记显示/隐藏正常", False, repr(exc))

    # ---------------- 托盘图标 ----------------
    try:
        from app.ui.tray import TrayIcon
        tray = TrayIcon(app, on_show=lambda: None, on_toggle=lambda: None,
                        on_stop=lambda: None, on_quit=lambda: None)
        tray.start()
        time.sleep(1.2)
        ok = bool(tray._hwnd)  # noqa: SLF001
        tray.stop()
        time.sleep(0.4)
        check("托盘图标能创建并销毁", ok, f"hwnd={tray._hwnd}")  # noqa: SLF001
    except Exception as exc:
        check("托盘图标能创建并销毁", False, repr(exc))

    # ---------------- 位置自动保存 ----------------
    try:
        app.tab_positions.add_position()
        app.update()
        card = app.tab_positions.cards[-1]
        card.name_field.set("测试点位")
        card.num_x.set(4321)
        card.num_y.set(1234)
        changed = card.collect()
        app.cfg.save()
        saved = app.cfg.positions()[-1]
        check("位置卡片改动会自动落盘",
              changed and saved.get("name") == "测试点位" and saved.get("x") == 4321,
              f"{saved.get('name')} ({saved.get('x')}, {saved.get('y')})")
    except Exception as exc:
        check("位置卡片改动会自动落盘", False, repr(exc))

    probe.destroy()
    app.quit_app()
    print("=" * 70)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：")
        for item in FAILED:
            print("   -", item)
        return 1
    print("识别端到端测试全部通过 ✅")
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
