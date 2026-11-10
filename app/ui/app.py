"""主界面与应用逻辑。"""

from __future__ import annotations

import os
import queue
import sys
import threading
import time
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Any, Callable, Dict, Optional, Tuple

from .. import screen as S
from .. import winapi as W
from ..clicker import Clicker
from ..config import APP_TITLE, ASSETS_DIR, config, ensure_dirs
from ..hotkeys import HotkeyManager
from ..recognizer import Recognizer, describe_task
from ..recorder import Player, Recorder, new_macro
from . import overlay, theme
from .theme import COLORS, FONTS, dp
from .widgets import LogBox, StatusDot

from .tabs_click import ClickTab
from .tabs_positions import PositionsTab
from .tabs_recognize import RecognizeTab
from .tabs_record import RecordTab
from .tabs_settings import SettingsTab


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        ensure_dirs()
        self.cfg = config
        theme.init(self, self.cfg.get("general.theme", "dark"))

        self.title(APP_TITLE)
        self.configure(bg=COLORS["bg"])
        try:
            icon = ASSETS_DIR / "icon.ico"
            if icon.exists():
                self.iconbitmap(default=str(icon))
        except Exception:
            pass
        self.minsize(dp(960), dp(700))
        geometry = self.cfg.get("general.window_geometry", "")
        if geometry:
            try:
                self.geometry(geometry)
            except Exception:
                self._default_geometry()
        else:
            self._default_geometry()

        self._ui_queue: "queue.Queue[tuple]" = queue.Queue()
        self._capture_target: Optional[str] = None
        self._capture_button = None
        self._quitting = False
        self._schedule_fired = False
        self._app_started = time.time()
        self._last_snapshot: Dict[str, Any] = {}
        self._recognize_started_with_click = False
        self._countdown_win = None
        self._mini = None
        self._tray = None

        # ---- 引擎 ----
        self.clicker = Clicker(self.cfg, on_event=self._on_engine_event)
        self.hotkeys = HotkeyManager(self.cfg, self._on_hotkey, on_record=self._record_event)
        self.hotkeys.is_clicking = self.clicker.is_running
        self.hotkeys.self_move_age = self.clicker.self_move_age
        self.recorder = Recorder()
        self.player = Player(on_log=lambda t: self.log(t), on_finish=self._on_playback_done)
        self.recognizer = Recognizer(self.cfg, self.clicker,
                                     on_log=lambda t: self.log(t, "ok"),
                                     on_hit=self._on_recognize_hit)

        # ---- 界面 ----
        self._build_header()
        self._build_tabs()
        self._build_footer()
        self.marker = overlay.MarkerOverlay(self)
        self.marker.set_source(self._marker_source)

        self.hotkeys.start()
        if self.cfg.get("general.show_overlay"):
            self.marker.show()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<F6>", lambda _e: self.toggle_clicking())
        self.bind("<Escape>", lambda _e: self.emergency_stop())
        self.after(80, self._drain_ui)
        self.after(200, self._tick)
        self.log("欢迎使用连点器！鼠标放到目标处按 F9 就能存下位置。", "ok")
        self.log("按 F6 开始 / 暂停，ESC 紧急停止。", "ok")
        self._maybe_first_run()

    # ==================================================================
    # 界面搭建
    # ==================================================================
    def _default_geometry(self) -> None:
        width, height = dp(1020), dp(760)
        try:
            vx, vy, vw, vh = W.virtual_screen_rect()
            x = vx + max(0, (vw - width) // 2)
            y = vy + max(0, (vh - height) // 3)
            self.geometry(f"{width}x{height}+{x}+{y}")
        except Exception:
            self.geometry(f"{width}x{height}")

    def _build_header(self) -> None:
        header = tk.Frame(self, bg=COLORS["card"])
        header.pack(fill="x", side="top")
        inner = tk.Frame(header, bg=COLORS["card"])
        inner.pack(fill="x", padx=dp(14), pady=dp(10))

        left = tk.Frame(inner, bg=COLORS["card"])
        left.pack(side="left", anchor="w")
        self.status_dot = StatusDot(left, 14)
        self.status_dot.pack(side="left", padx=(0, dp(8)))
        self.status_text = tk.StringVar(value="就绪")
        tk.Label(left, textvariable=self.status_text, bg=COLORS["card"], fg=COLORS["fg"],
                 font=FONTS["big"]).pack(side="left")
        self.stat_inline = tk.StringVar(value="已点击 0 次 · 0.0 次/秒 · 00:00")
        tk.Label(left, textvariable=self.stat_inline, bg=COLORS["card"], fg=COLORS["muted"],
                 font=FONTS["small"]).pack(side="left", padx=dp(14))

        right = tk.Frame(inner, bg=COLORS["card"])
        right.pack(side="right")
        self.btn_toggle = theme.tk_button(right, "开始 (F6)", self.toggle_clicking, "accent",
                                          font=FONTS["big"], padx=dp(20), pady=dp(8))
        self.btn_toggle.pack(side="left", padx=dp(4))
        self.btn_emergency = theme.tk_button(right, "紧急停止 (ESC)", self.emergency_stop, "danger",
                                             padx=dp(14), pady=dp(8))
        self.btn_emergency.pack(side="left", padx=dp(4))
        theme.tk_button(right, "迷你面板", self.toggle_mini, "ghost",
                        padx=dp(10), pady=dp(8)).pack(side="left", padx=dp(4))
        theme.tk_button(right, "隐藏到托盘", self.hide_to_tray, "ghost",
                        padx=dp(10), pady=dp(8)).pack(side="left", padx=dp(4))

    def _build_tabs(self) -> None:
        self.notebook = ttk.Notebook(self)
        self.notebook.pack(fill="both", expand=True, padx=dp(10), pady=(dp(8), dp(4)))
        self.tab_click = ClickTab(self.notebook, self)
        self.tab_recognize = RecognizeTab(self.notebook, self)
        self.tab_positions = PositionsTab(self.notebook, self)
        self.tab_record = RecordTab(self.notebook, self)
        self.tab_settings = SettingsTab(self.notebook, self)
        self.notebook.add(self.tab_click, text="连点")
        self.notebook.add(self.tab_recognize, text="智能识别")
        self.notebook.add(self.tab_positions, text="位置收藏")
        self.notebook.add(self.tab_record, text="录制回放")
        self.notebook.add(self.tab_settings, text="设置")
        self.notebook.bind("<<NotebookTabChanged>>", self._on_tab_changed)

    def _on_tab_changed(self, _event=None) -> None:
        try:
            current = self.notebook.nametowidget(self.notebook.select())
        except Exception:
            return
        hook = getattr(current, "on_show", None)
        if callable(hook):
            try:
                hook()
            except Exception as exc:
                self.log(f"刷新页签出错：{exc}", "error")

    def _build_footer(self) -> None:
        footer = tk.Frame(self, bg=COLORS["bg"])
        footer.pack(fill="x", side="bottom")
        self.logbox = LogBox(footer, height=5)
        self.logbox.pack(fill="x", padx=dp(10), pady=(0, dp(4)))
        bar = tk.Frame(footer, bg=COLORS["bg"])
        bar.pack(fill="x", padx=dp(12), pady=(0, dp(6)))
        self.footer_text = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.footer_text, bg=COLORS["bg"], fg=COLORS["muted"],
                 font=FONTS["small"]).pack(side="left")
        self.recognize_text = tk.StringVar(value="")
        tk.Label(bar, textvariable=self.recognize_text, bg=COLORS["bg"], fg=COLORS["muted"],
                 font=FONTS["small"]).pack(side="right")

    # ==================================================================
    # 线程安全工具
    # ==================================================================
    def ui_call(self, func: Callable, *args, **kwargs) -> None:
        self._ui_queue.put((func, args, kwargs))

    def _drain_ui(self) -> None:
        for _ in range(40):
            try:
                func, args, kwargs = self._ui_queue.get_nowait()
            except queue.Empty:
                break
            try:
                func(*args, **kwargs)
            except Exception as exc:
                self.log(f"界面刷新出错：{exc}", "error")
        if not self._quitting:
            self.after(80, self._drain_ui)

    def log(self, message: str, level: str = "info") -> None:
        try:
            self.logbox.log(message, level)
        except Exception:
            pass

    # ==================================================================
    # 引擎回调
    # ==================================================================
    def _on_engine_event(self, kind: str, payload: Any) -> None:
        if kind == "log":
            self.log(str(payload))
        elif kind == "count":
            self.ui_call(self._update_stats, payload if isinstance(payload, dict) else {})
        elif kind == "state":
            self.ui_call(self._update_state)
        elif kind == "finished":
            info = payload if isinstance(payload, dict) else {}
            self.log(f"连点结束：{info.get('reason', '')}", "warn")
            self.ui_call(self._update_state)

    def _on_recognize_hit(self, task: Dict[str, Any], hit: Dict[str, Any]) -> None:
        self.ui_call(self.tab_recognize.refresh_task_list)

    def _on_playback_done(self) -> None:
        self.ui_call(self.tab_record.on_playback_done)

    def _record_event(self, event: Dict[str, Any]) -> None:
        self.recorder.feed(event)

    # ==================================================================
    # 热键动作
    # ==================================================================
    def _on_hotkey(self, name: str, info: Dict[str, Any]) -> None:
        """热键回调——注意：这个函数跑在钩子派发线程里，**绝对不能直接碰 Tk 控件**。

        凡是会动界面的动作（开始/停止、倒计时窗口、弹框、状态刷新）都必须
        通过 ``ui_call`` 丢回主线程执行，否则 Tk 会报
        "main thread is not in main loop" 并可能直接崩掉。
        """
        if name == "captured":
            self.ui_call(self._finish_capture, info)
            return

        if name == "emergency_stop":
            # 急停要最快生效：先在当前线程直接把引擎停掉（线程安全），界面善后再回主线程
            reason = info.get("reason") or "紧急停止"
            self.clicker.stop(reason)
            self.recognizer.stop()
            self.player.stop()
            self.ui_call(self._after_emergency_stop, reason)
            return

        if name == "once":
            self.clicker.click_once()
            self.ui_call(self.log, "单次点击")
            return

        if name == "save_position":
            self.ui_call(self._save_position_from_hotkey, info)
            return

        if name == "toggle_window":
            self.ui_call(self.toggle_window)
            return

        if name.startswith("position:"):
            self._hotkey_position(name.split(":", 1)[1])
            return

        # toggle / delayed_start / hold_start / hold_stop / quit
        self.ui_call(self._hotkey_ui_action, name)

    def _hotkey_ui_action(self, name: str) -> None:
        """热键动作里需要操作界面的部分，统一在主线程执行。"""
        if name == "toggle":
            self.toggle_clicking()
        elif name == "delayed_start":
            self.start_clicking(delay=3.0)
        elif name == "hold_start":
            if not self.clicker.is_running():
                self.start_clicking(delay=0)
        elif name == "hold_stop":
            if self.clicker.is_running():
                self.stop_clicking("松开按键")
        elif name == "quit":
            self.quit_app()

    def _after_emergency_stop(self, reason: str) -> None:
        """急停的界面善后，在主线程执行。

        这里要**再停一次**：急停请求发出时可能还有更早排队的"开始连点"动作没执行完，
        急停的语义是"停下来"，所以不管队列顺序如何，处理完必须是停止状态。
        """
        self._cancel_countdown()
        if self.clicker.is_running():
            self.clicker.stop(reason)
        if self.recognizer.is_running():
            self.recognizer.stop()
        if self.player.is_running():
            self.player.stop()
        if self._recognize_started_with_click:
            self._recognize_started_with_click = False
        if self.recorder.is_recording():
            self.stop_recording()
        self.log(f"{reason}：已全部停止", "warn")
        self._update_state()

    def _save_position_from_hotkey(self, info: Dict[str, Any]) -> None:
        x = int(info.get("x", 0))
        y = int(info.get("y", 0))
        target = None
        for item in self.cfg.positions():
            if not item.get("hotkey"):
                target = item
                break
        if target is None:
            target = self.cfg.active_position()
        if target is None:
            return
        hwnd = W.foreground_window()
        target["x"], target["y"] = x, y
        target["window_title"] = W.window_title(hwnd)
        target["hwnd"] = hwnd
        if target.get("relative") and hwnd:
            cx, cy, _, _ = W.client_rect_on_screen(hwnd)
            target["offset_x"], target["offset_y"] = x - cx, y - cy
        self.cfg.save()
        self.log(f"已保存位置「{target.get('name')}」= ({x}, {y})", "ok")
        self.tab_positions.refresh()
        self.tab_click.refresh_positions()
        self.marker.refresh()

    def _hotkey_position(self, position_id: str) -> None:
        item = self.cfg.find_position(position_id)
        if not item:
            return
        from ..clicker import resolve_position
        point = resolve_position(item)
        if not point:
            return
        button = self.cfg.get("click.button", "left")
        W.click(button, int(self.cfg.get("click.hold_ms", 20) or 20), point[0], point[1])
        self.log(f"热键点击「{item.get('name')}」→ {point}")

    def begin_capture(self, hotkey_name: str, button=None) -> None:
        self._capture_target = hotkey_name
        self._capture_button = button
        self.hotkeys.capture_target = hotkey_name
        self.log("请按下你想设置的热键…（鼠标侧键也可以）")

    def _finish_capture(self, info: Dict[str, Any]) -> None:
        name = self._capture_target
        self._capture_target = None
        self._capture_button = None
        if not name:
            return
        text = W.format_hotkey(info.get("mods") or set(), info.get("vk"), info.get("mouse"))
        if not text:
            self.log("这个按键不能用作热键，换一个试试", "warn")
            return
        if name.startswith("position:"):
            position_id = name.split(":", 1)[1]
            item = self.cfg.find_position(position_id)
            if item is not None:
                item["hotkey"] = text
            self.cfg.save()
            self.hotkeys.reload()
            self.tab_positions.show_captured(name, text)
            self.log(f"位置专属热键已更新：{text}", "ok")
            return
        self.cfg.set(f"hotkeys.{name}", text)
        self.cfg.save()
        self.hotkeys.reload()
        self.log(f"热键已更新：{text}", "ok")
        self.tab_settings.refresh_hotkeys()

    # ==================================================================
    # 连点控制
    # ==================================================================
    def toggle_clicking(self) -> None:
        if self.clicker.is_running():
            self.stop_clicking("手动停止")
        else:
            self.start_clicking()

    def start_clicking(self, delay: Optional[float] = None) -> None:
        if self.clicker.is_running():
            return
        cfg = self.cfg
        cps = float(cfg.get("click.cps", 10) or 10)
        threshold = float(cfg.get("safety.cps_confirm_above", 50) or 50)
        if cfg.get("click.interval_mode") == "cps" and cps > threshold:
            if not messagebox.askyesno("速度太快了", f"你要每秒点 {cps:g} 次，可能会让电脑卡住。\n确定要继续吗？"):
                return
        if cfg.get("click.foreground_only") and not cfg.get("click.target_window_title"):
            self.log("勾选了「只在前台窗口点击」，但没设置目标窗口，已忽略该限制", "warn")
        if delay is None:
            delay = float(cfg.get("click.start_delay_s", 0) or 0)
        if delay and delay > 0:
            self._countdown_win = overlay.countdown_window(
                self, int(delay) if delay >= 1 else 1,
                on_done=lambda: self.clicker.start(delay=0))
            self.log(f"{delay:g} 秒后开始…")
        else:
            self.clicker.start(delay=0)
        if cfg.get("recognize.enabled") and not self.recognizer.is_running():
            self.recognizer.start()
            self._recognize_started_with_click = True

    def stop_clicking(self, reason: str = "手动停止") -> None:
        self._cancel_countdown()
        if self.clicker.is_running():
            self.clicker.stop(reason)
        if self._recognize_started_with_click and self.recognizer.is_running():
            self.recognizer.stop()
            self._recognize_started_with_click = False

    def _cancel_countdown(self) -> None:
        win = self._countdown_win
        self._countdown_win = None
        if win is None:
            return
        try:
            win.cancel()  # type: ignore[attr-defined]
        except Exception:
            try:
                win.destroy()
            except Exception:
                pass

    def emergency_stop(self, reason: str = "紧急停止") -> None:
        self._cancel_countdown()
        stopped = False
        if self.clicker.is_running():
            self.clicker.stop(reason)
            stopped = True
        if self.recognizer.is_running():
            self.recognizer.stop()
            stopped = True
        if self.player.is_running():
            self.player.stop()
            stopped = True
        if self.recorder.is_recording():
            self.stop_recording()
        self.log(f"{reason}：已全部停止" if stopped else f"{reason}（当前没有任务在跑）",
                 "warn" if stopped else "info")
        self._update_state()

    # ==================================================================
    # 界面刷新
    # ==================================================================
    def _tick(self) -> None:
        self._update_state()
        self._update_stats(self.clicker.snapshot())
        self._check_schedule()
        positions = self.cfg.positions()
        active = self.cfg.active_position()
        pointer = f"当前点位：{active.get('name')} ({active.get('x')}, {active.get('y')})" if active else "未设置点位"
        tasks = self.cfg.get_ref("recognize.tasks", []) or []
        enabled = sum(1 for t in tasks if t.get("enabled", True))
        engine = self.recognizer.engine_label() if tasks else "未使用"
        self.footer_text.set(f"{pointer}    ·    热键 F6 开始 / ESC 急停")
        self.recognize_text.set(f"识别任务 {enabled}/{len(tasks)} 条启用    ·    OCR 引擎：{engine}")
        if not self._quitting:
            self.after(400, self._tick)

    def _update_state(self) -> None:
        running = self.clicker.is_running()
        self.status_dot.set_state("running" if running else "stopped")
        self.status_text.set("运行中" if running else "就绪")
        self.btn_toggle.configure(text="停止 (F6)" if running else "开始 (F6)",
                                  bg=COLORS["danger"] if running else COLORS["accent"])

    def _update_stats(self, snap: Dict[str, Any]) -> None:
        self._last_snapshot = snap
        count = snap.get("count", 0)
        cps = snap.get("cps", 0)
        elapsed = snap.get("elapsed_text", "00:00")
        remaining = snap.get("remaining", "")
        countdown = snap.get("countdown", 0)
        if countdown and countdown > 0:
            self.stat_inline.set(f"{countdown:.1f} 秒后开始…")
        else:
            self.stat_inline.set(f"已点击 {count} 次 · {cps} 次/秒 · {elapsed} · {remaining}")
        try:
            self.tab_click.update_live(snap)
        except Exception:
            pass

    def _marker_source(self) -> Dict[str, Any]:
        marks = []
        active = self.cfg.active_position()
        for item in self.cfg.positions():
            marks.append({
                "name": item.get("name", ""),
                "x": item.get("x", 0),
                "y": item.get("y", 0),
                "active": bool(active and item.get("id") == active.get("id")),
            })
        return {"region": self.cfg.get("recognize.region"), "marks": marks}

    # ==================================================================
    # 屏幕交互
    # ==================================================================
    def select_region(self, callback: Callable[[Optional[Tuple[int, int, int, int]]], None]) -> None:
        try:
            self.withdraw()
        except Exception:
            pass
        self.after(180, lambda: overlay.RegionSelector(self, self._region_done_wrapper(callback)))

    def _region_done_wrapper(self, callback):
        def done(region):
            try:
                self.deiconify()
                self.lift()
            except Exception:
                pass
            callback(region)
        return done

    def pick_point(self, callback: Callable[[Optional[Tuple[int, int]]], None]) -> None:
        try:
            self.withdraw()
        except Exception:
            pass

        def done(point):
            try:
                self.deiconify()
                self.lift()
            except Exception:
                pass
            callback(point)

        self.after(180, lambda: overlay.PointPicker(self, done))

    def flash_at(self, x: int, y: int, w: int = 0, h: int = 0, text: str = "") -> None:
        overlay.flash_box(self, x, y, max(w, dp(30)), max(h, dp(20)), text=text)

    # ==================================================================
    # 迷你面板 / 托盘
    # ==================================================================
    def toggle_mini(self) -> None:
        if self._mini is not None:
            try:
                self._mini.destroy()
            except Exception:
                pass
            self._mini = None
            return
        try:
            from .mini_panel import MiniPanel
            self._mini = MiniPanel(self)
        except Exception as exc:
            self.log(f"迷你面板打开失败：{exc}", "error")

    def hide_to_tray(self) -> None:
        self.withdraw()
        if self._tray is None:
            try:
                from .tray import TrayIcon
                self._tray = TrayIcon(self, on_show=self.show_window, on_toggle=self.toggle_clicking,
                                      on_stop=self.emergency_stop, on_quit=self.quit_app)
                self._tray.start()
            except Exception as exc:
                self.log(f"托盘图标不可用：{exc}", "warn")
                self.show_window()
                return
        self.log("已隐藏到托盘，双击托盘图标可以打开界面")

    def show_window(self) -> None:
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except Exception:
            pass

    def toggle_window(self) -> None:
        try:
            if self.state() == "withdrawn":
                self.show_window()
            else:
                self.withdraw()
        except Exception:
            pass

    # ==================================================================
    # 录制
    # ==================================================================
    def start_recording(self) -> None:
        self.recorder.start(ignore_move=bool(self.cfg.get("record.ignore_move")))
        self.log("开始录制，去做你要录的操作，回来点「停止录制」", "ok")

    def stop_recording(self) -> Dict[str, Any]:
        macro = self.recorder.stop()
        if not macro.get("events"):
            self.log("这次没录到任何动作", "warn")
            return macro
        macros = self.cfg.get_ref("record.macros", []) or []
        macros.append(macro)
        self.cfg.set("record.macros", macros)
        self.cfg.save()
        self.log(f"录制完成：{macro['name']}（{len(macro['events'])} 个动作）", "ok")
        return macro

    # ==================================================================
    # 计划任务
    # ==================================================================
    def _check_schedule(self) -> None:
        schedule = self.cfg.get("schedule") or {}
        if not schedule.get("enabled"):
            self._schedule_fired = False
            return

        now = time.localtime()
        mode = schedule.get("mode", "once")
        try:
            hour, minute = (int(v) for v in str(schedule.get("time", "12:00")).split(":")[:2])
        except Exception:
            hour = minute = -1

        if self._schedule_fired:
            # 已经触发过。定时模式要等"离开这个时间点"才能再来一次（每天触发一次）；
            # 延迟模式触发一次就结束，想重新计时就把开关关掉再打开。
            if mode != "delay" and not (now.tm_hour == hour and now.tm_min == minute):
                self._schedule_fired = False
            return

        if mode == "delay":
            minutes = float(schedule.get("delay_minutes", 5) or 0)
            fire = (time.time() - self._app_started) >= minutes * 60
        else:
            fire = (hour, minute) == (now.tm_hour, now.tm_min)

        if fire and not self.clicker.is_running():
            self._schedule_fired = True
            self.log("计划任务触发，开始连点", "ok")
            self.start_clicking(delay=3)
            run_minutes = float(schedule.get("run_minutes", 0) or 0)
            if run_minutes > 0:
                self.after(int(run_minutes * 60 * 1000),
                           lambda: self.stop_clicking("计划任务到时自动停止"))

    # ==================================================================
    # 首次引导 / 退出
    # ==================================================================
    def _maybe_first_run(self) -> None:
        if not self.cfg.get("general.first_run", True):
            return
        self.cfg.set("general.first_run", False)
        self.cfg.save()
        self.after(400, self._show_guide)

    def _show_guide(self) -> None:
        guide = tk.Toplevel(self)
        guide.title("三步就能用")
        guide.configure(bg=COLORS["bg"])
        guide.attributes("-topmost", True)
        width, height = dp(560), dp(430)
        try:
            vx, vy, vw, vh = W.virtual_screen_rect()
            guide.geometry(f"{width}x{height}+{vx + (vw - width) // 2}+{vy + (vh - height) // 3}")
        except Exception:
            guide.geometry(f"{width}x{height}")
        frame = tk.Frame(guide, bg=COLORS["bg"], padx=dp(22), pady=dp(18))
        frame.pack(fill="both", expand=True)
        tk.Label(frame, text="三步就能用", bg=COLORS["bg"], fg=COLORS["fg"],
                 font=FONTS["title"]).pack(anchor="w")
        steps = [
            ("第 1 步：存下要点的位置",
             "把鼠标移到要点击的地方，按一下 F9，位置就存好了。\n"
             "想存多个位置，切到「位置收藏」页签，点「拾取」也能存。"),
            ("第 2 步：设定速度",
             "在「连点」页签填「每秒点几下」，不想管就用默认的 10 次/秒。"),
            ("第 3 步：按 F6 开始",
             "任何时候按 ESC 立刻停止，鼠标甩到屏幕左上角也会停。"),
            ("想让它看见文字/图片自动点？",
             "切到「智能识别」页签：输入要找的文字（比如「立即抢购」），\n"
             "选好命中后点哪个位置，点「开始识别」就行，全程不用写代码。"),
        ]
        for title, text in steps:
            tk.Label(frame, text=title, bg=COLORS["bg"], fg=COLORS["accent"],
                     font=FONTS["bold"]).pack(anchor="w", pady=(dp(10), dp(2)))
            tk.Label(frame, text=text, bg=COLORS["bg"], fg=COLORS["muted"], justify="left",
                     wraplength=dp(500), font=FONTS["base"]).pack(anchor="w")
        theme.tk_button(frame, "知道了", guide.destroy, "accent",
                        padx=dp(24), pady=dp(8)).pack(pady=dp(16))

    def quit_app(self) -> None:
        if self._quitting:
            return
        self._quitting = True
        try:
            self.cfg.set("general.window_geometry", self.geometry())
            self.cfg.save()
        except Exception:
            pass
        for closer in (self.clicker.stop, self.recognizer.stop, self.player.stop,
                       self.hotkeys.stop):
            try:
                closer()
            except Exception:
                pass
        if self._tray is not None:
            try:
                self._tray.stop()
            except Exception:
                pass
        try:
            self.marker.hide()
        except Exception:
            pass
        try:
            self.destroy()
        except Exception:
            pass

    def _on_close(self) -> None:
        if self.cfg.get("general.minimize_to_tray", True) and self._tray is not None:
            self.hide_to_tray()
            return
        self.quit_app()
