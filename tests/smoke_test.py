"""冒烟测试：验证各模块能正常协同工作（不会真的去点屏幕）。

运行：
    & "python" tests\\smoke_test.py
"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

FAILED: list[str] = []


def check(name: str, condition: bool, extra: str = "") -> None:
    mark = "OK  " if condition else "FAIL"
    print(f"[{mark}] {name}{('  -> ' + extra) if extra else ''}")
    if not condition:
        FAILED.append(name)


def main() -> int:
    print("=" * 70)
    print("连点器冒烟测试")
    print("=" * 70)

    # ---------- 基础 ----------
    from app import winapi as W
    from app import screen as S

    W.set_dpi_awareness()
    vx, vy, vw, vh = W.virtual_screen_rect()
    check("取得屏幕尺寸", vw > 0 and vh > 0, f"{vw}x{vh} @ ({vx},{vy})")

    image = S.grab()
    check("截屏成功", image.shape[0] == vh and image.shape[1] == vw,
          f"shape={image.shape} dtype={image.dtype}")

    start = time.perf_counter()
    for _ in range(3):
        S.grab((0, 0, 400, 300))
    cost = (time.perf_counter() - start) / 3 * 1000
    check("区域截屏速度正常", cost < 200, f"{cost:.1f} ms/次")

    monitors = S.monitors()
    check("枚举显示器", len(monitors) >= 1, str(monitors))

    # ---------- 热键字符串 ----------
    parsed = W.parse_hotkey("Ctrl+Alt+Q")
    check("解析组合热键", parsed is not None and parsed["vk"] == 0x51 and parsed["mods"] == {"ctrl", "alt"},
          str(parsed))
    check("解析单键热键", (W.parse_hotkey("F6") or {}).get("vk") == 0x75)
    mouse_key = W.parse_hotkey("MouseX2")
    check("解析鼠标热键", mouse_key is not None and mouse_key.get("mouse") == "x2")
    text = W.format_hotkey({"ctrl", "alt"}, 0x51)
    check("热键还原成文字", "Ctrl" in text and "Alt" in text and "Q" in text, text)
    check("非法热键返回 None", W.parse_hotkey("这不是键") is None)

    # ---------- 光标 ----------
    old = W.get_cursor_pos()
    W.set_cursor_pos(old[0], old[1])
    check("读写光标位置", W.get_cursor_pos() == old, str(old))

    fg = W.foreground_window()
    check("读取前台窗口", fg > 0, f"{W.window_title(fg)[:40]!r} pid={W.window_pid(fg)}")
    check("窗口矩形有效", W.window_rect(fg)[2] >= 0)

    # ---------- 配置 ----------
    from app.config import Config, config, ensure_dirs
    ensure_dirs()
    # 用一个全新的临时路径验证"默认值合并"，不受已有配置文件影响
    fresh_path = os.path.join(ROOT, "config", "_smoke_fresh.json")
    if os.path.exists(fresh_path):
        os.remove(fresh_path)
    fresh = Config(fresh_path)
    check("全新配置读取默认值", fresh.get("click.cps") == 10.0 and fresh.get("click.button") == "left",
          f"cps={fresh.get('click.cps')}")
    ref_before = fresh.get_ref("click")
    fresh.update({"click": {"cps": 42}})
    ref_after = fresh.get_ref("click")
    check("配置就地合并（不换对象，保住引用）",
          ref_before is ref_after and fresh.get("click.cps") == 42
          and fresh.get("click.button") == "left",
          "update 后子字典仍是同一个对象，兄弟字段没丢")
    fresh.save()
    reloaded = Config(fresh_path)
    check("配置能存能读", reloaded.get("click.cps") == 42, f"cps={reloaded.get('click.cps')}")
    try:
        os.remove(fresh_path)
    except OSError:
        pass

    check("运行中的配置点号可读写",
          (config.set("click.start_delay_s", 3),
           config.get("click.start_delay_s"))[1] == 3)
    check("位置卡片存在", len(config.positions()) >= 1, f"{len(config.positions())} 个")
    check("配置档列表", isinstance(config.profile_names(), list))

    # ---------- 连点节奏 ----------
    from app.clicker import Clicker, precise_sleep

    clicker = Clicker(config)
    snap = clicker.snapshot()
    check("初始状态快照", snap["running"] is False and snap["count"] == 0, str(snap["remaining"]))

    config.update({"click": {"interval_mode": "cps", "cps": 20, "humanize": False}})
    intervals = [clicker._next_interval() for _ in range(200)]  # noqa: SLF001
    average = sum(intervals) / len(intervals)
    check("固定 CPS 间隔正确", abs(average - 0.05) < 0.003, f"平均 {average * 1000:.2f} ms")

    config.update({"click": {"interval_mode": "random", "random_min_ms": 80,
                             "random_max_ms": 120, "gaussian": False}})
    intervals = [clicker._next_interval() for _ in range(400)]  # noqa: SLF001
    check("随机间隔落在范围内",
          all(0.079 <= v <= 0.121 for v in intervals) and max(intervals) - min(intervals) > 0.005,
          f"{min(intervals) * 1000:.1f}~{max(intervals) * 1000:.1f} ms")

    config.update({"click": {"humanize": True, "jitter_percent": 20, "interval_mode": "cps",
                             "cps": 10}})
    intervals = [clicker._next_interval() for _ in range(400)]  # noqa: SLF001
    check("拟人抖动有波动且不明显跑偏",
          0.07 < sum(intervals) / len(intervals) < 0.13,
          f"平均 {sum(intervals) / len(intervals) * 1000:.1f} ms")
    config.update({"click": {"humanize": False, "interval_mode": "cps", "cps": 10}})

    start = time.perf_counter()
    precise_sleep(0.05, __import__("threading").Event())
    cost = (time.perf_counter() - start) * 1000
    check("精确定时误差小", 48 <= cost <= 58, f"50ms 实际 {cost:.2f} ms")

    import threading
    event = threading.Event()
    event.set()
    check("被停止时 precise_sleep 立刻返回",
          precise_sleep(1.0, event) is False)

    # ---------- 位置解析 ----------
    from app.clicker import resolve_position
    position = {"x": 111, "y": 222, "relative": False}
    check("绝对坐标解析", resolve_position(position) == (111, 222))
    check("空位置返回 None", resolve_position(None) is None)

    # ---------- 热键钩子 ----------
    from app.hotkeys import HotkeyManager
    actions: list[tuple[str, dict]] = []
    manager = HotkeyManager(config, lambda name, info: actions.append((name, info)))
    manager.is_clicking = lambda: False
    manager.start()
    time.sleep(0.4)
    check("低级钩子安装成功", manager.alive() and bool(manager._hook_kb) and bool(manager._hook_ms),
          f"kb={bool(manager._hook_kb)} ms={bool(manager._hook_ms)}")
    check("热键已解析", len(manager._matchers) >= 6, f"{len(manager._matchers)} 条")
    manager.stop()
    time.sleep(0.3)
    check("钩子卸载干净", not manager.alive() or manager._hook_kb is None)

    # ---------- 录制 ----------
    from app.recorder import Player, Recorder, macro_summary, new_macro
    recorder = Recorder()
    recorder.start(ignore_move=True)
    now = time.perf_counter()
    recorder.feed({"type": "mouse_down", "x": 10, "y": 20, "button": "left", "t": now})
    recorder.feed({"type": "mouse_move", "x": 11, "y": 21, "t": now + 0.01})
    recorder.feed({"type": "key_down", "vk": 65, "t": now + 0.02})
    recorder.feed({"type": "key_up", "vk": 65, "t": now + 0.03})
    macro = recorder.stop()
    check("录制收集到动作", len(macro["events"]) == 3, macro_summary(macro))
    check("忽略鼠标移动生效", all(e["type"] != "mouse_move" for e in macro["events"]))

    player = Player()
    empty = new_macro("空录制")
    player.play(empty, speed=8)
    time.sleep(0.3)
    check("空录制回放不崩溃", True)

    # ---------- 识别任务 ----------
    from app.recognizer import Recognizer, describe_task, new_task
    task = new_task("text", keywords=["立即抢购"])
    check("新建文字任务", describe_task(task) == "找文字「立即抢购」", describe_task(task))
    recognizer = Recognizer(config, clicker)
    check("识别引擎可查询", isinstance(recognizer.engine_label(), str),
          recognizer.engine_label())

    # 用一张假图测试识别流程不抛异常
    tasks = config.get("recognize.tasks") or []
    tasks = [t for t in tasks if t.get("id") != task["id"]]
    config.set("recognize.tasks", tasks)
    try:
        info = recognizer.test_task(task)
        check("测试识别不抛异常", "message" in info, str(info.get("message"))[:60])
    except Exception as exc:
        check("测试识别不抛异常", False, repr(exc))

    # ---------- 界面 ----------
    print("-" * 70)
    print("构建界面…")
    from app.ui.app import App
    started = time.perf_counter()
    app = App()
    app.update()
    build_cost = time.perf_counter() - started
    check("主界面构建成功", True, f"{build_cost:.2f} 秒")

    for name, tab in (("连点", app.tab_click), ("智能识别", app.tab_recognize),
                      ("位置收藏", app.tab_positions), ("录制回放", app.tab_record),
                      ("设置", app.tab_settings)):
        try:
            app.notebook.select(tab)
            for _ in range(3):
                app.update()
                time.sleep(0.03)
            check(f"切换到「{name}」页签", True)
        except Exception as exc:
            check(f"切换到「{name}」页签", False, repr(exc))

    try:
        app.tab_recognize.add_task("text")
        app.update()
        check("界面新建识别任务", len(config.get("recognize.tasks") or []) >= 1)
        task_id = (config.get("recognize.tasks") or [])[-1]["id"]
        config.set("recognize.tasks", [t for t in config.get("recognize.tasks")
                                       if t["id"] != task_id])
    except Exception as exc:
        check("界面新建识别任务", False, repr(exc))

    try:
        app.tab_positions.add_position()
        app.update()
        check("界面添加位置", len(config.positions()) >= 2, f"{len(config.positions())} 个")
    except Exception as exc:
        check("界面添加位置", False, repr(exc))

    try:
        app.tab_click.refresh_positions()
        app.tab_click.update_live(clicker.snapshot())
        app.update()
        check("连点页签刷新", True)
    except Exception as exc:
        check("连点页签刷新", False, repr(exc))

    # ---- 真实鼠标注入（点自己的测试窗口，安全） ----
    import tkinter as tk
    app.withdraw()                     # 先把主界面收起来，别挡住靶子
    for _ in range(6):
        app.update()
        time.sleep(0.05)
    probe = tk.Toplevel(app)
    probe.overrideredirect(True)
    probe.geometry("360x240+120+120")
    probe.configure(bg="#334455")
    hits: list = []
    probe.bind("<Button-1>", lambda e: hits.append((e.x_root, e.y_root)))
    label = tk.Label(probe, text="注入测试窗口", bg="#334455", fg="white")
    label.pack(fill="both", expand=True)
    label.bind("<Button-1>", lambda e: hits.append((e.x_root, e.y_root)))
    probe.update()
    probe_hwnd = probe.winfo_id()
    # 置顶并抢焦点，避免被其它窗口挡住
    W.user32.SetWindowPos(probe_hwnd, -1, 120, 120, 360, 240, 0x0040)
    W.user32.SetForegroundWindow(probe_hwnd)
    for _ in range(10):
        app.update()
        time.sleep(0.05)
    px, py, pw, ph = W.window_rect(probe_hwnd)
    cx, cy = px + pw // 2, py + ph // 2
    at = W.window_from_point(cx, cy)
    print(f"      探针窗口 {probe_hwnd} @ {px},{py} {pw}x{ph}；该点窗口 = {at}")
    W.click("left", 30, cx, cy)
    for _ in range(30):
        app.update()
        time.sleep(0.02)
    check("真实鼠标点击注入生效（SendInput）", len(hits) > 0,
          f"收到 {len(hits)} 次点击 {hits[:2]}" if hits else "未收到（可能被其它窗口挡住）")
    probe.destroy()
    app.deiconify()
    app.update()

    # ---- 连点循环（把真正的点击替换成空操作，避免干扰屏幕） ----
    try:
        original_click = app.clicker._do_click  # noqa: SLF001
        app.clicker._do_click = lambda position=None, once=False: True  # type: ignore
        app.tab_click.num_delay.set(0)
        app.tab_click.var_limit.set("infinite")
        app.tab_click._autosave()  # noqa: SLF001
        app.toggle_clicking()
        for _ in range(25):
            app.update()
            time.sleep(0.03)
        running = app.clicker.is_running()
        count = app.clicker.snapshot()["count"]
        app.emergency_stop("冒烟测试")
        app.update()
        time.sleep(0.2)
        check("连点循环持续计数", running and count > 0, f"运行中={running} 计数={count}")
        check("急停后确实停了", not app.clicker.is_running())
        app.clicker._do_click = original_click  # type: ignore
    except Exception as exc:
        check("连点循环持续计数", False, repr(exc))

    # ---- 倒计时取消 ----
    try:
        app.start_clicking(delay=3)
        app.update()
        time.sleep(0.2)
        app.emergency_stop("取消倒计时")
        for _ in range(10):
            app.update()
            time.sleep(0.05)
        check("倒计时中急停不会偷偷开始点击",
              not app.clicker.is_running() and app._countdown_win is None)  # noqa: SLF001
    except Exception as exc:
        check("倒计时中急停不会偷偷开始点击", False, repr(exc))

    try:
        app.quit_app()
    except Exception as exc:
        check("正常退出", False, repr(exc))
    check("正常退出", True)

    print("=" * 70)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：")
        for item in FAILED:
            print("   -", item)
        return 1
    print("全部通过 ✅")
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        # 保证进程一定退出，避免残留全局钩子干扰用户
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
