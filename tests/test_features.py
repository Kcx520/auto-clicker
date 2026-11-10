"""功能专项测试：位置收藏(F9存点)、录制回放、配置档、计划任务、开机自启、主题切换。

这些是冒烟测试和识别端到端测试没覆盖到的部分，全部走真实界面对象，但把
「真的动鼠标」这一步换成打桩，避免干扰正在用的电脑。
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
    from app import winapi as W

    W.set_dpi_awareness()
    from app.ui.app import App

    app = App()
    app.cfg.set("general.first_run", False)
    app.update()

    # ================= 1. 位置收藏：F9 存点 =================
    for item in app.cfg.positions():
        item["hotkey"] = "已占用"     # 全部占满，验证它会挑"第一个没有热键的"再回退到当前目标
    app.cfg.set("click.position_id", app.cfg.positions()[0]["id"])
    before = (app.cfg.positions()[0].get("x"), app.cfg.positions()[0].get("y"))
    app._save_position_from_hotkey({"x": 137, "y": 246})  # noqa: SLF001
    app.update()
    target = app.cfg.active_position()
    check("F9 存点：坐标写进了当前位置", (target.get("x"), target.get("y")) == (137, 246),
          f"{before} -> {(target.get('x'), target.get('y'))}")

    # 空出一个热键位，验证它优先填进空位
    app.cfg.positions()[1]["hotkey"] = ""
    app._save_position_from_hotkey({"x": 333, "y": 444})  # noqa: SLF001
    app.update()
    check("F9 存点：优先存进没有热键的位置",
          (app.cfg.positions()[1].get("x"), app.cfg.positions()[1].get("y")) == (333, 444),
          f"位置2 = {(app.cfg.positions()[1].get('x'), app.cfg.positions()[1].get('y'))}")

    check("点位写盘后能从配置重新读出来",
          any(p.get("x") == 333 and p.get("y") == 444 for p in app.cfg.positions()))

    # ================= 2. 位置卡片：设为当前目标 =================
    app.tab_positions.refresh()
    app.update()
    card = app.tab_positions.cards[2]
    card.num_x.set(555)
    card.num_y.set(666)
    card.set_active()
    app.update()
    active = app.cfg.active_position()
    check("位置卡片：改坐标并设为当前目标", (active.get("x"), active.get("y")) == (555, 666),
          f"{active.get('name')} ({active.get('x')}, {active.get('y')})")

    # ================= 3. 位置专属热键 =================
    app.cfg.positions()[0]["hotkey"] = "Ctrl+F1"
    app.hotkeys.reload()
    names = [m.get("name") for m in app.hotkeys._matchers]  # noqa: SLF001
    check("位置专属热键被识别成可触发项",
          any(str(n).startswith("position:") for n in names),
          f"共 {len(names)} 条热键，含位置热键")

    # ================= 4. 录制回放 =================
    from app import recorder as recorder_mod
    from app.recorder import Recorder, macro_summary

    rec = Recorder()
    rec.start(ignore_move=False)
    t0 = time.perf_counter()
    rec.feed({"type": "mouse_down", "x": 10, "y": 20, "button": "left", "t": t0})
    time.sleep(0.06)
    rec.feed({"type": "mouse_up", "x": 10, "y": 20, "button": "left", "t": t0 + 0.05})
    time.sleep(0.06)
    rec.feed({"type": "key_down", "vk": 65, "t": t0 + 0.10})
    time.sleep(0.06)
    rec.feed({"type": "key_up", "vk": 65, "t": t0 + 0.12})
    time.sleep(0.06)
    macro = rec.stop()
    check("录制：动作和时间戳都记下来了",
          len(macro["events"]) == 4 and macro["duration"] > 0.1,
          macro_summary(macro))

    class FakeW:
        def __init__(self) -> None:
            self.calls: list = []

        def set_cursor_pos(self, x, y):
            self.calls.append(("move", x, y))

        def mouse_down(self, button="left"):
            self.calls.append(("down", button))

        def mouse_up(self, button="left"):
            self.calls.append(("up", button))

        def wheel(self, notches=1):
            self.calls.append(("wheel", notches))

        def key_down(self, vk):
            self.calls.append(("keydown", vk))

        def key_up(self, vk):
            self.calls.append(("keyup", vk))

    real_w = recorder_mod.W
    fake = FakeW()
    recorder_mod.W = fake
    try:
        from app.recorder import Player
        player = Player()
        started = time.perf_counter()
        player.play(macro, speed=10.0, loop=2, ignore_delay=False)
        while player.is_running() and time.perf_counter() - started < 5:
            app.update()
            time.sleep(0.02)
        elapsed = time.perf_counter() - started
        time.sleep(0.3)
        kinds = [c[0] for c in fake.calls]
        # 每个鼠标事件回放前都会先把光标移回录制时的位置，所以 move 会多出来
        check("回放：两遍动作顺序正确（按下前会先移回原位）",
              kinds == ["move", "down", "move", "up", "keydown", "keyup"] * 2,
              str(kinds))
        check("回放：10 倍速确实更快（宏长 0.12 秒 × 2 遍）", elapsed < 0.5,
              f"实际 {elapsed * 1000:.0f} ms")

        fake.calls.clear()
        player.play(macro, speed=1.0, loop=1, ignore_delay=True)
        while player.is_running() and time.perf_counter() < started + 8:
            app.update()
            time.sleep(0.02)
        check("回放：忽略停顿时几乎瞬间完成", len(fake.calls) == 6, f"{len(fake.calls)} 个动作")
    finally:
        recorder_mod.W = real_w

    # ================= 5. 配置档 =================
    app.cfg.set("click.cps", 7.5)
    app.cfg.save_profile("自动化测试档")
    check("配置档：保存后出现在列表里", "自动化测试档" in app.cfg.profile_names(),
          str(app.cfg.profile_names()))
    app.cfg.set("click.cps", 33.0)
    app.tab_settings.on_show()                      # 刷新配置档下拉框
    app.tab_settings.combo_profile.set("自动化测试档")
    app.tab_settings.load_profile()                 # 走界面路径，应该连带刷新所有页签
    app.update()
    check("配置档：加载后参数被还原", abs(app.cfg.get("click.cps") - 7.5) < 0.01,
          f"cps={app.cfg.get('click.cps')}")
    check("配置档：加载后界面控件同步刷新",
          abs(app.tab_click.num_cps.get() - 7.5) < 0.01,
          f"界面上的值={app.tab_click.num_cps.get()}")

    export_path = os.path.join(ROOT, "config", "导出测试.acp")
    app.cfg.export_to(export_path)
    app.cfg.set("click.cps", 99.0)
    imported = app.cfg.import_from(export_path)
    check("配置档：导出再导入，参数一致",
          imported and abs(app.cfg.get("click.cps") - 7.5) < 0.01,
          f"cps={app.cfg.get('click.cps')}")
    try:
        os.remove(export_path)
    except OSError:
        pass

    # ================= 6. 计划任务 =================
    original_click = app.clicker._do_click  # noqa: SLF001
    app.clicker._do_click = lambda position=None, once=False: True  # type: ignore
    try:
        app._schedule_fired = False  # noqa: SLF001
        app._app_started = time.time() - 10  # noqa: SLF001
        app.cfg.update({"schedule": {"enabled": True, "mode": "delay", "delay_minutes": 0,
                                     "run_minutes": 0}})
        app._check_schedule()  # noqa: SLF001
        for _ in range(6):
            app.update()
            time.sleep(0.05)
        fired = app._countdown_win is not None or app.clicker.is_running()  # noqa: SLF001
        check("计划任务：到点自动开始（进入了启动倒计时）", fired,
              f"倒计时窗口={'有' if app._countdown_win else '无'}")  # noqa: SLF001

        app.emergency_stop("取消上一次倒计时")
        for _ in range(4):
            app.update()
            time.sleep(0.05)
        check("急停能取消掉倒计时窗口", app._countdown_win is None)  # noqa: SLF001

        app.cfg.update({"schedule": {"mode": "once", "time": "00:00"}})
        app._schedule_fired = False  # noqa: SLF001
        app._check_schedule()  # noqa: SLF001
        check("计划任务：时间没到不会乱触发", app._countdown_win is None)  # noqa: SLF001

        app.cfg.update({"schedule": {"enabled": False}})
        app.emergency_stop("测试结束")
        for _ in range(4):
            app.update()
            time.sleep(0.05)
        check("计划任务：关掉开关后不再触发", app._countdown_win is None)  # noqa: SLF001
    finally:
        app.clicker._do_click = original_click  # type: ignore
        app.cfg.update({"schedule": {"enabled": False}})
        app.emergency_stop("测试收尾")

    # ================= 7. 开机自启（注册表写入，沙箱可能禁止） =================
    try:
        import winreg
        key = r"Software\Microsoft\Windows\CurrentVersion\Run"

        def read_run() -> str:
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as handle:
                    return winreg.QueryValueEx(handle, "AutoClickerPro")[0]
            except Exception:
                return ""

        existed = read_run()
        app.tab_settings.check_autostart.set(True)
        app.tab_settings._toggle_autostart()  # noqa: SLF001
        written = read_run()
        app.tab_settings.check_autostart.set(False)
        app.tab_settings._toggle_autostart()  # noqa: SLF001
        removed = read_run()
        if written:
            check("开机自启：能写入也能撤销（已恢复原状）", removed == existed,
                  f"写入={written[:60]!r} 撤销后={'空' if not removed else removed[:40]!r}")
        else:
            # 沙箱禁止写 HKCU 注册表，这里只能验证"失败被优雅处理、没有崩"
            can_write = False
            try:
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key, 0,
                                    winreg.KEY_SET_VALUE) as handle:
                    winreg.SetValueEx(handle, "DSH_Probe", 0, winreg.REG_SZ, "x")
                    winreg.DeleteValue(handle, "DSH_Probe")
                    can_write = True
            except Exception:
                can_write = False
            check("开机自启：注册表不可写时不会崩（当前沙箱禁止写）",
                  not can_write and removed == existed,
                  "沙箱禁止写 HKCU\\...\\Run，实机上可用")
    except Exception as exc:
        check("开机自启：注册表不可写时不会崩（当前沙箱禁止写）", False, repr(exc))

    # ================= 8. 主题切换（就地换色，所有模块都跟着变） =================
    from app.ui import theme as theme_mod
    from app.ui import widgets as widgets_mod

    check("主题：模块共享同一个配色字典", widgets_mod.COLORS is theme_mod.COLORS)
    try:
        theme_mod.init(app, "light")
        light_ok = (widgets_mod.COLORS["bg"] == theme_mod.LIGHT["bg"]
                    and theme_mod.COLORS is widgets_mod.COLORS)
        theme_mod.init(app, "dark")
        dark_ok = widgets_mod.COLORS["bg"] == theme_mod.DARK["bg"]
        check("主题：浅色/深色切换会传导到所有组件", light_ok and dark_ok,
              f"浅色 bg={theme_mod.LIGHT['bg']}  深色 bg={theme_mod.DARK['bg']}")
    except Exception as exc:
        check("主题：浅色/深色切换会传导到所有组件", False, repr(exc))

    # ================= 9. 单实例互斥 / 退出清理 =================
    import ctypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    handle = kernel32.CreateMutexW(None, False, "Global\\AutoClickerPro_SingleInstance")
    second = kernel32.CreateMutexW(None, False, "Global\\AutoClickerPro_SingleInstance")
    check("单实例：第二次创建能检测到已存在",
          bool(handle) and ctypes.get_last_error() == 183,
          f"GetLastError={ctypes.get_last_error()}")

    # ================= 10. 热键回调的线程安全（钩子线程里不能碰 Tk） =================
    import threading

    original_click2 = app.clicker._do_click  # noqa: SLF001
    app.clicker._do_click = lambda position=None, once=False: True  # type: ignore
    app.emergency_stop("线程测试准备")
    for _ in range(5):
        app.update()
        time.sleep(0.05)

    results: dict = {}

    def hotkey_worker() -> None:
        """模拟钩子派发线程调用热键回调。"""
        try:
            app._on_hotkey("toggle", {})          # noqa: SLF001
            results["toggle"] = "ok"
        except Exception as exc:
            results["toggle"] = repr(exc)
        time.sleep(0.1)
        try:
            app._on_hotkey("emergency_stop", {"reason": "线程测试急停"})  # noqa: SLF001
            results["stop"] = "ok"
        except Exception as exc:
            results["stop"] = repr(exc)
        results["stopped_immediately"] = not app.clicker.is_running()

    worker = threading.Thread(target=hotkey_worker, name="fake-hotkey-thread")
    worker.start()
    worker.join()
    check("热键回调在钩子线程里执行不会碰 Tk（无异常）",
          results.get("toggle") == "ok" and results.get("stop") == "ok", str(results))
    check("急停在钩子线程里立刻生效（不等主线程）",
          bool(results.get("stopped_immediately")))

    for _ in range(12):
        app.update()
        time.sleep(0.06)
    check("热键动作经主线程执行后状态正确",
          not app.clicker.is_running() and app._countdown_win is None,  # noqa: SLF001
          f"运行中={app.clicker.is_running()} 倒计时={app._countdown_win is not None}")  # noqa: SLF001
    app.clicker._do_click = original_click2  # type: ignore
    app.emergency_stop("线程测试收尾")
    for _ in range(4):
        app.update()
        time.sleep(0.05)

    app.quit_app()
    print("=" * 70)
    if FAILED:
        print(f"失败 {len(FAILED)} 项：")
        for item in FAILED:
            print("   -", item)
        return 1
    print("功能专项测试全部通过 ✅")
    return 0


if __name__ == "__main__":
    code = 1
    try:
        code = main()
    finally:
        sys.stdout.flush()
        sys.stderr.flush()
        os._exit(code)
