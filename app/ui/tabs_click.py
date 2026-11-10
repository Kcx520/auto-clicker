"""「连点」页签。"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk
from typing import Any, Dict, List, Optional

from .. import winapi as W
from . import theme
from .theme import COLORS, FONTS, dp
from .widgets import (Card, CheckField, Collapsible, ComboField, NumberField, ScrollFrame,
                      SliderField, TextField, add_help, hint, row)

MODE_KEYS = ["cps", "interval", "random"]
MODE_LABELS = ["每秒点几下（推荐）", "固定间隔（毫秒）", "随机间隔（毫秒）"]

LIMIT_KEYS = ["infinite", "count", "duration"]
LIMIT_LABELS = ["一直点，直到我停", "点固定次数就停", "点固定时间就停"]

BUTTON_KEYS = ["left", "right", "middle", "x1", "x2", "wheel_up", "wheel_down"]
BUTTON_LABELS = ["鼠标左键", "鼠标右键", "鼠标中键", "侧键 X1", "侧键 X2", "滚轮上滚", "滚轮下滚"]

ACTION_KEYS = ["single", "double", "triple", "hold", "drag"]
ACTION_LABELS = ["单击", "双击", "三连击", "长按（按住一会儿）", "拖拽（按住并拖动）"]

CORNER_KEYS = ["topleft", "topright", "bottomleft", "bottomright"]
CORNER_LABELS = ["左上角", "右上角", "左下角", "右下角"]


class ClickTab(ttk.Frame):
    def __init__(self, parent, app) -> None:
        super().__init__(parent, style="TFrame")
        self.app = app
        self.cfg = app.cfg
        self._last_saved: Dict[str, Any] = {}
        self._loading = True

        scroll = ScrollFrame(self)
        scroll.pack(fill="both", expand=True)
        holder = scroll.inner

        self._build_target(holder)
        self._build_speed(holder)
        self._build_action(holder)
        self._build_limit(holder)
        self._build_advanced(holder)
        self._build_live(holder)

        self._loading = False
        self._load()
        self.after(900, self._autosave)

    # ------------------------------------------------------------------
    def _card(self, parent, title: str, subtitle: str = "") -> Card:
        card = Card(parent, title=title, subtitle=subtitle)
        card.pack(fill="x", pady=dp(6))
        return card

    # ------------------------------------------------------------------
    def _build_target(self, parent) -> None:
        card = self._card(parent, "第 1 步：点哪里",
                          "把鼠标放到目标处，按 F9 就能把位置存下来。")
        body = card.body
        self.var_target_mode = tk.StringVar(value="fixed")
        ttk.Radiobutton(body, text="点我保存好的固定位置", value="fixed",
                        variable=self.var_target_mode, command=self._on_target_mode).grid(
            row=0, column=0, columnspan=3, sticky="w", pady=dp(2))
        ttk.Radiobutton(body, text="跟着我的鼠标走（点鼠标当前所在处）", value="cursor",
                        variable=self.var_target_mode, command=self._on_target_mode).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=dp(2))

        self.combo_position = ComboField(body, "使用位置", ["（还没有位置）"], value="",
                                         width=26)
        self.combo_position.grid(row=2, column=0, columnspan=2, sticky="w", pady=dp(6))
        self.combo_position.combo.bind("<<ComboboxSelected>>", lambda _e: self._on_position_change())

        buttons = ttk.Frame(body, style="Card.TFrame")
        buttons.grid(row=2, column=2, sticky="e")
        theme.tk_button(buttons, "去这个位置", self._goto_position, "ghost").pack(side="left",
                                                                                padx=dp(3))
        theme.tk_button(buttons, "测试点一下", self._test_click, "ghost").pack(side="left",
                                                                             padx=dp(3))
        tip = ttk.Label(body, text="热键 F9 = 把当前鼠标位置保存到「位置收藏」里的第一个空位；"
                                   "也可以在「位置收藏」页签里精确管理。",
                        style="Hint.TLabel", wraplength=dp(760), justify="left")
        tip.grid(row=3, column=0, columnspan=3, sticky="w", pady=(dp(6), 0))

    def _on_target_mode(self) -> None:
        fixed = self.var_target_mode.get() == "fixed"
        state = "readonly" if fixed else "disabled"
        try:
            self.combo_position.combo.configure(state=state)
        except Exception:
            pass
        self._schedule_save()

    def _on_position_change(self) -> None:
        position_id = self.combo_position.get()
        self.cfg.set("click.position_id", position_id)
        index = 0
        for i, item in enumerate(self.cfg.positions()):
            if item.get("id") == position_id:
                index = i
                break
        self.cfg.set("click.position_index", index)
        self._schedule_save()
        try:
            self.app.marker.refresh()
        except Exception:
            pass

    def _goto_position(self) -> None:
        position = self.cfg.find_position(self.combo_position.get())
        if not position:
            self.app.log("请先选一个位置，或者按 F9 存一个", "warn")
            return
        from ..clicker import resolve_position
        point = resolve_position(position)
        if point:
            W.set_cursor_pos(point[0], point[1])
            self.app.log(f"鼠标已移动到「{position.get('name')}」{point}")

    def _test_click(self) -> None:
        position = self.cfg.find_position(self.combo_position.get())
        if not position:
            self.app.log("请先选一个位置，或者按 F9 存一个", "warn")
            return
        from ..clicker import resolve_position
        point = resolve_position(position)
        if not point:
            return
        button = self.cfg.get("click.button", "left")
        W.click(button, int(self.cfg.get("click.hold_ms", 20) or 20), point[0], point[1])
        self.app.log(f"已在该位置点了一下：{point}")

    # ------------------------------------------------------------------
    def _build_speed(self, parent) -> None:
        card = self._card(parent, "第 2 步：点多快",
                          "不确定就用默认值：每秒 10 次。")
        body = card.body
        self.combo_mode = ComboField(body, "速度模式", MODE_LABELS, MODE_KEYS, "cps", width=20,
                                     command=lambda _v: self._on_mode_change())
        self.combo_mode.grid(row=0, column=0, columnspan=2, sticky="w", pady=dp(4))

        self.num_cps = NumberField(body, "每秒点几下", self.cfg.get("click.cps", 10),
                                   from_=0.1, to=1000, kind="float", decimals=1, width=8,
                                   help="例如填 10 就是每秒点 10 次")
        self.num_cps.grid(row=1, column=0, sticky="w", pady=dp(3))

        self.num_interval = NumberField(body, "固定间隔（毫秒）",
                                        self.cfg.get("click.interval_ms", 100),
                                        from_=1, to=600000, kind="int", width=8,
                                        help="两次点击之间等多少毫秒")
        self.num_interval.grid(row=2, column=0, sticky="w", pady=dp(3))

        random_row = ttk.Frame(body, style="Card.TFrame")
        random_row.grid(row=3, column=0, columnspan=3, sticky="w", pady=dp(3))
        self.num_random_min = NumberField(random_row, "随机间隔  最短", 80, from_=1, to=600000,
                                          kind="int", width=6)
        self.num_random_min.pack(side="left")
        self.num_random_max = NumberField(random_row, "最长", 150, from_=1, to=600000,
                                          kind="int", width=6)
        self.num_random_max.pack(side="left", padx=dp(10))
        ttk.Label(random_row, text="毫秒（两次点击之间在这个范围里随机）",
                  style="Hint.TLabel").pack(side="left")

        self.check_humanize = CheckField(body, "拟人模式（速度带一点随机抖动，更像真人操作）",
                                         False)
        self.check_humanize.grid(row=4, column=0, columnspan=3, sticky="w", pady=(dp(8), dp(2)))
        self.slider_jitter = SliderField(body, "抖动幅度", 15, 0, 50, kind="int", suffix="%",
                                         help="每次间隔上下浮动多少百分比")
        self.slider_jitter.grid(row=5, column=0, columnspan=2, sticky="w", pady=dp(3))
        self._on_mode_change()

    def _on_mode_change(self) -> None:
        mode = self.combo_mode.get()
        self.num_cps.entry.configure(state="normal" if mode == "cps" else "disabled")
        self.num_interval.entry.configure(state="normal" if mode == "interval" else "disabled")
        state = "normal" if mode == "random" else "disabled"
        self.num_random_min.entry.configure(state=state)
        self.num_random_max.entry.configure(state=state)
        self._schedule_save()

    # ------------------------------------------------------------------
    def _build_action(self, parent) -> None:
        card = self._card(parent, "第 3 步：怎么点")
        body = card.body
        self.combo_button = ComboField(body, "用哪个键", BUTTON_LABELS, BUTTON_KEYS, "left",
                                       width=14)
        self.combo_button.grid(row=0, column=0, sticky="w", pady=dp(3))
        self.combo_action = ComboField(body, "点击方式", ACTION_LABELS, ACTION_KEYS, "single",
                                       width=18,
                                       command=lambda _v: self._on_action_change())
        self.combo_action.grid(row=0, column=1, sticky="w", pady=dp(3), padx=dp(12))
        self.num_hold = NumberField(body, "按住时长", 60, from_=0, to=10000, kind="int",
                                    width=6, suffix="毫秒",
                                    help="按下和松开之间的间隔；有些程序需要长按才生效")
        self.num_hold.grid(row=1, column=0, sticky="w", pady=dp(3))
        self.num_double_gap = NumberField(body, "双击间隔", 40, from_=1, to=1000, kind="int",
                                          width=6, suffix="毫秒")
        self.num_double_gap.grid(row=1, column=1, sticky="w", pady=dp(3), padx=dp(12))
        self._on_action_change()

    def _on_action_change(self) -> None:
        action = self.combo_action.get()
        self.num_hold.entry.configure(state="disabled" if action == "drag" else "normal")
        self.num_double_gap.entry.configure(
            state="normal" if action in ("double", "triple") else "disabled")
        self._schedule_save()

    # ------------------------------------------------------------------
    def _build_limit(self, parent) -> None:
        card = self._card(parent, "点多久 / 点多少次")
        body = card.body
        self.var_limit = tk.StringVar(value="infinite")
        for index, (key, label) in enumerate(zip(LIMIT_KEYS, LIMIT_LABELS)):
            ttk.Radiobutton(body, text=label, value=key, variable=self.var_limit,
                            command=self._on_limit_change).grid(row=index, column=0, sticky="w",
                                                                pady=dp(2))
        self.num_count = NumberField(body, "点够", 1000, from_=1, to=100000000, kind="int",
                                     width=10, suffix="次就停")
        self.num_count.grid(row=1, column=1, sticky="w", pady=dp(2), padx=dp(8))
        self.num_minutes = NumberField(body, "点够", 5, from_=0.1, to=10000, kind="float",
                                       decimals=1, width=8, suffix="分钟就停")
        self.num_minutes.grid(row=2, column=1, sticky="w", pady=dp(2), padx=dp(8))
        self.num_delay = NumberField(body, "开始前倒计时", 3, from_=0, to=3600, kind="int",
                                     width=6, suffix="秒",
                                     help="点「开始」后先等几秒，方便你把手放回鼠标上")
        self.num_delay.grid(row=3, column=0, columnspan=2, sticky="w", pady=(dp(8), dp(2)))
        self._on_limit_change()

    def _on_limit_change(self) -> None:
        mode = self.var_limit.get()
        self.num_count.entry.configure(state="normal" if mode == "count" else "disabled")
        self.num_minutes.entry.configure(state="normal" if mode == "duration" else "disabled")
        self._schedule_save()

    # ------------------------------------------------------------------
    def _build_advanced(self, parent) -> None:
        holder = Card(parent, title="")
        holder.pack(fill="x", pady=dp(6))
        box = Collapsible(holder.body, "高级设置（不懂就别动，默认就能用）", expanded=False)
        box.pack(fill="x")
        body = box.body
        body.columnconfigure(1, weight=1)

        self.check_foreground = CheckField(
            body, "只在我指定的窗口最前面时才点（防止点到别的地方）", False,
            help="勾上后，如果目标窗口不在最前面，连点会自动跳过，不会乱点")
        self.check_foreground.grid(row=0, column=0, columnspan=3, sticky="w", pady=dp(2))
        title_row = ttk.Frame(body, style="Card.TFrame")
        title_row.grid(row=1, column=0, columnspan=3, sticky="w", pady=dp(2))
        self.text_window_title = TextField(title_row, "目标窗口关键字", "", width=24,
                                           help="填窗口标题里的一段字，例如「记事本」或者游戏名")
        self.text_window_title.pack(side="left")
        theme.tk_button(title_row, "抓取当前窗口", self._pick_window, "ghost").pack(side="left",
                                                                                 padx=dp(6))

        self.check_background = CheckField(
            body, "后台点击（不抢鼠标，可以一边点一边用电脑；部分程序不支持）", False)
        self.check_background.grid(row=2, column=0, columnspan=3, sticky="w", pady=dp(2))

        self.check_overlay = CheckField(body, "在屏幕上标出目标位置和识别范围", False,
                                        command=self._toggle_overlay)
        self.check_overlay.grid(row=3, column=0, columnspan=3, sticky="w", pady=dp(2))

        grid = ttk.Frame(body, style="Card.TFrame")
        grid.grid(row=4, column=0, columnspan=3, sticky="w", pady=dp(4))
        self.num_offset = NumberField(grid, "坐标随机偏移", 3, from_=0, to=200, kind="int",
                                      width=5, suffix="像素")
        self.num_offset.grid(row=0, column=0, padx=(0, dp(16)))
        self.num_miss = NumberField(grid, "随机漏点概率", 0, from_=0, to=100, kind="int",
                                    width=5, suffix="%")
        self.num_miss.grid(row=0, column=1, padx=(0, dp(16)))
        self.num_pause_every = NumberField(grid, "每", 50, from_=0, to=100000, kind="int",
                                           width=6, suffix="次休息一下")
        self.num_pause_every.grid(row=1, column=0, pady=(dp(6), 0))
        self.num_pause_min = NumberField(grid, "休息", 0.5, from_=0.05, to=60, kind="float",
                                         decimals=2, width=6, suffix="~")
        self.num_pause_min.grid(row=1, column=1, pady=(dp(6), 0))
        self.num_pause_max = NumberField(grid, "秒", 2.0, from_=0.05, to=60, kind="float",
                                         decimals=2, width=6, suffix="秒")
        self.num_pause_max.grid(row=1, column=2, pady=(dp(6), 0))

        drag_row = ttk.Frame(body, style="Card.TFrame")
        drag_row.grid(row=5, column=0, columnspan=3, sticky="w", pady=dp(4))
        self.num_drag_dx = NumberField(drag_row, "拖拽横向", 150, from_=-3000, to=3000,
                                       kind="int", width=7, suffix="像素")
        self.num_drag_dx.pack(side="left", padx=(0, dp(12)))
        self.num_drag_dy = NumberField(drag_row, "纵向", 0, from_=-3000, to=3000, kind="int",
                                       width=7, suffix="像素")
        self.num_drag_dy.pack(side="left", padx=(0, dp(12)))
        self.num_drag_duration = NumberField(drag_row, "用时", 300, from_=20, to=10000,
                                             kind="int", width=7, suffix="毫秒")
        self.num_drag_duration.pack(side="left")

        preset_row = ttk.Frame(body, style="Card.TFrame")
        preset_row.grid(row=6, column=0, columnspan=3, sticky="w", pady=(dp(8), 0))
        ttk.Label(preset_row, text="一键预设：", style="Card.TLabel").pack(side="left")
        theme.tk_button(preset_row, "精确模式", lambda: self._apply_preset("precise"),
                        "ghost").pack(side="left", padx=dp(4))
        theme.tk_button(preset_row, "拟人模式", lambda: self._apply_preset("human"),
                        "ghost").pack(side="left", padx=dp(4))

    def _pick_window(self) -> None:
        hwnd = W.foreground_window()
        title = W.window_title(hwnd)
        if not title:
            self.app.log("没抓到窗口，请先把目标程序切到最前面再点这个按钮", "warn")
            return
        self.text_window_title.set(title)
        self.check_foreground.set(True)
        self.app.log(f"目标窗口已设置为：{title}")
        self._schedule_save()

    def _toggle_overlay(self) -> None:
        want = self.check_overlay.get()
        try:
            if want:
                self.app.marker.show()
            else:
                self.app.marker.hide()
        except Exception:
            pass

    def _apply_preset(self, kind: str) -> None:
        if kind == "human":
            self.check_humanize.set(True)
            self.slider_jitter.set(20)
            self.num_offset.set(4)
            self.num_pause_every.set(30)
            self.num_pause_min.set(0.5)
            self.num_pause_max.set(1.5)
            self.num_miss.set(2)
            self.app.log("已套用「拟人模式」预设", "ok")
        else:
            self.check_humanize.set(False)
            self.slider_jitter.set(0)
            self.num_offset.set(0)
            self.num_pause_every.set(0)
            self.num_miss.set(0)
            self.app.log("已套用「精确模式」预设", "ok")
        self._schedule_save()

    # ------------------------------------------------------------------
    def _build_live(self, parent) -> None:
        card = self._card(parent, "实时状态")
        body = card.body
        self.var_count = tk.StringVar(value="0 次")
        self.var_cps = tk.StringVar(value="0.0 次/秒")
        self.var_elapsed = tk.StringVar(value="00:00")
        self.var_remaining = tk.StringVar(value="不限")
        for index, (caption, var) in enumerate((("已点击", self.var_count),
                                                ("当前速度", self.var_cps),
                                                ("已运行", self.var_elapsed),
                                                ("剩余", self.var_remaining))):
            ttk.Label(body, text=caption, style="Hint.TLabel").grid(row=0, column=index,
                                                                    sticky="w", padx=dp(14))
            ttk.Label(body, textvariable=var, style="Card.TLabel",
                      font=FONTS["big"]).grid(row=1, column=index, sticky="w", padx=dp(14))
        self.progress = ttk.Progressbar(body, orient="horizontal", mode="determinate",
                                        maximum=100)
        self.progress.grid(row=2, column=0, columnspan=4, sticky="ew", pady=(dp(10), 0))
        body.columnconfigure(3, weight=1)

    def update_live(self, snap: Dict[str, Any]) -> None:
        countdown = snap.get("countdown", 0) or 0
        if countdown > 0:
            self.var_count.set(f"{countdown:.1f} 秒后开始")
        else:
            self.var_count.set(f"{snap.get('count', 0)} 次")
        self.var_cps.set(f"{snap.get('cps', 0)} 次/秒")
        self.var_elapsed.set(snap.get("elapsed_text", "00:00"))
        self.var_remaining.set(snap.get("remaining", "不限"))
        target = snap.get("target_count", 0)
        if target:
            self.progress.configure(maximum=max(1, target),
                                    value=min(target, snap.get("count", 0)))
        else:
            limit = float(self.cfg.get("click.max_minutes", 0) or 0) * 60 if \
                self.cfg.get("click.limit_mode") == "duration" else 0
            if limit:
                self.progress.configure(maximum=limit,
                                        value=min(limit, snap.get("elapsed", 0)))
            else:
                self.progress.configure(value=0)

    # ------------------------------------------------------------------
    # 载入 / 保存
    # ------------------------------------------------------------------
    def _load(self) -> None:
        cfg = self.cfg
        self.var_target_mode.set(cfg.get("click.target_mode", "fixed"))
        self.combo_mode.set(cfg.get("click.interval_mode", "cps"))
        self.num_cps.set(cfg.get("click.cps", 10))
        self.num_interval.set(cfg.get("click.interval_ms", 100))
        self.num_random_min.set(cfg.get("click.random_min_ms", 80))
        self.num_random_max.set(cfg.get("click.random_max_ms", 150))
        self.check_humanize.set(cfg.get("click.humanize", False))
        self.slider_jitter.set(cfg.get("click.jitter_percent", 15))
        self.combo_button.set(cfg.get("click.button", "left"))
        self.combo_action.set(cfg.get("click.action", "single"))
        self.num_hold.set(cfg.get("click.hold_ms", 60))
        self.num_double_gap.set(cfg.get("click.double_click_interval_ms", 40))
        self.var_limit.set(cfg.get("click.limit_mode", "infinite"))
        self.num_count.set(cfg.get("click.max_count", 1000))
        self.num_minutes.set(cfg.get("click.max_minutes", 5))
        self.num_delay.set(cfg.get("click.start_delay_s", 3))
        self.check_foreground.set(cfg.get("click.foreground_only", False))
        self.text_window_title.set(cfg.get("click.target_window_title", ""))
        self.check_background.set(cfg.get("click.background_mode", False))
        self.check_overlay.set(cfg.get("general.show_overlay", False))
        self.num_offset.set(cfg.get("click.offset_px", 3))
        self.num_miss.set(cfg.get("click.miss_chance", 0) * 100 if
                          float(cfg.get("click.miss_chance", 0) or 0) <= 1 else
                          cfg.get("click.miss_chance", 0))
        self.num_pause_every.set(cfg.get("click.micro_pause_every", 50))
        self.num_pause_min.set(cfg.get("click.micro_pause_min_s", 0.5))
        self.num_pause_max.set(cfg.get("click.micro_pause_max_s", 2.0))
        self.num_drag_dx.set(cfg.get("click.drag_dx", 150))
        self.num_drag_dy.set(cfg.get("click.drag_dy", 0))
        self.num_drag_duration.set(cfg.get("click.drag_duration_ms", 300))
        self._on_mode_change()
        self._on_action_change()
        self._on_limit_change()
        self._on_target_mode()
        self.refresh_positions()

    def refresh_positions(self) -> None:
        positions = self.cfg.positions()
        labels = [f"{p.get('name', '位置')}  ({p.get('x')}, {p.get('y')})" for p in positions]
        self.combo_position.values = labels or ["（还没有位置）"]
        try:
            self.combo_position.combo.configure(values=self.combo_position.values)
        except Exception:
            pass
        current = self.cfg.get("click.position_id", "")
        if not current:
            active = self.cfg.active_position()
            current = active.get("id") if active else ""
        self.combo_position.keys = [p.get("id", "") for p in positions] or [""]
        self.combo_position.set(current)

    def _collect(self) -> Dict[str, Any]:
        return {
            "click": {
                "target_mode": self.var_target_mode.get(),
                "position_id": self.combo_position.get(),
                "interval_mode": self.combo_mode.get(),
                "cps": self.num_cps.get(),
                "interval_ms": self.num_interval.get_int(),
                "random_min_ms": self.num_random_min.get_int(),
                "random_max_ms": self.num_random_max.get_int(),
                "humanize": self.check_humanize.get(),
                "jitter_percent": self.slider_jitter.get(),
                "button": self.combo_button.get(),
                "action": self.combo_action.get(),
                "hold_ms": self.num_hold.get_int(),
                "double_click_interval_ms": self.num_double_gap.get_int(),
                "limit_mode": self.var_limit.get(),
                "max_count": self.num_count.get_int(),
                "max_minutes": self.num_minutes.get(),
                "start_delay_s": self.num_delay.get(),
                "foreground_only": self.check_foreground.get(),
                "target_window_title": self.text_window_title.get(),
                "background_mode": self.check_background.get(),
                "offset_px": self.num_offset.get_int(),
                "miss_chance": self.num_miss.get() / 100.0,
                "micro_pause_every": self.num_pause_every.get_int(),
                "micro_pause_min_s": self.num_pause_min.get(),
                "micro_pause_max_s": self.num_pause_max.get(),
                "drag_dx": self.num_drag_dx.get_int(),
                "drag_dy": self.num_drag_dy.get_int(),
                "drag_duration_ms": self.num_drag_duration.get_int(),
            },
            "general": {"show_overlay": self.check_overlay.get()},
        }

    def _schedule_save(self) -> None:
        if self._loading:
            return
        try:
            self.after(500, self._autosave)
        except Exception:
            pass

    def _autosave(self) -> None:
        try:
            if not self._loading:
                data = self._collect()
                if data != self._last_saved:
                    self._last_saved = data
                    self.cfg.update(data)
                    self.cfg.save()
                    self.combo_position.keys = [p.get("id", "") for p in self.cfg.positions()]
        except Exception as exc:
            self.app.log(f"保存设置出错：{exc}", "error")
        try:
            self.after(900, self._autosave)
        except Exception:
            pass
