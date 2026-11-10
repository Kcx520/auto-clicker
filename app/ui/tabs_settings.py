"""「设置」页签：热键、安全、通用、配置档、计划任务。"""

from __future__ import annotations

import os
import sys
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List

from .. import winapi as W
from ..config import (ASSETS_DIR, CONFIG_DIR, HOTKEY_LABELS, Config, DEFAULT_HOTKEYS)
from . import theme
from .theme import COLORS, FONTS, dp
from .widgets import (Card, CheckField, ComboField, HotkeyButton, NumberField, ScrollFrame,
                      TextField, hint)

CORNER_KEYS = ["topleft", "topright", "bottomleft", "bottomright"]
CORNER_LABELS = ["屏幕左上角", "屏幕右上角", "屏幕左下角", "屏幕右下角"]

THEME_KEYS = ["dark", "light"]
THEME_LABELS = ["深色", "浅色"]

SCHEDULE_KEYS = ["once", "daily", "delay"]
SCHEDULE_LABELS = ["指定时刻开始（今天）", "每天固定时间", "N 分钟后开始"]


class SettingsTab(ttk.Frame):
    def __init__(self, parent, app) -> None:
        super().__init__(parent, style="TFrame")
        self.app = app
        self.cfg = app.cfg
        self.hotkey_buttons: Dict[str, HotkeyButton] = {}

        scroll = ScrollFrame(self)
        scroll.pack(fill="both", expand=True)
        self.scroll = scroll
        holder = scroll.inner

        self._build_hotkeys(holder)
        self._build_safety(holder)
        self._build_general(holder)
        self._build_profiles(holder)
        self._build_schedule(holder)
        self._build_about(holder)
        self.after(1200, self._autosave)

    # ------------------------------------------------------------------
    def _build_hotkeys(self, parent) -> None:
        card = Card(parent, title="全局热键",
                    subtitle="点一下右边的按钮，然后直接按下你想用的键即可（支持 Ctrl+Alt+Q 这种组合，"
                             "也支持鼠标侧键）。")
        card.pack(fill="x", pady=dp(6), padx=dp(2))
        body = card.body
        for index, (name, label) in enumerate(HOTKEY_LABELS.items()):
            ttk.Label(body, text=label, style="Card.TLabel").grid(row=index, column=0, sticky="w",
                                                                  pady=dp(3))
            button = HotkeyButton(body, (lambda n=name: self.cfg.get(f"hotkeys.{n}", "")),
                                  (lambda n=name: self.app.begin_capture(n)))
            button.grid(row=index, column=1, sticky="w", padx=dp(8), pady=dp(3))
            self.hotkey_buttons[name] = button
        theme.tk_button(body, "恢复默认热键", self.reset_hotkeys, "ghost").grid(
            row=len(HOTKEY_LABELS), column=0, sticky="w", pady=(dp(10), 0))

    def reset_hotkeys(self) -> None:
        self.cfg.set("hotkeys", dict(DEFAULT_HOTKEYS))
        self.cfg.save()
        self.app.hotkeys.reload()
        self.refresh_hotkeys()
        self.app.log("热键已恢复默认", "ok")

    def refresh_hotkeys(self) -> None:
        for name, button in self.hotkey_buttons.items():
            button.refresh()
        try:
            self.app.tab_positions.refresh_hotkeys()
        except Exception:
            pass

    # ------------------------------------------------------------------
    def _build_safety(self, parent) -> None:
        card = Card(parent, title="安全保护")
        card.pack(fill="x", pady=dp(6), padx=dp(2))
        body = card.body
        self.check_emergency = CheckField(body, "启用急停（ESC 键）", True,
                                          help="任何时候按 ESC 都会立刻停下所有任务")
        self.check_emergency.grid(row=0, column=0, columnspan=3, sticky="w", pady=dp(3))
        self.check_corner = CheckField(body, "鼠标甩到屏幕角落也急停", True)
        self.check_corner.grid(row=1, column=0, columnspan=3, sticky="w", pady=dp(3))
        self.combo_corner = ComboField(body, "急停角落", CORNER_LABELS, CORNER_KEYS, "topleft",
                                       width=14)
        self.combo_corner.grid(row=2, column=0, sticky="w", pady=dp(3))
        self.num_corner_margin = NumberField(body, "触发距离", 5, from_=1, to=200, kind="int",
                                             width=5, suffix="像素")
        self.num_corner_margin.grid(row=2, column=1, sticky="w", padx=dp(12), pady=dp(3))
        self.num_cps_limit = NumberField(body, "超过这个速度要二次确认", 50, from_=1, to=1000,
                                         kind="int", width=5, suffix="次/秒")
        self.num_cps_limit.grid(row=3, column=0, columnspan=2, sticky="w", pady=dp(3))
        self.check_confirm_long = CheckField(body, "长时间运行前弹确认框", True)
        self.check_confirm_long.grid(row=4, column=0, columnspan=2, sticky="w", pady=dp(3))

    # ------------------------------------------------------------------
    def _build_general(self, parent) -> None:
        card = Card(parent, title="通用")
        card.pack(fill="x", pady=dp(6), padx=dp(2))
        body = card.body
        self.combo_theme = ComboField(body, "界面主题", THEME_LABELS, THEME_KEYS,
                                      self.cfg.get("general.theme", "dark"), width=10,
                                      command=lambda _v: self.app.log("主题改动重启后生效"))
        self.combo_theme.grid(row=0, column=0, sticky="w", pady=dp(3))
        self.check_tray = CheckField(body, "关闭窗口时最小化到托盘", True)
        self.check_tray.grid(row=1, column=0, sticky="w", pady=dp(3))
        self.check_autostart = CheckField(body, "开机自动启动", False,
                                          command=self._toggle_autostart)
        self.check_autostart.grid(row=2, column=0, sticky="w", pady=dp(3))
        self.check_overlay = CheckField(body, "在屏幕上显示位置标记和识别范围", False,
                                        command=self._toggle_overlay)
        self.check_overlay.grid(row=3, column=0, sticky="w", pady=dp(3))

        admin_row = ttk.Frame(body, style="Card.TFrame")
        admin_row.grid(row=4, column=0, sticky="w", pady=(dp(8), 0))
        state = "当前已是管理员权限" if W.is_admin() else "当前是普通权限"
        ttk.Label(admin_row, text=state, style="Hint.TLabel").pack(side="left")
        theme.tk_button(admin_row, "以管理员身份重启", self._relaunch_admin, "ghost").pack(
            side="left", padx=dp(8))
        ttk.Label(admin_row, text="（要点击的任务若以管理员运行，需要提权）",
                  style="Hint.TLabel").pack(side="left")

        self._load_general()

    def _load_general(self) -> None:
        self.check_emergency.set(self.cfg.get("safety.emergency_enabled", True))
        self.check_corner.set(self.cfg.get("safety.corner_stop_enabled", True))
        self.combo_corner.set(self.cfg.get("safety.corner", "topleft"))
        self.num_corner_margin.set(self.cfg.get("safety.corner_margin", 5))
        self.num_cps_limit.set(self.cfg.get("safety.cps_confirm_above", 50))
        self.check_confirm_long.set(self.cfg.get("safety.confirm_long_run", True))
        self.combo_theme.set(self.cfg.get("general.theme", "dark"))
        self.check_tray.set(self.cfg.get("general.minimize_to_tray", True))
        self.check_autostart.set(bool(self._autostart_enabled()))
        self.check_overlay.set(bool(self.cfg.get("general.show_overlay")))

    def _toggle_overlay(self) -> None:
        want = self.check_overlay.get()
        self.cfg.set("general.show_overlay", want)
        self.cfg.save()
        try:
            self.app.marker.show() if want else self.app.marker.hide()
        except Exception:
            pass
        try:
            self.app.tab_positions.check_overlay.set(want)
            self.app.tab_click.check_overlay.set(want)
        except Exception:
            pass

    # -------- 开机自启 --------
    def _run_key(self) -> str:
        return r"Software\Microsoft\Windows\CurrentVersion\Run"

    def _autostart_command(self) -> str:
        if getattr(sys, "frozen", False):
            return f'"{sys.executable}"'
        python = sys.executable
        pythonw = python.replace("python.exe", "pythonw.exe")
        if not os.path.exists(pythonw):
            pythonw = python
        main_py = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))), "main.py")
        return f'"{pythonw}" "{main_py}"'

    def _autostart_enabled(self) -> bool:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._run_key()) as key:
                value, _ = winreg.QueryValueEx(key, "AutoClickerPro")
                return bool(value)
        except Exception:
            return False

    def _toggle_autostart(self) -> None:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self._run_key(), 0,
                                winreg.KEY_SET_VALUE) as key:
                if self.check_autostart.get():
                    winreg.SetValueEx(key, "AutoClickerPro", 0, winreg.REG_SZ,
                                      self._autostart_command())
                    self.app.log("已设置开机自启", "ok")
                else:
                    try:
                        winreg.DeleteValue(key, "AutoClickerPro")
                    except FileNotFoundError:
                        pass
                    self.app.log("已取消开机自启")
        except Exception as exc:
            self.app.log(f"设置开机自启失败：{exc}", "error")

    def _relaunch_admin(self) -> None:
        if W.is_admin():
            messagebox.showinfo("提示", "现在已经是管理员权限了。")
            return
        if not messagebox.askyesno("以管理员身份重启",
                                   "会重新打开一个连点器窗口（需要你在系统弹窗里点“是”）。\n继续吗？"):
            return
        if W.relaunch_as_admin():
            self.app.quit_app()
        else:
            self.app.log("提权失败（可能被系统拦截了）", "error")

    # ------------------------------------------------------------------
    def _build_profiles(self, parent) -> None:
        card = Card(parent, title="配置档",
                    subtitle="把一整套设置（速度、位置、识别任务）保存成一个档，之后一键切换。")
        card.pack(fill="x", pady=dp(6), padx=dp(2))
        body = card.body
        names = self.cfg.profile_names()
        self.combo_profile = ComboField(body, "当前配置档", names, names,
                                        self.cfg.get("general.active_profile", "默认配置"),
                                        width=18)
        self.combo_profile.grid(row=0, column=0, sticky="w", pady=dp(3))
        bar = ttk.Frame(body, style="Card.TFrame")
        bar.grid(row=1, column=0, columnspan=3, sticky="w", pady=dp(6))
        theme.tk_button(bar, "加载", self.load_profile, "accent").pack(side="left", padx=dp(3))
        theme.tk_button(bar, "另存为…", self.save_profile_as, "ghost").pack(side="left", padx=dp(3))
        theme.tk_button(bar, "导出文件", self.export_profile, "ghost").pack(side="left", padx=dp(3))
        theme.tk_button(bar, "导入文件", self.import_profile, "ghost").pack(side="left", padx=dp(3))

    def _refresh_profiles(self) -> None:
        names = self.cfg.profile_names()
        self.combo_profile.values = names
        self.combo_profile.keys = names
        try:
            self.combo_profile.combo.configure(values=names)
        except Exception:
            pass
        self.combo_profile.set(self.cfg.get("general.active_profile", "默认配置"))

    def load_profile(self) -> None:
        name = self.combo_profile.get()
        if not self.cfg.load_profile(name):
            self.app.log("加载失败：找不到这个配置档", "error")
            return
        self.app.log(f"已加载配置档「{name}」", "ok")
        self._reload_all()

    def save_profile_as(self) -> None:
        from tkinter import simpledialog
        name = simpledialog.askstring("保存配置档", "给这套设置起个名字：", parent=self)
        if not name:
            return
        self._collect()
        self.cfg.save_profile(name)
        self._refresh_profiles()
        self.app.log(f"已保存配置档「{name}」", "ok")

    def export_profile(self) -> None:
        path = filedialog.asksaveasfilename(title="导出配置", defaultextension=".acp",
                                            filetypes=[("连点器配置", "*.acp *.json")])
        if path:
            self._collect()
            self.cfg.export_to(path)
            self.app.log(f"已导出到 {path}", "ok")

    def import_profile(self) -> None:
        path = filedialog.askopenfilename(title="导入配置",
                                          filetypes=[("连点器配置", "*.acp *.json")])
        if not path:
            return
        if not self.cfg.import_from(path):
            self.app.log("导入失败：文件格式不对", "error")
            return
        self.app.log("配置已导入", "ok")
        self._reload_all()

    def _reload_all(self) -> None:
        self._load_general()
        try:
            self.app.tab_click._load()  # noqa: SLF001
            self.app.tab_positions.refresh()
            self.app.tab_recognize.refresh()
            self.app.tab_record.refresh()
            self.app.hotkeys.reload()
            self.app.marker.refresh()
        except Exception as exc:
            self.app.log(f"刷新界面出错：{exc}", "error")

    # ------------------------------------------------------------------
    def _build_schedule(self, parent) -> None:
        card = Card(parent, title="计划任务",
                    subtitle="让它到点自己开始跑（比如每天早上 9:00 自动开始挂机）。")
        card.pack(fill="x", pady=dp(6), padx=dp(2))
        body = card.body
        self.check_schedule = CheckField(body, "启用计划任务", False)
        self.check_schedule.grid(row=0, column=0, columnspan=3, sticky="w", pady=dp(3))
        self.combo_schedule_mode = ComboField(body, "什么时候开始", SCHEDULE_LABELS,
                                              SCHEDULE_KEYS, "once", width=20)
        self.combo_schedule_mode.grid(row=1, column=0, sticky="w", pady=dp(3))
        self.text_schedule_time = TextField(body, "时刻", "12:00", width=8,
                                            help="24 小时制，例如 09:30")
        self.text_schedule_time.grid(row=2, column=0, sticky="w", pady=dp(3))
        self.num_schedule_delay = NumberField(body, "多少分钟后开始",
                                              self.cfg.get("schedule.delay_minutes", 5),
                                              from_=1, to=10000, kind="int", width=6, suffix="分钟")
        self.num_schedule_delay.grid(row=3, column=0, sticky="w", pady=dp(3))
        self.num_schedule_run = NumberField(body, "运行多长时间后自动停",
                                            self.cfg.get("schedule.run_minutes", 0), from_=0,
                                            to=10000, kind="int", width=6, suffix="分钟（0=不停）")
        self.num_schedule_run.grid(row=4, column=0, sticky="w", pady=dp(3))
        self.check_schedule.set(bool(self.cfg.get("schedule.enabled")))
        self.combo_schedule_mode.set(self.cfg.get("schedule.mode", "once"))
        self.text_schedule_time.set(self.cfg.get("schedule.time", "12:00"))

    # ------------------------------------------------------------------
    def _build_about(self, parent) -> None:
        card = Card(parent, title="关于")
        card.pack(fill="x", pady=dp(6), padx=dp(2))
        body = card.body
        from .. import __version__
        ttk.Label(body, text=f"连点器 AutoClicker Pro  v{__version__}    纯本地运行，不联网、不上传任何数据",
                  style="Card.TLabel").pack(anchor="w")
        bar = ttk.Frame(body, style="Card.TFrame")
        bar.pack(fill="x", pady=(dp(8), 0))
        theme.tk_button(bar, "打开配置文件目录", self.open_config_dir, "ghost").pack(side="left")
        theme.tk_button(bar, "再看一次新手引导", self.show_guide, "ghost").pack(side="left",
                                                                          padx=dp(8))
        theme.tk_button(bar, "紧急停止全部", self.app.emergency_stop, "danger").pack(side="left")

    def open_config_dir(self) -> None:
        try:
            os.startfile(str(CONFIG_DIR))
        except Exception as exc:
            self.app.log(f"打不开目录：{exc}", "error")

    def show_guide(self) -> None:
        self.app._show_guide()  # noqa: SLF001

    # ------------------------------------------------------------------
    def _collect(self) -> None:
        try:
            time_text = self.text_schedule_time.get().strip()
        except Exception:
            time_text = "12:00"
        self.cfg.update({
            "safety": {
                "emergency_enabled": self.check_emergency.get(),
                "corner_stop_enabled": self.check_corner.get(),
                "corner": self.combo_corner.get(),
                "corner_margin": self.num_corner_margin.get_int(),
                "cps_confirm_above": self.num_cps_limit.get_int(),
                "confirm_long_run": self.check_confirm_long.get(),
            },
            "general": {
                "theme": self.combo_theme.get(),
                "minimize_to_tray": self.check_tray.get(),
                "show_overlay": self.check_overlay.get(),
            },
            "schedule": {
                "enabled": self.check_schedule.get(),
                "mode": self.combo_schedule_mode.get(),
                "time": time_text,
                "delay_minutes": self.num_schedule_delay.get_int(),
                "run_minutes": self.num_schedule_run.get_int(),
            },
        })

    def _autosave(self) -> None:
        try:
            self._collect()
            self.cfg.save()
        except Exception as exc:
            self.app.log(f"设置保存出错：{exc}", "error")
        try:
            self.after(1500, self._autosave)
        except Exception:
            pass

    def on_show(self) -> None:
        self._load_general()
        self._refresh_profiles()
