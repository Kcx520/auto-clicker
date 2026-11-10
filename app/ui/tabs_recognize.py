"""「智能识别」页签：看到指定文字 / 图片 / 颜色就自动去点。"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Any, Dict, List, Optional

from .. import screen as S
from .. import winapi as W
from ..config import TEMPLATE_DIR
from ..recognizer import describe_task, new_task
from . import theme
from .theme import COLORS, FONTS, dp
from .widgets import (Card, CheckField, Collapsible, ComboField, NumberField, ScrollFrame,
                      SliderField, TextField, add_help, hint)

KIND_KEYS = ["text", "image", "color"]
KIND_LABELS = ["找文字（OCR）", "找图片（截图对比）", "找颜色"]

MODE_KEYS = ["any", "all"]
MODE_LABELS = ["出现任意一个就触发", "全部出现才触发"]

BUTTON_KEYS = ["left", "right", "middle", "x1", "x2"]
BUTTON_LABELS = ["鼠标左键", "鼠标右键", "鼠标中键", "侧键 X1", "侧键 X2"]

ACTION_KEYS = ["single", "double", "hold"]
ACTION_LABELS = ["单击", "双击", "长按"]


class TaskCard(Card):
    """一条识别任务的全部设置。"""

    def __init__(self, parent, tab: "RecognizeTab", task: Dict[str, Any], index: int):
        super().__init__(parent, title="")
        self.tab = tab
        self.app = tab.app
        self.cfg = tab.cfg
        self.task = task
        body = self.body
        body.columnconfigure(1, weight=1)

        head = ttk.Frame(body, style="Card.TFrame")
        head.grid(row=0, column=0, columnspan=4, sticky="ew")
        self.var_enabled = tk.BooleanVar(value=bool(task.get("enabled", True)))
        ttk.Checkbutton(head, text=f"任务 {index + 1}", variable=self.var_enabled,
                        style="Card.TCheckbutton",
                        command=self._toggle_enabled).pack(side="left")
        self.title_label = ttk.Label(head, text=describe_task(task), style="Card.TLabel",
                                     font=FONTS["bold"])
        self.title_label.pack(side="left", padx=dp(10))
        theme.tk_button(head, "删除", self.delete, "danger").pack(side="right")
        theme.tk_button(head, "测试识别", self.test, "ghost").pack(side="right", padx=dp(6))

        self.combo_kind = ComboField(body, "识别什么", KIND_LABELS, KIND_KEYS,
                                     task.get("kind", "text"), width=18,
                                     command=lambda _v: self._on_kind())
        self.combo_kind.grid(row=1, column=0, columnspan=2, sticky="w", pady=dp(4))

        # ---- 文字 ----
        self.text_frame = ttk.Frame(body, style="Card.TFrame")
        self.text_frame.grid(row=2, column=0, columnspan=4, sticky="ew", pady=dp(2))
        ttk.Label(self.text_frame, text="要找的文字", style="Card.TLabel").pack(side="left",
                                                                          anchor="n")
        self.text_content = tk.Text(self.text_frame, height=3, width=34, bd=0, relief="flat",
                                    bg=COLORS["entry"], fg=COLORS["fg"], insertbackground=COLORS["fg"],
                                    font=FONTS["base"], padx=dp(6), pady=dp(4))
        self.text_content.pack(side="left", padx=dp(6))
        self.text_content.insert("1.0", "\n".join(task.get("keywords") or []))
        tip = ttk.Label(self.text_frame, text="一行一个，例如：\n立即抢购\n确认", style="Hint.TLabel",
                        justify="left")
        tip.pack(side="left")
        self.combo_match = ComboField(body, "匹配方式", MODE_LABELS, MODE_KEYS,
                                      task.get("match_mode", "any"), width=18)
        self.combo_match.grid(row=3, column=0, columnspan=2, sticky="w", pady=dp(2))

        # ---- 图片 ----
        self.image_frame = ttk.Frame(body, style="Card.TFrame")
        self.image_frame.grid(row=4, column=0, columnspan=4, sticky="ew", pady=dp(2))
        self.image_frame.columnconfigure(0, weight=1)
        self.list_templates = tk.Listbox(self.image_frame, height=3, bd=0, relief="flat",
                                         bg=COLORS["entry"], fg=COLORS["fg"],
                                         selectbackground=COLORS["accent"],
                                         font=FONTS["small"], highlightthickness=0)
        self.list_templates.grid(row=0, column=0, sticky="ew")
        for path in task.get("template_paths") or []:
            self.list_templates.insert("end", os.path.basename(path))
        buttons = ttk.Frame(self.image_frame, style="Card.TFrame")
        buttons.grid(row=0, column=1, sticky="n", padx=dp(6))
        theme.tk_button(buttons, "截图取图", self.capture_template, "accent").pack(fill="x",
                                                                              pady=dp(2))
        theme.tk_button(buttons, "选图片文件", self.browse_template, "ghost").pack(fill="x",
                                                                              pady=dp(2))
        theme.tk_button(buttons, "删除选中", self.remove_template, "ghost").pack(fill="x",
                                                                           pady=dp(2))
        self.slider_threshold = SliderField(body, "相似度", float(task.get("threshold", 0.85)) * 100,
                                            50, 100, kind="int", suffix="%",
                                            help="越低越容易匹配到，但也更容易误判")
        self.slider_threshold.grid(row=5, column=0, columnspan=2, sticky="w", pady=dp(2))

        # ---- 颜色 ----
        self.color_frame = ttk.Frame(body, style="Card.TFrame")
        self.color_frame.grid(row=6, column=0, columnspan=4, sticky="ew", pady=dp(2))
        color = task.get("color") or [255, 0, 0]
        self.num_r = NumberField(self.color_frame, "R", color[0], 0, 255, width=4)
        self.num_r.pack(side="left")
        self.num_g = NumberField(self.color_frame, "G", color[1], 0, 255, width=4)
        self.num_g.pack(side="left", padx=dp(6))
        self.num_b = NumberField(self.color_frame, "B", color[2], 0, 255, width=4)
        self.num_b.pack(side="left", padx=dp(6))
        self.num_tolerance = NumberField(self.color_frame, "容差", task.get("color_tolerance", 30),
                                         0, 255, width=4)
        self.num_tolerance.pack(side="left", padx=dp(10))
        self.swatch = tk.Canvas(self.color_frame, width=dp(28), height=dp(20),
                                highlightthickness=0, bg=COLORS["entry"])
        self.swatch.pack(side="left", padx=dp(6))
        self._update_swatch()
        theme.tk_button(self.color_frame, "从屏幕取色", self.pick_color, "ghost").pack(side="left",
                                                                                  padx=dp(6))

        # ---- 范围 ----
        region_row = ttk.Frame(body, style="Card.TFrame")
        region_row.grid(row=7, column=0, columnspan=4, sticky="ew", pady=(dp(6), dp(2)))
        ttk.Label(region_row, text="识别范围", style="Card.TLabel").pack(side="left")
        self.var_region = tk.StringVar(value="global" if not task.get("region") else "custom")
        ttk.Radiobutton(region_row, text="用全局范围", value="global", variable=self.var_region,
                        command=self._on_region).pack(side="left", padx=dp(6))
        ttk.Radiobutton(region_row, text="单独指定", value="custom", variable=self.var_region,
                        command=self._on_region).pack(side="left", padx=dp(6))
        theme.tk_button(region_row, "框选范围", self.select_region, "ghost").pack(side="left",
                                                                              padx=dp(6))
        self.region_label = ttk.Label(region_row, text=self._region_text(), style="Hint.TLabel")
        self.region_label.pack(side="left", padx=dp(6))

        # ---- 命中后 ----
        click_row = ttk.Frame(body, style="Card.TFrame")
        click_row.grid(row=8, column=0, columnspan=4, sticky="ew", pady=(dp(8), dp(2)))
        ttk.Label(click_row, text="找到以后", style="Card.TLabel").pack(side="left")
        self.var_where = tk.StringVar(value=task.get("click_where", "position"))
        ttk.Radiobutton(click_row, text="点击我保存好的位置", value="position",
                        variable=self.var_where, command=self._on_where).pack(side="left", padx=dp(6))
        ttk.Radiobutton(click_row, text="直接点识别到的地方", value="found",
                        variable=self.var_where, command=self._on_where).pack(side="left", padx=dp(6))

        self.position_row = ttk.Frame(body, style="Card.TFrame")
        self.position_row.grid(row=9, column=0, columnspan=4, sticky="w", pady=dp(2))
        self.combo_position = ComboField(self.position_row, "目标位置", ["（还没有位置）"],
                                         value=task.get("position_id", ""), width=26)
        self.combo_position.pack(side="left")
        theme.tk_button(self.position_row, "去这个位置", self.goto_position, "ghost").pack(
            side="left", padx=dp(6))
        self.refresh_positions()

        self.offset_row = ttk.Frame(body, style="Card.TFrame")
        self.offset_row.grid(row=10, column=0, columnspan=4, sticky="w", pady=dp(2))
        self.num_offset_x = NumberField(self.offset_row, "微调横向", task.get("found_offset_x", 0),
                                        -2000, 2000, width=6, suffix="像素")
        self.num_offset_x.pack(side="left")
        self.num_offset_y = NumberField(self.offset_row, "纵向", task.get("found_offset_y", 0),
                                        -2000, 2000, width=6, suffix="像素")
        self.num_offset_y.pack(side="left", padx=dp(10))

        option_row = ttk.Frame(body, style="Card.TFrame")
        option_row.grid(row=11, column=0, columnspan=4, sticky="ew", pady=(dp(6), dp(2)))
        self.combo_button = ComboField(option_row, "用哪个键", BUTTON_LABELS, BUTTON_KEYS,
                                       task.get("button", "left"), width=12)
        self.combo_button.pack(side="left")
        self.combo_action = ComboField(option_row, "点击方式", ACTION_LABELS, ACTION_KEYS,
                                       task.get("action", "single"), width=10)
        self.combo_action.pack(side="left", padx=dp(10))
        self.num_cooldown = NumberField(option_row, "命中后休息", task.get("cooldown_s", 3),
                                        from_=0, to=3600, kind="float", decimals=1, width=6,
                                        suffix="秒")
        self.num_cooldown.pack(side="left", padx=dp(10))

        extra_row = ttk.Frame(body, style="Card.TFrame")
        extra_row.grid(row=12, column=0, columnspan=4, sticky="ew", pady=dp(2))
        self.num_max_hits = NumberField(extra_row, "最多命中", task.get("max_hits", 0), from_=0,
                                        to=1000000, width=6, suffix="次（0=不限）")
        self.num_max_hits.pack(side="left")
        self.check_stop_after = CheckField(extra_row, "命中一次就停",
                                           bool(task.get("stop_after_hit")))
        self.check_stop_after.pack(side="left", padx=dp(12))
        self.hit_label = ttk.Label(extra_row, text=f"已命中 {task.get('hits', 0)} 次",
                                   style="Hint.TLabel")
        self.hit_label.pack(side="left", padx=dp(8))

        self.result_label = ttk.Label(body, text="", style="Hint.TLabel", wraplength=dp(760),
                                      justify="left")
        self.result_label.grid(row=13, column=0, columnspan=4, sticky="w", pady=(dp(6), 0))

        self._on_kind()
        self._on_where()

    # ------------------------------------------------------------------
    def _update_swatch(self) -> None:
        try:
            color = "#%02x%02x%02x" % (self.num_r.get_int(), self.num_g.get_int(),
                                       self.num_b.get_int())
            self.swatch.configure(bg=color)
        except Exception:
            pass

    def _toggle_enabled(self) -> None:
        self.task["enabled"] = bool(self.var_enabled.get())
        self.tab.save()

    def _on_kind(self) -> None:
        kind = self.combo_kind.get()
        for frame in (self.text_frame, self.image_frame, self.color_frame):
            frame.grid_remove()
        if kind == "text":
            self.text_frame.grid()
            self.combo_match.grid()
        elif kind == "image":
            self.image_frame.grid()
            self.slider_threshold.grid()
        else:
            self.color_frame.grid()
        if kind != "image":
            self.slider_threshold.grid_remove()
        if kind != "text":
            self.combo_match.grid_remove()
        self.tab.save()

    def _on_where(self) -> None:
        if self.var_where.get() == "position":
            self.position_row.grid()
            self.offset_row.grid_remove()
        else:
            self.position_row.grid_remove()
            self.offset_row.grid()
        self.tab.save()

    def _on_region(self) -> None:
        if self.var_region.get() == "global":
            self.task["region"] = None
        self.region_label.configure(text=self._region_text())
        self.tab.save()

    def _region_text(self) -> str:
        region = self.task.get("region")
        if not region:
            return "（用全局范围）"
        return f"({region[0]}, {region[1]}) {region[2]}×{region[3]}"

    def select_region(self) -> None:
        def done(region):
            if region:
                self.task["region"] = list(region)
                self.var_region.set("custom")
                self.region_label.configure(text=self._region_text())
                self.tab.save()
                self.app.log(f"识别范围已设为 {region}")
        self.app.select_region(done)

    def refresh_positions(self) -> None:
        positions = self.cfg.positions()
        self.combo_position.values = [f"{p.get('name')}  ({p.get('x')}, {p.get('y')})"
                                      for p in positions] or ["（还没有位置）"]
        try:
            self.combo_position.combo.configure(values=self.combo_position.values)
        except Exception:
            pass
        self.combo_position.keys = [p.get("id", "") for p in positions] or [""]
        self.combo_position.set(self.task.get("position_id", ""))

    def goto_position(self) -> None:
        from ..clicker import resolve_position
        item = self.cfg.find_position(self.combo_position.get())
        point = resolve_position(item)
        if point:
            W.set_cursor_pos(point[0], point[1])
            self.app.log(f"鼠标已移动到 {point}")

    def capture_template(self) -> None:
        def done(region):
            if not region:
                return
            image = S.grab(tuple(int(v) for v in region))
            if image.size == 0:
                self.app.log("截图失败，请重试", "error")
                return
            TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
            name = f"模板_{len(self.task.get('template_paths') or []) + 1}_{region[2]}x{region[3]}.png"
            path = str(TEMPLATE_DIR / name)
            if S.save_png(path, image):
                self.task.setdefault("template_paths", []).append(path)
                self.list_templates.insert("end", name)
                self.app.log(f"已截取模板图：{name}", "ok")
                self.tab.save()
            else:
                self.app.log("模板图保存失败", "error")
        self.app.select_region(done)

    def browse_template(self) -> None:
        path = filedialog.askopenfilename(title="选择模板图片",
                                          filetypes=[("图片", "*.png *.jpg *.jpeg *.bmp")])
        if not path:
            return
        TEMPLATE_DIR.mkdir(parents=True, exist_ok=True)
        target = str(TEMPLATE_DIR / os.path.basename(path))
        try:
            if os.path.abspath(path) != os.path.abspath(target):
                import shutil
                shutil.copyfile(path, target)
        except Exception:
            target = path
        self.task.setdefault("template_paths", []).append(target)
        self.list_templates.insert("end", os.path.basename(target))
        self.tab.save()

    def remove_template(self) -> None:
        selection = list(self.list_templates.curselection())
        if not selection:
            return
        index = selection[0]
        paths = self.task.get("template_paths") or []
        if 0 <= index < len(paths):
            paths.pop(index)
            self.list_templates.delete(index)
            self.tab.save()

    def pick_color(self) -> None:
        def done(point):
            if not point:
                return
            image = S.grab((point[0], point[1], 1, 1))
            if image.size:
                r, g, b = (int(v) for v in image[0, 0][:3])
                self.num_r.set(r)
                self.num_g.set(g)
                self.num_b.set(b)
                self._update_swatch()
                self.app.log(f"已取到颜色 RGB({r}, {g}, {b})")
                self.tab.save()
        self.app.pick_point(done)

    def test(self) -> None:
        self.tab.collect()
        self.app.log("正在测试识别…")
        self.result_label.configure(text="正在识别…", style="Hint.TLabel")

        def worker() -> None:
            info = self.app.recognizer.test_task(self.task)
            self.app.ui_call(self._show_result, info)

        import threading
        threading.Thread(target=worker, daemon=True).start()

    def _show_result(self, info: Dict[str, Any]) -> None:
        ok = info.get("ok")
        self.result_label.configure(text=("✅ " if ok else "❌ ") + info.get("message", ""),
                                    style="Ok.TLabel" if ok else "Danger.TLabel")
        if ok:
            self.app.flash_at(info.get("x", 0), info.get("y", 0), info.get("w", 0),
                              info.get("h", 0), "找到啦")
        self.app.log(info.get("message", ""), "ok" if ok else "warn")

    def delete(self) -> None:
        if not messagebox.askyesno("删除任务", "确定删除这条识别任务吗？"):
            return
        tasks = self.cfg.get_ref("recognize.tasks", []) or []
        for index, item in enumerate(tasks):
            if item.get("id") == self.task.get("id"):
                tasks.pop(index)
                break
        self.cfg.set("recognize.tasks", tasks)
        self.cfg.save()
        self.tab.refresh()

    # ------------------------------------------------------------------
    def collect(self) -> None:
        task = self.task
        task["kind"] = self.combo_kind.get()
        task["keywords"] = [line.strip() for line in
                            self.text_content.get("1.0", "end").splitlines() if line.strip()]
        task["match_mode"] = self.combo_match.get()
        task["threshold"] = self.slider_threshold.get() / 100.0
        task["color"] = [self.num_r.get_int(), self.num_g.get_int(), self.num_b.get_int()]
        task["color_tolerance"] = self.num_tolerance.get_int()
        task["click_where"] = self.var_where.get()
        task["position_id"] = self.combo_position.get()
        task["found_offset_x"] = self.num_offset_x.get_int()
        task["found_offset_y"] = self.num_offset_y.get_int()
        task["button"] = self.combo_button.get()
        task["action"] = self.combo_action.get()
        task["cooldown_s"] = self.num_cooldown.get()
        task["max_hits"] = self.num_max_hits.get_int()
        task["stop_after_hit"] = self.check_stop_after.get()
        task["enabled"] = bool(self.var_enabled.get())
        if self.var_region.get() == "global":
            task["region"] = None
        task["name"] = describe_task(task)
        self.title_label.configure(text=task["name"])
        self.hit_label.configure(text=f"已命中 {task.get('hits', 0)} 次")


class RecognizeTab(ttk.Frame):
    def __init__(self, parent, app) -> None:
        super().__init__(parent, style="TFrame")
        self.app = app
        self.cfg = app.cfg
        self.cards: List[TaskCard] = []

        scroll = ScrollFrame(self)
        scroll.pack(fill="both", expand=True)
        self.scroll = scroll

        self._build_header(scroll.inner)
        self._build_global_region(scroll.inner)
        self.card_holder = ttk.Frame(scroll.inner, style="TFrame")
        self.card_holder.pack(fill="x")
        self._build_actions(scroll.inner)
        self.refresh()
        self.after(1000, self._autosave)

    # ------------------------------------------------------------------
    def _build_header(self, parent) -> None:
        card = Card(parent, title="自动识别点击",
                    subtitle="填一段文字（或截一张小图），软件盯着屏幕看；一旦看到，鼠标就自己去点你指定的位置。")
        card.pack(fill="x", pady=dp(6), padx=dp(2))
        body = card.body
        bar = ttk.Frame(body, style="Card.TFrame")
        bar.grid(row=0, column=0, sticky="ew")
        self.btn_recognize = theme.tk_button(bar, "开始识别", self.toggle_recognize, "ok",
                                             font=FONTS["big"], padx=dp(20), pady=dp(8))
        self.btn_recognize.pack(side="left")
        theme.tk_button(bar, "停止识别", self.stop_recognize, "ghost",
                        pady=dp(8)).pack(side="left", padx=dp(6))
        self.check_auto = CheckField(bar, "连点开始时自动一起启动识别",
                                     bool(self.cfg.get("recognize.enabled")))
        self.check_auto.pack(side="left", padx=dp(14))
        self.engine_label = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.engine_label, style="Hint.TLabel").pack(side="right")
        return card

    def _build_global_region(self, parent) -> None:
        card = Card(parent, title="识别设置")
        card.pack(fill="x", pady=dp(6), padx=dp(2))
        body = card.body
        self.num_poll = NumberField(body, "多久看一次屏幕",
                                    self.cfg.get("recognize.poll_interval_s", 0.5),
                                    from_=0.1, to=10, kind="float", decimals=1, width=6,
                                    suffix="秒",
                                    help="间隔越短反应越快，但更费电脑；0.5 秒适合大多数情况")
        self.num_poll.grid(row=0, column=0, sticky="w", pady=dp(3))

        region_row = ttk.Frame(body, style="Card.TFrame")
        region_row.grid(row=1, column=0, sticky="w", pady=dp(3))
        ttk.Label(region_row, text="全局识别范围", style="Card.TLabel").pack(side="left")
        theme.tk_button(region_row, "框选范围", self.select_global_region, "ghost").pack(
            side="left", padx=dp(6))
        theme.tk_button(region_row, "改回全屏", self.clear_global_region, "ghost").pack(
            side="left", padx=dp(3))
        self.region_label = ttk.Label(region_row, text=self._region_text(), style="Hint.TLabel")
        self.region_label.pack(side="left", padx=dp(8))

        self.check_stop_not_found = CheckField(
            body, "识别引擎不可用时自动停止并提示（推荐）",
            bool(self.cfg.get("recognize.stop_when_not_found")))
        self.check_stop_not_found.grid(row=2, column=0, sticky="w", pady=dp(3))
        self.check_beep = CheckField(body, "命中时发出提示音",
                                     bool(self.cfg.get("recognize.notify_on_hit", True)))
        self.check_beep.grid(row=3, column=0, sticky="w", pady=dp(3))

    def _region_text(self) -> str:
        region = self.cfg.get("recognize.region")
        if not region:
            return "当前：全屏"
        return f"当前：({region[0]}, {region[1]}) {region[2]}×{region[3]}"

    def _build_actions(self, parent) -> None:
        card = Card(parent, title="")
        card.pack(fill="x", pady=dp(6), padx=dp(2))
        bar = ttk.Frame(card.body, style="Card.TFrame")
        bar.pack(fill="x")
        theme.tk_button(bar, "＋ 新建文字识别", lambda: self.add_task("text"), "accent").pack(
            side="left", padx=dp(3))
        theme.tk_button(bar, "＋ 新建图片识别", lambda: self.add_task("image"), "accent").pack(
            side="left", padx=dp(3))
        theme.tk_button(bar, "＋ 新建颜色识别", lambda: self.add_task("color"), "ghost").pack(
            side="left", padx=dp(3))
        ttk.Label(bar, text="提示：先「截图取图」或输入文字，再点任务上的「测试识别」确认能找到。",
                  style="Hint.TLabel").pack(side="right")

    # ------------------------------------------------------------------
    def add_task(self, kind: str) -> None:
        task = new_task(kind)
        if kind == "text":
            task["keywords"] = ["立即抢购"]
            task["name"] = describe_task(task)
        active = self.cfg.active_position()
        if active:
            task["position_id"] = active.get("id", "")
        tasks = self.cfg.get("recognize.tasks") or []
        tasks.append(task)
        self.cfg.set("recognize.tasks", tasks)
        self.cfg.save()
        self.refresh()
        self.app.log(f"已新建任务：{task['name']}，记得点【测试识别】")

    def refresh(self) -> None:
        for card in self.cards:
            try:
                card.destroy()
            except Exception:
                pass
        self.cards = []
        tasks = self.cfg.get_ref("recognize.tasks", []) or []
        for index, task in enumerate(tasks):
            card = TaskCard(self.card_holder, self, task, index)
            card.pack(fill="x", pady=dp(5), padx=dp(2))
            self.cards.append(card)
        if not tasks:
            ttk.Label(self.card_holder,
                      text="还没有识别任务。点下面的「新建文字识别」，输入你要等的文字即可。",
                      style="HintBg.TLabel").pack(anchor="w", padx=dp(8), pady=dp(6))
        try:
            self.scroll._on_inner()
        except Exception:
            pass

    def refresh_task_list(self) -> None:
        tasks = self.cfg.get_ref("recognize.tasks", []) or []
        for card in self.cards:
            if card.task in tasks:
                card.hit_label.configure(text=f"已命中 {card.task.get('hits', 0)} 次")

    # ------------------------------------------------------------------
    def toggle_recognize(self) -> None:
        if self.app.recognizer.is_running():
            self.stop_recognize()
        else:
            self.start_recognize()

    def start_recognize(self) -> None:
        self.collect()
        tasks = [t for t in (self.cfg.get_ref("recognize.tasks", []) or [])
                 if t.get("enabled", True)]
        if not tasks:
            messagebox.showinfo("还没有任务", "请先新建一个识别任务，比如「找文字『立即抢购』」。")
            return
        for task in tasks:
            task["hits"] = 0
            task["last_hit_at"] = 0.0
        engine = self.app.recognizer.engine()
        if engine is None or not getattr(engine, "available", False):
            messagebox.showwarning("识别引擎不可用",
                                   "系统的文字识别引擎没能启动。\n"
                                   "可以先试试「找图片」任务（不需要 OCR）。")
        self.app.recognizer.start()
        self._update_button()

    def stop_recognize(self) -> None:
        self.app.recognizer.stop()
        self._update_button()

    def _update_button(self) -> None:
        running = self.app.recognizer.is_running()
        self.btn_recognize.configure(text="识别中…（点此停止）" if running else "开始识别",
                                     bg=COLORS["danger"] if running else COLORS["ok"])

    # ------------------------------------------------------------------
    def select_global_region(self) -> None:
        def done(region):
            if region:
                self.cfg.set("recognize.region", list(region))
                self.cfg.save()
                self.region_label.configure(text=self._region_text())
                try:
                    self.app.marker.refresh()
                except Exception:
                    pass
                self.app.log(f"全局识别范围已设为 {region}")
        self.app.select_region(done)

    def clear_global_region(self) -> None:
        self.cfg.set("recognize.region", None)
        self.cfg.save()
        self.region_label.configure(text=self._region_text())
        try:
            self.app.marker.refresh()
        except Exception:
            pass

    # ------------------------------------------------------------------
    def collect(self) -> None:
        for card in self.cards:
            card.collect()
        self.cfg.update({
            "recognize": {
                "poll_interval_s": self.num_poll.get(),
                "enabled": bool(self.check_auto.get()),
                "stop_when_not_found": self.check_stop_not_found.get(),
                "notify_on_hit": self.check_beep.get(),
            }
        })
        self.cfg.save()

    def save(self) -> None:
        self.collect()

    def _autosave(self) -> None:
        try:
            self.collect()
            self._update_button()
            engine = self.app.recognizer.engine_label()
            self.engine_label.set(f"文字识别引擎：{engine}")
        except Exception as exc:
            self.app.log(f"识别设置保存出错：{exc}", "error")
        try:
            self.after(1500, self._autosave)
        except Exception:
            pass

    def on_show(self) -> None:
        for card in self.cards:
            card.refresh_positions()
        try:
            self.app.recognizer.probe_async()
        except Exception:
            pass
